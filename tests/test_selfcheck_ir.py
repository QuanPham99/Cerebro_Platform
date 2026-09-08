from __future__ import annotations

import pytest
import text2sql_factories as factories

from cerebro.models import (
    AggregateNode,
    Ambiguity,
    BinaryExpression,
    CaseExpression,
    ColumnExpression,
    ColumnRef,
    DirectionMappingAssumption,
    FilterNode,
    FunctionExpression,
    GovernedLiteralRef,
    GroundingRefusal,
    InExpression,
    JoinNode,
    LimitNode,
    LiteralExpression,
    MetricExpression,
    NamedExpression,
    ObjectAmbiguityCandidate,
    ProjectNode,
    QuestionLiteralRef,
    RelationalQueryIR,
    RelationshipAmbiguityCandidate,
    RequestedDisclosure,
    ScanNode,
    SetOperationNode,
    SnapshotGovernedLiteral,
    SortKey,
    SortNode,
    TableGroundingNeed,
    WhenThen,
    WindowExpression,
    WindowNode,
)
from cerebro.provenance import canonicalize_question
from cerebro.selfcheck import (
    ExpressionTypeRegistry,
    GroundingIndex,
    check_grounding_refusal,
    validate_clarification,
    validate_ir,
)

QUESTION = "Show accounts in London"


def codes(violations) -> set[str]:
    return {violation.code for violation in violations}


@pytest.fixture(scope="module")
def snapshot():
    return factories.valid_snapshot()


@pytest.fixture()
def canonical_question() -> str:
    return canonicalize_question(QUESTION)


def _column(table: str, name: str) -> ColumnExpression:
    return ColumnExpression(kind="column", ref=ColumnRef(table_id=table, column=name))


def _ir(root: str, *nodes, **extra) -> RelationalQueryIR:
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id=root,
        nodes=tuple(nodes),
        warning_decisions=extra.get("warning_decisions", ()),
        assumptions=extra.get("assumptions", ()),
        requested_disclosures=extra.get("requested_disclosures", ()),
    )


def _scan(node_id: str = "scan_accounts", table: str = "table.accounts") -> ScanNode:
    return ScanNode(kind="scan", node_id=node_id, table_id=table)


def _project(
    node_id: str = "project_accounts",
    input_id: str = "scan_accounts",
    outputs=None,
) -> ProjectNode:
    return ProjectNode(
        kind="project",
        node_id=node_id,
        input_id=input_id,
        outputs=outputs
        or (
            NamedExpression(
                alias="account_id", expression=_column("table.accounts", "account_id")
            ),
        ),
    )


def _valid_ir() -> RelationalQueryIR:
    return _ir("project_accounts", _scan(), _project())


# --- graph shape and containment --------------------------------------------


def _graph_mutation(mutation: str) -> RelationalQueryIR:
    if mutation == "duplicate_node":
        return _ir(
            "project_accounts",
            _scan(),
            _scan(),
            _project(),
        )
    if mutation == "cycle":
        return _ir(
            "filter_a",
            _scan(),
            FilterNode(
                kind="filter",
                node_id="filter_a",
                input_id="filter_b",
                predicate=BinaryExpression(
                    kind="binary",
                    operator="eq",
                    left=_column("table.accounts", "account_id"),
                    right=_column("table.accounts", "account_id"),
                ),
            ),
            FilterNode(
                kind="filter",
                node_id="filter_b",
                input_id="filter_a",
                predicate=BinaryExpression(
                    kind="binary",
                    operator="eq",
                    left=_column("table.accounts", "account_id"),
                    right=_column("table.accounts", "account_id"),
                ),
            ),
        )
    if mutation == "orphan":
        return _ir(
            "project_accounts",
            _scan(),
            _scan("scan_orphan", "table.branches"),
            _project(),
        )
    if mutation == "missing_root":
        return _ir("project_absent", _scan(), _project())
    if mutation == "unauthorized_table":
        return _ir(
            "project_atms",
            _scan("scan_atms", "table.atms"),
            _project("project_atms", "scan_atms"),
        )
    if mutation == "disconnected_join":
        # Every scan is reachable, but `table.transactions` is never linked by a
        # declared relationship, so only the connectivity rule can catch it.
        return _ir(
            "join_b",
            _scan(),
            _scan("scan_branches", "table.branches"),
            _scan("scan_transactions", "table.transactions"),
            JoinNode(
                kind="join",
                node_id="join_a",
                left_id="scan_accounts",
                right_id="scan_branches",
                relationship_id="relationship.account_branch",
                join_type="inner",
            ),
            JoinNode(
                kind="join",
                node_id="join_b",
                left_id="join_a",
                right_id="scan_transactions",
                relationship_id="relationship.account_branch",
                join_type="inner",
            ),
        )
    raise ValueError(mutation)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("duplicate_node", "duplicate_ir_node"),
        ("cycle", "cyclic_ir"),
        ("orphan", "orphan_ir_node"),
        ("missing_root", "invalid_ir_root"),
        ("unauthorized_table", "ungrounded_ir_reference"),
        ("disconnected_join", "disconnected_ir"),
    ],
)
def test_invalid_ir_fails_before_compilation(
    snapshot, canonical_question, mutation, expected
):
    result = validate_ir(
        _graph_mutation(mutation),
        snapshot,
        canonical_question,
        generation_route="default_ir",
    )
    assert expected in codes(result.violations)
    assert result.validated_ir is None


