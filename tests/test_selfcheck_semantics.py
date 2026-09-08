from __future__ import annotations

import pytest
import text2sql_factories as factories

from cerebro.models import (
    AggregateNode,
    ColumnExpression,
    ColumnRef,
    FunctionExpression,
    NamedExpression,
    ProjectNode,
    RelationalQueryIR,
    RequestedDisclosure,
    ScanNode,
)
from cerebro.provenance import canonicalize_question
from cerebro.selfcheck import DisclosureCaps, authorize_compiled_query
from cerebro.sql_compiler import DialectCompiler


def codes(violations) -> set[str]:
    return {violation.code for violation in violations}


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


def _compile_and_authorize(
    ir, snapshot, question, *, max_rows=100, caps=None, route="default_ir"
):
    canonical = canonicalize_question(question)
    validated = factories.validated_ir(ir, snapshot, canonical, generation_route=route)
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, canonical, max_rows=max_rows
    )
    result = authorize_compiled_query(
        compiled, validated, snapshot, canonical, caps or DisclosureCaps.defaults()
    )
    return compiled, result


def _column(table: str, name: str) -> ColumnExpression:
    return ColumnExpression(kind="column", ref=ColumnRef(table_id=table, column=name))


# --- disclosure caps -------------------------------------------------------


def test_disclosure_caps_have_the_specified_defaults():
    caps = DisclosureCaps.defaults()
    assert caps.row_limit == 50
    assert caps.minimum_group_size == 5


def test_bounded_sensitive_projection_with_disclosure_is_authorized(snapshot):
    question = "Which five customer names are in London"
    _, result = _compile_and_authorize(
        factories.sensitive_ir(snapshot), snapshot, question
    )
    assert result.violations == ()
    assert result.disclosures
    disclosure = result.disclosures[0]
    assert disclosure.output_name == "customer_name"
    assert disclosure.classification == "confidential"
    assert disclosure.row_limit <= DisclosureCaps.defaults().row_limit
    assert "London" not in disclosure.model_dump_json()


def test_unbounded_sensitive_projection_is_refused(snapshot):
    """No limit node and no disclosure: the confidential column cannot ship."""
    ir = RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="project_names",
        nodes=(
            ScanNode(kind="scan", node_id="scan_accounts", table_id="table.accounts"),
            ProjectNode(
                kind="project",
                node_id="project_names",
                input_id="scan_accounts",
                outputs=(
                    NamedExpression(
                        alias="customer_name",
                        expression=_column("table.accounts", "customer_name"),
                    ),
                ),
            ),
        ),
    )
    _, result = _compile_and_authorize(ir, snapshot, "customer names", max_rows=1000)
    assert "unbounded_sensitive_projection" in codes(result.violations)
    assert result.output_lineage == ()
    assert result.disclosures == ()


def test_disclosure_record_never_exceeds_the_policy_cap(snapshot):
    """The authorizer cannot read a bound limit, so it records the policy cap.

    A parameterized limit is deliberately opaque here: the compiler already
    tightened it, and the disclosure record must still respect policy.
    """
    question = "Which five customer names are in London"
    caps = DisclosureCaps(row_limit=3, minimum_group_size=5)
    _, result = _compile_and_authorize(
        factories.sensitive_ir(snapshot), snapshot, question, max_rows=1000, caps=caps
    )
    assert result.violations == ()
    assert result.disclosures[0].row_limit <= caps.row_limit


def test_inline_limit_above_the_policy_cap_is_refused(snapshot):
    """An inline cap is visible, so an over-wide sensitive limit must fail."""
    question = canonicalize_question("Which five customer names are in London")
    validated = factories.validated_ir(
        factories.sensitive_ir(snapshot), snapshot, question
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=1000
    )
    inline = compiled.model_copy(
        update={
            "sql": compiled.sql.replace("LIMIT ?", "LIMIT 900"),
            "parameters": compiled.parameters[:-1],
        }
    )
    from cerebro.selfcheck import authorize_compiled_query

    result = authorize_compiled_query(
        inline, validated, snapshot, question, DisclosureCaps.defaults()
    )
    assert "compiled_limit_mismatch" in codes(result.violations)


