from __future__ import annotations

import pytest
import text2sql_factories as factories

from cerebro.models import BoundParameter
from cerebro.provenance import canonicalize_question
from cerebro.selfcheck import (
    DisclosureCaps,
    SQLReferenceGraph,
    authorize_compiled_query,
)
from cerebro.sql_compiler import DialectCompiler

QUESTION = "Show accounts in London"


def codes(violations) -> set[str]:
    return {violation.code for violation in violations}


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


@pytest.fixture()
def canonical_question() -> str:
    return canonicalize_question(QUESTION)


@pytest.fixture()
def validated_ir(snapshot, canonical_question):
    return factories.validated_ir(
        factories.filtered_account_ir(snapshot),
        snapshot,
        canonical_question,
        generation_route="default_ir",
    )


@pytest.fixture()
def compiled_query(validated_ir, snapshot, canonical_question):
    return DialectCompiler("duckdb").compile(
        validated_ir, snapshot, canonical_question, max_rows=100
    )


def _authorize(compiled, validated_ir, snapshot, question, caps=None):
    return authorize_compiled_query(
        compiled,
        validated_ir,
        snapshot,
        question,
        caps or DisclosureCaps.defaults(),
    )


# --- the honest path -------------------------------------------------------


def test_compiler_output_authorizes_cleanly(
    compiled_query, validated_ir, snapshot, canonical_question
):
    result = _authorize(compiled_query, validated_ir, snapshot, canonical_question)
    assert result.violations == ()
    assert result.output_lineage
    assert all(item.output_name for item in result.output_lineage)


def test_authorization_never_exposes_parameter_values(
    compiled_query, validated_ir, snapshot, canonical_question
):
    result = _authorize(compiled_query, validated_ir, snapshot, canonical_question)
    serialized = "".join(item.model_dump_json() for item in result.output_lineage)
    assert "London" not in serialized
    for violation in result.violations:
        assert "London" not in violation.model_dump_json()


def test_reference_graph_resolves_sources_columns_and_placeholders(compiled_query):
    graph = SQLReferenceGraph.from_sql(compiled_query.sql)
    assert graph.tables == {"accounts"}
    assert ("t0", "city") in graph.columns or ("accounts", "city") in graph.columns
    assert graph.placeholder_count == 1
    assert graph.statement_count == 1
    assert graph.inline_literals


# --- compiler defect matrix ------------------------------------------------


def _mutate(compiled, mutation):
    sql = compiled.sql
    if mutation == "extra_table":
        mutated = sql.replace(
            "FROM accounts AS t0",
            "FROM accounts AS t0 INNER JOIN branches AS t9 ON t0.branch_id = t9.branch_id",
        )
        return compiled.model_copy(update={"sql": mutated})
    if mutation == "extra_column":
        return compiled.model_copy(
            update={
                "sql": sql.replace(
                    "SELECT t0.account_id AS account_id",
                    "SELECT t0.account_id AS account_id, t0.customer_name AS leaked",
                )
            }
        )
    if mutation == "wrong_join":
        mutated = sql.replace(
            "FROM accounts AS t0",
            "FROM accounts AS t0 INNER JOIN branches AS t1 ON t0.account_id = t1.branch_id",
        )
        return compiled.model_copy(update={"sql": mutated})
    if mutation == "extra_filter":
        return compiled.model_copy(
            update={"sql": sql.replace("WHERE", "WHERE t0.status <> t0.city AND")}
        )
    if mutation == "external_scan":
        return compiled.model_copy(
            update={"sql": sql.replace("accounts AS t0", "read_csv('leak.csv') AS t0")}
        )
    if mutation == "extra_literal":
        return compiled.model_copy(
            update={"sql": sql.replace("WHERE", "WHERE t0.branch_id = 7 AND")}
        )
    if mutation == "parameter_position_swap":
        return compiled.model_copy(
            update={
                "parameters": (
                    BoundParameter(position=1, data_type="integer", value=7),
                )
            }
        )
    if mutation == "larger_limit":
        return compiled.model_copy(
            update={"sql": sql.replace("LIMIT 100", "LIMIT 5000")}
        )
    raise ValueError(mutation)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("extra_table", "ungrounded_sql_reference"),
        ("extra_column", "ungrounded_sql_reference"),
        ("wrong_join", "relationship_mismatch"),
        ("extra_filter", "undeclared_filter"),
        ("external_scan", "unsafe_sql_source"),
        ("extra_literal", "undeclared_literal"),
        ("parameter_position_swap", "compiled_parameter_mismatch"),
        ("larger_limit", "compiled_limit_mismatch"),
    ],
)
def test_compiler_defect_stops_before_engine(
    compiled_query, validated_ir, snapshot, canonical_question, mutation, expected
):
    corrupted = _mutate(compiled_query, mutation)
    result = _authorize(corrupted, validated_ir, snapshot, canonical_question)
    assert expected in codes(result.violations)
    assert result.output_lineage == ()
    assert result.disclosures == ()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "INSERT INTO accounts VALUES (1)",
        "CREATE TABLE leak AS SELECT 1",
        "DELETE FROM accounts",
        "ATTACH 'other.duckdb'",
        "COPY accounts TO 'leak.csv'",
        "INSTALL httpfs",
    ],
)
def test_non_read_only_or_unsafe_statements_are_refused(
    compiled_query, validated_ir, snapshot, canonical_question, sql
):
    corrupted = compiled_query.model_copy(update={"sql": sql})
    result = _authorize(corrupted, validated_ir, snapshot, canonical_question)
    assert result.violations
    assert codes(result.violations) & {
        "unsafe_sql_source",
        "non_select_statement",
        "unparsable_sql",
    }
    assert result.output_lineage == ()


