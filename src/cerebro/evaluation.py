"""Centralized runtime composition and the offline deterministic reference run.

This module owns the one legitimate way to build a Text-to-SQL runtime. The CLI
and every evaluation path import `build_agent` instead of assembling prompts,
providers, cache keys, snapshots, or agents ad hoc, so a gate can never be
omitted by a caller that forgot it.

Grounding is resolved by the agent's own `GroundingResolver`. No HTTP or MCP
`GroundingResponse` is ever accepted as authorization evidence: `api.py` stays
advisory metadata retrieval.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import Field, ValidationError, model_validator

from .bundle import load_validated_bundle
from .complexity import ComplexityRouter
from .executor import DuckDBExecutor
from .hosted_provider import CassetteProvider, GuardedProvider, OrganizerModelGateway
from .models import (
    AuthorizationScope,
    BudgetLimits,
    BudgetUsage,
    CacheStatus,
    Classification,
    ColumnName,
    DisclosureRecord,
    OutputLineage,
    ResponseGenerationRoute,
    ScalarType,
    Sha256,
    SQLArtifact,
    SQLGenerationRequest,
    StrictFrozenModel,
    VersionTag,
)
from .paths import DEFAULT_BUNDLE, ROOT
from .provenance import (
    authorization_scope_sha256,
    canonical_question_sha256,
    canonicalize_question,
)
from .retrieval import GroundingResolver, SemanticRetriever
from .selfcheck import DisclosureCaps
from .sql_compiler import DialectCompiler
from .text2sql import RuntimeVersions, Text2SQLAgent
from .text2sql_cache import Text2SQLCache
from .text2sql_provider import Text2SQLGenerationProvider

RETRIEVAL_CONFIG_VERSION = "008.retrieval.v1"
OFFLINE_REFERENCE_VERSION = "008.reference.v1"
DEFAULT_SUPPORTED_QUESTIONS = (
    ROOT / "tests" / "fixtures" / "text2sql-supported-questions.yaml"
)

#: The only provider compositions that exist. There is no implicit fallback
#: between them: a caller states which transport it wants, and a mismatch is an
#: error rather than a silent downgrade to a scripted or live provider.
PROVIDER_MODES = ("organizer", "cassette", "scripted")

_DEFAULT_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"public", "internal", "confidential", "restricted"}
)


class RuntimeCompositionError(Exception):
    """Raised when a runtime cannot be composed from explicit inputs."""


class ReferenceQuestionError(Exception):
    """Raised when the supported-question fixture is absent or malformed."""


def retrieval_config_sha256(*, top_k: int, depth: int, dialect: str = "duckdb") -> str:
    """Hash the exact retrieval configuration the snapshot was resolved under."""
    payload = json.dumps(
        {
            "version": RETRIEVAL_CONFIG_VERSION,
            "ranking": "lexical",
            "embedder": None,
            "top_k": top_k,
            "depth": depth,
            "dialect": dialect,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# --- authorization scope input ---------------------------------------------


def build_authorization_scope(
    *,
    allowed_object_ids: Iterable[str],
    policy_version: str,
    tenant_scope_hash: str,
    allowed_classifications: Iterable[str] | None = None,
) -> AuthorizationScope:
    """Build a scope whose integrity hash is derived from its own payload."""
    draft = AuthorizationScope(
        scope_version="008.scope.v1",
        tenant_scope_hash=tenant_scope_hash,
        policy_version=policy_version,
        allowed_object_ids=frozenset(allowed_object_ids),
        allowed_classifications=frozenset(
            allowed_classifications
            if allowed_classifications is not None
            else _DEFAULT_CLASSIFICATIONS
        ),
        authorization_scope_hash="0" * 64,
    )
    return draft.model_copy(
        update={"authorization_scope_hash": authorization_scope_sha256(draft)}
    )


def load_authorization_scope(path: Path | str) -> AuthorizationScope:
    """Load one explicit trusted authorization scope from an operator input.

    The hash field is integrity evidence, not authorization, so an input that
    omits it gets it derived. An input that declares a mismatching hash is
    rejected: silently repairing it would erase the only tamper signal.
    """
    location = Path(path)
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeCompositionError(
            "authorization scope input is missing or unreadable"
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeCompositionError(
            "authorization scope input is not valid JSON"
        ) from error
    if not isinstance(payload, Mapping):
        raise RuntimeCompositionError("authorization scope input must be an object")

    declared = payload.get("authorization_scope_hash")
    try:
        scope = build_authorization_scope(
            allowed_object_ids=payload.get("allowed_object_ids") or (),
            policy_version=str(payload.get("policy_version", "")),
            tenant_scope_hash=str(payload.get("tenant_scope_hash", "")),
            allowed_classifications=payload.get("allowed_classifications"),
        )
    except ValidationError as error:
        raise RuntimeCompositionError(
            "authorization scope input does not satisfy the scope contract"
        ) from error
    if declared is not None and declared != scope.authorization_scope_hash:
        raise RuntimeCompositionError(
            "authorization scope hash does not match its payload"
        )
    return scope


# --- runtime composition ----------------------------------------------------


@dataclass(frozen=True)
class AgentRuntime:
    """One fully composed runtime plus the identities its evidence must cite."""

    agent: Text2SQLAgent
    resolver: GroundingResolver
    provider: Any
    cache: Text2SQLCache
    engine: DuckDBExecutor
    versions: RuntimeVersions
    authorization_scope: AuthorizationScope
    budget_limits: BudgetLimits
    disclosure_caps: DisclosureCaps
    provider_mode: str
    database_path: str
    bundle_path: str
    semantic_version: str
    retrieval_config_hash: str

    def close(self) -> None:
        """Release the read-only engine connection this runtime owns."""
        self.engine.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> Literal[False]:
        self.close()
        return False


def _compose_provider(
    provider_mode: str,
    provider: Any,
    *,
    cassette_path: Path | str | None,
    environ: Mapping[str, str] | None,
    gateway_options: Mapping[str, Any] | None,
) -> Any:
    if provider_mode not in PROVIDER_MODES:
        raise RuntimeCompositionError(
            f"unknown provider mode: expected one of {', '.join(PROVIDER_MODES)}"
        )

    if provider_mode == "scripted":
        if provider is None:
            raise RuntimeCompositionError(
                "scripted mode requires an explicit offline provider"
            )
        # Structural conformance is the contract: a scripted double is accepted
        # only when it already speaks the guarded provider protocol.
        if not isinstance(provider, Text2SQLGenerationProvider):
            raise RuntimeCompositionError(
                "a scripted provider must implement Text2SQLGenerationProvider"
            )
        return provider

    if provider is not None:
        raise RuntimeCompositionError(
            f"{provider_mode} mode builds its own provider and accepts no override"
        )

    if provider_mode == "cassette":
        if cassette_path is None:
            raise RuntimeCompositionError("cassette mode requires a cassette path")
        return GuardedProvider(CassetteProvider(Path(cassette_path)))

    gateway = OrganizerModelGateway.from_environment(
        dict(environ if environ is not None else os.environ),
        **dict(gateway_options or {}),
    )
    return GuardedProvider(gateway)


def build_agent(
    database_path: Path | str,
    provider_mode: str,
    authorization_scope: AuthorizationScope,
    provider: Any = None,
    *,
    bundle_path: Path | str = DEFAULT_BUNDLE,
    top_k: int = 10,
    depth: int = 1,
    budget_limits: BudgetLimits | None = None,
    disclosure_caps: DisclosureCaps | None = None,
    cassette_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    gateway_options: Mapping[str, Any] | None = None,
) -> AgentRuntime:
    """Compose the single authorized runtime for one process.

    The order is fixed: trusted scope, canonicalizer/resolver, guarded provider,
    router, compiler, cache, then validator/executor. Nothing downstream may
    substitute a gate, so no caller can accidentally build a partial pipeline.
    """
    if not isinstance(authorization_scope, AuthorizationScope):
        raise RuntimeCompositionError(
            "composition requires a trusted AuthorizationScope"
        )
    if authorization_scope_sha256(authorization_scope) != (
        authorization_scope.authorization_scope_hash
    ):
        raise RuntimeCompositionError(
            "authorization scope hash does not match its payload"
        )

    composed_provider = _compose_provider(
        provider_mode,
        provider,
        cassette_path=cassette_path,
        environ=environ,
        gateway_options=gateway_options,
    )

    bundle = load_validated_bundle(bundle_path)
    retrieval_config_hash = retrieval_config_sha256(top_k=top_k, depth=depth)
    resolver = GroundingResolver(
        SemanticRetriever(bundle),
        retrieval_config_hash=retrieval_config_hash,
        top_k=top_k,
        depth=depth,
    )
    versions = RuntimeVersions(
        semantic_version=bundle.version,
        policy_version=authorization_scope.policy_version,
    )
    engine = DuckDBExecutor(str(database_path))
    cache = Text2SQLCache()
    agent = Text2SQLAgent(
        resolver=resolver,
        provider=composed_provider,
        router=ComplexityRouter(),
        compiler=DialectCompiler("duckdb"),
        cache=cache,
        validator=engine,
        executor=engine,
        disclosure_caps=disclosure_caps or DisclosureCaps.defaults(),
        budget_limits=budget_limits or BudgetLimits.defaults(),
        versions=versions,
    )
    return AgentRuntime(
        agent=agent,
        resolver=resolver,
        provider=composed_provider,
        cache=cache,
        engine=engine,
        versions=versions,
        authorization_scope=authorization_scope,
        budget_limits=budget_limits or BudgetLimits.defaults(),
        disclosure_caps=disclosure_caps or DisclosureCaps.defaults(),
        provider_mode=provider_mode,
        database_path=str(database_path),
        bundle_path=str(bundle_path),
        semantic_version=bundle.version,
        retrieval_config_hash=retrieval_config_hash,
    )


# --- supported question fixtures -------------------------------------------


@dataclass(frozen=True)
class ReferenceQuestion:
    """One supported capability case, identified by ID rather than by text."""

    id: str
    question: str

    @property
    def canonical_question(self) -> str:
        return canonicalize_question(self.question)

    @property
    def canonical_question_hash(self) -> str:
        return canonical_question_sha256(self.canonical_question)


def load_reference_questions(
    path: Path | str = DEFAULT_SUPPORTED_QUESTIONS,
) -> tuple[ReferenceQuestion, ...]:
    """Load the supported capability set without reordering or deduplicating."""
    location = Path(path)
    try:
        raw = yaml.safe_load(location.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReferenceQuestionError("question fixture is missing") from error
    if not isinstance(raw, Mapping) or not isinstance(raw.get("questions"), list):
        raise ReferenceQuestionError("question fixture must declare a question list")
    questions: list[ReferenceQuestion] = []
    seen: set[str] = set()
    for entry in raw["questions"]:
        if not isinstance(entry, Mapping):
            raise ReferenceQuestionError("each question must be a mapping")
        question_id = str(entry.get("id", ""))
        text = str(entry.get("question", ""))
        if not question_id or not text:
            raise ReferenceQuestionError("each question needs an id and a question")
        if question_id in seen:
            raise ReferenceQuestionError(f"duplicate question id: {question_id}")
        seen.add(question_id)
        questions.append(ReferenceQuestion(id=question_id, question=text))
    if not questions:
        raise ReferenceQuestionError("question fixture declares no questions")
    return tuple(questions)


# --- offline reference evidence --------------------------------------------


class OfflineReferenceEntry(StrictFrozenModel):
    """One persisted reference outcome. It carries no question text or values."""

    question_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    canonical_question_hash: Sha256
    authorization_scope_hash: Sha256
    snapshot_hash: Sha256
    status: Literal["ok", "refused", "check_failed"]
    generation_route: ResponseGenerationRoute
    cache_status: CacheStatus
    semantic_calls: int = Field(ge=0)
    budget_usage: BudgetUsage
    ir_hash: Sha256 | None = None
    sql_artifact: SQLArtifact | None = None
    result_columns: tuple[ColumnName, ...] = ()
    result_column_types: tuple[ScalarType, ...] = ()
    row_count: int | None = Field(default=None, ge=0)
    output_lineage: tuple[OutputLineage, ...] = ()
    disclosures: tuple[DisclosureRecord, ...] = ()
    violation_codes: tuple[str, ...] = ()
    refusal_reason: str | None = None

    @model_validator(mode="after")
    def _status_matches_evidence(self) -> OfflineReferenceEntry:
        if self.status == "ok":
            if self.sql_artifact is None or self.row_count is None:
                raise ValueError("an ok entry reports its artifact and row count")
            if not self.output_lineage:
                raise ValueError("an ok entry reports output lineage")
        if self.status == "refused" and self.refusal_reason is None:
            raise ValueError("a refusal reports its typed reason")
        if self.status == "check_failed" and not self.violation_codes:
            raise ValueError("a check failure reports at least one violation code")
        if self.semantic_calls != self.budget_usage.semantic_calls:
            raise ValueError("semantic calls must agree with the budget usage")
        return self


class OfflineReferenceArtifact(StrictFrozenModel):
    """The value-free artifact `cerebro reference` writes."""

    run_kind: Literal["offline_reference"]
    reference_version: Literal["008.reference.v1"]
    semantic_version: VersionTag
    policy_version: VersionTag
    dialect: Literal["duckdb"]
    provider: str
    model: str
    model_revision: str
    schema_mechanism: str
    authorization_scope_hash: Sha256
    retrieval_config_sha256: Sha256
    canonicalization_version: Literal["008.question.v1"]
    literal_registry_version: Literal["008.literal-span.v1"]
    ir_contract_version: Literal["008.ir.v1"]
    type_registry_version: Literal["008.types.v1"]
    prompt_version: VersionTag
    router_version: VersionTag
    compiler_version: VersionTag
    checker_version: VersionTag
    budget_limits: BudgetLimits
    total_questions: int = Field(ge=1)
    ok_count: int = Field(ge=0)
    entries: tuple[OfflineReferenceEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _totals_are_derived_from_entries(self) -> OfflineReferenceArtifact:
        if self.total_questions != len(self.entries):
            raise ValueError("the reported total must equal the entry count")
        if self.ok_count != sum(1 for item in self.entries if item.status == "ok"):
            raise ValueError("the ok count must be derived from the entries")
        identifiers = [item.question_id for item in self.entries]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("every entry must carry a unique question id")
        return self


@dataclass(frozen=True)
class OfflineReferenceRun:
    """The persisted artifact plus the in-memory responses tests assert on."""

    artifact: OfflineReferenceArtifact
    responses_by_id: Mapping[str, Any]

    @property
    def entries_by_id(self) -> Mapping[str, OfflineReferenceEntry]:
        return {item.question_id: item for item in self.artifact.entries}


def _entry_from_response(
    question: ReferenceQuestion, response: Any
) -> OfflineReferenceEntry:
    result = getattr(response, "result", None)
    artifact = getattr(response, "sql_artifact", None)
    return OfflineReferenceEntry(
        question_id=question.id,
        canonical_question_hash=response.canonical_question_hash,
        authorization_scope_hash=response.authorization_scope_hash,
        snapshot_hash=response.snapshot_hash,
        status=response.status,
        generation_route=response.generation_route,
        cache_status=response.cache_status,
        semantic_calls=response.budget_usage.semantic_calls,
        budget_usage=response.budget_usage,
        ir_hash=artifact.ir_hash if artifact is not None else None,
        sql_artifact=artifact,
        result_columns=tuple(result.columns) if result is not None else (),
        result_column_types=tuple(result.column_types) if result is not None else (),
        row_count=result.row_count if result is not None else None,
        output_lineage=tuple(getattr(response, "output_lineage", ()) or ()),
        disclosures=tuple(getattr(response, "disclosures", ()) or ()),
        violation_codes=tuple(
            violation.code for violation in getattr(response, "violations", ()) or ()
        ),
        refusal_reason=getattr(response, "reason", None),
    )


def run_offline_reference(
    runtime: AgentRuntime,
    questions: Sequence[ReferenceQuestion] | None = None,
    *,
    questions_path: Path | str = DEFAULT_SUPPORTED_QUESTIONS,
    max_rows: int = 1000,
) -> OfflineReferenceRun:
    """Run the supported capability set through the real compiler, offline.

    Reference runs never use the live organizer: a deterministic baseline that
    could reach a hosted model would not be a reference at all.
    """
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeCompositionError("the reference run requires a composed runtime")
    if runtime.provider_mode == "organizer":
        raise RuntimeCompositionError(
            "the offline reference accepts only scripted or cassette providers"
        )
    cases = (
        tuple(questions)
        if questions is not None
        else load_reference_questions(questions_path)
    )

    entries: list[OfflineReferenceEntry] = []
    responses: dict[str, Any] = {}
    for case in cases:
        response = runtime.agent.run(
            SQLGenerationRequest(
                question=case.question,
                authorization_scope=runtime.authorization_scope,
                dialect="duckdb",
                max_rows=max_rows,
            )
        )
        responses[case.id] = response
        entries.append(_entry_from_response(case, response))

    provider = runtime.provider
    artifact = OfflineReferenceArtifact(
        run_kind="offline_reference",
        reference_version=OFFLINE_REFERENCE_VERSION,
        semantic_version=runtime.semantic_version,
        policy_version=runtime.authorization_scope.policy_version,
        dialect="duckdb",
        provider=getattr(provider, "provider", "unknown"),
        model=getattr(provider, "model", "unknown"),
        model_revision=getattr(provider, "model_revision", "unknown"),
        schema_mechanism=getattr(provider, "schema_mechanism", "json_schema"),
        authorization_scope_hash=(runtime.authorization_scope.authorization_scope_hash),
        retrieval_config_sha256=runtime.retrieval_config_hash,
        canonicalization_version="008.question.v1",
        literal_registry_version="008.literal-span.v1",
        ir_contract_version="008.ir.v1",
        type_registry_version="008.types.v1",
        prompt_version=runtime.versions.prompt_version,
        router_version=runtime.versions.router_version,
        compiler_version=runtime.agent.compiler_version,
        checker_version=runtime.versions.checker_version,
        budget_limits=runtime.budget_limits,
        total_questions=len(entries),
        ok_count=sum(1 for item in entries if item.status == "ok"),
        entries=tuple(entries),
    )
    return OfflineReferenceRun(artifact=artifact, responses_by_id=responses)


def write_offline_reference(
    artifact: OfflineReferenceArtifact, output: Path | str
) -> Path:
    """Write a validated offline artifact atomically, and nothing else."""
    if not isinstance(artifact, OfflineReferenceArtifact):
        raise RuntimeCompositionError("only a validated offline artifact is written")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    temporary.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


# --- legacy grounding-only retrieval evaluation -----------------------------


def run_evaluation(
    bundle_path: Path | str = DEFAULT_BUNDLE,
    questions_path: Path | str = ROOT / "evaluation" / "golden-questions.yaml",
) -> list[dict]:
    """Score advisory grounding retrieval only. This authorizes no execution."""
    retriever = SemanticRetriever(load_validated_bundle(bundle_path))
    cases = yaml.safe_load(Path(questions_path).read_text(encoding="utf-8"))[
        "questions"
    ]
    results = []
    for case in cases:
        grounding = retriever.grounding(case["question"], limit=10)
        actual = {
            item["id"]
            for item in grounding.concepts
            + grounding.tables
            + grounding.metrics
            + grounding.joins
        }
        expected = set(case["required_ids"])
        missing = sorted(expected - actual)
        results.append(
            {
                "id": case["id"],
                "passed": not missing,
                "missing": missing,
                "question": case["question"],
            }
        )
    return results


_ = Classification  # re-exported vocabulary for scope inputs