def test_sensitive_disclosure_must_cover_every_contributing_source(snapshot):
    ir = factories.sensitive_ir(snapshot)
    stripped = ir.model_copy(
        update={
            "requested_disclosures": (
                RequestedDisclosure(
                    source_columns=(
                        ColumnRef(table_id="table.accounts", column="city"),
                    ),
                    limit_ref=ir.requested_disclosures[0].limit_ref,
                ),
            )
        }
    )
    _, result = _compile_and_authorize(
        stripped, snapshot, "Which five customer names are in London"
    )
    assert "unbounded_sensitive_projection" in codes(result.violations)


# --- aggregates over sensitive columns -------------------------------------


def _aggregate_ir(function: str, column: str = "amount") -> RelationalQueryIR:
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="aggregate_transactions",
        nodes=(
            ScanNode(
                kind="scan", node_id="scan_transactions", table_id="table.transactions"
            ),
            AggregateNode(
                kind="aggregate",
                node_id="aggregate_transactions",
                input_id="scan_transactions",
                group_by=(),
                measures=(
                    NamedExpression(
                        alias="measure",
                        expression=FunctionExpression(
                            kind="function",
                            function=function,
                            arguments=(_column("table.transactions", column),),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_reducing_metric_without_a_group_guard_is_refused(snapshot):
    """`SUM` over a confidential column needs an explicit minimum group size."""
    _, result = _compile_and_authorize(
        factories.branch_volume_ir(snapshot), snapshot, "transaction volume by branch"
    )
    assert "missing_minimum_group_size" in codes(result.violations)


def test_reducing_aggregate_over_sensitive_values_requires_a_group_threshold(snapshot):
    _, result = _compile_and_authorize(
        _aggregate_ir("sum"), snapshot, "total transaction amount"
    )
    assert "missing_minimum_group_size" in codes(result.violations)


def test_count_over_sensitive_values_needs_no_group_threshold(snapshot):
    _, result = _compile_and_authorize(
        _aggregate_ir("count"), snapshot, "transaction count"
    )
    assert "missing_minimum_group_size" not in codes(result.violations)


def test_value_preserving_aggregate_over_sensitive_values_needs_disclosure(snapshot):
    _, result = _compile_and_authorize(
        _aggregate_ir("max"), snapshot, "largest transaction amount"
    )
    assert "unbounded_sensitive_projection" in codes(result.violations)


# --- metric fidelity -------------------------------------------------------


def test_governed_metric_root_is_authorized(snapshot):
    _, result = _compile_and_authorize(
        factories.guarded_branch_volume_ir(snapshot),
        snapshot,
        factories.GUARDED_VOLUME_QUESTION,
    )
    assert "metric_root_mismatch" not in codes(result.violations)
    assert result.violations == ()


def test_metric_output_wrapped_by_a_compiler_defect_is_refused(snapshot):
    question = canonicalize_question("transaction volume by branch")
    validated = factories.validated_ir(
        factories.branch_volume_ir(snapshot), snapshot, question
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=50
    )
    # `formula + 1` keeps every reference grounded but changes the output.
    mutated = compiled.sql.replace(
        "SUM(t0.amount) AS transaction_volume",
        "SUM(t0.amount) + 1 AS transaction_volume",
    )
    result = authorize_compiled_query(
        compiled.model_copy(update={"sql": mutated}),
        validated,
        snapshot,
        question,
        DisclosureCaps.defaults(),
    )
    assert "metric_root_mismatch" in codes(result.violations)


def test_metric_denominator_change_is_refused(snapshot):
    question = canonicalize_question("transaction volume by branch")
    validated = factories.validated_ir(
        factories.branch_volume_ir(snapshot), snapshot, question
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=50
    )
    mutated = compiled.sql.replace(
        "SUM(t0.amount) AS transaction_volume",
        "SUM(t0.amount) / 2 AS transaction_volume",
    )
    result = authorize_compiled_query(
        compiled.model_copy(update={"sql": mutated}),
        validated,
        snapshot,
        question,
        DisclosureCaps.defaults(),
    )
    assert "metric_root_mismatch" in codes(result.violations)


# --- relative time and assumptions -----------------------------------------


def test_wall_clock_relative_time_is_refused(snapshot):
    question = canonicalize_question("Show transaction growth over the last 24 months")
    validated = factories.validated_ir(
        factories.relative_growth_ir(snapshot), snapshot, question
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=50
    )
    mutated = compiled.sql.replace(
        "(SELECT MAX(txn_date) FROM transactions)", "CURRENT_DATE"
    )
    assert mutated != compiled.sql
    result = authorize_compiled_query(
        compiled.model_copy(update={"sql": mutated}),
        validated,
        snapshot,
        question,
        DisclosureCaps.defaults(),
    )
    assert result.violations
    assert codes(result.violations) & {"wall_clock_relative_time", "undeclared_filter"}


def test_warning_control_is_reapplied_against_the_compiled_ast(snapshot):
    _, result = _compile_and_authorize(
        factories.guarded_branch_volume_ir(snapshot),
        snapshot,
        factories.GUARDED_VOLUME_QUESTION,
    )
    assert "unaddressed_warning" not in codes(result.violations)


def test_metric_volume_stays_positive_without_a_direction_mapping(snapshot):
    question = canonicalize_question("transaction volume by branch")
    validated = factories.validated_ir(
        factories.branch_volume_ir(snapshot), snapshot, question
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=50
    )
    # Reinterpreting governed volume as signed net requires an explicit typed
    # direction mapping, which this IR does not carry.
    mutated = compiled.sql.replace(
        "SUM(t0.amount)",
        "SUM(CASE WHEN t0.txn_type = 'Deposit' THEN t0.amount ELSE -t0.amount END)",
    )
    result = authorize_compiled_query(
        compiled.model_copy(update={"sql": mutated}),
        validated,
        snapshot,
        question,
        DisclosureCaps.defaults(),
    )
    assert result.violations


# --- lineage ---------------------------------------------------------------


def test_lineage_is_ordered_one_to_one_with_outputs(snapshot):
    _, result = _compile_and_authorize(
        factories.guarded_branch_volume_ir(snapshot),
        snapshot,
        factories.GUARDED_VOLUME_QUESTION,
    )
    assert [item.output_name for item in result.output_lineage] == [
        "branch_name",
        "transaction_volume",
    ]
    volume = result.output_lineage[1]
    assert volume.metric_ids == ("metric.transaction-volume",)
    assert volume.classification in {"confidential", "internal"}


def test_lineage_propagates_the_strictest_source_classification(snapshot):
    _, result = _compile_and_authorize(
        factories.sensitive_ir(snapshot),
        snapshot,
        "Which five customer names are in London",
    )
    lineage = result.output_lineage[0]
    assert lineage.classification == "confidential"
    assert lineage.source_columns == (
        ColumnRef(table_id="table.accounts", column="customer_name"),
    )


def test_set_operation_lineage_merges_only_after_every_leaf_passes(snapshot):
    question = canonicalize_question("accounts")
    ir = factories.complex_node_ir(snapshot, "set_operation")
    validated = factories.validated_ir(
        ir, snapshot, question, generation_route="planned_ir"
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=10
    )
    good = authorize_compiled_query(
        compiled, validated, snapshot, question, DisclosureCaps.defaults()
    )
    assert good.violations == ()

    # One unsafe leaf must stop the whole set operation.
    mutated = compiled.sql.replace("accounts AS t1", "read_csv('leak.csv') AS t1", 1)
    if mutated == compiled.sql:
        mutated = compiled.sql.replace(
            "accounts AS t0", "read_csv('leak.csv') AS t0", 1
        )
    unsafe = authorize_compiled_query(
        compiled.model_copy(update={"sql": mutated}),
        validated,
        snapshot,
        question,
        DisclosureCaps.defaults(),
    )
    assert "unsafe_sql_source" in codes(unsafe.violations)
    assert unsafe.output_lineage == ()


def test_authorization_calls_no_validator_or_executor(snapshot):
    import inspect

    from cerebro import selfcheck

    source = inspect.getsource(selfcheck)
    # `duckdb` appears only as the sqlglot dialect name; what must be absent is
    # any engine, provider, or transport dependency.
    for forbidden in (
        "import duckdb",
        "EngineValidator",
        "Executor",
        "httpx",
        "GuardedProvider",
        "OrganizerModelGateway",
    ):
        assert forbidden not in source, forbidden
    assert "sqlglot" in source