def test_join_disjunction_is_refused(
    compiled_query, validated_ir, snapshot, canonical_question
):
    mutated = compiled_query.sql.replace(
        "FROM accounts AS t0",
        "FROM accounts AS t0 INNER JOIN branches AS t1 "
        "ON t0.branch_id = t1.branch_id OR t0.account_id = t1.branch_id",
    )
    result = _authorize(
        compiled_query.model_copy(update={"sql": mutated}),
        validated_ir,
        snapshot,
        canonical_question,
    )
    assert result.violations


def test_placeholder_count_must_match_bound_parameters(
    compiled_query, validated_ir, snapshot, canonical_question
):
    mutated = compiled_query.sql.replace("t0.city = ?", "t0.city = ? AND t0.status = ?")
    result = _authorize(
        compiled_query.model_copy(update={"sql": mutated}),
        validated_ir,
        snapshot,
        canonical_question,
    )
    assert "compiled_parameter_mismatch" in codes(result.violations)


def test_natural_join_is_refused(
    compiled_query, validated_ir, snapshot, canonical_question
):
    corrupted = compiled_query.model_copy(
        update={"sql": "SELECT t0.account_id FROM accounts AS t0 NATURAL JOIN branches"}
    )
    result = _authorize(corrupted, validated_ir, snapshot, canonical_question)
    assert "unsafe_join_condition" in codes(result.violations)
    assert result.output_lineage == ()


def test_two_hop_join_authorizes_both_declared_relationships(snapshot):
    question = canonicalize_question(factories.GUARDED_VOLUME_QUESTION)
    validated = factories.validated_ir(
        factories.guarded_branch_volume_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=50
    )
    result = _authorize(compiled, validated, snapshot, question)
    assert result.violations == ()


def test_direct_edge_not_declared_by_ir_is_refused(snapshot):
    question = canonicalize_question(factories.GUARDED_VOLUME_QUESTION)
    validated = factories.validated_ir(
        factories.guarded_branch_volume_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    compiled = DialectCompiler("duckdb").compile(
        validated, snapshot, question, max_rows=50
    )
    # A defect that shortcuts transactions straight to branches must fail even
    # though both tables are grounded.
    mutated = compiled.sql.replace(
        "INNER JOIN branches AS t2 ON t1.branch_id = t2.branch_id",
        "INNER JOIN branches AS t2 ON t0.account_id = t2.branch_id",
    )
    result = _authorize(
        compiled.model_copy(update={"sql": mutated}), validated, snapshot, question
    )
    assert "relationship_mismatch" in codes(result.violations)
