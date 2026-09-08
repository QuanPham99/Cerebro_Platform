"""Separate offline reference and evidence-bound live baseline runners."""
from __future__ import annotations
import hashlib
from pathlib import Path
import uuid
from datetime import datetime,timezone
from .preflight import runtime_revision,probe_capability
from .hosted_provider import HostedProvider
import yaml
from .bundle import load_validated_bundle
from .executor import DuckDBExecutor
from .grounding import GroundingResolver,trusted_scope
from .hosted_provider import provider_from_environment
from .prompting import GuardedProvider
from .query_models import SQLGenerationRequest,MaterializationReceipt,ProviderCapabilityReceipt,OkResponse
from .paths import DEFAULT_BUNDLE,ROOT
from .provenance import QueryFault,atomic_json,digest,sha256_file,bundle_digest
from .reference import GoldenProvider,policy_literals
from .retrieval import SemanticRetriever
from .text2sql import Text2SQLAgent


def run_evaluation(bundle_path=DEFAULT_BUNDLE,questions_path=ROOT/'evaluation'/'golden-questions.yaml'):
    retriever=SemanticRetriever(load_validated_bundle(bundle_path));results=[]
    for case in yaml.safe_load(Path(questions_path).read_text())['questions']:
        g=retriever.grounding(case['question'],limit=10)
        actual={x['id'] for x in g.concepts+g.tables+g.metrics+g.joins}
        missing=sorted(set(case['required_ids'])-actual)
        results.append(dict(id=case['id'],passed=not missing,missing=missing,question=case['question']))
    return results


def build_agent(database_path,provider=None,bundle=None,cache=None):
    bundle=bundle or load_validated_bundle(DEFAULT_BUNDLE)
    provider=provider if provider is not None else provider_from_environment()
    executor=DuckDBExecutor(database_path)
    resolver=GroundingResolver(bundle,governed_literals=policy_literals())
    return Text2SQLAgent(GuardedProvider(provider),resolver,executor,cache=cache),executor


def _run(bundle_path,questions_path,database_path,provider,run_kind):
    bundle=load_validated_bundle(bundle_path);agent,executor=build_agent(database_path,provider,bundle)
    scope=trusted_scope(bundle,tenant='local-cli')
    cases=yaml.safe_load(Path(questions_path).read_text())['questions'];entries=[]
    try:
        for case in cases:
            response=agent.run(SQLGenerationRequest(question=case['question'],authorization_scope=scope))
            # No question, source/result values, literal resolutions or credentials.
            evidence=response.model_dump(mode='json',exclude={'result'})
            entries.append(dict(id=case['id'],status=response.status,evidence=evidence))
    finally: executor.close()
    totals={status:sum(x['status']==status for x in entries) for status in ('ok','refused','check_failed')}
    return dict(run_id=str(uuid.uuid4()),run_kind=run_kind,provider=agent.provider.name,model=agent.provider.model,
                model_revision=agent.provider.model_revision,schema_mechanism=agent.provider.schema_mechanism,
                semantic_version=bundle.version,bundle_sha256=bundle_digest(bundle),database_sha256=sha256_file(database_path),
                golden_sha256=sha256_file(questions_path),questions=entries,totals=totals,
                decoding_defects=sum(any(v['code'].startswith('unparsable_') for v in e['evidence']['violations']) for e in entries),
                resource_limits=executor.effective_limits)


def run_offline_reference(bundle_path=DEFAULT_BUNDLE,questions_path=ROOT/'evaluation'/'golden-questions.yaml',database_path=ROOT/'data'/'workshop.duckdb',artifact_path=None):
    report=_run(bundle_path,questions_path,database_path,GoldenProvider(),'offline_reference')
    if artifact_path is not None: atomic_json(artifact_path,report)
    return report


