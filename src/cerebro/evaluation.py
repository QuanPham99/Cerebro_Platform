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


# --- live Option B baseline --------------------------------------------------

EXPECTED_GOLDEN_QUESTIONS = ROOT / "evaluation" / "golden-questions.yaml"
EXPECTED_GOLDEN_QUESTION_COUNT = 10
_GENERATION_STAGES = frozenset({"default_ir", "planned_ir"})


class LiveBaselineError(Exception):
    """Raised when a live baseline cannot even be attempted."""


def checker_sha256() -> str:
    """Hash the exact checker implementation this run authorized SQL with."""
    from . import selfcheck as _selfcheck

    return hashlib.sha256(Path(_selfcheck.__file__).read_bytes()).hexdigest()


def budget_limits_sha256(limits: BudgetLimits) -> str:
    return hashlib.sha256(limits.model_dump_json().encode("utf-8")).hexdigest()


def code_revision() -> tuple[str, bool]:
    """Return the exact revision and whether the working tree is dirty.

    An unknown revision is reported as such rather than guessed: a baseline that
    cannot name its own code is not reproducible, and validation blocks on it.
    """
    import subprocess

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    tracked = [
        line
        for line in status.splitlines()
        if line.strip() and not line.startswith("??")
    ]
    return (revision or "unknown"), bool(tracked)


@dataclass(frozen=True)
class RetainedEvidencePaths:
    """The immutable inputs a baseline must be able to reread afterwards."""

    source_manifest: Path
    materialization_receipt: Path
    bundle: Path
    database: Path
    capability_receipt: Path
    golden_set: Path


@dataclass(frozen=True)
class ValidatedLiveEvidence:
    """External evidence, resolved and hashed once, ready to be rechecked."""

    paths: RetainedEvidencePaths
    source_manifest_sha256: str
    materialization_receipt_sha256: str
    bundle_sha256: str
    database_sha256: str
    capability_receipt_sha256: str
    golden_set_sha256: str
    capability_receipt: Any
    materialization_receipt: Any
    expected_question_ids: tuple[str, ...]
    code_revision: str
    code_dirty: bool


def _hash_retained(paths: RetainedEvidencePaths) -> dict[str, str]:
    from .bundle import load_validated_bundle as _load
    from .provenance import semantic_bundle_sha256, sha256_file

    return {
        "source_manifest_sha256": sha256_file(paths.source_manifest),
        "materialization_receipt_sha256": sha256_file(paths.materialization_receipt),
        "bundle_sha256": semantic_bundle_sha256(_load(paths.bundle)),
        "database_sha256": sha256_file(paths.database),
        "capability_receipt_sha256": sha256_file(paths.capability_receipt),
        "golden_set_sha256": sha256_file(paths.golden_set),
    }


def prepare_live_evidence(
    *,
    source_manifest: Path | str,
    materialization_receipt: Path | str,
    bundle: Path | str,
    database: Path | str,
    capability_receipt: Path | str,
    golden_set: Path | str = EXPECTED_GOLDEN_QUESTIONS,
) -> ValidatedLiveEvidence:
    """Resolve, read, and hash every retained evidence path exactly once."""
    from .models import MaterializationReceipt, ProviderCapabilityReceipt

    paths = RetainedEvidencePaths(
        source_manifest=Path(source_manifest).resolve(),
        materialization_receipt=Path(materialization_receipt).resolve(),
        bundle=Path(bundle).resolve(),
        database=Path(database).resolve(),
        capability_receipt=Path(capability_receipt).resolve(),
        golden_set=Path(golden_set).resolve(),
    )
    missing = [
        name
        for name, value in (
            ("source manifest", paths.source_manifest),
            ("materialization receipt", paths.materialization_receipt),
            ("bundle", paths.bundle),
            ("database", paths.database),
            ("capability receipt", paths.capability_receipt),
            ("golden set", paths.golden_set),
        )
        if not value.exists()
    ]
    if missing:
        raise LiveBaselineError(f"missing live evidence: {', '.join(sorted(missing))}")

    try:
        receipt = ProviderCapabilityReceipt.model_validate_json(
            paths.capability_receipt.read_bytes()
        )
    except ValidationError as error:
        raise LiveBaselineError("capability receipt is not valid evidence") from error

    try:
        materialization = MaterializationReceipt.model_validate_json(
            paths.materialization_receipt.read_bytes()
        )
    except ValidationError as error:
        raise LiveBaselineError(
            "materialization receipt is not valid evidence"
        ) from error

    expected = tuple(item.id for item in load_reference_questions(paths.golden_set))
    revision, dirty = code_revision()
    return ValidatedLiveEvidence(
        paths=paths,
        capability_receipt=receipt,
        materialization_receipt=materialization,
        expected_question_ids=expected,
        code_revision=revision,
        code_dirty=dirty,
        **_hash_retained(paths),
    )


