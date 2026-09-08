from __future__ import annotations

import json
import re

import pytest
import text2sql_factories as factories

from cerebro.models import (
    ColumnRef,
    GovernedLiteralRef,
    LimitNode,
    LiteralExpression,
    NamedExpression,
    ProjectNode,
    QuestionLiteralRef,
    RelationalQueryIR,
    ScanNode,
    SnapshotGovernedLiteral,
    relational_ir_sha256,
)
from cerebro.provenance import canonicalize_question
from cerebro.sql_compiler import (
    CompilerError,
    DialectCompiler,
    LiteralResolver,
    compiler_version,
    to_sql_artifact,
)


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


def normalized_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower().replace('"', "")


def _compile(ir, snapshot, question, *, max_rows=1000, route="default_ir"):
    validated = factories.validated_ir(ir, snapshot, question, generation_route=route)
    return DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=max_rows
    )


# --- determinism ------------------------------------------------------------


def test_identical_ir_compiles_to_identical_bytes(snapshot):
    question = canonicalize_question("transaction volume by branch")
    validated = factories.validated_ir(
        factories.branch_volume_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    compiler = DialectCompiler("duckdb")
    first = compiler.compile(validated, snapshot, question, max_rows=1000)
    second = compiler.compile(validated, snapshot, question, max_rows=1000)
    assert first.model_dump_json() == second.model_dump_json()
    assert [
        (item.position, item.data_type, item.value) for item in first.parameters
    ] == [(item.position, item.data_type, item.value) for item in second.parameters]
    assert first.ir_hash == second.ir_hash
    assert first.ir_hash == relational_ir_sha256(validated.ir)


def test_compiler_version_is_stable_and_recorded(snapshot):
    question = canonicalize_question("accounts")
    compiled = _compile(factories.minimal_ir(snapshot), snapshot, question)
    assert compiled.compiler_version == compiler_version()
    assert compiler_version() == compiler_version()
    assert compiled.dialect == "duckdb"


def test_only_duckdb_is_compiled():
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("postgres")
    assert caught.value.code == "unsupported_dialect"


# --- literal handling -------------------------------------------------------


def test_question_span_literal_is_parameterized_and_public_models_are_value_free(
    snapshot,
):
    question = canonicalize_question("Show accounts in London")
    city_ref = QuestionLiteralRef(kind="question", start=17, end=23, data_type="string")
    ir = factories.filtered_account_ir(snapshot, city_ref=city_ref)
    compiled = _compile(ir, snapshot, question, max_rows=100)
    artifact = to_sql_artifact(compiled)

    assert "London" not in ir.model_dump_json()
    assert "London" not in compiled.sql
    assert "?" in compiled.sql
    assert [item.value for item in compiled.parameters] == ["London"]
    assert "London" not in artifact.model_dump_json()
    assert artifact.parameter_count == 1
    assert artifact.parameter_types == ("string",)
    assert artifact.ir_hash == compiled.ir_hash


def test_governed_literal_resolves_only_by_snapshot_id():
    governed = SnapshotGovernedLiteral(
        literal_id="literal.account-active",
        data_type="string",
        value="ACTIVE",
        source_object_id="table.accounts",
    )
    snapshot = factories.valid_snapshot(governed_literals=(governed,))
    question = canonicalize_question("Show active accounts")
    ref = GovernedLiteralRef(kind="governed", literal_id="literal.account-active")
    ir = factories.filtered_account_ir(snapshot, city_ref=ref, column="status")
    compiled = _compile(ir, snapshot, question)
    assert compiled.parameters[0].value == "ACTIVE"
    assert "ACTIVE" not in compiled.sql


@pytest.mark.parametrize(
    ("token", "data_type", "expected"),
    [
        ("London", "string", "London"),
        ("24", "integer", 24),
        ("seventeen", "integer", 17),
        ("ninety", "integer", 90),
        ("-5", "integer", -5),
        ("12.5", "decimal", 12.5),
        ("true", "boolean", True),
        ("FALSE", "boolean", False),
        ("2024-02-29", "date", "2024-02-29"),
        ("2024-02-29T12:30:45", "timestamp", "2024-02-29T12:30:45"),
    ],
)
def test_scalar_parser_registry_accepts_exact_tokens(
    snapshot, token, data_type, expected
):
    question = canonicalize_question(f"value {token} here")
    start = question.index(token)
    ref = QuestionLiteralRef(
        kind="question", start=start, end=start + len(token), data_type=data_type
    )
    resolved = LiteralResolver().resolve(ref, question, snapshot, data_type)
    assert resolved.value == expected
    assert resolved.data_type == data_type


@pytest.mark.parametrize(
    ("token", "data_type"),
    [
        ("twelve.5", "decimal"),
        ("1e5", "decimal"),
        ("5.5", "integer"),
        ("many", "integer"),
        ("yes", "boolean"),
        ("2023-02-29", "date"),
        ("2024-02-29", "timestamp"),
    ],
)
def test_scalar_parser_registry_rejects_invalid_tokens(snapshot, token, data_type):
    question = canonicalize_question(f"value {token} here")
    start = question.index(token)
    ref = QuestionLiteralRef(
        kind="question", start=start, end=start + len(token), data_type=data_type
    )
    with pytest.raises(CompilerError) as caught:
        LiteralResolver().resolve(ref, question, snapshot, data_type)
    assert caught.value.code == "invalid_literal_type"


def test_quoted_multiword_token_is_one_literal(snapshot):
    question = canonicalize_question("Show accounts in 'New York'")
    start = question.index("'New York'")
    ref = QuestionLiteralRef(
        kind="question", start=start, end=start + len("'New York'"), data_type="string"
    )
    resolved = LiteralResolver().resolve(ref, question, snapshot, "string")
    assert resolved.value == "New York"


def _literal_mutation(snapshot, mutation):
    question = canonicalize_question("Show accounts in London")
    resolver = LiteralResolver()
    if mutation == "span_out_of_bounds":
        ref = QuestionLiteralRef(
            kind="question",
            start=len(question),
            end=len(question) + 6,
            data_type="string",
        )
        return resolver.resolve(ref, question, snapshot, "string")
    if mutation == "span_splits_token":
        ref = QuestionLiteralRef(kind="question", start=17, end=20, data_type="string")
        return resolver.resolve(ref, question, snapshot, "string")
    if mutation == "scalar_parse_mismatch":
        ref = QuestionLiteralRef(kind="question", start=17, end=23, data_type="integer")
        return resolver.resolve(ref, question, snapshot, "integer")
    if mutation == "unknown_governed_literal":
        ref = GovernedLiteralRef(kind="governed", literal_id="literal.absent")
        return resolver.resolve(ref, question, snapshot, "string")
    if mutation == "governed_type_mismatch":
        governed = SnapshotGovernedLiteral(
            literal_id="literal.account-active",
            data_type="string",
            value="ACTIVE",
            source_object_id="table.accounts",
        )
        local = factories.valid_snapshot(governed_literals=(governed,))
        ref = GovernedLiteralRef(kind="governed", literal_id="literal.account-active")
        return resolver.resolve(ref, question, local, "integer")
    raise ValueError(mutation)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("span_out_of_bounds", "invalid_literal_reference"),
        ("span_splits_token", "invalid_literal_reference"),
        ("scalar_parse_mismatch", "invalid_literal_type"),
        ("unknown_governed_literal", "ungrounded_literal_reference"),
        ("governed_type_mismatch", "invalid_literal_type"),
    ],
)
def test_literal_resolution_fails_without_partial_query(snapshot, mutation, expected):
    with pytest.raises(CompilerError) as caught:
        _literal_mutation(snapshot, mutation)
    assert caught.value.code == expected
    assert not hasattr(caught.value, "sql")
    assert "value" not in str(caught.value).lower()
    assert "london" not in str(caught.value).lower()