def test_valid_default_ir_is_accepted_unchanged(snapshot, canonical_question):
    ir = _valid_ir()
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert result.violations == ()
    assert result.validated_ir == ir


def test_two_hop_join_ir_is_accepted(snapshot, canonical_question):
    result = validate_ir(
        factories.branch_volume_ir(snapshot),
        snapshot,
        canonical_question,
        generation_route="default_ir",
    )
    assert result.violations == ()
    assert result.validated_ir is not None


# --- expression and dataflow typing -----------------------------------------


def _type_mutation(mutation: str) -> RelationalQueryIR:
    account_id = _column("table.accounts", "account_id")
    if mutation == "numeric_filter":
        return _ir(
            "filter_accounts",
            _scan(),
            FilterNode(
                kind="filter",
                node_id="filter_accounts",
                input_id="scan_accounts",
                predicate=account_id,
            ),
        )
    if mutation == "wrong_function_arity":
        return _ir(
            "project_accounts",
            _scan(),
            _project(
                outputs=(
                    NamedExpression(
                        alias="bad_call",
                        expression=FunctionExpression(
                            kind="function",
                            function="nullif",
                            arguments=(account_id,),
                        ),
                    ),
                )
            ),
        )
    if mutation == "aggregate_in_filter":
        return _ir(
            "filter_accounts",
            _scan(),
            FilterNode(
                kind="filter",
                node_id="filter_accounts",
                input_id="scan_accounts",
                predicate=BinaryExpression(
                    kind="binary",
                    operator="gt",
                    left=FunctionExpression(
                        kind="function", function="count", arguments=(account_id,)
                    ),
                    right=LiteralExpression(
                        kind="literal",
                        ref=QuestionLiteralRef(
                            kind="question", start=0, end=4, data_type="integer"
                        ),
                    ),
                ),
            ),
        )
    if mutation == "window_in_project":
        return _ir(
            "project_accounts",
            _scan(),
            _project(
                outputs=(
                    NamedExpression(
                        alias="ranked",
                        expression=FunctionExpression(
                            kind="function", function="max", arguments=(account_id,)
                        ),
                    ),
                )
            ),
        )
    if mutation == "nested_window_aggregate":
        return _ir(
            "aggregate_accounts",
            _scan(),
            AggregateNode(
                kind="aggregate",
                node_id="aggregate_accounts",
                input_id="scan_accounts",
                group_by=(),
                measures=(
                    NamedExpression(
                        alias="nested",
                        expression=FunctionExpression(
                            kind="function",
                            function="sum",
                            arguments=(
                                FunctionExpression(
                                    kind="function",
                                    function="count",
                                    arguments=(account_id,),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    if mutation == "duplicate_alias":
        return _ir(
            "project_accounts",
            _scan(),
            _project(
                outputs=(
                    NamedExpression(alias="same", expression=account_id),
                    NamedExpression(
                        alias="same",
                        expression=_column("table.accounts", "branch_id"),
                    ),
                )
            ),
        )
    if mutation == "wrong_node_input_count":
        return _ir(
            "join_missing_side",
            _scan(),
            JoinNode(
                kind="join",
                node_id="join_missing_side",
                left_id="scan_accounts",
                right_id="scan_absent",
                relationship_id="relationship.account_branch",
                join_type="inner",
            ),
        )
    if mutation == "set_arity_mismatch":
        return _ir(
            "union_accounts",
            _scan(),
            _scan("scan_accounts_two", "table.accounts"),
            _project("project_one", "scan_accounts"),
            ProjectNode(
                kind="project",
                node_id="project_two",
                input_id="scan_accounts_two",
                outputs=(
                    NamedExpression(alias="account_id", expression=account_id),
                    NamedExpression(
                        alias="branch_id",
                        expression=_column("table.accounts", "branch_id"),
                    ),
                ),
            ),
            SetOperationNode(
                kind="set_operation",
                node_id="union_accounts",
                left_id="project_one",
                right_id="project_two",
                operator="union",
                all=False,
            ),
        )
    if mutation == "set_incompatible_types":
        return _ir(
            "union_accounts",
            _scan(),
            _scan("scan_accounts_two", "table.accounts"),
            _project("project_one", "scan_accounts"),
            ProjectNode(
                kind="project",
                node_id="project_two",
                input_id="scan_accounts_two",
                outputs=(
                    NamedExpression(
                        alias="account_id",
                        expression=_column("table.accounts", "status"),
                    ),
                ),
            ),
            SetOperationNode(
                kind="set_operation",
                node_id="union_accounts",
                left_id="project_one",
                right_id="project_two",
                operator="union",
                all=False,
            ),
        )
    raise ValueError(mutation)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("numeric_filter", "non_boolean_filter"),
        ("wrong_function_arity", "invalid_function_signature"),
        ("aggregate_in_filter", "invalid_aggregate_placement"),
        ("window_in_project", "invalid_aggregate_placement"),
        ("nested_window_aggregate", "nested_aggregate_or_window"),
        ("duplicate_alias", "duplicate_output_alias"),
        ("wrong_node_input_count", "invalid_node_arity"),
        ("set_arity_mismatch", "set_output_arity_mismatch"),
        ("set_incompatible_types", "set_output_type_mismatch"),
    ],
)
def test_named_type_failures_stop_before_compilation(
    snapshot, canonical_question, mutation, expected
):
    result = validate_ir(
        _type_mutation(mutation),
        snapshot,
        canonical_question,
        generation_route="planned_ir" if mutation.startswith("set_") else "default_ir",
    )
    assert codes(result.violations) == {expected}
    assert result.validated_ir is None


def test_window_expression_outside_window_node_is_rejected(
    snapshot, canonical_question
):
    ir = _ir(
        "window_accounts",
        _scan(),
        WindowNode(
            kind="window",
            node_id="window_accounts",
            input_id="scan_accounts",
            outputs=(
                WindowExpression(
                    alias="previous_account",
                    function="lag",
                    argument=_column("table.accounts", "account_id"),
                    partition_by=(),
                    order_by=(
                        SortKey(
                            expression=_column("table.accounts", "account_id"),
                            direction="asc",
                            nulls="last",
                        ),
                    ),
                ),
            ),
        ),
    )
    planned = validate_ir(
        ir, snapshot, canonical_question, generation_route="planned_ir"
    )
    assert planned.violations == ()
    assert planned.validated_ir is not None


def test_column_not_available_from_node_input_is_ungrounded(
    snapshot, canonical_question
):
    ir = _ir(
        "project_accounts",
        _scan(),
        _project(
            outputs=(
                NamedExpression(
                    alias="branch_name",
                    expression=_column("table.branches", "branch_name"),
                ),
            )
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_unknown_column_of_a_grounded_table_is_ungrounded(snapshot, canonical_question):
    ir = _ir(
        "project_accounts",
        _scan(),
        _project(
            outputs=(
                NamedExpression(
                    alias="secret",
                    expression=_column("table.accounts", "not_a_column"),
                ),
            )
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_unknown_metric_reference_is_ungrounded(snapshot, canonical_question):
    ir = _ir(
        "aggregate_accounts",
        _scan(),
        AggregateNode(
            kind="aggregate",
            node_id="aggregate_accounts",
            input_id="scan_accounts",
            group_by=(),
            measures=(
                NamedExpression(
                    alias="volume",
                    expression=MetricExpression(
                        kind="metric", metric_id="metric.not-governed"
                    ),
                ),
            ),
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_case_condition_must_be_boolean(snapshot, canonical_question):
    ir = _ir(
        "project_accounts",
        _scan(),
        _project(
            outputs=(
                NamedExpression(
                    alias="labelled",
                    expression=CaseExpression(
                        kind="case",
                        branches=(
                            WhenThen(
                                when=_column("table.accounts", "account_id"),
                                then=_column("table.accounts", "status"),
                            ),
                        ),
                        else_expression=_column("table.accounts", "status"),
                    ),
                ),
            )
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "non_boolean_filter" in codes(result.violations)


def test_incompatible_in_operands_are_rejected(snapshot, canonical_question):
    ir = _ir(
        "filter_accounts",
        _scan(),
        FilterNode(
            kind="filter",
            node_id="filter_accounts",
            input_id="scan_accounts",
            predicate=InExpression(
                kind="in",
                expression=_column("table.accounts", "account_id"),
                values=(
                    LiteralExpression(
                        kind="literal",
                        ref=QuestionLiteralRef(
                            kind="question", start=5, end=13, data_type="string"
                        ),
                    ),
                ),
                negated=False,
            ),
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "invalid_function_signature" in codes(result.violations)


# --- generation route ------------------------------------------------------


@pytest.mark.parametrize("kind", ["window", "set_operation"])
def test_default_route_cannot_smuggle_complex_node(snapshot, canonical_question, kind):
    if kind == "window":
        ir = _ir(
            "window_accounts",
            _scan(),
            WindowNode(
                kind="window",
                node_id="window_accounts",
                input_id="scan_accounts",
                outputs=(
                    WindowExpression(
                        alias="ranked",
                        function="row_number",
                        partition_by=(),
                        order_by=(
                            SortKey(
                                expression=_column("table.accounts", "account_id"),
                                direction="asc",
                                nulls="last",
                            ),
                        ),
                    ),
                ),
            ),
        )
    else:
        ir = _type_mutation("set_arity_mismatch").model_copy(
            update={
                "nodes": tuple(
                    node
                    for node in _type_mutation("set_arity_mismatch").nodes
                    if node.node_id != "project_two"
                )
                + (
                    ProjectNode(
                        kind="project",
                        node_id="project_two",
                        input_id="scan_accounts_two",
                        outputs=(
                            NamedExpression(
                                alias="account_id",
                                expression=_column("table.accounts", "account_id"),
                            ),
                        ),
                    ),
                )
            }
        )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "complex_node_requires_planned_route" in codes(result.violations)
    assert result.validated_ir is None


# --- literal refs and value-free assumptions --------------------------------


def test_question_literal_span_must_be_inside_the_canonical_question(
    snapshot, canonical_question
):
    ir = _ir(
        "filter_accounts",
        _scan(),
        FilterNode(
            kind="filter",
            node_id="filter_accounts",
            input_id="scan_accounts",
            predicate=BinaryExpression(
                kind="binary",
                operator="eq",
                left=_column("table.accounts", "city"),
                right=LiteralExpression(
                    kind="literal",
                    ref=QuestionLiteralRef(
                        kind="question",
                        start=len(canonical_question),
                        end=len(canonical_question) + 6,
                        data_type="string",
                    ),
                ),
            ),
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_question_literal_span_must_be_a_whole_token(snapshot, canonical_question):
    ir = _ir(
        "filter_accounts",
        _scan(),
        FilterNode(
            kind="filter",
            node_id="filter_accounts",
            input_id="scan_accounts",
            predicate=BinaryExpression(
                kind="binary",
                operator="eq",
                left=_column("table.accounts", "city"),
                right=LiteralExpression(
                    kind="literal",
                    ref=QuestionLiteralRef(
                        kind="question",
                        start=canonical_question.index("London"),
                        end=canonical_question.index("London") + 3,
                        data_type="string",
                    ),
                ),
            ),
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_governed_literal_ref_must_exist_in_the_snapshot(snapshot, canonical_question):
    ir = _ir(
        "filter_accounts",
        _scan(),
        FilterNode(
            kind="filter",
            node_id="filter_accounts",
            input_id="scan_accounts",
            predicate=BinaryExpression(
                kind="binary",
                operator="eq",
                left=_column("table.accounts", "status"),
                right=LiteralExpression(
                    kind="literal",
                    ref=GovernedLiteralRef(
                        kind="governed", literal_id="literal.invented"
                    ),
                ),
            ),
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_governed_literal_type_must_match_the_snapshot_declaration():
    governed = SnapshotGovernedLiteral(
        literal_id="literal.open-status",
        data_type="string",
        value="OPEN",
        source_object_id="table.accounts",
    )
    snapshot = factories.valid_snapshot(governed_literals=(governed,))
    question = canonicalize_question(QUESTION)
    ir = _ir(
        "filter_accounts",
        _scan(),
        FilterNode(
            kind="filter",
            node_id="filter_accounts",
            input_id="scan_accounts",
            predicate=BinaryExpression(
                kind="binary",
                operator="eq",
                left=_column("table.accounts", "account_id"),
                right=LiteralExpression(
                    kind="literal",
                    ref=GovernedLiteralRef(
                        kind="governed", literal_id="literal.open-status"
                    ),
                ),
            ),
        ),
    )
    result = validate_ir(ir, snapshot, question, generation_route="default_ir")
    assert "invalid_function_signature" in codes(result.violations)


def test_assumption_operands_must_be_grounded_refs(snapshot, canonical_question):
    ir = _valid_ir().model_copy(
        update={
            "assumptions": (
                DirectionMappingAssumption(
                    kind="direction_mapping",
                    column=ColumnRef(table_id="table.transactions", column="txn_type"),
                    inflow_refs=(
                        GovernedLiteralRef(kind="governed", literal_id="literal.in"),
                    ),
                    outflow_refs=(
                        GovernedLiteralRef(kind="governed", literal_id="literal.out"),
                    ),
                ),
            )
        }
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_requested_disclosure_sources_must_be_grounded(snapshot, canonical_question):
    ir = _valid_ir().model_copy(
        update={
            "requested_disclosures": (
                RequestedDisclosure(
                    source_columns=(
                        ColumnRef(table_id="table.accounts", column="not_a_column"),
                    ),
                    limit_ref=QuestionLiteralRef(
                        kind="question", start=0, end=4, data_type="integer"
                    ),
                ),
            )
        }
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert "ungrounded_ir_reference" in codes(result.violations)


def test_sensitive_ir_from_factories_validates_against_its_question(snapshot):
    question = canonicalize_question("Which five customer names are in London")
    result = validate_ir(
        factories.sensitive_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    assert result.violations == ()
    assert result.validated_ir is not None


def test_relative_time_ir_validates_against_its_question(snapshot):
    question = canonicalize_question("Show transaction growth over the last 24 months")
    result = validate_ir(
        factories.relative_growth_ir(snapshot),
        snapshot,
        question,
        generation_route="default_ir",
    )
    assert result.violations == ()


# --- supporting registries -------------------------------------------------


def test_grounding_index_exposes_only_snapshot_membership(snapshot):
    index = GroundingIndex.from_snapshot(snapshot)
    assert "table.accounts" in index.table_ids
    assert ("table.accounts", "account_id") in index.columns
    assert "metric.transaction-volume" in index.metric_result_types
    assert "relationship.account_branch" in index.relationships
    assert index.column_type("table.accounts", "balance") is None
    assert index.column_type("table.accounts", "account_id") == "integer"


def test_expression_type_registry_is_version_pinned():
    registry = ExpressionTypeRegistry()
    assert registry.version == "008.types.v1"
    with pytest.raises(ValueError):
        ExpressionTypeRegistry(version="008.types.v2")
    assert registry.is_aggregate("sum") is True
    assert registry.is_aggregate("date_trunc") is False


def test_sort_and_limit_nodes_validate_their_inputs(snapshot, canonical_question):
    ir = _ir(
        "limit_accounts",
        _scan(),
        _project(),
        SortNode(
            kind="sort",
            node_id="sort_accounts",
            input_id="project_accounts",
            keys=(
                SortKey(
                    expression=_column("table.accounts", "account_id"),
                    direction="asc",
                    nulls="last",
                ),
            ),
        ),
        LimitNode(
            kind="limit",
            node_id="limit_accounts",
            input_id="sort_accounts",
            count=QuestionLiteralRef(
                kind="question", start=0, end=4, data_type="integer"
            ),
        ),
    )
    result = validate_ir(
        ir, snapshot, canonical_question, generation_route="default_ir"
    )
    assert result.violations == ()


# --- clarification ---------------------------------------------------------


CLARIFICATION_QUESTION = "volume by customer or account?"


def _clarification_request(question: str, snapshot):
    from cerebro.models import ClarificationRequest

    start = question.index("customer")
    return ClarificationRequest(
        outcome="clarification_request",
        ambiguities=(
            Ambiguity(
                ambiguity_id="target_object",
                start=start,
                end=start + len("customer"),
                candidates=(
                    ObjectAmbiguityCandidate(kind="object", object_id="table.accounts"),
                    ObjectAmbiguityCandidate(
                        kind="object", object_id="table.transactions"
                    ),
                ),
            ),
        ),
    )


def test_valid_clarification_requires_no_fallback_or_engine(snapshot):
    question = canonicalize_question(CLARIFICATION_QUESTION)
    request = _clarification_request(question, snapshot)
    decision = validate_clarification(request, question, snapshot)
    assert decision.reason == "clarification_required"
    assert decision.ambiguities == request.ambiguities
    assert decision.violation is None


def _mutate_clarification(request, question, snapshot, mutation):
    ambiguity = request.ambiguities[0]
    if mutation == "out_of_bounds_span":
        update = {"start": len(question), "end": len(question) + 4}
    elif mutation == "partial_token_span":
        update = {"start": ambiguity.start, "end": ambiguity.start + 3}
    elif mutation == "duplicate_candidates" or mutation == "one_candidate":
        update = {}
    elif mutation == "mixed_candidate_kinds":
        update = {
            "candidates": (
                ambiguity.candidates[0],
                RelationshipAmbiguityCandidate(
                    kind="relationship",
                    relationship_id="relationship.account_branch",
                ),
            )
        }
    elif mutation == "ungrounded_candidate":
        update = {
            "candidates": (
                ambiguity.candidates[0],
                ObjectAmbiguityCandidate(kind="object", object_id="table.atms"),
            )
        }
    elif mutation == "irrelevant_candidate":
        update = {
            "candidates": (
                ambiguity.candidates[0],
                ObjectAmbiguityCandidate(
                    kind="object", object_id="policy.sensitive-output"
                ),
            )
        }
    else:
        raise ValueError(mutation)

    if mutation in {"duplicate_candidates", "one_candidate"}:
        # `Ambiguity` itself forbids these, so the local validator must see them
        # through a payload that bypasses model construction, exactly as a
        # provider response would arrive.
        payload = request.model_dump(mode="python")
        first = payload["ambiguities"][0]
        if mutation == "one_candidate":
            first["candidates"] = first["candidates"][:1]
        else:
            first["candidates"] = [first["candidates"][0], first["candidates"][0]]
        return payload
    return request.model_copy(
        update={"ambiguities": (ambiguity.model_copy(update=update),)}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "out_of_bounds_span",
        "partial_token_span",
        "duplicate_candidates",
        "one_candidate",
        "mixed_candidate_kinds",
        "ungrounded_candidate",
        "irrelevant_candidate",
    ],
)
def test_invalid_clarification_request_is_check_failed(snapshot, mutation):
    question = canonicalize_question(CLARIFICATION_QUESTION)
    request = _mutate_clarification(
        _clarification_request(question, snapshot), question, snapshot, mutation
    )
    decision = validate_clarification(request, question, snapshot)
    assert decision.violation is not None
    assert decision.violation.code == "invalid_clarification_request"
    assert decision.reason is None


def test_clarification_decision_carries_no_prose(snapshot):
    question = canonicalize_question(CLARIFICATION_QUESTION)
    decision = validate_clarification(
        _clarification_request(question, snapshot), question, snapshot
    )
    for ambiguity in decision.ambiguities:
        assert not hasattr(ambiguity, "message")
        assert "value" not in ambiguity.model_dump(mode="python")


# --- grounding refusal ----------------------------------------------------


def test_absent_object_refusal_is_accepted(snapshot):
    refusal = GroundingRefusal(
        outcome="grounding_refusal",
        unmet_needs=(TableGroundingNeed(kind="table", object_id="table.atms"),),
    )
    decision = check_grounding_refusal(refusal, snapshot)
    assert decision.reason == "missing_grounding"
    assert decision.violation is None


def test_false_missing_grounding_claim_is_check_failed(snapshot):
    refusal = GroundingRefusal(
        outcome="grounding_refusal",
        unmet_needs=(TableGroundingNeed(kind="table", object_id="table.accounts"),),
    )
    decision = check_grounding_refusal(refusal, snapshot)
    assert decision.reason is None
    assert decision.violation is not None
    assert decision.violation.code == "false_missing_grounding"