@dataclass(frozen=True)
class LiveBaselineCandidate:
    """One completed live run, before validation decides whether to publish it.

    Only `run_live_baseline` constructs this. Runtime identity is read off the
    gateway that actually answered, so a caller cannot assert an identity or
    supply its own capability receipt.
    """

    run_id: str
    provider: str
    model: str
    model_revision: str
    schema_mechanism: str
    capability_receipt_sha256: str
    probe_count: int
    probed_before_first_question: bool
    authorization_scope_hash: str
    semantic_version: str
    policy_version: str
    retrieval_config_sha256: str
    prompt_version: str
    router_version: str
    checker_version: str
    compiler_version: str
    budget_limits: BudgetLimits
    questions: tuple[Any, ...]
    canonical_questions: tuple[str, ...]
    #: Per-response contract versions, in question order. Drift in any one of
    #: them makes the run's evidence incomparable with the declared provenance.
    contract_versions: tuple[tuple[str, str, str, str], ...]
    reported_total: int

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "provider",
            "model",
            "model_revision",
            "schema_mechanism",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise TypeError(f"{field_name} must be derived from the live gateway")


def _question_from_response(question_id: str, response: Any) -> Any:
    from .models import EvaluationQuestion

    result = getattr(response, "result", None)
    artifact = getattr(response, "sql_artifact", None)
    return EvaluationQuestion(
        question_id=question_id,
        canonical_question_hash=response.canonical_question_hash,
        authorization_scope_hash=response.authorization_scope_hash,
        snapshot_hash=response.snapshot_hash,
        status=response.status,
        generation_route=response.generation_route,
        cache_status=response.cache_status,
        ir_hash=artifact.ir_hash if artifact is not None else None,
        sql_artifact=artifact,
        attempt_records=tuple(response.attempt_records),
        budget_usage=response.budget_usage,
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


def run_live_baseline(
    runtime: AgentRuntime,
    *,
    capability_receipt_dir: Path | str,
    questions_path: Path | str = EXPECTED_GOLDEN_QUESTIONS,
    max_rows: int = 1000,
    run_id: str | None = None,
) -> tuple[LiveBaselineCandidate, Path]:
    """Run the golden set live, probing the real gateway exactly once first."""
    import uuid

    from .hosted_provider import probe_provider_schema
    from .provenance import sha256_file

    if not isinstance(runtime, AgentRuntime):
        raise LiveBaselineError("the live baseline requires a composed runtime")
    if runtime.provider_mode != "organizer":
        raise LiveBaselineError("the live baseline accepts only the live organizer")
    gateway = getattr(runtime.provider, "_inner", None)
    if gateway is None:
        raise LiveBaselineError(
            "the live baseline requires a guarded organizer gateway"
        )

    cases = load_reference_questions(questions_path)
    identifier = run_id or f"run-{uuid.uuid4().hex}"

    # Exactly one fresh metadata-only probe, before the first question.
    _receipt, receipt_path = probe_provider_schema(
        gateway=gateway, receipt_dir=Path(capability_receipt_dir)
    )
    if receipt_path is None:  # pragma: no cover - a directory is always given
        raise LiveBaselineError("the capability probe wrote no receipt")

    questions = []
    canonical: list[str] = []
    contract_versions: list[tuple[str, str, str, str]] = []
    for case in cases:
        response = runtime.agent.run(
            SQLGenerationRequest(
                question=case.question,
                authorization_scope=runtime.authorization_scope,
                dialect="duckdb",
                max_rows=max_rows,
            )
        )
        questions.append(_question_from_response(case.id, response))
        canonical.append(case.canonical_question)
        contract_versions.append(
            (
                response.canonicalization_version,
                response.literal_registry_version,
                response.type_registry_version,
                response.ir_contract_version,
            )
        )

    candidate = LiveBaselineCandidate(
        run_id=identifier,
        provider=runtime.provider.provider,
        model=runtime.provider.model,
        model_revision=runtime.provider.model_revision,
        schema_mechanism=runtime.provider.schema_mechanism,
        capability_receipt_sha256=sha256_file(receipt_path),
        probe_count=1,
        probed_before_first_question=True,
        authorization_scope_hash=(runtime.authorization_scope.authorization_scope_hash),
        semantic_version=runtime.semantic_version,
        policy_version=runtime.authorization_scope.policy_version,
        retrieval_config_sha256=runtime.retrieval_config_hash,
        prompt_version=runtime.versions.prompt_version,
        router_version=runtime.versions.router_version,
        checker_version=runtime.versions.checker_version,
        compiler_version=runtime.agent.compiler_version,
        budget_limits=runtime.budget_limits,
        questions=tuple(questions),
        canonical_questions=tuple(canonical),
        contract_versions=tuple(contract_versions),
        reported_total=len(questions),
    )
    return candidate, receipt_path


def _route_and_budget_blockers(candidate: LiveBaselineCandidate) -> set[str]:
    blockers: set[str] = set()
    limits = candidate.budget_limits
    for question in candidate.questions:
        usage = question.budget_usage
        route, cache = question.generation_route, question.cache_status
        accepted = [
            record
            for record in question.attempt_records
            if record.outcome == "accepted"
        ]
        stages = [record.stage for record in accepted]
        calls = len(
            {
                record.stage
                for record in accepted
                if record.stage in _GENERATION_STAGES and record.cache_status == "miss"
            }
        )
        transitions = sum(
            1
            for record in accepted
            if record.stage == "complexity" and record.cache_status == "miss"
        )

        if cache == "miss":
            expected_calls = 2 if route == "planned_ir" else 1
            expected_capacity = 2 if route == "planned_ir" else 1
            if calls != expected_calls:
                blockers.add("semantic_call_count_mismatch")
            if usage.semantic_call_capacity != expected_capacity:
                blockers.add("generation_route_mismatch")
            if route == "planned_ir":
                if transitions != 1:
                    blockers.add(
                        "repeated_budget_transition"
                        if transitions > 1
                        else "unauthorized_budget_transition"
                    )
                if not usage.planned_ir_authorized:
                    blockers.add("unauthorized_budget_transition")
            elif usage.planned_ir_authorized or transitions:
                blockers.add("unauthorized_budget_transition")
        else:
            if calls != 0:
                blockers.add("cache_status_mismatch")
            # A cached IR is re-earned: it revalidates on its original route,
            # recompiles locally, is reauthorized, and runs exactly one EXPLAIN.
            revalidated = any(
                record.stage == route and record.cache_status == "hit"
                for record in accepted
            )
            if not revalidated:
                blockers.add("cached_route_not_revalidated")
            if question.status == "ok" and stages.count("engine_validation") != 1:
                blockers.add("cached_route_not_revalidated")
            for required in ("compile", "ast_check"):
                if question.status == "ok" and required not in stages:
                    blockers.add("cached_route_not_revalidated")

        if question.status == "ok" and stages.count("engine_validation") != 1:
            blockers.add("generation_route_mismatch")
        if calls > usage.semantic_call_capacity:
            blockers.add("budget_overflow")
        if usage.semantic_calls > limits.planned_semantic_call_capacity:
            blockers.add("budget_overflow")
        if usage.transport_attempts > (
            max(usage.semantic_calls, 1)
            * limits.max_transport_attempts_per_semantic_call
        ):
            blockers.add("budget_overflow")
        if usage.input_tokens > limits.max_input_tokens:
            blockers.add("budget_overflow")
        if usage.output_tokens > limits.max_output_tokens:
            blockers.add("budget_overflow")
        if usage.cost_usd > limits.max_cost_usd:
            blockers.add("budget_overflow")
        if usage.elapsed_ms > limits.end_to_end_deadline_ms:
            blockers.add("budget_overflow")
    return blockers


def _value_free_blockers(candidate: LiveBaselineCandidate) -> set[str]:
    blockers: set[str] = set()
    serialized = json.dumps(
        [question.model_dump(mode="json") for question in candidate.questions],
        sort_keys=True,
    )
    for canonical in candidate.canonical_questions:
        if canonical and canonical in serialized:
            blockers.add("serialized_canonical_question")
    for marker in ('"value"', '"parameters"', '"rows"', '"intent"'):
        if marker in serialized:
            blockers.add("serialized_resolved_value")
    return blockers


def validate_live_baseline(
    candidate: LiveBaselineCandidate, evidence: ValidatedLiveEvidence
) -> Any:
    """Publish an artifact only when every piece of evidence still agrees."""
    from .models import (
        ArtifactProvenance,
        BlockedEvaluation,
        EvaluationArtifact,
        EvaluationQuestion,
    )
    from .text2sql_cache import CACHE_IDENTITY_VERSION

    if not isinstance(candidate, LiveBaselineCandidate):
        raise TypeError("validation accepts only a runtime-derived candidate")
    if not isinstance(evidence, ValidatedLiveEvidence):
        raise TypeError("validation accepts only prepared live evidence")
    if any(
        not isinstance(question, EvaluationQuestion) for question in candidate.questions
    ):
        raise TypeError("every question must be validated evidence")

    blockers: set[str] = set()

    # 1. Reopen and rehash every retained immutable path.
    try:
        fresh = _hash_retained(evidence.paths)
    except (OSError, ValueError):
        fresh = {}
    recorded = {
        "source_manifest_sha256": evidence.source_manifest_sha256,
        "materialization_receipt_sha256": evidence.materialization_receipt_sha256,
        "bundle_sha256": evidence.bundle_sha256,
        "database_sha256": evidence.database_sha256,
        "capability_receipt_sha256": evidence.capability_receipt_sha256,
        "golden_set_sha256": evidence.golden_set_sha256,
    }
    if fresh != recorded:
        blockers.add("evidence_drift")

    # The bundle and database this run actually read must be the ones the
    # materialization receipt attests to. Without this, a baseline could cite
    # authoritative data evidence while having queried something else.
    materialization = evidence.materialization_receipt
    if materialization.bundle_sha256 != evidence.bundle_sha256:
        blockers.add("evidence_drift")
    if materialization.database_sha256 != evidence.database_sha256:
        blockers.add("evidence_drift")

    # 2. The receipt must be the one this run's own probe produced.
    if candidate.capability_receipt_sha256 != evidence.capability_receipt_sha256:
        blockers.add("stale_capability_probe")
    if candidate.probe_count != 1 or not candidate.probed_before_first_question:
        blockers.add("stale_capability_probe")

    receipt = evidence.capability_receipt
    for attribute, actual, code in (
        ("provider", candidate.provider, "provider_identity_mismatch"),
        ("model", candidate.model, "model_identity_mismatch"),
        ("revision", candidate.model_revision, "model_revision_mismatch"),
        (
            "schema_mechanism",
            candidate.schema_mechanism,
            "schema_mechanism_mismatch",
        ),
    ):
        if getattr(receipt, attribute) != actual:
            blockers.add(code)

    # 3. Exact code revision.
    if evidence.code_revision == "unknown":
        blockers.add("unknown_code_revision")
    elif evidence.code_dirty:
        blockers.add("dirty_code_revision")

    # 4. Question cardinality and identity.
    identifiers = [question.question_id for question in candidate.questions]
    expected = evidence.expected_question_ids
    if len(set(expected)) != EXPECTED_GOLDEN_QUESTION_COUNT:
        blockers.add("question_cardinality_mismatch")
    if len(identifiers) != len(expected):
        blockers.add("question_cardinality_mismatch")
    if len(set(identifiers)) != len(identifiers):
        blockers.add("duplicate_question_id")
    if set(identifiers) - set(expected):
        blockers.add("unexpected_question_id")

    # 5. Totals and non-vacuity.
    if candidate.reported_total != len(candidate.questions):
        blockers.add("falsified_totals")
    if not any(question.status == "ok" for question in candidate.questions):
        blockers.add("all_questions_failed")

    # 6. Version identity of the pipeline that produced the evidence.
    for question in candidate.questions:
        if question.authorization_scope_hash != candidate.authorization_scope_hash:
            blockers.add("evidence_drift")
    for name in (
        "prompt_version",
        "router_version",
        "checker_version",
        "compiler_version",
        "retrieval_config_sha256",
    ):
        if not getattr(candidate, name):
            blockers.add("missing_v3_provenance")

    declared = (
        "008.question.v1",
        "008.literal-span.v1",
        "008.types.v1",
        "008.ir.v1",
    )
    drift_codes = (
        "canonicalization_version_drift",
        "literal_registry_version_drift",
        "type_registry_version_drift",
        "missing_v3_provenance",
    )
    for observed in candidate.contract_versions:
        for actual, expected, code in zip(observed, declared, drift_codes, strict=True):
            if actual != expected:
                blockers.add(code)

    blockers |= _route_and_budget_blockers(candidate)
    blockers |= _value_free_blockers(candidate)

    if blockers:
        return BlockedEvaluation(
            run_kind="blocked",
            artifact_version="008.artifact.v3",
            run_id=candidate.run_id,
            blockers=tuple(sorted(blockers)),
        )

    provenance = ArtifactProvenance(
        contract_version="008.artifact.v3",
        run_id=candidate.run_id,
        dialect="duckdb",
        retrieval_config_sha256=candidate.retrieval_config_sha256,
        policy_version=candidate.policy_version,
        semantic_version=candidate.semantic_version,
        authorization_scope_hash=candidate.authorization_scope_hash,
        canonicalization_version="008.question.v1",
        canonical_question_hash_algorithm="sha256",
        literal_registry_version="008.literal-span.v1",
        prompt_version=candidate.prompt_version,
        ir_contract_version="008.ir.v1",
        type_registry_version="008.types.v1",
        router_version=candidate.router_version,
        compiler_version=candidate.compiler_version,
        checker_sha256=checker_sha256(),
        cache_identity_version=CACHE_IDENTITY_VERSION,
        budget_limits=candidate.budget_limits,
        budget_limits_sha256=budget_limits_sha256(candidate.budget_limits),
        provider=candidate.provider,
        model=candidate.model,
        model_revision=candidate.model_revision,
        schema_mechanism=candidate.schema_mechanism,
        capability_receipt_sha256=evidence.capability_receipt_sha256,
        source_manifest_sha256=evidence.source_manifest_sha256,
        materialization_receipt_sha256=evidence.materialization_receipt_sha256,
        bundle_sha256=evidence.bundle_sha256,
        database_sha256=evidence.database_sha256,
        golden_set_sha256=evidence.golden_set_sha256,
        code_revision=evidence.code_revision,
        code_dirty=evidence.code_dirty,
    )
    statuses = [question.status for question in candidate.questions]
    return EvaluationArtifact(
        run_kind="live_unadapted_baseline",
        artifact_version="008.artifact.v3",
        provenance=provenance,
        questions=candidate.questions,
        total_questions=len(candidate.questions),
        ok_count=statuses.count("ok"),
        refused_count=statuses.count("refused"),
        check_failed_count=statuses.count("check_failed"),
    )


def write_evaluation_artifact(artifact: Any, output: Path | str) -> Path:
    """Write only a validated `EvaluationArtifact`, atomically.

    A blocked outcome must leave the filesystem exactly as it was: neither
    creating an output nor touching an existing one.
    """
    from .models import EvaluationArtifact

    if not isinstance(artifact, EvaluationArtifact):
        raise LiveBaselineError("only a validated EvaluationArtifact is written")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    temporary.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination
