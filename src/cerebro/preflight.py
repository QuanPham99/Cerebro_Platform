"""Read-only runtime/evidence readiness and explicit organizer capability probe."""
from __future__ import annotations
from datetime import datetime,timezone
from importlib.metadata import version
from pathlib import Path
import os
import subprocess
from . import query_models as m
from .provenance import QueryFault,digest,atomic_json,sha256_file
from .paths import ROOT
from .grounding import GroundingResolver,trusted_scope
from .models import SemanticBundle
from .prompting import GuardedProvider
from .query_budget import RequestBudget
from .text2sql_provider import GuardedGenerationRequest

class PreflightReport(m.StrictFrozenModel):
    offline_ready: bool
    live_prerequisites_ready: bool
    blockers: tuple[m.CheckViolation,...]


def runtime_revision():
    try:
        dirty=subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,text=True)
        revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        if dirty or len(revision)!=40: raise QueryFault('unverified_code_revision')
        return revision
    except (OSError,subprocess.SubprocessError): raise QueryFault('unverified_code_revision') from None


def check_preflight(*,csv_dir=None,manifest_path=None,bundle_path=None,environ=None,database_path=None,materialization_receipt_path=None,provider_capability_receipt_path=None):
    env=os.environ if environ is None else environ
    blockers=[]
    def missing(condition,code):
        if condition: blockers.append(m.CheckViolation(code=code))
    offline=version('duckdb')=='1.5.5' and version('sqlglot')=='30.17.0'
    missing(not offline,'runtime_version_mismatch')
    for key,code in [('CEREBRO_API_KEY','missing_api_key'),('CEREBRO_MODEL','missing_model'),('CEREBRO_BASE_URL','missing_provider_endpoint'),('CEREBRO_MODEL_REVISION','missing_model_revision')]:
        missing(not env.get(key),code)
    for path,code in [(manifest_path,'missing_data_manifest'),(bundle_path,'missing_bundle'),(csv_dir,'missing_csv_directory'),(database_path,'missing_database'),(materialization_receipt_path,'missing_materialization_receipt'),(provider_capability_receipt_path,'missing_provider_capability')]:
        missing(path is None or not Path(path).exists(),code)
    try: runtime_revision()
    except QueryFault as e: blockers.append(m.CheckViolation(code=e.code))
    return PreflightReport(offline_ready=offline,live_prerequisites_ready=not blockers,blockers=tuple(blockers))


def probe_capability(provider,run_id):
    bundle=SemanticBundle(name='provider-probe',version='008.probe.v1',root='',objects=[])
    question='Identify missing table.atms'
    snapshot=GroundingResolver(bundle).resolve(question,trusted_scope(bundle,tenant='provider-probe'))
    budget=RequestBudget();budget.before_call('default_ir')
    request=GuardedGenerationRequest(question,snapshot,'provider_probe')
    generated=GuardedProvider(provider).generate(request,m.OUTCOME_ADAPTER,budget=budget)
    budget.account(generated)
    if not isinstance(generated.output,m.GroundingRefusal) or tuple(x.object_id for x in generated.output.unmet_needs)!=('table.atms',):
        raise QueryFault('provider_capability_failed')
    payload=dict(run_id=run_id,provider=provider.name,model=provider.model,model_revision=provider.model_revision,
                 schema_mechanism=provider.schema_mechanism,verified_at=datetime.now(timezone.utc).isoformat(),
                 request_sha256=digest({'schema':m.OUTCOME_ADAPTER.json_schema(),'snapshot':snapshot.snapshot_hash,'question':question}))
    return m.ProviderCapabilityReceipt(**payload,receipt_sha256=digest(payload))