def test_non_positive_limit_literal_is_rejected(snapshot):
    question = canonicalize_question("Show 0 accounts")
    start = question.index("0")
    ir = RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="limit_accounts",
        nodes=(
            ScanNode(kind="scan", node_id="scan_accounts", table_id="table.accounts"),
            ProjectNode(
                kind="project",
                node_id="project_accounts",
                input_id="scan_accounts",
                outputs=(
                    NamedExpression(
                        alias="account_id",
                        expression=factories.models.ColumnExpression(
                            kind="column",
                            ref=ColumnRef(
                                table_id="table.accounts", column="account_id"
                            ),
                        ),
                    ),
                ),
            ),
            LimitNode(
                kind="limit",
                node_id="limit_accounts",
                input_id="project_accounts",
                count=QuestionLiteralRef(
                    kind="question",
                    start=start,
                    end=start + 1,
                    data_type="integer",
                ),
            ),
        ),
    )
    with pytest.raises(CompilerError) as caught:
        _compile(ir, snapshot, question)
    assert caught.value.code == "invalid_literal_bound"


# --- metric fidelity --------------------------------------------------------


def test_metric_formula_comes_from_snapshot_not_ir(snapshot):
    question = canonicalize_question("transaction volume by branch")
    compiled = _compile(factories.branch_volume_ir(snapshot), snapshot, question)
    metric = next(
        item
        for item in snapshot.objects
        if item.object_id == "metric.transaction-volume"
    )
    assert "sum(" in normalized_sql(compiled.sql)
    assert "amount" in normalized_sql(compiled.sql)
    assert metric.formula is not None
    assert "metric.transaction-volume" not in compiled.sql