def validate_live_evidence(report,*,materialization,capability,bundle_path,database_path,questions_path):
    if materialization.source_kind!='authoritative': raise QueryFault('live_baseline_blocked')
    if report.get('run_kind')!='live_unadapted_baseline' or report.get('provider')!='organizer': raise QueryFault('live_baseline_blocked')
    if digest(materialization,exclude={'receipt_sha256'})!=materialization.receipt_sha256 or digest(capability,exclude={'receipt_sha256'})!=capability.receipt_sha256: raise QueryFault('evidence_integrity_error')
    if materialization.database_sha256!=sha256_file(database_path) or materialization.bundle_sha256!=bundle_digest(load_validated_bundle(bundle_path)): raise QueryFault('evidence_drift')
    if report['database_sha256']!=materialization.database_sha256 or report['bundle_sha256']!=materialization.bundle_sha256 or report['golden_sha256']!=sha256_file(questions_path): raise QueryFault('evidence_drift')
    if any(report[k]!=getattr(capability,k) for k in ('provider','model','model_revision','schema_mechanism')): raise QueryFault('evidence_drift')
    cases=yaml.safe_load(Path(questions_path).read_text())['questions'];ids=[x['id'] for x in report['questions']]
    if len(ids)!=10 or len(set(ids))!=10 or set(ids)!={x['id'] for x in cases}: raise QueryFault('golden_set_mismatch')
    totals={s:sum(e['status']==s for e in report['questions']) for s in ('ok','refused','check_failed')}
    if totals!=report['totals'] or not totals['ok']: raise QueryFault('invalid_baseline_totals')
    if report['run_id']!=capability.run_id: raise QueryFault('stale_capability_receipt')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(capability.verified_at)).total_seconds()
    if age<0 or age>3600: raise QueryFault('stale_capability_receipt')
    if report.get('code_revision')!=runtime_revision(): raise QueryFault('evidence_drift')
    return report


def run_sql_baseline(bundle_path=DEFAULT_BUNDLE,questions_path=ROOT/'evaluation'/'golden-questions.yaml',database_path=ROOT/'data'/'workshop.duckdb',provider=None,artifact_path=None,materialization_receipt_path=None,capability_receipt_path=None):
    if not materialization_receipt_path or artifact_path is None: raise QueryFault('live_baseline_blocked')
    try:
        materialization=MaterializationReceipt.model_validate_json(Path(materialization_receipt_path).read_text())
    except (OSError,ValueError): raise QueryFault('live_baseline_blocked') from None
    # Verify all local prerequisites before spending organizer quota.
    if materialization.source_kind!='authoritative': raise QueryFault('live_baseline_blocked')
    revision=runtime_revision()
    if digest(materialization,exclude={'receipt_sha256'})!=materialization.receipt_sha256 or materialization.database_sha256!=sha256_file(database_path) or materialization.bundle_sha256!=bundle_digest(load_validated_bundle(bundle_path)):
        raise QueryFault('evidence_drift')
    provider=provider if provider is not None else provider_from_environment()
    if type(provider) is not HostedProvider: raise QueryFault('live_baseline_blocked')
    run_id=str(uuid.uuid4())
    # Old capability receipts cannot be promoted into a new run. A fresh probe
    # verifies the same union schema through the same guarded provider boundary.
    capability=probe_capability(provider,run_id)
    evidence_dir=Path(artifact_path).parent/'evidence'/run_id
    evidence_dir.mkdir(parents=True,exist_ok=False)
    retained_materialization=evidence_dir/'materialization.json'
    retained_capability=evidence_dir/'capability.json'
    atomic_json(retained_materialization,materialization)
    atomic_json(retained_capability,capability)
    report=_run(bundle_path,questions_path,database_path,provider,'live_unadapted_baseline')
    report.update(run_id=run_id,code_revision=revision,
                  materialization_receipt_sha256=materialization.receipt_sha256,
                  capability_receipt_sha256=capability.receipt_sha256,
                  materialization_receipt_path=str(retained_materialization.resolve()),
                  capability_receipt_path=str(retained_capability.resolve()))
    validate_live_evidence(report,materialization=MaterializationReceipt.model_validate_json(retained_materialization.read_text()),capability=ProviderCapabilityReceipt.model_validate_json(retained_capability.read_text()),bundle_path=bundle_path,database_path=database_path,questions_path=questions_path)
    atomic_json(artifact_path,report)
    return report
