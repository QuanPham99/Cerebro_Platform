"""Offline deterministic reference: real compiler, real gates, scripted IR only.

Every case here runs the production composition root. Nothing stubs the
compiler, the checker, `EXPLAIN`, or execution, so an `ok` outcome means the
whole pipeline agreed rather than that a fixture asserted its own output.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import golden_answers as golden
import pytest
from pydantic import ValidationError

from cerebro.evaluation import (
    AgentRuntime,
    OfflineReferenceArtifact,
    ReferenceQuestion,
    RuntimeCompositionError,
    build_agent,
    build_authorization_scope,
    load_authorization_scope,
    load_reference_questions,
    run_offline_reference,
    write_offline_reference,
)
from cerebro.models import (
    AggregateNode,
    FunctionExpression,
    NamedExpression,
    SQLGenerationRequest,
)
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.selfcheck import DisclosureCaps

SUPPORTED_CASE_IDS = (
    "metric-fidelity",
    "two-hop-join",
    "relative-time",
    "bounded-disclosure",
    "zero-row",
)

_SCHEMA = (
    """CREATE TABLE branches (
        branch_id BIGINT, branch_name VARCHAR, city VARCHAR,
        state VARCHAR, opened_date DATE, ifsc_code VARCHAR)""",
    """CREATE TABLE customers (
        customer_id BIGINT, name VARCHAR, gender VARCHAR, date_of_birth DATE,
        city VARCHAR, state VARCHAR, phone BIGINT, email VARCHAR,
        occupation VARCHAR, annual_income BIGINT, join_date DATE,
        credit_score BIGINT)""",
    """CREATE TABLE accounts (
        account_id BIGINT, customer_id BIGINT, branch_id BIGINT,
        account_type VARCHAR, balance DOUBLE, open_date DATE, status VARCHAR)""",
    """CREATE TABLE transactions (
        transaction_id BIGINT, account_id BIGINT, txn_date DATE,
        txn_type VARCHAR, amount DOUBLE, channel VARCHAR,
        merchant_category VARCHAR)""",
    """CREATE TABLE cards (
        card_id BIGINT, customer_id BIGINT, account_id BIGINT,
        card_type VARCHAR, issue_date DATE, expiry_date DATE,
        credit_limit BIGINT, status VARCHAR)""",
    """CREATE TABLE card_transactions (
        card_txn_id BIGINT, card_id BIGINT, txn_date DATE,
        merchant_category VARCHAR, amount DOUBLE, is_fraud BIGINT)""",
)


@pytest.fixture(scope="module")
def reference_bundle(tmp_path_factory) -> Path:
    """Materialize the workshop bundle locally; the governed tree is read-only."""
    return golden.materialize_reference_bundle(
        DEFAULT_BUNDLE, tmp_path_factory.mktemp("bundle") / "bank-workshop"
    )


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    """A small database whose schema matches the governed bundle exactly."""
    path = tmp_path_factory.mktemp("reference") / "reference.duckdb"
    connection = duckdb.connect(str(path))
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.executemany(
        "INSERT INTO branches VALUES (?, ?, ?, ?, ?, ?)",
        [
            (index, f"Branch {index}", "Delhi", "DL", "2020-01-01", f"IFSC{index:04d}")
            for index in range(1, 4)
        ],
    )
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                index,
                f"Customer {index}",
                "F" if index % 2 else "M",
                "1990-01-01",
                "Delhi",
                "DL",
                9000000000 + index,
                f"customer{index}@example.invalid",
                "Engineer",
                500000 + index * 1000,
                "2021-06-01",
                700 + index,
            )
            for index in range(1, 13)
        ],
    )
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                index,
                index,
                index % 3 + 1,
                "SAVINGS",
                1000.0 * index,
                "2021-07-01",
                "ACTIVE",
            )
            for index in range(1, 13)
        ],
    )
    connection.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                index,
                index % 12 + 1,
                f"2026-{index % 12 + 1:02d}-05",
                "DEPOSIT",
                100.0 * index,
                "ATM",
                "GROCERY",
            )
            for index in range(1, 37)
        ],
    )
    connection.executemany(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                index,
                index,
                index,
                "CREDIT" if index % 2 else "DEBIT",
                "2022-01-01",
                "2030-01-01",
                100000,
                "ACTIVE",
            )
            for index in range(1, 13)
        ],
    )
    connection.executemany(
        "INSERT INTO card_transactions VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                index,
                index % 12 + 1,
                "2026-03-01",
                "GROCERY",
                25.0 * index,
                index % 5 == 0,
            )
            for index in range(1, 25)
        ],
    )
    connection.close()
    return str(path)


@pytest.fixture(scope="module")
def reference_scope():
    return build_authorization_scope(
        allowed_object_ids=golden.REFERENCE_OBJECT_IDS,
        policy_version=golden.REFERENCE_POLICY_VERSION,
        tenant_scope_hash=golden.REFERENCE_TENANT_SCOPE_HASH,
    )


@pytest.fixture()
def runtime_factory(database, reference_bundle, reference_scope):
    built: list[AgentRuntime] = []

    def build(provider=None, *, questions=None):
        runtime = build_agent(
            database,
            "scripted",
            reference_scope,
            provider or golden.GoldenProvider(questions),
            bundle_path=reference_bundle,
        )
        built.append(runtime)
        return runtime

    yield build
    for runtime in built:
        runtime.close()


@pytest.fixture(scope="module")
def offline_run(database, reference_bundle, reference_scope):
    runtime = build_agent(
        database,
        "scripted",
        reference_scope,
        golden.GoldenProvider(),
        bundle_path=reference_bundle,
    )
    try:
        yield run_offline_reference(runtime)
    finally:
        runtime.close()


# --- supported capability set ----------------------------------------------


@pytest.mark.parametrize("case_id", SUPPORTED_CASE_IDS)
def test_supported_case_reaches_ok(case_id, offline_run):
    response = offline_run.responses_by_id[case_id]
    assert response.status == "ok", getattr(response, "violations", response)
    assert response.output_lineage
    expected_route = "planned_ir" if case_id == "relative-time" else "default_ir"
    expected_calls = 2 if case_id == "relative-time" else 1
    assert response.generation_route == expected_route
    assert response.cache_status == "miss"
    assert response.budget_usage.semantic_calls == expected_calls
    serialized = response.model_dump_json()
    assert '"intent"' not in serialized
    assert '"value"' not in response.ir.model_dump_json()


def test_planned_route_authorizes_exactly_one_capacity_transition(offline_run):
    planned = offline_run.responses_by_id["relative-time"]
    assert planned.budget_usage.planned_ir_authorized is True
    assert planned.budget_usage.semantic_call_capacity == 2
    for case_id in SUPPORTED_CASE_IDS:
        if case_id == "relative-time":
            continue
        default = offline_run.responses_by_id[case_id]
        assert default.budget_usage.planned_ir_authorized is False
        assert default.budget_usage.semantic_call_capacity == 1


def test_zero_row_case_executes_once_without_semantic_retry(offline_run):
    response = offline_run.responses_by_id["zero-row"]
    assert response.result.rows == ()
    assert response.result.row_count == 0
    assert response.budget_usage.semantic_calls == 1
    stages = [
        record.stage
        for record in response.attempt_records
        if record.stage == "execution"
    ]
    assert stages == ["execution"]


def test_count_star_returns_one_row_for_an_empty_population(runtime_factory):
    """A count over the same empty population is one row, not zero rows."""
    case = ReferenceQuestion(
        id="zero-row", question="List card transaction IDs with amount below 0"
    )

    def counted(request):
        base = golden.zero_row_ir(request)
        nodes = tuple(
            node for node in base.nodes if node.node_id != "project_card_txn_ids"
        ) + (
            AggregateNode(
                kind="aggregate",
                node_id="aggregate_card_txn_count",
                input_id="filter_negative_amount",
                measures=(
                    NamedExpression(
                        alias="card_txn_count",
                        expression=FunctionExpression(
                            kind="function",
                            function="count",
                            arguments=(
                                golden._column(
                                    "table.card_transactions", "card_txn_id"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
        return base.model_copy(
            update={"nodes": nodes, "root_node_id": "aggregate_card_txn_count"}
        )

    provider = golden.GoldenProvider([case], {"zero-row": (counted,)})
    runtime = runtime_factory(provider)
    response = runtime.agent.run(
        SQLGenerationRequest(
            question=case.question,
            authorization_scope=runtime.authorization_scope,
            dialect="duckdb",
            max_rows=1000,
        )
    )
    assert response.status == "ok", getattr(response, "violations", response)
    assert response.result.row_count == 1
    assert response.result.rows[0][0] == 0


def test_bounded_disclosure_records_every_sensitive_source(offline_run):
    response = offline_run.responses_by_id["bounded-disclosure"]
    disclosed = {
        (column.table_id, column.column)
        for record in response.disclosures
        for column in record.source_columns
    }
    assert ("table.customers", "annual_income") in disclosed
    assert ("table.customers", "name") in disclosed
    # The question's own cap is a bound parameter, so the disclosed bound is the
    # policy cap; the executed row count proves the tighter cap actually applied.
    caps = DisclosureCaps.defaults()
    assert all(record.row_limit <= caps.row_limit for record in response.disclosures)
    assert response.result.row_count == 5
    assert response.sql_artifact.parameter_count == 1
    assert "5" not in response.sql_artifact.sql


def test_metric_case_carries_metric_lineage(offline_run):
    response = offline_run.responses_by_id["metric-fidelity"]
    metric_ids = {
        metric_id for item in response.output_lineage for metric_id in item.metric_ids
    }
    assert metric_ids == {"metric.card-fraud-rate"}


def test_provider_call_counts_match_the_declared_routes(
    database, reference_bundle, reference_scope
):
    provider = golden.GoldenProvider()
    runtime = build_agent(
        database, "scripted", reference_scope, provider, bundle_path=reference_bundle
    )
    try:
        run_offline_reference(runtime)
    finally:
        runtime.close()
    assert provider.calls_for("relative-time") == ("default_ir", "planned_ir")
    for case_id in SUPPORTED_CASE_IDS:
        if case_id == "relative-time":
            continue
        assert provider.calls_for(case_id) == ("default_ir",)
    assert provider.semantic_calls == 6


# --- artifact ---------------------------------------------------------------


def test_offline_artifact_is_value_free_and_question_free(offline_run):
    artifact = offline_run.artifact
    assert artifact.run_kind == "offline_reference"
    assert artifact.total_questions == len(SUPPORTED_CASE_IDS)
    assert artifact.ok_count == len(SUPPORTED_CASE_IDS)
    serialized = artifact.model_dump_json()
    for case in load_reference_questions():
        assert case.question not in serialized
        assert case.canonical_question not in serialized
        assert case.canonical_question_hash in serialized
    assert '"rows"' not in serialized
    assert '"parameters"' not in serialized


def test_offline_artifact_totals_must_be_derived(offline_run):
    payload = offline_run.artifact.model_dump(mode="python")
    payload["ok_count"] = payload["ok_count"] + 1
    with pytest.raises(ValidationError):
        OfflineReferenceArtifact.model_validate(payload)


def test_written_artifact_round_trips(offline_run, tmp_path):
    destination = write_offline_reference(
        offline_run.artifact, tmp_path / "offline-reference.json"
    )
    reloaded = OfflineReferenceArtifact.model_validate_json(
        destination.read_text(encoding="utf-8")
    )
    assert reloaded == offline_run.artifact
    assert not list(tmp_path.glob("*.partial"))


def test_writer_rejects_anything_but_a_validated_artifact(tmp_path):
    with pytest.raises(RuntimeCompositionError):
        write_offline_reference({"run_kind": "offline_reference"}, tmp_path / "x.json")


# --- composition root -------------------------------------------------------


def test_scripted_mode_requires_an_explicit_provider(
    database, reference_bundle, reference_scope
):
    with pytest.raises(RuntimeCompositionError):
        build_agent(
            database, "scripted", reference_scope, None, bundle_path=reference_bundle
        )


def test_organizer_mode_refuses_a_provider_override(
    database, reference_bundle, reference_scope
):
    with pytest.raises(RuntimeCompositionError):
        build_agent(
            database,
            "organizer",
            reference_scope,
            golden.GoldenProvider(),
            bundle_path=reference_bundle,
        )


def test_unknown_provider_mode_has_no_fallback(
    database, reference_bundle, reference_scope
):
    with pytest.raises(RuntimeCompositionError):
        build_agent(
            database, "live", reference_scope, None, bundle_path=reference_bundle
        )


def test_composition_requires_a_trusted_scope(
    database, reference_bundle, reference_scope
):
    tampered = reference_scope.model_copy(update={"policy_version": "policy.other"})
    with pytest.raises(RuntimeCompositionError):
        build_agent(
            database,
            "scripted",
            tampered,
            golden.GoldenProvider(),
            bundle_path=reference_bundle,
        )


def test_reference_run_refuses_the_live_organizer(runtime_factory):
    runtime = runtime_factory()
    live = AgentRuntime(
        **{
            **{
                field: getattr(runtime, field) for field in runtime.__dataclass_fields__
            },
            "provider_mode": "organizer",
        }
    )
    with pytest.raises(RuntimeCompositionError):
        run_offline_reference(live)


def test_authorization_scope_input_rejects_a_mismatching_hash(
    tmp_path, reference_scope
):
    payload = json.loads(reference_scope.model_dump_json())
    payload["authorization_scope_hash"] = "0" * 64
    location = tmp_path / "scope.json"
    location.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeCompositionError):
        load_authorization_scope(location)


def test_authorization_scope_input_derives_an_absent_hash(tmp_path, reference_scope):
    payload = json.loads(reference_scope.model_dump_json())
    del payload["authorization_scope_hash"]
    location = tmp_path / "scope.json"
    location.write_text(json.dumps(payload), encoding="utf-8")
    assert load_authorization_scope(location) == reference_scope


# --- grounding-only boundary ------------------------------------------------


def test_advisory_grounding_response_cannot_authorize_generation(reference_bundle):
    """`/api/grounding` metadata is not an authorization scope, structurally."""
    from cerebro.bundle import load_validated_bundle
    from cerebro.retrieval import SemanticRetriever

    grounding = SemanticRetriever(load_validated_bundle(reference_bundle)).grounding(
        "What is transaction volume by branch?", limit=10
    )
    with pytest.raises(ValidationError):
        SQLGenerationRequest(
            question="What is transaction volume by branch?",
            authorization_scope=grounding.model_dump(mode="json"),
            dialect="duckdb",
            max_rows=100,
        )


# --- CLI composition --------------------------------------------------------


def test_cli_reference_writes_a_value_free_artifact(
    tmp_path, database, reference_bundle, reference_scope, monkeypatch
):
    from cerebro import cli

    scope_path = tmp_path / "scope.json"
    scope_path.write_text(reference_scope.model_dump_json(), encoding="utf-8")
    output = tmp_path / "artifacts" / "offline-reference.json"
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent))
    code = cli.main(
        [
            "reference",
            "--bundle",
            str(reference_bundle),
            "--database",
            database,
            "--authorization-scope",
            str(scope_path),
            "--provider-factory",
            "golden_answers:GoldenProvider",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    artifact = OfflineReferenceArtifact.model_validate_json(
        output.read_text(encoding="utf-8")
    )
    assert artifact.run_kind == "offline_reference"
    assert artifact.ok_count == len(SUPPORTED_CASE_IDS)


def test_cli_reference_requires_exactly_one_offline_transport(
    tmp_path, database, reference_bundle, reference_scope, capsys
):
    from cerebro import cli

    scope_path = tmp_path / "scope.json"
    scope_path.write_text(reference_scope.model_dump_json(), encoding="utf-8")
    code = cli.main(
        [
            "reference",
            "--bundle",
            str(reference_bundle),
            "--database",
            database,
            "--authorization-scope",
            str(scope_path),
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "offline_provider_required"


def test_cli_ask_without_provider_configuration_returns_typed_json(
    tmp_path, database, reference_bundle, reference_scope, capsys, monkeypatch
):
    from cerebro import cli

    for name in (
        "CEREBRO_BASE_URL",
        "CEREBRO_API_KEY",
        "CEREBRO_MODEL",
        "CEREBRO_LLM_BASE_URL",
        "CEREBRO_LLM_API_KEY",
        "CEREBRO_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(reference_scope.model_dump_json(), encoding="utf-8")
    code = cli.main(
        [
            "ask",
            "--question",
            "What is transaction volume by branch?",
            "--bundle",
            str(reference_bundle),
            "--database",
            database,
            "--authorization-scope",
            str(scope_path),
        ]
    )
    assert code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["command"] == "ask"


def test_cli_modes_are_explicit_and_disjoint():
    from cerebro import cli

    actions = {
        action.dest: action for action in cli.build_parser()._subparsers._group_actions
    }
    choices = actions["command"].choices
    assert {"ask", "reference", "baseline"} <= set(choices)
    assert "sql" not in choices
    baseline = choices["baseline"]
    options = {
        option for action in baseline._actions for option in action.option_strings
    }
    assert "--cassette" not in options
    assert "--provider-factory" not in options


# --- live baseline boundary -------------------------------------------------


def test_live_baseline_refuses_an_offline_runtime(runtime_factory, tmp_path):
    """A scripted or golden provider can never produce a live baseline."""
    from cerebro.evaluation import LiveBaselineError, run_live_baseline

    runtime = runtime_factory()
    with pytest.raises(LiveBaselineError):
        run_live_baseline(runtime, capability_receipt_dir=tmp_path / "capability")


def test_baseline_command_declares_the_full_evidence_surface():
    from cerebro import cli

    choices = {
        action.dest: action for action in cli.build_parser()._subparsers._group_actions
    }["command"].choices
    options = {
        option
        for action in choices["baseline"]._actions
        for option in action.option_strings
    }
    assert {
        "--manifest",
        "--materialization-receipt",
        "--capability-receipt-dir",
        "--authorization-scope",
        "--database",
        "--bundle",
        "--output",
        "--questions",
    } <= options


def test_baseline_defaults_to_the_ten_golden_questions():
    from cerebro.evaluation import (
        EXPECTED_GOLDEN_QUESTION_COUNT,
        EXPECTED_GOLDEN_QUESTIONS,
    )

    cases = load_reference_questions(EXPECTED_GOLDEN_QUESTIONS)
    assert len(cases) == EXPECTED_GOLDEN_QUESTION_COUNT
    assert len({case.id for case in cases}) == EXPECTED_GOLDEN_QUESTION_COUNT


def test_baseline_without_provider_configuration_returns_typed_json(
    tmp_path, database, reference_bundle, reference_scope, capsys, monkeypatch
):
    from cerebro import cli

    for name in (
        "CEREBRO_BASE_URL",
        "CEREBRO_API_KEY",
        "CEREBRO_MODEL",
        "CEREBRO_LLM_BASE_URL",
        "CEREBRO_LLM_API_KEY",
        "CEREBRO_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(reference_scope.model_dump_json(), encoding="utf-8")
    output = tmp_path / "baseline.json"
    code = cli.main(
        [
            "baseline",
            "--bundle",
            str(reference_bundle),
            "--database",
            database,
            "--authorization-scope",
            str(scope_path),
            "--capability-receipt-dir",
            str(tmp_path / "capability"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--materialization-receipt",
            str(tmp_path / "receipt.json"),
            "--output",
            str(output),
        ]
    )
    assert code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["command"] == "baseline"
    assert not output.exists()