def test_two_hop_join_emits_both_exact_predicates(snapshot):
    question = canonicalize_question("transaction volume by branch")
    compiled = _compile(factories.branch_volume_ir(snapshot), snapshot, question)
    sql = normalized_sql(compiled.sql)
    assert "join" in sql
    assert sql.count("join") == 2
    assert "account_id" in sql
    assert "branch_id" in sql


# --- integrity and route ----------------------------------------------------


def test_ir_contract_contains_no_raw_sql_intent_or_literal_value_field():
    schema = json.dumps(RelationalQueryIR.model_json_schema(), sort_keys=True)
    assert '"sql"' not in schema
    assert '"expression_sql"' not in schema
    assert '"intent"' not in schema
    assert '"value"' not in json.dumps(
        LiteralExpression.model_json_schema(), sort_keys=True
    )


def test_mutated_validated_ir_fails_integrity_before_compilation(snapshot):
    question = canonicalize_question("transaction volume by branch")
    accepted = factories.validated_ir(
        factories.branch_volume_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    tampered = accepted.model_copy(update={"ir_hash": "0" * 64})
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("duckdb").compile(tampered, snapshot, question, max_rows=100)
    assert caught.value.code == "validated_ir_integrity_error"
    assert not hasattr(caught.value, "sql")


def test_snapshot_or_question_drift_fails_integrity(snapshot):
    question = canonicalize_question("transaction volume by branch")
    accepted = factories.validated_ir(
        factories.branch_volume_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("duckdb").compile(
            accepted, snapshot, canonicalize_question("different question"), max_rows=5
        )
    assert caught.value.code == "validated_ir_integrity_error"

    other_snapshot = snapshot.model_copy(update={"snapshot_hash": "e" * 64})
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("duckdb").compile(
            accepted, other_snapshot, question, max_rows=5
        )
    assert caught.value.code == "validated_ir_integrity_error"


def test_unsupported_node_position_fails_without_partial_query(snapshot):
    """A join whose right side is a set operation has no supported rendering."""
    question = canonicalize_question("accounts")
    union_ir = factories.complex_node_ir(snapshot, "set_operation")
    nodes = union_ir.nodes + (
        factories.models.ScanNode(
            kind="scan", node_id="scan_branches", table_id="table.branches"
        ),
        factories.models.JoinNode(
            kind="join",
            node_id="join_union",
            left_id="scan_branches",
            right_id="union_accounts",
            relationship_id="relationship.account_branch",
            join_type="inner",
        ),
    )
    broken = union_ir.model_copy(update={"nodes": nodes, "root_node_id": "join_union"})
    validated = factories.validated_ir(
        broken, snapshot, question, generation_route="planned_ir"
    )
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("duckdb").compile(validated, snapshot, question, max_rows=10)
    assert caught.value.code == "unsupported_compiler_node"
    assert not hasattr(caught.value, "sql")


@pytest.mark.parametrize("kind", ["window", "set_operation"])
def test_complex_nodes_require_the_planned_route_at_compile_time(snapshot, kind):
    question = canonicalize_question("accounts")
    ir = factories.complex_node_ir(snapshot, kind)
    planned = factories.validated_ir(
        ir, snapshot, question, generation_route="planned_ir"
    )
    compiled = DialectCompiler("duckdb").compile(
        planned, snapshot, question, max_rows=10
    )
    assert compiled.sql

    smuggled = planned.model_copy(
        update={"generation_route": "default_ir", "accepted_complex_plan_hash": None}
    )
    with pytest.raises(CompilerError) as caught:
        DialectCompiler("duckdb").compile(smuggled, snapshot, question, max_rows=10)
    assert caught.value.code == "complex_node_requires_planned_route"


# --- row limits -------------------------------------------------------------


def test_effective_limit_is_the_minimum_of_request_and_ir_limit(snapshot):
    question = canonicalize_question("Which five customer names are in London")
    compiled = _compile(
        factories.sensitive_ir(snapshot), snapshot, question, max_rows=1000
    )
    assert compiled.parameters[-1].value == 5
    assert "limit" in normalized_sql(compiled.sql)

    tighter = _compile(factories.sensitive_ir(snapshot), snapshot, question, max_rows=2)
    assert tighter.parameters[-1].value == 2


def test_request_cap_applies_when_the_ir_declares_no_limit(snapshot):
    question = canonicalize_question("accounts")
    compiled = _compile(factories.minimal_ir(snapshot), snapshot, question, max_rows=7)
    # A trusted deployment cap is configuration, so it renders inline and never
    # becomes a bound parameter that could be mistaken for a model literal.
    assert "limit 7" in normalized_sql(compiled.sql)
    assert compiled.parameters == ()


def test_parameter_order_is_deterministic_depth_first(snapshot):
    question = canonicalize_question("Which five customer names are in London")
    compiled = _compile(factories.sensitive_ir(snapshot), snapshot, question)
    assert [item.position for item in compiled.parameters] == list(
        range(1, len(compiled.parameters) + 1)
    )
    assert [item.value for item in compiled.parameters] == ["London", 5]


def test_relative_time_compiles_against_the_data_maximum(snapshot):
    question = canonicalize_question("Show transaction growth over the last 24 months")
    compiled = _compile(factories.relative_growth_ir(snapshot), snapshot, question)
    sql = normalized_sql(compiled.sql)
    assert "max(" in sql
    assert "txn_date" in sql
    assert 24 in [item.value for item in compiled.parameters]
    assert "current_date" not in sql
    assert "now(" not in sql


# --- artifact ---------------------------------------------------------------


def test_sql_artifact_never_carries_parameter_values(snapshot):
    question = canonicalize_question("Which five customer names are in London")
    compiled = _compile(factories.sensitive_ir(snapshot), snapshot, question)
    artifact = to_sql_artifact(compiled)
    serialized = artifact.model_dump_json()
    assert "London" not in serialized
    assert artifact.parameter_count == len(compiled.parameters)
    assert artifact.parameter_types == tuple(
        item.data_type for item in compiled.parameters
    )
    assert artifact.sql == compiled.sql
    assert artifact.compiler_version == compiled.compiler_version


def test_compiled_sql_is_a_single_read_only_select(snapshot):
    question = canonicalize_question("Which five customer names are in London")
    compiled = _compile(factories.sensitive_ir(snapshot), snapshot, question)
    sql = normalized_sql(compiled.sql)
    assert sql.startswith("select")
    assert ";" not in compiled.sql.rstrip(";")
    for forbidden in (
        "insert",
        "update",
        "delete",
        "create",
        "attach",
        "copy",
        "read_csv",
    ):
        assert forbidden not in sql


# --- defects found by running compiled SQL against a real engine ------------


def test_metric_formula_columns_are_requalified_without_leftover_parts(snapshot):
    """A three-part formula column must not keep its catalog or schema parts."""
    question = canonicalize_question("transaction volume by branch")
    compiled = _compile(factories.branch_volume_ir(snapshot), snapshot, question)
    assert '"table".' not in compiled.sql
    assert "table.transactions" not in compiled.sql
    # Exactly one alias qualifier remains on the aggregated column.
    assert re.search(r"SUM\(t\d+\.amount\)", compiled.sql)


def test_relative_time_anchor_is_a_scalar_subquery_not_a_bare_aggregate(snapshot):
    """`MAX(...)` in a WHERE clause is invalid SQL; the anchor must be a subquery."""
    question = canonicalize_question("Show transaction growth over the last 24 months")
    compiled = _compile(factories.relative_growth_ir(snapshot), snapshot, question)
    sql = normalized_sql(compiled.sql)
    where_clause = sql.split(" where ", 1)[1].split(" group by ")[0]
    assert "select max(" in where_clause
    assert not re.search(
        r"(?<!select )max\(", where_clause.replace("(select max(", "(")
    )


def test_count_star_needs_no_argument_and_compiles(snapshot):
    """`How many X by Y` is answered by COUNT(*), so zero arguments is legal.

    Requiring one argument rejected the natural form of every counting question
    and made the model look wrong for emitting standard SQL.
    """
    from cerebro import models
    from cerebro.selfcheck import ExpressionTypeRegistry, validate_ir

    assert ExpressionTypeRegistry().function_signature("count") == (0, 1)

    ir = models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="aggregate_accounts",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="scan_accounts", table_id="table.accounts"
            ),
            models.AggregateNode(
                kind="aggregate",
                node_id="aggregate_accounts",
                input_id="scan_accounts",
                group_by=(
                    models.NamedExpression(
                        alias="status",
                        expression=models.ColumnExpression(
                            kind="column",
                            ref=models.ColumnRef(
                                table_id="table.accounts", column="status"
                            ),
                        ),
                    ),
                ),
                measures=(
                    models.NamedExpression(
                        alias="account_count",
                        expression=models.FunctionExpression(
                            kind="function", function="count", arguments=()
                        ),
                    ),
                ),
            ),
        ),
    )
    question = factories.canonical_question("How many accounts are there by status")
    result = validate_ir(ir, snapshot, question, "default_ir")
    assert result.validated_ir is not None, factories.codes(result.violations)

    compiled = DialectCompiler("duckdb").compile(
        factories.validated_ir(ir=ir, snapshot=snapshot, question=question),
        snapshot,
        question,
        100,
    )
    assert "COUNT(*)" in compiled.sql.upper()
    # A row count derives from no column, so it discloses nothing.
    assert compiled.parameters == ()
