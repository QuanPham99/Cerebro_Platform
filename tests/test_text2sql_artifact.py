"""Live Option B baseline evidence: schema, drift, and atomic-write contracts.

Nothing here needs live credentials or the real corpus. The questions carry
genuine attempt records produced by the offline reference run, so the validation
rules are exercised against evidence the pipeline actually emitted rather than
hand-written dictionaries.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import duckdb
import golden_answers as golden
import pytest
from pydantic import ValidationError

from cerebro.evaluation import (
    EXPECTED_GOLDEN_QUESTIONS,
    LiveBaselineCandidate,
    LiveBaselineError,
    ValidatedLiveEvidence,
    _question_from_response,
    budget_limits_sha256,
    build_agent,
    build_authorization_scope,
    checker_sha256,
    load_reference_questions,
    prepare_live_evidence,
    run_offline_reference,
    validate_live_baseline,
    write_evaluation_artifact,
)
from cerebro.models import (
    BudgetLimits,
    EvaluationArtifact,
    EvaluationQuestion,
    ProviderCapabilityReceipt,
)
from cerebro.paths import DEFAULT_BUNDLE

OPTION_B_PROVENANCE_FIELDS = (
    "retrieval_config_sha256",
    "policy_version",
    "canonicalization_version",
    "canonical_question_hash_algorithm",
    "literal_registry_version",
    "prompt_version",
    "ir_contract_version",
    "type_registry_version",
    "router_version",
    "compiler_version",
    "checker_sha256",
    "budget_limits",
)


# --- shared offline evidence -------------------------------------------------


@pytest.fixture(scope="module")
def reference_bundle() -> Path:
    """The governed bundle, read directly. Nothing copies or amends it."""
    return DEFAULT_BUNDLE


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    from test_baseline_evaluation import _SCHEMA

    path = tmp_path_factory.mktemp("artifact-db") / "artifact.duckdb"
    connection = duckdb.connect(str(path))
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.executemany(
        "INSERT INTO branches VALUES (?, ?, ?, ?, ?, ?)",
        [
            (i, f"Branch {i}", "Delhi", "DL", "2020-01-01", f"IFSC{i:04d}")
            for i in (1, 2)
        ],
    )
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                i,
                f"Customer {i}",
                "F",
                "1990-01-01",
                "Delhi",
                "DL",
                9000000000 + i,
                f"c{i}@example.invalid",
                "Engineer",
                500000 + i,
                "2021-06-01",
                700,
            )
            for i in range(1, 9)
        ],
    )
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (i, i, i % 2 + 1, "SAVINGS", 100.0 * i, "2021-07-01", "ACTIVE")
            for i in range(1, 9)
        ],
    )
    connection.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (i, i % 8 + 1, f"2026-{i % 12 + 1:02d}-05", "DEPOSIT", 10.0 * i, "ATM", "G")
            for i in range(1, 25)
        ],
    )
    connection.executemany(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                i,
                i,
                i,
                "CREDIT" if i % 2 else "DEBIT",
                "2022-01-01",
                "2030-01-01",
                1,
                "A",
            )
            for i in range(1, 9)
        ],
    )
    connection.executemany(
        "INSERT INTO card_transactions VALUES (?, ?, ?, ?, ?, ?)",
        [(i, i % 8 + 1, "2026-03-01", "G", 5.0 * i, i % 4 == 0) for i in range(1, 17)],
    )
    connection.close()
    return str(path)


@pytest.fixture(scope="module")
def offline_responses(database, reference_bundle):
    """Real terminal responses to reuse as genuine per-question evidence."""
    scope = build_authorization_scope(
        allowed_object_ids=golden.REFERENCE_OBJECT_IDS,
        policy_version=golden.REFERENCE_POLICY_VERSION,
        tenant_scope_hash=golden.REFERENCE_TENANT_SCOPE_HASH,
    )
    runtime = build_agent(
        database,
        "scripted",
        scope,
        golden.GoldenProvider(),
        bundle_path=reference_bundle,
    )
    try:
        run = run_offline_reference(runtime)
        yield scope, dict(run.responses_by_id)
    finally:
        runtime.close()


@pytest.fixture(scope="module")
def golden_ids() -> tuple[str, ...]:
    return tuple(
        item.id for item in load_reference_questions(EXPECTED_GOLDEN_QUESTIONS)
    )


@pytest.fixture(scope="module")
def ten_questions(offline_responses, golden_ids) -> tuple[EvaluationQuestion, ...]:
    _scope, responses = offline_responses
    ordered = [responses[case_id] for case_id in sorted(responses)]
    return tuple(
        _question_from_response(question_id, ordered[index % len(ordered)])
        for index, question_id in enumerate(golden_ids)
    )


@pytest.fixture()
def evidence(tmp_path, reference_bundle, database) -> ValidatedLiveEvidence:
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "name": "branches",
                        "file_name": "branches.csv",
                        "sha256": "1" * 64,
                        "row_count": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from cerebro.bundle import load_validated_bundle
    from cerebro.provenance import semantic_bundle_sha256, sha256_file

    receipt = tmp_path / "materialization.json"
    receipt.write_text(
        json.dumps(
            {
                "source_manifest_sha256": "2" * 64,
                "bundle_sha256": semantic_bundle_sha256(
                    load_validated_bundle(reference_bundle)
                ),
                "tables": [
                    {
                        "table_id": "table.branches",
                        "source_file_sha256": "1" * 64,
                        "row_count": 2,
                    }
                ],
                "database_sha256": sha256_file(Path(database)),
                "engine": "duckdb",
                "engine_version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    capability = tmp_path / "capability.json"
    capability.write_text(
        ProviderCapabilityReceipt(
            provider="golden",
            model="golden-answers",
            revision="008.golden.v1",
            schema_mechanism="json_schema",
        ).model_dump_json(),
        encoding="utf-8",
    )
    prepared = prepare_live_evidence(
        source_manifest=manifest,
        materialization_receipt=receipt,
        bundle=reference_bundle,
        database=database,
        capability_receipt=capability,
    )
    # A committed revision is an external precondition, not something this suite
    # can create, so the clean-revision case is stated explicitly.
    return dataclasses.replace(prepared, code_revision="a" * 40, code_dirty=False)


def _candidate(scope, questions, evidence, **overrides) -> LiveBaselineCandidate:
    fields = {
        "run_id": "run-test",
        "provider": "golden",
        "model": "golden-answers",
        "model_revision": "008.golden.v1",
        "schema_mechanism": "json_schema",
        "capability_receipt_sha256": evidence.capability_receipt_sha256,
        "probe_count": 1,
        "probed_before_first_question": True,
        "authorization_scope_hash": scope.authorization_scope_hash,
        "semantic_version": "0.1.0",
        "policy_version": scope.policy_version,
        "retrieval_config_sha256": "5" * 64,
        "prompt_version": "008.prompt.v1",
        "router_version": "008.router.v1",
        "checker_version": "008.checker.v1",
        "compiler_version": "008.compiler.v1",
        "budget_limits": BudgetLimits.defaults(),
        "questions": tuple(questions),
        "canonical_questions": tuple(
            item.canonical_question
            for item in load_reference_questions(EXPECTED_GOLDEN_QUESTIONS)
        ),
        "contract_versions": tuple(
            ("008.question.v1", "008.literal-span.v1", "008.types.v1", "008.ir.v1")
            for _ in questions
        ),
        "reported_total": len(questions),
    }
    fields.update(overrides)
    return LiveBaselineCandidate(**fields)


@pytest.fixture()
def candidate(offline_responses, ten_questions, evidence):
    scope, _responses = offline_responses
    return _candidate(scope, ten_questions, evidence)


@pytest.fixture()
def valid_artifact_dict(candidate, evidence):
    artifact = validate_live_baseline(candidate, evidence)
    assert artifact.run_kind == "live_unadapted_baseline", artifact
    return lambda: json.loads(artifact.model_dump_json())


# --- Step 1: every Option B provenance field is mandatory -------------------


def test_valid_artifact_payload_is_the_control_case(valid_artifact_dict):
    """Every rejection test below must fail for its own reason, not this one."""
    payload = valid_artifact_dict()
    artifact = EvaluationArtifact.model_validate_json(json.dumps(payload))
    assert artifact.total_questions == 10


@pytest.mark.parametrize("field", OPTION_B_PROVENANCE_FIELDS)
def test_live_artifact_requires_option_b_provenance(field, valid_artifact_dict):
    payload = valid_artifact_dict()
    del payload["provenance"][field]
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(payload))


def test_live_artifact_round_trips_and_pins_every_identity(valid_artifact_dict):
    artifact = EvaluationArtifact.model_validate_json(json.dumps(valid_artifact_dict()))
    provenance = artifact.provenance
    assert artifact.run_kind == "live_unadapted_baseline"
    assert provenance.canonical_question_hash_algorithm == "sha256"
    assert provenance.checker_sha256 == checker_sha256()
    assert provenance.budget_limits_sha256 == budget_limits_sha256(
        provenance.budget_limits
    )
    assert provenance.code_dirty is False
    for name in (
        "source_manifest_sha256",
        "materialization_receipt_sha256",
        "bundle_sha256",
        "database_sha256",
        "golden_set_sha256",
        "capability_receipt_sha256",
    ):
        assert len(getattr(provenance, name)) == 64


def test_scripted_run_kind_is_not_a_live_baseline(valid_artifact_dict):
    payload = valid_artifact_dict()
    payload["run_kind"] = "offline_reference"
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(payload))


# --- Step 3: derived evidence ----------------------------------------------


def test_question_semantic_calls_must_be_derived_from_attempts(valid_artifact_dict):
    payload = valid_artifact_dict()
    payload["questions"][0]["budget_usage"]["semantic_calls"] = 7
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(payload))


def test_cache_hit_may_not_report_a_semantic_call():
    with pytest.raises(ValidationError):
        EvaluationQuestion.model_validate(
            {
                "question_id": "GQ-01",
                "canonical_question_hash": "a" * 64,
                "authorization_scope_hash": "b" * 64,
                "snapshot_hash": "c" * 64,
                "status": "check_failed",
                "generation_route": "default_ir",
                "cache_status": "hit",
                "attempt_records": [
                    {
                        "stage": "default_ir",
                        "ordinal": 1,
                        "outcome": "accepted",
                        "latency_ms": 0,
                        "violation_codes": [],
                        "generation_route": "default_ir",
                        "cache_status": "miss",
                    }
                ],
                "budget_usage": {
                    "semantic_call_capacity": 1,
                    "planned_ir_authorized": False,
                    "semantic_calls": 1,
                    "transport_attempts": 1,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": "0",
                    "elapsed_ms": 0,
                },
                "violation_codes": ["cache_integrity_error"],
            }
        )


def test_generation_route_cache_literal_excludes_a_cache_route(valid_artifact_dict):
    payload = valid_artifact_dict()
    payload["questions"][0]["generation_route"] = "cache"
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(payload))


def test_totals_must_be_derived_and_non_vacuous(valid_artifact_dict):
    for mutate in (
        lambda payload: payload.update({"ok_count": payload["ok_count"] + 1}),
        lambda payload: payload.update({"total_questions": 11}),
        lambda payload: payload.update({"refused_count": 3}),
    ):
        payload = valid_artifact_dict()
        mutate(payload)
        with pytest.raises(ValidationError):
            EvaluationArtifact.model_validate_json(json.dumps(payload))


def test_all_check_failed_run_cannot_satisfy_the_artifact(valid_artifact_dict):
    payload = valid_artifact_dict()
    for question in payload["questions"]:
        question["status"] = "check_failed"
        question["violation_codes"] = ["explain_failed"]
        question["output_lineage"] = []
    payload["ok_count"] = 0
    payload["check_failed_count"] = len(payload["questions"])
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(payload))


def test_duplicate_question_ids_are_rejected(valid_artifact_dict):
    payload = valid_artifact_dict()
    payload["questions"][1]["question_id"] = payload["questions"][0]["question_id"]
    with pytest.raises(ValidationError):
        EvaluationArtifact.model_validate_json(json.dumps(payload))


# --- Step 5: every blocker leaves output untouched -------------------------


def _blockers(outcome) -> set[str]:
    assert outcome.run_kind == "blocked", outcome
    return set(outcome.blockers)


def test_evidence_drift_blocks_the_artifact(candidate, evidence):
    drifted = dataclasses.replace(evidence, database_sha256="0" * 64)
    assert "evidence_drift" in _blockers(validate_live_baseline(candidate, drifted))


def test_stale_probe_and_identity_mismatches_block(
    offline_responses, ten_questions, evidence
):
    scope, _ = offline_responses
    cases = {
        "stale_capability_probe": {"capability_receipt_sha256": "0" * 64},
        "provider_identity_mismatch": {"provider": "other"},
        "model_identity_mismatch": {"model": "other-model"},
        "model_revision_mismatch": {"model_revision": "other-revision"},
        "schema_mechanism_mismatch": {"schema_mechanism": "none"},
    }
    for code, overrides in cases.items():
        outcome = validate_live_baseline(
            _candidate(scope, ten_questions, evidence, **overrides), evidence
        )
        assert code in _blockers(outcome), code


def test_repeated_probe_blocks(offline_responses, ten_questions, evidence):
    scope, _ = offline_responses
    outcome = validate_live_baseline(
        _candidate(scope, ten_questions, evidence, probe_count=2), evidence
    )
    assert "stale_capability_probe" in _blockers(outcome)


def test_dirty_and_unknown_revisions_block(candidate, evidence):
    dirty = dataclasses.replace(evidence, code_dirty=True)
    assert "dirty_code_revision" in _blockers(validate_live_baseline(candidate, dirty))
    unknown = dataclasses.replace(evidence, code_revision="unknown", code_dirty=True)
    assert "unknown_code_revision" in _blockers(
        validate_live_baseline(candidate, unknown)
    )


def test_question_cardinality_and_identity_block(
    offline_responses, ten_questions, evidence
):
    scope, _ = offline_responses
    short = validate_live_baseline(
        _candidate(scope, ten_questions[:4], evidence), evidence
    )
    assert "question_cardinality_mismatch" in _blockers(short)

    renamed = ten_questions[0].model_copy(update={"question_id": "GQ-99"})
    swapped = (renamed, *ten_questions[1:])
    outcome = validate_live_baseline(_candidate(scope, swapped, evidence), evidence)
    assert "unexpected_question_id" in _blockers(outcome)


def test_falsified_totals_block(offline_responses, ten_questions, evidence):
    scope, _ = offline_responses
    outcome = validate_live_baseline(
        _candidate(scope, ten_questions, evidence, reported_total=99), evidence
    )
    assert "falsified_totals" in _blockers(outcome)


def test_version_drift_blocks(offline_responses, ten_questions, evidence):
    scope, _ = offline_responses
    drifted = {
        "canonicalization_version_drift": (
            "008.question.v2",
            "008.literal-span.v1",
            "008.types.v1",
            "008.ir.v1",
        ),
        "literal_registry_version_drift": (
            "008.question.v1",
            "008.literal-span.v2",
            "008.types.v1",
            "008.ir.v1",
        ),
        "type_registry_version_drift": (
            "008.question.v1",
            "008.literal-span.v1",
            "008.types.v2",
            "008.ir.v1",
        ),
    }
    for code, versions in drifted.items():
        outcome = validate_live_baseline(
            _candidate(
                scope,
                ten_questions,
                evidence,
                contract_versions=tuple(versions for _ in ten_questions),
            ),
            evidence,
        )
        assert code in _blockers(outcome), code


def test_budget_overflow_blocks(offline_responses, ten_questions, evidence):
    scope, _ = offline_responses
    first = ten_questions[0]
    overflowed = first.model_copy(
        update={
            "budget_usage": first.budget_usage.model_copy(
                update={"input_tokens": 10**6}
            )
        }
    )
    outcome = validate_live_baseline(
        _candidate(scope, (overflowed, *ten_questions[1:]), evidence), evidence
    )
    assert "budget_overflow" in _blockers(outcome)


def test_unauthorized_transition_blocks(offline_responses, ten_questions, evidence):
    scope, _ = offline_responses
    planned = next(
        item for item in ten_questions if item.generation_route == "planned_ir"
    )
    forged = planned.model_copy(
        update={
            "attempt_records": tuple(
                record
                for record in planned.attempt_records
                if record.stage != "complexity"
            )
        }
    )
    others = tuple(item for item in ten_questions if item is not planned)
    outcome = validate_live_baseline(
        _candidate(scope, (forged, *others[: len(ten_questions) - 1]), evidence),
        evidence,
    )
    assert "unauthorized_budget_transition" in _blockers(outcome)


def test_blocked_validation_never_creates_or_changes_output(
    candidate, evidence, tmp_path
):
    destination = tmp_path / "baseline.json"
    drifted = dataclasses.replace(evidence, bundle_sha256="0" * 64)
    outcome = validate_live_baseline(candidate, drifted)
    with pytest.raises(LiveBaselineError):
        write_evaluation_artifact(outcome, destination)
    assert not destination.exists()

    destination.write_text("PRIOR", encoding="utf-8")
    before = destination.read_bytes()
    with pytest.raises(LiveBaselineError):
        write_evaluation_artifact(outcome, destination)
    assert destination.read_bytes() == before
    assert not list(tmp_path.glob("*.partial"))


def test_validated_artifact_writes_atomically(candidate, evidence, tmp_path):
    artifact = validate_live_baseline(candidate, evidence)
    destination = write_evaluation_artifact(
        artifact, tmp_path / "out" / "baseline.json"
    )
    reloaded = EvaluationArtifact.model_validate_json(
        destination.read_text(encoding="utf-8")
    )
    assert reloaded == artifact
    assert not list(destination.parent.glob("*.partial"))


def test_artifact_carries_no_question_text_or_resolved_value(candidate, evidence):
    artifact = validate_live_baseline(candidate, evidence)
    serialized = artifact.model_dump_json()
    for case in load_reference_questions(EXPECTED_GOLDEN_QUESTIONS):
        assert case.question not in serialized
        assert case.canonical_question not in serialized
    for marker in ('"rows"', '"parameters"', '"intent"'):
        assert marker not in serialized


def test_validation_rejects_hand_written_candidates_and_evidence(candidate, evidence):
    with pytest.raises(TypeError):
        validate_live_baseline({"run_id": "x"}, evidence)
    with pytest.raises(TypeError):
        validate_live_baseline(candidate, {"bundle_sha256": "0" * 64})


def test_candidate_requires_gateway_derived_identity(
    offline_responses, ten_questions, evidence
):
    scope, _ = offline_responses
    with pytest.raises(TypeError):
        _candidate(scope, ten_questions, evidence, provider="")
    with pytest.raises(TypeError):
        _candidate(scope, ten_questions, evidence, model=None)


def test_prepare_live_evidence_requires_every_retained_path(tmp_path, database):
    with pytest.raises(LiveBaselineError):
        prepare_live_evidence(
            source_manifest=tmp_path / "absent.json",
            materialization_receipt=tmp_path / "absent-receipt.json",
            bundle=tmp_path / "absent-bundle",
            database=database,
            capability_receipt=tmp_path / "absent-capability.json",
        )


def test_golden_set_declares_exactly_ten_unique_ids(golden_ids):
    assert len(golden_ids) == 10
    assert len(set(golden_ids)) == 10


def test_materialization_receipt_must_attest_the_bundle_and_database(
    candidate, evidence
):
    """A baseline may not cite data evidence for something it did not query."""
    for field in ("bundle_sha256", "database_sha256"):
        forged = evidence.materialization_receipt.model_copy(update={field: "0" * 64})
        drifted = dataclasses.replace(evidence, materialization_receipt=forged)
        assert "evidence_drift" in _blockers(validate_live_baseline(candidate, drifted))
