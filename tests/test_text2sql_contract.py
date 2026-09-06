from __future__ import annotations

import dataclasses
import inspect
import json
import math
from dataclasses import FrozenInstanceError
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

models = import_module("cerebro.models")

# Keep the first RED attributable to the absent v3 contract, rather than to a
# cascading fixture error or one arbitrarily ordered `from ... import` name.
_REQUIRED_V3_SYMBOLS = (
    "TEXT2SQL_CONTRACT_VERSION",
    "GROUNDING_SNAPSHOT_VERSION",
    "RELATIONAL_IR_VERSION",
    "QUESTION_CANONICALIZATION_VERSION",
    "LITERAL_SPAN_REGISTRY_VERSION",
    "EXPRESSION_TYPE_REGISTRY_VERSION",
    "MAX_EXPRESSION_DEPTH",
    "MAX_IR_NODES",
    "MAX_EXPRESSION_ITEMS",
    "SUPPORTED_DEFAULT_NODE_KINDS",
    "SUPPORTED_COMPLEX_NODE_KINDS",
    "StrictModel",
    "StrictFrozenModel",
    "Sha256",
    "NodeId",
    "ColumnName",
    "MetricId",
    "RelationshipId",
    "PolicyId",
    "SemanticObjectId",
    "GovernedLiteralId",
    "Classification",
    "ScalarType",
    "JsonScalar",
    "GenerationRoute",
    "AllowedFunction",
    "AllowedBinaryOperator",
    "ComplexOperatorId",
    "AuthorizationScope",
    "ColumnRef",
    "SnapshotColumn",
    "SnapshotRelationship",
    "SnapshotWarning",
    "SnapshotGovernedLiteral",
    "SnapshotMetadataObject",
    "DialectCapabilities",
    "GroundingSnapshot",
    "QuestionLiteralRef",
    "GovernedLiteralRef",
    "LiteralRef",
    "ColumnExpression",
    "MetricExpression",
    "LiteralExpression",
    "FunctionExpression",
    "BinaryExpression",
    "InExpression",
    "WhenThen",
    "CaseExpression",
    "RelativeTimeExpression",
    "IRExpression",
    "NamedExpression",
    "SortKey",
    "WindowExpression",
    "ScanNode",
    "JoinNode",
    "FilterNode",
    "AggregateNode",
    "ProjectNode",
    "SortNode",
    "LimitNode",
    "WindowNode",
    "SetOperationNode",
    "IRNode",
    "RelationalQueryIR",
    "WarningRef",
    "WarningDecision",
    "DirectionMappingAssumption",
    "StatusMappingAssumption",
    "GrainMappingAssumption",
    "SnapshotAssumption",
    "Assumption",
    "RequestedDisclosure",
    "TableGroundingNeed",
    "ColumnGroundingNeed",
    "MetricGroundingNeed",
    "RelationshipGroundingNeed",
    "GroundingNeed",
    "CheckViolation",
    "GroundingUsage",
    "AttemptRecord",
    "QueryResult",
    "OutputLineage",
    "DisclosureRecord",
    "ValidatedIR",
    "CachedGeneration",
    "ComplexPlanStep",
    "ComplexQueryPlan",
    "complex_plan_sha256",
    "AcceptedComplexRoute",
    "_create_accepted_complex_route",
    "GuardedGenerationRequest",
    "ObjectAmbiguityCandidate",
    "RelationshipAmbiguityCandidate",
    "GovernedLiteralAmbiguityCandidate",
    "GrainAmbiguityCandidate",
    "OperatorAmbiguityCandidate",
    "AmbiguityCandidate",
    "Ambiguity",
    "ClarificationRequest",
    "LiteralClarificationNeed",
    "GroundingRefusal",
    "IRGenerationOutcome",
    "BoundParameter",
    "CompiledQuery",
    "SQLArtifact",
    "BudgetLimits",
    "BudgetUsage",
    "SQLGenerationRequest",
    "ResponseBase",
    "OkResponse",
    "CheckFailedResponse",
    "RefusedResponse",
    "SQLGenerationResponse",
)
_MISSING_V3_SYMBOLS = tuple(
    name for name in _REQUIRED_V3_SYMBOLS if not hasattr(models, name)
)
if _MISSING_V3_SYMBOLS:
    raise ImportError(
        "missing Task 2 v3 contract symbols: " + ", ".join(_MISSING_V3_SYMBOLS)
    )

factories = import_module("text2sql_factories")
ROOT = Path(__file__).resolve().parents[1]


def _adapter(alias_name: str) -> TypeAdapter:
    return TypeAdapter(getattr(models, alias_name))


def _model_payload(value) -> dict:
    return value.model_dump(mode="python")


def _all_mapping_keys(value) -> set[str]:
    if isinstance(value, BaseModel):
        return _all_mapping_keys(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return set(value).union(
            *(_all_mapping_keys(item) for item in value.values()), set()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return set().union(*(_all_mapping_keys(item) for item in value), set())
    return set()


def _schema_property_names(value) -> set[str]:
    if isinstance(value, dict):
        names = set(value.get("properties", {}))
        return names.union(
            *(_schema_property_names(item) for item in value.values()), set()
        )
    if isinstance(value, list):
        return set().union(*(_schema_property_names(item) for item in value), set())
    return set()


def _column_expression(table_id: str = "table.accounts", column: str = "account_id"):
    return models.ColumnExpression(
        kind="column",
        ref=models.ColumnRef(table_id=table_id, column=column),
    )


def _literal_expression(
    question: str = "Show accounts in London", token: str = "London"
):
    return models.LiteralExpression(
        kind="literal",
        ref=factories.question_literal_ref(question, token=token),
    )


_RECURSIVE_EXPRESSION_EDGES = (
    "function.arguments",
    "binary.left",
    "binary.right",
    "in.expression",
    "in.values",
    "case.when",
    "case.then",
    "case.else_expression",
)


def _column_expression_payload() -> dict:
    return {
        "kind": "column",
        "ref": {"table_id": "table.accounts", "column": "account_id"},
    }


def _expression_payload(depth: int, edge: str = "function.arguments") -> dict:
    if depth < 1:
        raise ValueError("depth starts at one")
    if edge not in _RECURSIVE_EXPRESSION_EDGES:
        raise ValueError(f"unknown recursive expression edge: {edge}")

    expression = _column_expression_payload()
    for _ in range(depth - 1):
        sibling = _column_expression_payload()
        if edge == "function.arguments":
            expression = {
                "kind": "function",
                "function": "coalesce",
                "arguments": [expression],
            }
        elif edge == "binary.left":
            expression = {
                "kind": "binary",
                "operator": "eq",
                "left": expression,
                "right": sibling,
            }
        elif edge == "binary.right":
            expression = {
                "kind": "binary",
                "operator": "eq",
                "left": sibling,
                "right": expression,
            }
        elif edge == "in.expression":
            expression = {
                "kind": "in",
                "expression": expression,
                "values": [sibling],
                "negated": False,
            }
        elif edge == "in.values":
            expression = {
                "kind": "in",
                "expression": sibling,
                "values": [expression],
                "negated": False,
            }
        elif edge == "case.when":
            expression = {
                "kind": "case",
                "branches": [{"when": expression, "then": sibling}],
                "else_expression": sibling,
            }
        elif edge == "case.then":
            expression = {
                "kind": "case",
                "branches": [{"when": sibling, "then": expression}],
                "else_expression": sibling,
            }
        else:
            expression = {
                "kind": "case",
                "branches": [{"when": sibling, "then": sibling}],
                "else_expression": expression,
            }
    return expression


def _ir_payload_with_expression(expression: dict) -> dict:
    return {
        "outcome": "ir",
        "ir_version": "008.ir.v1",
        "root_node_id": "filter_accounts",
        "nodes": [
            {
                "kind": "scan",
                "node_id": "scan_accounts",
                "table_id": "table.accounts",
            },
            {
                "kind": "filter",
                "node_id": "filter_accounts",
                "input_id": "scan_accounts",
                "predicate": expression,
            },
        ],
        "warning_decisions": [],
        "assumptions": [],
        "requested_disclosures": [],
    }


def _complex_window_ir():
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="window_growth",
        nodes=(
            models.ScanNode(
                kind="scan",
                node_id="scan_transactions",
                table_id="table.transactions",
            ),
            models.WindowNode(
                kind="window",
                node_id="window_growth",
                input_id="scan_transactions",
                outputs=(
                    models.WindowExpression(
                        alias="previous_amount",
                        function="lag",
                        argument=_column_expression("table.transactions", "amount"),
                        partition_by=(),
                        order_by=(
                            models.SortKey(
                                expression=_column_expression(
                                    "table.transactions", "txn_date"
                                ),
                                direction="asc",
                                nulls="last",
                            ),
                        ),
                        offset=None,
                    ),
                ),
            ),
        ),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(),
    )


def _complex_set_ir():
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="union_accounts",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="left_accounts", table_id="table.accounts"
            ),
            models.ScanNode(
                kind="scan", node_id="right_accounts", table_id="table.accounts"
            ),
            models.SetOperationNode(
                kind="set_operation",
                node_id="union_accounts",
                left_id="left_accounts",
                right_id="right_accounts",
                operator="union",
                all=False,
            ),
        ),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(),
    )


def _result():
    return models.QueryResult(
        columns=("customer_name",),
        column_types=("string",),
        rows=(("Example Customer",),),
        row_count=1,
        truncated=False,
        elapsed_ms=2,
    )


def _ok_payload(snapshot=None) -> dict:
    snapshot = snapshot or factories.valid_snapshot()
    ir = factories.sensitive_ir(snapshot)
    compiled = factories.compiled_query(ir)
    return {
        **_model_payload(factories.valid_response_base(snapshot)),
        "status": "ok",
        "generation_route": "default_ir",
        "snapshot_hash": snapshot.snapshot_hash,
        "ir": ir,
        "sql_artifact": factories.sql_artifact(compiled),
        "result": _result(),
        "output_lineage": (
            models.OutputLineage(
                output_name="customer_name",
                source_columns=(
                    models.ColumnRef(table_id="table.accounts", column="customer_name"),
                ),
                metric_ids=(),
                classification="confidential",
            ),
        ),
        "disclosures": (
            models.DisclosureRecord(
                output_name="customer_name",
                source_columns=(
                    models.ColumnRef(table_id="table.accounts", column="customer_name"),
                ),
                classification="confidential",
                row_limit=5,
            ),
        ),
    }


def _violation(
    code: str = "invalid_ir",
    stage: str = "ast_check",
    subject_ids: tuple[str, ...] = ("project_customer_names",),
):
    return models.CheckViolation(code=code, stage=stage, subject_ids=subject_ids)


def _check_failed_payload(snapshot=None) -> dict:
    snapshot = snapshot or factories.valid_snapshot()
    violation = _violation()
    return {
        **_model_payload(factories.valid_response_base(snapshot)),
        "status": "check_failed",
        "executable": False,
        "ir": factories.minimal_ir(snapshot),
        "sql_artifact": factories.sql_artifact(),
        "violations": (violation,),
    }


def _planned_attempt_records():
    return (
        models.AttemptRecord(
            stage="default_ir",
            ordinal=1,
            outcome="accepted",
            latency_ms=1,
            violation_codes=(),
            generation_route="default_ir",
            cache_status="miss",
        ),
        models.AttemptRecord(
            stage="complexity",
            ordinal=2,
            outcome="accepted",
            latency_ms=1,
            violation_codes=(),
            generation_route="default_ir",
            cache_status="miss",
        ),
        models.AttemptRecord(
            stage="planned_ir",
            ordinal=3,
            outcome="accepted",
            latency_ms=1,
            violation_codes=(),
            generation_route="planned_ir",
            cache_status="miss",
        ),
    )


def _planned_budget_usage():
    return models.BudgetUsage(
        semantic_call_capacity=2,
        planned_ir_authorized=True,
        semantic_calls=2,
        transport_attempts=2,
        input_tokens=20,
        output_tokens=20,
        cost_usd="0.02",
        elapsed_ms=3,
    )


def _apply_planned_generation_evidence(payload: dict) -> dict:
    payload.update(
        generation_route="planned_ir",
        attempt_records=_planned_attempt_records(),
        budget_usage=_planned_budget_usage(),
    )
    return payload


def _pre_generation_response_base_payload() -> dict:
    payload = _model_payload(factories.valid_response_base())
    payload.update(
        snapshot_hash=None,
        generation_route="none",
        cache_status="disabled",
        grounding_usage=models.GroundingUsage(
            object_ids=frozenset(),
            relationship_ids=frozenset(),
            governed_literal_ids=frozenset(),
        ),
        assumptions=(),
        attempt_records=(),
        budget_usage=models.BudgetUsage(
            semantic_call_capacity=1,
            planned_ir_authorized=False,
            semantic_calls=0,
            transport_attempts=0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal(0),
            elapsed_ms=0,
        ),
        violations=(),
    )
    return payload


def _pre_generation_check_failed_payload() -> dict:
    return {
        **_pre_generation_response_base_payload(),
        "status": "check_failed",
        "executable": False,
        "ir": None,
        "sql_artifact": None,
        # A pre-generation failure may only cite a pre-generation stage; an
        # `ast_check` subject would itself be generation evidence.
        "violations": (
            _violation(
                "pre_generation_failure",
                stage="snapshot",
                subject_ids=("table.accounts",),
            ),
        ),
    }


def _table_need():
    return models.TableGroundingNeed(kind="table", object_id="table.atms")


def _refused_payload(reason: str, snapshot=None) -> dict:
    snapshot = snapshot or factories.valid_snapshot()
    payload = {
        **_model_payload(factories.valid_response_base(snapshot)),
        "status": "refused",
        "reason": reason,
        "unmet_needs": (),
        "ambiguities": (),
        "literal_needs": (),
        "policy_ids": (),
        "unsupported_operator_ids": (),
    }
    if reason == "missing_grounding":
        payload["unmet_needs"] = (_table_need(),)
    elif reason == "policy_disallowed":
        payload["policy_ids"] = ("policy.sensitive-output",)
    elif reason == "clarification_required":
        payload["ambiguities"] = (
            factories.valid_clarification_request(snapshot=snapshot).ambiguities[0],
        )
    elif reason == "unsupported_complexity":
        payload["unsupported_operator_ids"] = ("window.period_over_period.v1",)
    else:
        raise ValueError(reason)
    return payload


def _mint_route_or_reject(snapshot_hash: str, plan_hash: str, plan):
    """Mint a capability, tolerating a creator that validates its own arguments.

    Task 2 owns `complex_plan_sha256`, so the private creator is free to reject a
    plan hash that disagrees with the contract helper. Return `None` when minting
    itself is the rejection point.
    """
    try:
        return models._create_accepted_complex_route(
            snapshot_hash=snapshot_hash,
            plan_hash=plan_hash,
            plan=plan,
        )
    except (TypeError, ValueError):
        return None


def _empty_probe_snapshot():
    payload = _model_payload(factories.valid_snapshot())
    payload.update(
        objects=(),
        governed_literals=(),
        ranking_evidence=(),
        authorized_object_ids=frozenset(),
        policy_ids=frozenset(),
    )
    return models.GroundingSnapshot.model_validate(payload)


_VALUE_FREE_CONTRACT_FIELD_INVENTORIES = {
    "ColumnRef": {"table_id", "column"},
    "QuestionLiteralRef": {"kind", "start", "end", "data_type"},
    "GovernedLiteralRef": {"kind", "literal_id"},
    "ColumnExpression": {"kind", "ref"},
    "MetricExpression": {"kind", "metric_id"},
    "LiteralExpression": {"kind", "ref"},
    "FunctionExpression": {"kind", "function", "arguments"},
    "BinaryExpression": {"kind", "operator", "left", "right"},
    "InExpression": {"kind", "expression", "values", "negated"},
    "WhenThen": {"when", "then"},
    "CaseExpression": {"kind", "branches", "else_expression"},
    "RelativeTimeExpression": {
        "kind",
        "date_column",
        "anchor",
        "amount_ref",
        "unit",
        "lower_inclusive",
        "upper_inclusive",
    },
    "NamedExpression": {"alias", "expression"},
    "SortKey": {"expression", "direction", "nulls"},
    "WindowExpression": {
        "alias",
        "function",
        "argument",
        "partition_by",
        "order_by",
        "offset",
    },
    "ScanNode": {"kind", "node_id", "table_id"},
    "JoinNode": {
        "kind",
        "node_id",
        "left_id",
        "right_id",
        "relationship_id",
        "join_type",
    },
    "FilterNode": {"kind", "node_id", "input_id", "predicate"},
    "AggregateNode": {
        "kind",
        "node_id",
        "input_id",
        "group_by",
        "measures",
        "minimum_group_size",
    },
    "ProjectNode": {"kind", "node_id", "input_id", "outputs"},
    "SortNode": {"kind", "node_id", "input_id", "keys"},
    "LimitNode": {"kind", "node_id", "input_id", "count"},
    "WindowNode": {"kind", "node_id", "input_id", "outputs"},
    "SetOperationNode": {
        "kind",
        "node_id",
        "left_id",
        "right_id",
        "operator",
        "all",
    },
    "WarningRef": {"object_id", "warning_hash"},
    "WarningDecision": {"warning", "control_id", "decision"},
    "DirectionMappingAssumption": {
        "kind",
        "column",
        "inflow_refs",
        "outflow_refs",
    },
    "StatusMappingAssumption": {
        "kind",
        "column",
        "semantic_state_id",
        "literal_refs",
    },
    "GrainMappingAssumption": {
        "kind",
        "source_table_ids",
        "grouping_columns",
        "physical_operands",
    },
    "SnapshotAssumption": {
        "kind",
        "column",
        "interpretation_code",
        "physical_operands",
    },
    "RequestedDisclosure": {"source_columns", "limit_ref"},
    "TableGroundingNeed": {"kind", "object_id"},
    "ColumnGroundingNeed": {"kind", "ref"},
    "MetricGroundingNeed": {"kind", "metric_id"},
    "RelationshipGroundingNeed": {"kind", "relationship_id"},
    "CheckViolation": {"code", "stage", "subject_ids"},
    "ComplexPlanStep": {
        "step_id",
        "operator_id",
        "depends_on",
        "input_object_ids",
        "output_names",
    },
    "ComplexQueryPlan": {
        "outcome",
        "plan_version",
        "operator_ids",
        "steps",
        "expected_outputs",
    },
    "ObjectAmbiguityCandidate": {"kind", "object_id"},
    "RelationshipAmbiguityCandidate": {"kind", "relationship_id"},
    "GovernedLiteralAmbiguityCandidate": {"kind", "literal_id"},
    "GrainAmbiguityCandidate": {"kind", "grain", "grouping_columns"},
    "OperatorAmbiguityCandidate": {"kind", "operator_id"},
    "Ambiguity": {"ambiguity_id", "start", "end", "candidates"},
    "ClarificationRequest": {"outcome", "ambiguities"},
    "LiteralClarificationNeed": {
        "kind",
        "issue",
        "expected_type",
        "target_column",
        "literal_ref",
    },
    "GroundingRefusal": {"outcome", "unmet_needs"},
    "RelationalQueryIR": {
        "outcome",
        "ir_version",
        "root_node_id",
        "nodes",
        "warning_decisions",
        "assumptions",
        "requested_disclosures",
    },
}

_VALUE_FREE_UNION_ALIASES = (
    "LiteralRef",
    "IRExpression",
    "IRNode",
    "Assumption",
    "GroundingNeed",
    "AmbiguityCandidate",
    "IRGenerationOutcome",
)

_PROVIDER_AUTHORITY_FORBIDDEN_FIELDS = frozenset(
    {
        "authorization_text",
        "bound_value",
        "control_text",
        "data",
        "description",
        "explanation",
        "expression_sql",
        "formula",
        "formula_sql",
        "intent",
        "literal_value",
        "message",
        "parameters",
        "physical_value",
        "predicate_sql",
        "prompt",
        "prose",
        "query",
        "query_text",
        "raw_query",
        "raw_sql",
        "raw_value",
        "reason",
        "reasoning",
        "resolved_value",
        "rationale",
        "source_value",
        "sql",
        "text",
        "value",
    }
)


def _value_free_contract_controls():
    question_ref = factories.question_literal_ref("Show accounts in London")
    integer_ref = factories.question_literal_ref(
        "Show five accounts", token="five", data_type="integer"
    )
    governed_ref = factories.governed_literal_ref("literal.active-status")
    column_ref = models.ColumnRef(table_id="table.accounts", column="account_id")
    column = models.ColumnExpression(kind="column", ref=column_ref)
    metric = models.MetricExpression(
        kind="metric", metric_id="metric.transaction-volume"
    )
    literal = models.LiteralExpression(kind="literal", ref=question_ref)
    function = models.FunctionExpression(
        kind="function", function="coalesce", arguments=(column,)
    )
    binary = models.BinaryExpression(
        kind="binary", operator="eq", left=column, right=literal
    )
    in_expression = models.InExpression(
        kind="in", expression=column, values=(literal,), negated=False
    )
    branch = models.WhenThen(when=binary, then=literal)
    case = models.CaseExpression(
        kind="case", branches=(branch,), else_expression=literal
    )
    relative_time = models.RelativeTimeExpression(
        kind="relative_time",
        date_column=models.ColumnRef(table_id="table.transactions", column="txn_date"),
        anchor="data_max",
        amount_ref=factories.question_literal_ref(
            "Show 24 months", token="24", data_type="integer"
        ),
        unit="month",
        lower_inclusive=True,
        upper_inclusive=True,
    )
    named = models.NamedExpression(alias="account_id", expression=column)
    sort_key = models.SortKey(expression=column, direction="asc", nulls="last")
    window_expression = models.WindowExpression(
        alias="previous_account",
        function="lag",
        argument=column,
        partition_by=(column,),
        order_by=(sort_key,),
        offset=integer_ref,
    )
    scan = models.ScanNode(
        kind="scan", node_id="scan_accounts", table_id="table.accounts"
    )
    join = models.JoinNode(
        kind="join",
        node_id="join_accounts",
        left_id="scan_accounts",
        right_id="scan_branches",
        relationship_id="relationship.account_branch",
        join_type="inner",
    )
    filter_node = models.FilterNode(
        kind="filter",
        node_id="filter_accounts",
        input_id="scan_accounts",
        predicate=binary,
    )
    aggregate = models.AggregateNode(
        kind="aggregate",
        node_id="aggregate_accounts",
        input_id="scan_accounts",
        group_by=(named,),
        measures=(models.NamedExpression(alias="account_count", expression=metric),),
        minimum_group_size=integer_ref,
    )
    project = models.ProjectNode(
        kind="project",
        node_id="project_accounts",
        input_id="scan_accounts",
        outputs=(named,),
    )
    sort_node = models.SortNode(
        kind="sort",
        node_id="sort_accounts",
        input_id="scan_accounts",
        keys=(sort_key,),
    )
    limit = models.LimitNode(
        kind="limit",
        node_id="limit_accounts",
        input_id="scan_accounts",
        count=integer_ref,
    )
    window = models.WindowNode(
        kind="window",
        node_id="window_accounts",
        input_id="scan_accounts",
        outputs=(window_expression,),
    )
    set_operation = models.SetOperationNode(
        kind="set_operation",
        node_id="union_accounts",
        left_id="scan_accounts",
        right_id="scan_branches",
        operator="union",
        all=False,
    )
    warning_ref = models.WarningRef(
        object_id="metric.transaction-volume", warning_hash="a" * 64
    )
    warning_decision = models.WarningDecision(
        warning=warning_ref,
        control_id="control.positive_transaction_volume",
        decision="applied",
    )
    direction = models.DirectionMappingAssumption(
        kind="direction_mapping",
        column=models.ColumnRef(table_id="table.transactions", column="txn_type"),
        inflow_refs=(factories.governed_literal_ref("literal.inflow"),),
        outflow_refs=(factories.governed_literal_ref("literal.outflow"),),
    )
    status = models.StatusMappingAssumption(
        kind="status_mapping",
        column=models.ColumnRef(table_id="table.accounts", column="status"),
        semantic_state_id="state.active",
        literal_refs=(governed_ref,),
    )
    grain = models.GrainMappingAssumption(
        kind="grain_mapping",
        source_table_ids=("table.transactions",),
        grouping_columns=(
            models.ColumnRef(table_id="table.transactions", column="txn_date"),
        ),
        physical_operands=(governed_ref,),
    )
    snapshot_assumption = models.SnapshotAssumption(
        kind="snapshot",
        column=models.ColumnRef(table_id="table.transactions", column="txn_date"),
        interpretation_code="point_in_time",
        physical_operands=(governed_ref,),
    )
    disclosure = models.RequestedDisclosure(
        source_columns=(
            models.ColumnRef(table_id="table.accounts", column="customer_name"),
        ),
        limit_ref=integer_ref,
    )
    grounding_needs = (
        models.TableGroundingNeed(kind="table", object_id="table.atms"),
        models.ColumnGroundingNeed(
            kind="column",
            ref=models.ColumnRef(table_id="table.accounts", column="city"),
        ),
        models.MetricGroundingNeed(
            kind="metric", metric_id="metric.transaction-volume"
        ),
        models.RelationshipGroundingNeed(
            kind="relationship",
            relationship_id="relationship.transaction_account",
        ),
    )
    plan = factories.complex_window_plan()
    candidates = (
        models.ObjectAmbiguityCandidate(kind="object", object_id="table.accounts"),
        models.RelationshipAmbiguityCandidate(
            kind="relationship",
            relationship_id="relationship.transaction_account",
        ),
        models.GovernedLiteralAmbiguityCandidate(
            kind="governed_literal", literal_id="literal.active-status"
        ),
        models.GrainAmbiguityCandidate(
            kind="grain",
            grain="month",
            grouping_columns=(
                models.ColumnRef(table_id="table.transactions", column="txn_date"),
            ),
        ),
        models.OperatorAmbiguityCandidate(
            kind="operator", operator_id="window.period_over_period.v1"
        ),
    )
    clarification = factories.valid_clarification_request()
    literal_need = models.LiteralClarificationNeed(
        kind="literal_need",
        issue="missing_literal",
        expected_type="string",
        target_column=None,
        literal_ref=None,
    )
    refusal = models.GroundingRefusal(
        outcome="grounding_refusal", unmet_needs=(grounding_needs[0],)
    )
    return (
        column_ref,
        question_ref,
        governed_ref,
        column,
        metric,
        literal,
        function,
        binary,
        in_expression,
        branch,
        case,
        relative_time,
        named,
        sort_key,
        window_expression,
        scan,
        join,
        filter_node,
        aggregate,
        project,
        sort_node,
        limit,
        window,
        set_operation,
        warning_ref,
        warning_decision,
        direction,
        status,
        grain,
        snapshot_assumption,
        disclosure,
        *grounding_needs,
        _violation(),
        plan.steps[0],
        plan,
        *candidates,
        clarification.ambiguities[0],
        clarification,
        literal_need,
        refusal,
        factories.minimal_ir(),
    )


def test_v3_contract_symbol_inventory_is_complete():
    assert not _MISSING_V3_SYMBOLS


def test_contract_versions_limits_routes_and_node_sets_are_exact():
    assert models.TEXT2SQL_CONTRACT_VERSION == "008.v3"
    assert models.GROUNDING_SNAPSHOT_VERSION == "008.grounding.v1"
    assert models.RELATIONAL_IR_VERSION == "008.ir.v1"
    assert models.QUESTION_CANONICALIZATION_VERSION == "008.question.v1"
    assert models.LITERAL_SPAN_REGISTRY_VERSION == "008.literal-span.v1"
    assert models.EXPRESSION_TYPE_REGISTRY_VERSION == "008.types.v1"
    assert models.MAX_EXPRESSION_DEPTH == 32
    assert models.MAX_IR_NODES == 256
    assert models.MAX_EXPRESSION_ITEMS == 100
    assert set(get_args(models.GenerationRoute)) == {"default_ir", "planned_ir"}
    assert models.SUPPORTED_DEFAULT_NODE_KINDS == frozenset(
        {"scan", "join", "filter", "aggregate", "project", "sort", "limit"}
    )
    assert models.SUPPORTED_COMPLEX_NODE_KINDS == frozenset({"window", "set_operation"})


def test_function_binary_and_complex_operator_allowlists_are_exact():
    assert set(get_args(models.AllowedFunction)) == {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "stddev",
        "variance",
        "date_trunc",
        "nullif",
        "coalesce",
    }
    assert set(get_args(models.AllowedBinaryOperator)) == {
        "eq",
        "neq",
        "lt",
        "lte",
        "gt",
        "gte",
        "and",
        "or",
        "add",
        "subtract",
        "multiply",
        "divide",
    }
    assert set(get_args(models.ComplexOperatorId)) == {
        "window.period_over_period.v1",
        "set_operation.safe_binary.v1",
    }


@pytest.mark.parametrize(
    ("alias_name", "valid", "invalid"),
    [
        ("Sha256", "a" * 64, "A" * 64),
        ("Sha256", "0" * 64, "0" * 63),
        ("NodeId", "n" + "0" * 63, "n" + "0" * 64),
        ("NodeId", "scan_accounts", "Scan_accounts"),
        ("ColumnName", "account_id", "account-id"),
        ("TableId", "table.account_2", "table.account__2"),
        ("TableId", "table.accounts", "accounts"),
        ("MetricId", "metric.transaction-volume", "metric.Transaction"),
        (
            "RelationshipId",
            "relationship.transaction_account",
            "relationship.transaction-account",
        ),
        ("PolicyId", "policy.sensitive-output", "policy.Sensitive"),
        ("SemanticObjectId", "concept.cash-flow", "literal.cash-flow"),
        ("GovernedLiteralId", "literal.in-flow", "literal.In-flow"),
    ],
)
def test_identifier_types_validate_at_parse_time(alias_name, valid, invalid):
    adapter = _adapter(alias_name)
    assert adapter.validate_python(valid) == valid
    with pytest.raises(ValidationError):
        adapter.validate_python(invalid)


def test_table_id_keeps_the_task0_fail_closed_underscore_grammar():
    adapter = _adapter("TableId")
    for invalid in ("table._accounts", "table.accounts_", "table.accounts__daily"):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_constrained_aliases_versions_types_and_hashes_are_used_by_owners():
    scope = factories.valid_scope()
    scope_mutations = {
        "scope_version": "008.scope.v2",
        "tenant_scope_hash": "A" * 64,
        "allowed_object_ids": frozenset({"literal.not-a-semantic-object"}),
        "allowed_classifications": frozenset({"secret"}),
        "authorization_scope_hash": "0" * 63,
    }
    for field, value in scope_mutations.items():
        payload = scope.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.AuthorizationScope.model_validate(payload)

    column_payload = {
        "ref": {"table_id": "table.accounts", "column": "account_id"},
        "data_type": "integer",
        "description": "Account identifier.",
        "classification": "internal",
    }
    for path, value in (
        (("ref", "table_id"), "accounts"),
        (("ref", "column"), "Account-ID"),
        (("data_type",), "bigint"),
        (("classification",), "secret"),
    ):
        payload = {
            **column_payload,
            "ref": dict(column_payload["ref"]),
        }
        if len(path) == 2:
            payload[path[0]][path[1]] = value
        else:
            payload[path[0]] = value
        with pytest.raises(ValidationError):
            models.SnapshotColumn.model_validate(payload)

    metric = models.SnapshotMetadataObject(
        object_id="metric.transaction-volume",
        object_type="metric",
        description="Governed metric.",
        formula="sum(table.transactions.amount)",
        metric_result_type="decimal",
    )
    with pytest.raises(ValidationError):
        models.SnapshotMetadataObject.model_validate(
            {**metric.model_dump(mode="python"), "metric_result_type": "number"}
        )

    for payload in (
        {"kind": "scan", "node_id": "Scan_accounts", "table_id": "table.accounts"},
        {"kind": "scan", "node_id": "scan_accounts", "table_id": "accounts"},
        {
            "kind": "join",
            "node_id": "join_accounts",
            "left_id": "Scan_accounts",
            "right_id": "scan_branches",
            "relationship_id": "relationship.account_branch",
            "join_type": "inner",
        },
    ):
        model_class = models.JoinNode if payload["kind"] == "join" else models.ScanNode
        with pytest.raises(ValidationError):
            model_class.model_validate(payload)

    snapshot_payload = factories.valid_snapshot().model_dump(mode="python")
    snapshot_payload["retrieval_config_hash"] = "A" * 64
    with pytest.raises(ValidationError):
        models.GroundingSnapshot.model_validate(snapshot_payload)
    with pytest.raises(ValidationError):
        models.SnapshotWarning(
            object_id="table.accounts",
            warning_hash="A" * 64,
            kind="informational",
            control_id="",
        )
    with pytest.raises(ValidationError):
        models.ValidatedIR(
            ir=factories.minimal_ir(),
            ir_hash="0" * 63,
            snapshot_hash="b" * 64,
            canonical_question_hash="c" * 64,
            generation_route="default_ir",
            accepted_complex_plan_hash=None,
        )


def test_v3_base_configs_forbid_extras_without_weakening_task0_strictness():
    assert models.StrictModel.model_config["extra"] == "forbid"
    assert models.StrictFrozenModel.model_config["extra"] == "forbid"
    assert models.StrictFrozenModel.model_config["frozen"] is True
    assert models.StrictModel.model_config.get("strict") is not True
    assert models.StrictFrozenModel.model_config.get("strict") is not True
    assert models.SourceTableManifest.model_config["strict"] is True
    assert models.SourceTableManifest.model_config["frozen"] is True


def test_serializable_v3_models_are_extra_forbidden():
    serializable_names = (
        "AuthorizationScope",
        "ColumnRef",
        "SnapshotColumn",
        "SnapshotRelationship",
        "SnapshotWarning",
        "SnapshotGovernedLiteral",
        "SnapshotMetadataObject",
        "DialectCapabilities",
        "GroundingSnapshot",
        "QuestionLiteralRef",
        "GovernedLiteralRef",
        "ColumnExpression",
        "MetricExpression",
        "LiteralExpression",
        "FunctionExpression",
        "BinaryExpression",
        "InExpression",
        "WhenThen",
        "CaseExpression",
        "RelativeTimeExpression",
        "NamedExpression",
        "SortKey",
        "WindowExpression",
        "ScanNode",
        "JoinNode",
        "FilterNode",
        "AggregateNode",
        "ProjectNode",
        "SortNode",
        "LimitNode",
        "WindowNode",
        "SetOperationNode",
        "RelationalQueryIR",
        "WarningRef",
        "WarningDecision",
        "DirectionMappingAssumption",
        "StatusMappingAssumption",
        "GrainMappingAssumption",
        "SnapshotAssumption",
        "RequestedDisclosure",
        "TableGroundingNeed",
        "ColumnGroundingNeed",
        "MetricGroundingNeed",
        "RelationshipGroundingNeed",
        "CheckViolation",
        "GroundingUsage",
        "AttemptRecord",
        "QueryResult",
        "OutputLineage",
        "DisclosureRecord",
        "ValidatedIR",
        "CachedGeneration",
        "ComplexPlanStep",
        "ComplexQueryPlan",
        "ObjectAmbiguityCandidate",
        "RelationshipAmbiguityCandidate",
        "GovernedLiteralAmbiguityCandidate",
        "GrainAmbiguityCandidate",
        "OperatorAmbiguityCandidate",
        "Ambiguity",
        "ClarificationRequest",
        "LiteralClarificationNeed",
        "GroundingRefusal",
        "BoundParameter",
        "CompiledQuery",
        "SQLArtifact",
        "BudgetLimits",
        "BudgetUsage",
        "SQLGenerationRequest",
        "ResponseBase",
        "OkResponse",
        "CheckFailedResponse",
        "RefusedResponse",
    )
    for name in serializable_names:
        assert getattr(models, name).model_config["extra"] == "forbid", name


def test_immutable_evidence_and_result_contracts_are_frozen():
    immutable_names = (
        "AuthorizationScope",
        "ColumnRef",
        "SnapshotColumn",
        "SnapshotRelationship",
        "SnapshotWarning",
        "SnapshotGovernedLiteral",
        "SnapshotMetadataObject",
        "DialectCapabilities",
        "GroundingSnapshot",
        "ValidatedIR",
        "CachedGeneration",
        "QueryResult",
        "OutputLineage",
        "DisclosureRecord",
        "BoundParameter",
        "CompiledQuery",
        "SQLArtifact",
    )
    for name in immutable_names:
        assert getattr(models, name).model_config["frozen"] is True, name
    # The brief prescribes `extra="forbid"` plus exact defaults/bounds for
    # `BudgetLimits`, not immutability, so frozen-ness is deliberately not
    # required here. See `test_budget_contract_defaults_are_exact` and
    # `test_budget_limits_enforce_finite_positive_and_transport_bounds`.
    assert models.BudgetLimits.model_config["extra"] == "forbid"

    scope = factories.valid_scope()
    with pytest.raises(ValidationError):
        scope.policy_version = "changed"
    nested_ref = factories.valid_snapshot().objects[0].columns[0].ref
    with pytest.raises(ValidationError):
        nested_ref.column = "changed"
    result = _result()
    with pytest.raises(ValidationError):
        result.row_count = 0


def test_scope_set_fields_remain_frozensets_and_serialize_sorted():
    scope = factories.valid_scope(
        allowed_object_ids={"table.transactions", "table.accounts"}
    )
    assert isinstance(scope.allowed_object_ids, frozenset)
    assert isinstance(scope.allowed_classifications, frozenset)
    dumped = scope.model_dump(mode="json")
    assert dumped["allowed_object_ids"] == ["table.accounts", "table.transactions"]
    assert dumped["allowed_classifications"] == [
        "confidential",
        "internal",
        "public",
        "restricted",
    ]


def test_snapshot_set_fields_are_immutable_and_serialize_sorted():
    snapshot = factories.valid_snapshot()
    assert isinstance(snapshot.authorized_object_ids, frozenset)
    assert isinstance(snapshot.policy_ids, frozenset)
    dumped = snapshot.model_dump(mode="json")
    assert dumped["authorized_object_ids"] == sorted(snapshot.authorized_object_ids)
    assert dumped["policy_ids"] == sorted(snapshot.policy_ids)


def test_valid_snapshot_honors_narrow_scope_and_relationship_dependencies():
    allowed_ids = frozenset(
        {
            "table.accounts",
            "table.branches",
            "relationship.account_branch",
            "policy.sensitive-output",
        }
    )
    scope = factories.valid_scope(allowed_object_ids=allowed_ids)
    out_of_scope_literal = models.SnapshotGovernedLiteral(
        literal_id="literal.transaction-only",
        data_type="string",
        value="outflow",
        source_object_id="table.transactions",
    )
    snapshot = factories.valid_snapshot(
        scope=scope,
        governed_literals=(out_of_scope_literal,),
    )

    assert snapshot.authorized_object_ids == allowed_ids
    assert {item.object_id for item in snapshot.objects} == allowed_ids
    assert snapshot.policy_ids == frozenset({"policy.sensitive-output"})
    assert snapshot.governed_literals == ()
    for item in snapshot.objects:
        for column in item.columns:
            assert column.ref.table_id in allowed_ids
        for relationship in item.relationships:
            assert relationship.relationship_id in allowed_ids
            assert relationship.left.table_id in allowed_ids
            assert relationship.right.table_id in allowed_ids
    assert factories.minimal_ir(snapshot).nodes[0].table_id == "table.accounts"


def test_snapshot_ranking_evidence_is_not_the_mutable_legacy_ranked_result():
    annotation = models.GroundingSnapshot.model_fields["ranking_evidence"].annotation
    item_type = get_args(annotation)[0]
    assert item_type is not models.RankedResult
    assert issubclass(item_type, BaseModel)
    assert item_type.model_config["extra"] == "forbid"
    assert item_type.model_config["frozen"] is True
    assert "RankedResult" not in json.dumps(
        models.GroundingSnapshot.model_json_schema(), sort_keys=True
    )


def test_dialect_capabilities_have_exact_minimal_shape_and_defaults():
    capabilities = models.DialectCapabilities(
        supports_window=True, supports_set_operations=False
    )
    assert capabilities.parameter_style == "qmark"
    assert set(capabilities.model_fields) == {
        "parameter_style",
        "supports_window",
        "supports_set_operations",
    }
    with pytest.raises(ValidationError):
        models.DialectCapabilities(
            parameter_style="named",
            supports_window=True,
            supports_set_operations=True,
        )


def test_snapshot_warning_actionability_requires_exact_control_authority():
    actionable = models.SnapshotWarning(
        object_id="table.accounts",
        warning_hash="a" * 64,
        kind="actionable",
        control_id="control.account_filter",
    )
    assert actionable.control_id == "control.account_filter"
    informational = models.SnapshotWarning(
        object_id="table.accounts",
        warning_hash="b" * 64,
        kind="informational",
        control_id="",
    )
    assert informational.control_id == ""

    with pytest.raises(ValidationError):
        models.SnapshotWarning(
            object_id="table.accounts",
            warning_hash="a" * 64,
            kind="actionable",
            control_id="",
        )
    with pytest.raises(ValidationError):
        models.SnapshotWarning(
            object_id="table.accounts",
            warning_hash="a" * 64,
            kind="informational",
            control_id="control.must_not_authorize",
        )


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("string", "London"),
        ("integer", 5),
        ("decimal", 12),
        ("decimal", 12.5),
        ("boolean", True),
        ("date", "2024-02-29"),
        ("timestamp", "2024-02-29T12:30:45.123"),
        ("timestamp", "2024-02-29 12:30:45"),
    ],
)
def test_governed_literal_accepts_exact_scalar_values(data_type, value):
    literal = models.SnapshotGovernedLiteral(
        literal_id="literal.test-value",
        data_type=data_type,
        value=value,
        source_object_id="table.accounts",
    )
    assert literal.value == value
    assert type(literal.value) is type(value)


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("string", 5),
        ("string", None),
        ("integer", True),
        ("integer", 5.0),
        ("integer", 5.5),
        ("integer", "5"),
        ("decimal", True),
        ("decimal", "12.5"),
        ("decimal", math.inf),
        ("decimal", -math.inf),
        ("decimal", math.nan),
        ("boolean", 1),
        ("boolean", "true"),
        ("date", "2023-02-29"),
        ("date", "2024-02-29T00:00:00"),
        ("timestamp", "2024-02-30T12:00:00"),
        ("timestamp", "2024-02-29T12:00:00Z"),
        ("timestamp", "2024-02-29"),
    ],
)
def test_governed_literal_rejects_coercion_invalid_dates_and_nonfinite_numbers(
    data_type, value
):
    with pytest.raises(ValidationError):
        models.SnapshotGovernedLiteral(
            literal_id="literal.test-value",
            data_type=data_type,
            value=value,
            source_object_id="table.accounts",
        )


def test_snapshot_metric_fields_are_required_exactly_for_metrics():
    metric = models.SnapshotMetadataObject(
        object_id="metric.transaction-volume",
        object_type="metric",
        description="Governed metric.",
        formula="sum(table.transactions.amount)",
        metric_result_type="decimal",
    )
    assert metric.formula is not None
    assert metric.metric_result_type == "decimal"

    for removed in ("formula", "metric_result_type"):
        payload = metric.model_dump(mode="python")
        payload.pop(removed)
        with pytest.raises(ValidationError):
            models.SnapshotMetadataObject.model_validate(payload)

    for extra in (
        {"formula": "sum(table.accounts.account_id)"},
        {"metric_result_type": "integer"},
        {
            "formula": "sum(table.accounts.account_id)",
            "metric_result_type": "integer",
        },
    ):
        with pytest.raises(ValidationError):
            models.SnapshotMetadataObject(
                object_id="table.accounts",
                object_type="table",
                description="Accounts.",
                **extra,
            )


def test_snapshot_types_and_metric_result_type_are_strict():
    payload = factories.valid_snapshot().model_dump(mode="json")
    metric = next(
        item for item in payload["objects"] if item["object_type"] == "metric"
    )
    del metric["metric_result_type"]
    with pytest.raises(ValidationError):
        models.GroundingSnapshot.model_validate(payload)


def test_snapshot_requires_exact_versions_dialect_and_hashes():
    snapshot = factories.valid_snapshot()
    mutations = {
        "snapshot_version": "008.grounding.v2",
        "canonicalization_version": "008.question.v2",
        "literal_registry_version": "008.literal-span.v2",
        "type_registry_version": "008.types.v2",
        "dialect": "postgres",
        "snapshot_hash": "A" * 64,
    }
    for field, value in mutations.items():
        payload = snapshot.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.GroundingSnapshot.model_validate(payload)


def test_literal_refs_are_span_or_governed_id_only_and_validate_spans():
    question_ref = factories.question_literal_ref("Show accounts in London")
    governed_ref = factories.governed_literal_ref("literal.active-status")
    assert set(question_ref.model_fields) == {"kind", "start", "end", "data_type"}
    assert set(governed_ref.model_fields) == {"kind", "literal_id"}
    for ref in (question_ref, governed_ref):
        assert "value" not in ref.model_dump(mode="python")
        with pytest.raises(ValidationError):
            type(ref).model_validate({**ref.model_dump(), "value": "private"})

    with pytest.raises(ValidationError):
        models.QuestionLiteralRef(kind="question", start=-1, end=2, data_type="string")
    with pytest.raises(ValidationError):
        models.QuestionLiteralRef(kind="question", start=3, end=3, data_type="string")
    with pytest.raises(ValidationError):
        models.QuestionLiteralRef(kind="question", start=4, end=3, data_type="string")


def test_provider_outcome_schema_has_no_sql_value_or_free_form_intent():
    schema = TypeAdapter(models.IRGenerationOutcome).json_schema()
    property_names = _schema_property_names(schema)
    assert _PROVIDER_AUTHORITY_FORBIDDEN_FIELDS.isdisjoint(property_names)

    serialized_schema = json.dumps(schema, sort_keys=True)
    literal_schema = json.dumps(
        models.LiteralExpression.model_json_schema(), sort_keys=True
    )
    for forbidden in ('"sql"', '"raw_sql"', '"query"', '"intent"'):
        assert forbidden not in serialized_schema
    assert '"value"' not in literal_schema
    for leaked in ("SQLArtifact", "CompiledQuery", "BoundParameter"):
        assert leaked not in serialized_schema


@pytest.mark.parametrize("alias_name", _VALUE_FREE_UNION_ALIASES)
def test_value_free_union_schemas_are_recursively_free_of_authority_channels(
    alias_name,
):
    property_names = _schema_property_names(_adapter(alias_name).json_schema())
    assert property_names, alias_name
    assert _PROVIDER_AUTHORITY_FORBIDDEN_FIELDS.isdisjoint(property_names), alias_name


@pytest.mark.parametrize("name", sorted(_VALUE_FREE_CONTRACT_FIELD_INVENTORIES))
def test_value_free_variant_schemas_expose_only_inventoried_properties(name):
    schema = getattr(models, name).model_json_schema()
    property_names = _schema_property_names(schema)
    assert _VALUE_FREE_CONTRACT_FIELD_INVENTORIES[name] <= property_names, name
    assert _PROVIDER_AUTHORITY_FORBIDDEN_FIELDS.isdisjoint(property_names), name
    serialized = json.dumps(schema, sort_keys=True)
    for forbidden in (
        '"sql"',
        '"raw_sql"',
        '"query"',
        '"intent"',
        '"raw_value"',
        '"resolved_value"',
        '"reasoning"',
        '"message"',
    ):
        assert forbidden not in serialized, name


def test_provider_outcome_and_supporting_field_inventories_are_exact():
    controls = {
        type(control).__name__: control for control in _value_free_contract_controls()
    }
    assert set(controls) == set(_VALUE_FREE_CONTRACT_FIELD_INVENTORIES)
    for name, expected_fields in _VALUE_FREE_CONTRACT_FIELD_INVENTORIES.items():
        model_class = getattr(models, name)
        assert set(model_class.model_fields) == expected_fields, name
        assert set(controls[name].model_fields) == expected_fields, name


def test_every_provider_supporting_variant_rejects_value_prose_and_sql_aliases():
    for control in _value_free_contract_controls():
        payload = control.model_dump(mode="python")
        for forbidden in _PROVIDER_AUTHORITY_FORBIDDEN_FIELDS:
            with pytest.raises(ValidationError):
                type(control).model_validate(
                    {**payload, forbidden: "forbidden-authority-channel"}
                )


def test_value_free_schema_guard_does_not_ban_snapshot_or_compiler_fields():
    snapshot_object = factories.valid_snapshot().objects[0]
    assert "description" in snapshot_object.model_fields
    assert snapshot_object.description
    assert set(models.CompiledQuery.model_fields) >= {"sql", "parameters"}
    assert "sql" in models.SQLArtifact.model_fields


def test_literal_expression_rejects_embedded_value():
    valid = _literal_expression()
    assert valid.ref.kind == "question"
    with pytest.raises(ValidationError):
        models.LiteralExpression.model_validate(
            {
                **valid.model_dump(mode="python"),
                "value": "London",
                "data_type": "string",
            }
        )


def test_expression_union_has_exact_discriminated_variants():
    schema = TypeAdapter(models.IRExpression).json_schema()
    mapping = schema["discriminator"]["mapping"]
    assert set(mapping) == {
        "column",
        "metric",
        "literal",
        "function",
        "binary",
        "in",
        "case",
        "relative_time",
    }
    with pytest.raises(ValidationError):
        TypeAdapter(models.IRExpression).validate_python(
            {"kind": "sql", "sql": "SELECT 1"}
        )


def test_all_expression_variants_have_valid_controls():
    literal = _literal_expression()
    controls = (
        _column_expression(),
        models.MetricExpression(kind="metric", metric_id="metric.transaction-volume"),
        literal,
        models.FunctionExpression(
            kind="function", function="coalesce", arguments=(_column_expression(),)
        ),
        models.BinaryExpression(
            kind="binary",
            operator="eq",
            left=_column_expression(),
            right=literal,
        ),
        models.InExpression(
            kind="in",
            expression=_column_expression("table.accounts", "city"),
            values=(literal,),
            negated=False,
        ),
        models.CaseExpression(
            kind="case",
            branches=(
                models.WhenThen(
                    when=models.BinaryExpression(
                        kind="binary",
                        operator="eq",
                        left=_column_expression("table.accounts", "city"),
                        right=literal,
                    ),
                    then=literal,
                ),
            ),
            else_expression=literal,
        ),
        models.RelativeTimeExpression(
            kind="relative_time",
            date_column=models.ColumnRef(
                table_id="table.transactions", column="txn_date"
            ),
            anchor="data_max",
            amount_ref=factories.question_literal_ref(
                "Show the last 24 months", token="24", data_type="integer"
            ),
            unit="month",
            lower_inclusive=True,
            upper_inclusive=True,
        ),
    )
    adapter = TypeAdapter(models.IRExpression)
    assert [adapter.validate_python(item).kind for item in controls] == [
        "column",
        "metric",
        "literal",
        "function",
        "binary",
        "in",
        "case",
        "relative_time",
    ]


@pytest.mark.parametrize("function", ["median", "random", "read_csv"])
def test_function_expression_rejects_non_allowlisted_functions(function):
    with pytest.raises(ValidationError):
        models.FunctionExpression(
            kind="function", function=function, arguments=(_column_expression(),)
        )


@pytest.mark.parametrize("operator", ["like", "concat", "regexp", "sql"])
def test_binary_expression_rejects_non_allowlisted_operators(operator):
    with pytest.raises(ValidationError):
        models.BinaryExpression(
            kind="binary",
            operator=operator,
            left=_column_expression(),
            right=_literal_expression(),
        )


@pytest.mark.parametrize("edge", _RECURSIVE_EXPRESSION_EDGES)
def test_expression_depth_accepts_32_and_rejects_33_through_every_edge(edge):
    adapter = TypeAdapter(models.IRExpression)
    accepted_payload = _expression_payload(32, edge)
    rejected_payload = _expression_payload(33, edge)

    assert adapter.validate_python(accepted_payload).kind in {
        "function",
        "binary",
        "in",
        "case",
    }
    accepted_ir = models.RelationalQueryIR.model_validate(
        _ir_payload_with_expression(accepted_payload)
    )
    assert accepted_ir.nodes[-1].kind == "filter"

    with pytest.raises(ValidationError):
        adapter.validate_python(rejected_payload)
    with pytest.raises(ValidationError):
        models.RelationalQueryIR.model_validate(
            _ir_payload_with_expression(rejected_payload)
        )


def test_function_arguments_accept_100_and_reject_101():
    leaf = _column_expression()
    accepted = models.FunctionExpression(
        kind="function", function="coalesce", arguments=(leaf,) * 100
    )
    assert len(accepted.arguments) == 100
    with pytest.raises(ValidationError):
        models.FunctionExpression(
            kind="function", function="coalesce", arguments=(leaf,) * 101
        )


def test_in_values_require_one_and_accept_100_reject_101():
    literal = _literal_expression()
    accepted = models.InExpression(
        kind="in",
        expression=_column_expression(),
        values=(literal,) * 100,
        negated=False,
    )
    assert len(accepted.values) == 100
    with pytest.raises(ValidationError):
        models.InExpression(
            kind="in", expression=_column_expression(), values=(), negated=False
        )
    with pytest.raises(ValidationError):
        models.InExpression(
            kind="in",
            expression=_column_expression(),
            values=(literal,) * 101,
            negated=False,
        )


def test_case_branches_require_one_and_accept_100_reject_101():
    branch = models.WhenThen(
        when=models.BinaryExpression(
            kind="binary",
            operator="eq",
            left=_column_expression(),
            right=_literal_expression(),
        ),
        then=_literal_expression(),
    )
    accepted = models.CaseExpression(
        kind="case", branches=(branch,) * 100, else_expression=_literal_expression()
    )
    assert len(accepted.branches) == 100
    with pytest.raises(ValidationError):
        models.CaseExpression(
            kind="case", branches=(), else_expression=_literal_expression()
        )
    with pytest.raises(ValidationError):
        models.CaseExpression(
            kind="case",
            branches=(branch,) * 101,
            else_expression=_literal_expression(),
        )


def test_window_expression_collections_accept_100_and_reject_101():
    expression = _column_expression()
    key = models.SortKey(expression=expression, direction="asc", nulls="last")
    accepted = models.WindowExpression(
        alias="ranked",
        function="row_number",
        partition_by=(expression,) * 100,
        order_by=(key,) * 100,
    )
    assert len(accepted.partition_by) == 100
    assert len(accepted.order_by) == 100
    with pytest.raises(ValidationError):
        models.WindowExpression(
            alias="ranked",
            function="row_number",
            partition_by=(expression,) * 101,
            order_by=(),
        )
    with pytest.raises(ValidationError):
        models.WindowExpression(
            alias="ranked",
            function="row_number",
            partition_by=(),
            order_by=(key,) * 101,
        )


def test_relative_time_is_data_relative_and_uses_only_a_literal_ref():
    expression = next(
        node.predicate
        for node in factories.relative_growth_ir().nodes
        if node.kind == "filter"
    )
    assert expression.anchor == "data_max"
    assert expression.amount_ref.kind == "question"
    assert "value" not in expression.model_dump(mode="python")
    for bad_anchor in ("now", "current_date", "wall_clock"):
        payload = expression.model_dump(mode="python")
        payload["anchor"] = bad_anchor
        with pytest.raises(ValidationError):
            models.RelativeTimeExpression.model_validate(payload)


def test_ir_node_union_has_exact_discriminated_variants():
    schema = TypeAdapter(models.IRNode).json_schema()
    assert set(schema["discriminator"]["mapping"]) == {
        "scan",
        "join",
        "filter",
        "aggregate",
        "project",
        "sort",
        "limit",
        "window",
        "set_operation",
    }
    with pytest.raises(ValidationError):
        TypeAdapter(models.IRNode).validate_python(
            {"kind": "raw_sql", "node_id": "raw", "sql": "SELECT 1"}
        )


def test_all_relational_node_variants_have_valid_controls():
    expression = _column_expression()
    literal_ref = factories.question_literal_ref("Show the first five", token="five")
    nodes = (
        models.ScanNode(kind="scan", node_id="scan_a", table_id="table.accounts"),
        models.JoinNode(
            kind="join",
            node_id="join_a",
            left_id="scan_a",
            right_id="scan_b",
            relationship_id="relationship.account_branch",
            join_type="left",
        ),
        models.FilterNode(
            kind="filter", node_id="filter_a", input_id="scan_a", predicate=expression
        ),
        models.AggregateNode(
            kind="aggregate",
            node_id="aggregate_a",
            input_id="scan_a",
            group_by=(
                models.NamedExpression(alias="account_id", expression=expression),
            ),
            measures=(
                models.NamedExpression(
                    alias="count_accounts",
                    expression=models.FunctionExpression(
                        kind="function", function="count", arguments=(expression,)
                    ),
                ),
            ),
            minimum_group_size=literal_ref,
        ),
        models.ProjectNode(
            kind="project",
            node_id="project_a",
            input_id="scan_a",
            outputs=(
                models.NamedExpression(alias="account_id", expression=expression),
            ),
        ),
        models.SortNode(
            kind="sort",
            node_id="sort_a",
            input_id="scan_a",
            keys=(
                models.SortKey(expression=expression, direction="asc", nulls="last"),
            ),
        ),
        models.LimitNode(
            kind="limit",
            node_id="limit_a",
            input_id="scan_a",
            count=literal_ref,
        ),
        _complex_window_ir().nodes[-1],
        _complex_set_ir().nodes[-1],
    )
    adapter = TypeAdapter(models.IRNode)
    assert {adapter.validate_python(node).kind for node in nodes} == {
        "scan",
        "join",
        "filter",
        "aggregate",
        "project",
        "sort",
        "limit",
        "window",
        "set_operation",
    }


def test_relational_ir_requires_one_node_accepts_256_and_rejects_257():
    def scans(count: int):
        return tuple(
            models.ScanNode(kind="scan", node_id=f"n{index}", table_id="table.accounts")
            for index in range(count)
        )

    accepted = models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="n255",
        nodes=scans(256),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(),
    )
    assert len(accepted.nodes) == 256
    with pytest.raises(ValidationError):
        models.RelationalQueryIR(
            outcome="ir",
            ir_version="008.ir.v1",
            root_node_id="root",
            nodes=(),
            warning_decisions=(),
            assumptions=(),
            requested_disclosures=(),
        )
    with pytest.raises(ValidationError):
        models.RelationalQueryIR(
            outcome="ir",
            ir_version="008.ir.v1",
            root_node_id="n256",
            nodes=scans(257),
            warning_decisions=(),
            assumptions=(),
            requested_disclosures=(),
        )


def test_relational_ir_enforces_schema_not_task5_graph_semantics():
    ir = models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="duplicate",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="duplicate", table_id="table.accounts"
            ),
            models.ScanNode(
                kind="scan", node_id="duplicate", table_id="table.transactions"
            ),
        ),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(),
    )
    assert len(ir.nodes) == 2


def test_relational_ir_rejects_wrong_version_and_free_form_fields():
    ir = factories.minimal_ir()
    for field, value in (
        ("ir_version", "008.ir.v2"),
        ("intent", "Show account identifiers"),
        ("sql", "SELECT account_id FROM accounts"),
        ("value", "private"),
        ("expression_sql", "account_id"),
    ):
        payload = ir.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.RelationalQueryIR.model_validate(payload)


def test_factory_irs_have_no_physical_value_sql_or_free_form_intent():
    irs = (
        factories.minimal_ir(),
        factories.branch_volume_ir(),
        factories.relative_growth_ir(),
        factories.sensitive_ir(),
    )
    for ir in irs:
        assert not {
            "value",
            "sql",
            "intent",
            "expression_sql",
            "raw_value",
        }.intersection(_all_mapping_keys(ir))


def test_warning_refs_decisions_are_hash_bound_and_extra_forbidden():
    warning = models.WarningRef(
        object_id="metric.transaction-volume", warning_hash="a" * 64
    )
    decision = models.WarningDecision(
        warning=warning,
        control_id="control.positive_transaction_volume",
        decision="applied",
    )
    assert decision.warning.warning_hash == "a" * 64
    with pytest.raises(ValidationError):
        models.WarningDecision.model_validate(
            {**decision.model_dump(), "warning_text": "Do not trust prose."}
        )
    with pytest.raises(ValidationError):
        models.WarningDecision(
            warning=warning,
            control_id="control.positive_transaction_volume",
            decision="ignored",
        )


def test_assumption_variants_are_discriminated_typed_and_value_free():
    inflow = factories.governed_literal_ref("literal.inflow")
    outflow = factories.governed_literal_ref("literal.outflow")
    status = factories.governed_literal_ref("literal.active-status")
    column = models.ColumnRef(table_id="table.transactions", column="txn_type")
    assumptions = (
        models.DirectionMappingAssumption(
            kind="direction_mapping",
            column=column,
            inflow_refs=(inflow,),
            outflow_refs=(outflow,),
        ),
        models.StatusMappingAssumption(
            kind="status_mapping",
            column=models.ColumnRef(table_id="table.accounts", column="status"),
            semantic_state_id="state.active",
            literal_refs=(status,),
        ),
        models.GrainMappingAssumption(
            kind="grain_mapping",
            source_table_ids=("table.transactions",),
            grouping_columns=(
                models.ColumnRef(table_id="table.transactions", column="txn_date"),
            ),
            physical_operands=(status,),
        ),
        models.SnapshotAssumption(
            kind="snapshot",
            column=models.ColumnRef(table_id="table.transactions", column="txn_date"),
            interpretation_code="point_in_time",
            physical_operands=(status,),
        ),
    )
    adapter = TypeAdapter(models.Assumption)
    assert [adapter.validate_python(item).kind for item in assumptions] == [
        "direction_mapping",
        "status_mapping",
        "grain_mapping",
        "snapshot",
    ]
    for assumption in assumptions:
        assert "value" not in _all_mapping_keys(assumption)
        with pytest.raises(ValidationError):
            type(assumption).model_validate(
                {**assumption.model_dump(), "resolved_value": "private"}
            )


def test_direction_status_and_grain_assumptions_enforce_nonempty_distinct_refs():
    inflow = factories.governed_literal_ref("literal.inflow")
    outflow = factories.governed_literal_ref("literal.outflow")
    column = models.ColumnRef(table_id="table.transactions", column="txn_type")
    for inflow_refs, outflow_refs in (
        ((), (outflow,)),
        ((inflow,), ()),
        ((inflow,), (inflow,)),
    ):
        with pytest.raises(ValidationError):
            models.DirectionMappingAssumption(
                kind="direction_mapping",
                column=column,
                inflow_refs=inflow_refs,
                outflow_refs=outflow_refs,
            )
    with pytest.raises(ValidationError):
        models.StatusMappingAssumption(
            kind="status_mapping",
            column=models.ColumnRef(table_id="table.accounts", column="status"),
            semantic_state_id="state.active",
            literal_refs=(),
        )
    with pytest.raises(ValidationError):
        models.GrainMappingAssumption(
            kind="grain_mapping",
            source_table_ids=(),
            grouping_columns=(
                models.ColumnRef(table_id="table.transactions", column="txn_date"),
            ),
            physical_operands=(),
        )
    with pytest.raises(ValidationError):
        models.GrainMappingAssumption(
            kind="grain_mapping",
            source_table_ids=("table.transactions",),
            grouping_columns=(),
            physical_operands=(),
        )


def test_requested_disclosure_requires_sources_and_ref_only_limit():
    source = models.ColumnRef(table_id="table.accounts", column="customer_name")
    limit_ref = factories.question_literal_ref(
        "Show five customer names", token="five", data_type="integer"
    )
    disclosure = models.RequestedDisclosure(
        source_columns=(source,), limit_ref=limit_ref
    )
    assert disclosure.limit_ref.kind == "question"
    assert "value" not in disclosure.model_dump(mode="python")
    with pytest.raises(ValidationError):
        models.RequestedDisclosure(source_columns=(), limit_ref=limit_ref)
    with pytest.raises(ValidationError):
        models.RequestedDisclosure.model_validate(
            {
                "source_columns": [source.model_dump()],
                "limit_ref": {"kind": "literal", "value": 5},
            }
        )


def test_grounding_need_union_has_all_strict_typed_variants():
    needs = (
        models.TableGroundingNeed(kind="table", object_id="table.atms"),
        models.ColumnGroundingNeed(
            kind="column",
            ref=models.ColumnRef(table_id="table.accounts", column="city"),
        ),
        models.MetricGroundingNeed(
            kind="metric", metric_id="metric.transaction-volume"
        ),
        models.RelationshipGroundingNeed(
            kind="relationship",
            relationship_id="relationship.transaction_account",
        ),
    )
    adapter = TypeAdapter(models.GroundingNeed)
    assert [adapter.validate_python(item).kind for item in needs] == [
        "table",
        "column",
        "metric",
        "relationship",
    ]
    with pytest.raises(ValidationError):
        models.TableGroundingNeed(
            kind="table", object_id="table.atms", description="Need ATM data"
        )


def test_grounding_usage_set_fields_are_frozensets_and_sorted_on_serialization():
    usage = models.GroundingUsage(
        object_ids=frozenset({"table.transactions", "table.accounts"}),
        relationship_ids=frozenset(
            {"relationship.transaction_account", "relationship.account_branch"}
        ),
        governed_literal_ids=frozenset({"literal.outflow", "literal.inflow"}),
    )
    assert isinstance(usage.object_ids, frozenset)
    dumped = usage.model_dump(mode="json")
    assert dumped["object_ids"] == ["table.accounts", "table.transactions"]
    assert dumped["relationship_ids"] == [
        "relationship.account_branch",
        "relationship.transaction_account",
    ]
    assert dumped["governed_literal_ids"] == [
        "literal.inflow",
        "literal.outflow",
    ]


def test_attempt_stage_allowlist_and_numeric_bounds_are_exact():
    stages = (
        "snapshot",
        "cache",
        "default_ir",
        "clarification",
        "complexity",
        "planned_ir",
        "literal_resolution",
        "compile",
        "ast_check",
        "engine_validation",
        "execution",
        "provider_transport",
    )
    for ordinal, stage in enumerate(stages, 1):
        record = models.AttemptRecord(
            stage=stage,
            ordinal=ordinal,
            outcome="accepted",
            latency_ms=0,
            violation_codes=(),
            generation_route="none" if stage == "snapshot" else "default_ir",
            cache_status="disabled",
        )
        assert record.stage == stage
    with pytest.raises(ValidationError):
        models.AttemptRecord(
            stage="sql_generation",
            ordinal=1,
            outcome="accepted",
            latency_ms=0,
            violation_codes=(),
            generation_route="default_ir",
            cache_status="miss",
        )
    for field, value in (("ordinal", 0), ("latency_ms", -1)):
        payload = {
            "stage": "compile",
            "ordinal": 1,
            "outcome": "accepted",
            "latency_ms": 0,
            "violation_codes": (),
            "generation_route": "default_ir",
            "cache_status": "miss",
        }
        payload[field] = value
        with pytest.raises(ValidationError):
            models.AttemptRecord.model_validate(payload)


def test_check_violation_rejects_prose_and_raw_exception_extras():
    violation = _violation()
    assert factories.codes((violation,)) == ("invalid_ir",)
    for extra in ("message", "raw_exception", "resolved_value", "sql"):
        payload = violation.model_dump(mode="python")
        payload[extra] = "private detail"
        with pytest.raises(ValidationError):
            models.CheckViolation.model_validate(payload)


def test_query_result_row_count_is_consistent_and_immutable():
    result = _result()
    assert result.row_count == len(result.rows)
    with pytest.raises(ValidationError):
        models.QueryResult.model_validate(
            {**result.model_dump(mode="python"), "row_count": 0}
        )
    with pytest.raises(ValidationError):
        models.QueryResult.model_validate(
            {**result.model_dump(mode="python"), "elapsed_ms": -1}
        )


def test_query_result_rejects_nonfinite_json_scalars():
    result = _result().model_dump(mode="python")
    result["rows"] = ((math.inf,),)
    with pytest.raises(ValidationError):
        models.QueryResult.model_validate(result)


def test_output_lineage_and_disclosure_are_typed_frozen_and_extra_forbidden():
    lineage = _ok_payload()["output_lineage"][0]
    disclosure = _ok_payload()["disclosures"][0]
    assert lineage.classification == "confidential"
    assert disclosure.row_limit == 5
    for value in (lineage, disclosure):
        with pytest.raises(ValidationError):
            type(value).model_validate(
                {**value.model_dump(), "description": "reader prose"}
            )
        with pytest.raises(ValidationError):
            value.output_name = "changed"


def test_validated_ir_and_cache_route_plan_hash_matrix():
    simple_ir = factories.minimal_ir()
    complex_ir = _complex_window_ir()
    classes_and_base = (
        (
            models.ValidatedIR,
            {
                "ir_hash": "a" * 64,
                "snapshot_hash": "b" * 64,
                "canonical_question_hash": "c" * 64,
            },
        ),
        (models.CachedGeneration, {"payload_sha256": "d" * 64}),
    )
    for model_class, base in classes_and_base:
        default = model_class(
            ir=simple_ir,
            generation_route="default_ir",
            accepted_complex_plan_hash=None,
            **base,
        )
        assert default.accepted_complex_plan_hash is None
        planned = model_class(
            ir=complex_ir,
            generation_route="planned_ir",
            accepted_complex_plan_hash="e" * 64,
            **base,
        )
        assert planned.accepted_complex_plan_hash == "e" * 64

        with pytest.raises(ValidationError):
            model_class(
                ir=simple_ir,
                generation_route="default_ir",
                accepted_complex_plan_hash="e" * 64,
                **base,
            )
        with pytest.raises(ValidationError):
            model_class(
                ir=complex_ir,
                generation_route="default_ir",
                accepted_complex_plan_hash=None,
                **base,
            )
        with pytest.raises(ValidationError):
            model_class(
                ir=complex_ir,
                generation_route="planned_ir",
                accepted_complex_plan_hash=None,
                **base,
            )


def test_default_route_rejects_each_complex_node_kind():
    for ir in (_complex_window_ir(), _complex_set_ir()):
        with pytest.raises(ValidationError):
            models.ValidatedIR(
                ir=ir,
                ir_hash="a" * 64,
                snapshot_hash="b" * 64,
                canonical_question_hash="c" * 64,
                generation_route="default_ir",
                accepted_complex_plan_hash=None,
            )


def test_complex_plan_versions_operator_ids_and_extras_are_strict():
    plan = factories.complex_window_plan()
    assert plan.plan_version == "008.complex-plan.v1"
    assert plan.operator_ids == ("window.period_over_period.v1",)
    for field, value in (
        ("plan_version", "008.complex-plan.v2"),
        ("operator_ids", ("window.arbitrary.v1",)),
        ("reasoning", "Use a window"),
    ):
        payload = plan.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.ComplexQueryPlan.model_validate(payload)


def test_accepted_complex_route_cannot_be_caller_constructed():
    snapshot = factories.valid_snapshot()
    plan = factories.complex_window_plan(snapshot)
    assert dataclasses.is_dataclass(models.AcceptedComplexRoute)
    assert models.AcceptedComplexRoute.__dataclass_params__.frozen is True
    assert "__slots__" in vars(models.AcceptedComplexRoute)
    assert not issubclass(models.AcceptedComplexRoute, BaseModel)
    assert not hasattr(models.AcceptedComplexRoute, "model_validate")
    assert not hasattr(models.AcceptedComplexRoute, "model_dump")

    with pytest.raises(TypeError):
        models.AcceptedComplexRoute()
    with pytest.raises(TypeError):
        models.AcceptedComplexRoute(
            snapshot_hash=snapshot.snapshot_hash,
            plan_hash=models.complex_plan_sha256(plan),
            plan=plan,
            _router_token=object(),
        )


def test_contract_owns_the_plan_hash_helper_and_derives_it_from_the_plan():
    snapshot = factories.valid_snapshot()
    plan = factories.complex_window_plan(snapshot)
    plan_hash = models.complex_plan_sha256(plan)

    # The helper is the single source of plan-hash authority: deterministic for
    # equal plans, and distinguishing for different plans. The exact byte form is
    # the contract's business, not the test's.
    assert plan_hash == models.complex_plan_sha256(
        factories.complex_window_plan(snapshot)
    )
    assert _adapter("Sha256").validate_python(plan_hash) == plan_hash

    other_plan_payload = plan.model_dump(mode="python")
    other_step = dict(other_plan_payload["steps"][0])
    other_step["step_id"] = "period_growth_alt"
    other_plan_payload["steps"] = (other_step,)
    other_plan = models.ComplexQueryPlan.model_validate(other_plan_payload)
    assert models.complex_plan_sha256(other_plan) != plan_hash


def test_private_route_creator_mints_exact_capability_and_valid_planned_request():
    snapshot = factories.valid_snapshot()
    plan = factories.complex_window_plan(snapshot)
    plan_hash = models.complex_plan_sha256(plan)
    accepted = models._create_accepted_complex_route(
        snapshot_hash=snapshot.snapshot_hash,
        plan_hash=plan_hash,
        plan=plan,
    )

    assert {
        field.name
        for field in dataclasses.fields(accepted)
        if not field.name.startswith("_")
    } == {"generation_route", "snapshot_hash", "plan_hash", "plan"}
    assert accepted.generation_route == "planned_ir"
    assert accepted.snapshot_hash == snapshot.snapshot_hash
    assert accepted.plan_hash == plan_hash
    assert accepted.plan == plan

    request = models.GuardedGenerationRequest(
        mode="planned_ir",
        canonical_question=factories.canonical_question(),
        snapshot=snapshot,
        accepted_complex_route=accepted,
        prior_violations=(),
    )
    assert request.accepted_complex_route is accepted
    with pytest.raises(TypeError):
        dataclasses.replace(accepted, plan_hash="f" * 64)


def test_planned_request_rejects_wrong_snapshot_and_plan_hash_bindings():
    snapshot = factories.valid_snapshot()
    plan = factories.complex_window_plan(snapshot)
    plan_hash = models.complex_plan_sha256(plan)
    accepted = models._create_accepted_complex_route(
        snapshot_hash=snapshot.snapshot_hash,
        plan_hash=plan_hash,
        plan=plan,
    )

    # A correctly derived hash is accepted by a guarded planned request.
    accepted_request = models.GuardedGenerationRequest(
        mode="planned_ir",
        canonical_question=factories.canonical_question(),
        snapshot=snapshot,
        accepted_complex_route=accepted,
        prior_violations=(),
    )
    assert accepted_request.accepted_complex_route.plan_hash == plan_hash

    other_snapshot_payload = snapshot.model_dump(mode="python")
    other_snapshot_payload["snapshot_hash"] = "e" * 64
    other_snapshot = models.GroundingSnapshot.model_validate(other_snapshot_payload)
    with pytest.raises((TypeError, ValueError)):
        models.GuardedGenerationRequest(
            mode="planned_ir",
            canonical_question=factories.canonical_question(),
            snapshot=other_snapshot,
            accepted_complex_route=accepted,
            prior_violations=(),
        )

    mismatched_hash = "f" * 64
    assert mismatched_hash != plan_hash
    wrong_hash_route = _mint_route_or_reject(
        snapshot.snapshot_hash, mismatched_hash, plan
    )
    if wrong_hash_route is not None:
        with pytest.raises((TypeError, ValueError)):
            models.GuardedGenerationRequest(
                mode="planned_ir",
                canonical_question=factories.canonical_question(),
                snapshot=snapshot,
                accepted_complex_route=wrong_hash_route,
                prior_violations=(),
            )


def test_guarded_generation_request_is_local_frozen_slotted_and_non_wire():
    request = models.GuardedGenerationRequest(
        mode="default_ir",
        canonical_question=factories.canonical_question(),
        snapshot=factories.valid_snapshot(),
        accepted_complex_route=None,
        prior_violations=(),
    )
    assert dataclasses.is_dataclass(request)
    assert request.__dataclass_params__.frozen is True
    assert "__slots__" in vars(type(request))
    assert not hasattr(request, "__dict__")
    assert not isinstance(request, BaseModel)
    for name in ("model_validate", "model_dump", "model_dump_json", "dict", "json"):
        assert not hasattr(request, name)
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        request.mode = "planned_ir"


def test_guarded_request_mode_capability_and_probe_matrix():
    snapshot = factories.valid_snapshot()
    models.GuardedGenerationRequest(
        mode="default_ir",
        canonical_question=factories.canonical_question(),
        snapshot=snapshot,
        accepted_complex_route=None,
        prior_violations=(),
    )
    with pytest.raises((TypeError, ValueError)):
        models.GuardedGenerationRequest(
            mode="default_ir",
            canonical_question=factories.canonical_question(),
            snapshot=snapshot,
            accepted_complex_route=object(),
            prior_violations=(),
        )
    with pytest.raises((TypeError, ValueError)):
        models.GuardedGenerationRequest(
            mode="planned_ir",
            canonical_question=factories.canonical_question(),
            snapshot=snapshot,
            accepted_complex_route=None,
            prior_violations=(),
        )

    probe = models.GuardedGenerationRequest(
        mode="provider_probe",
        canonical_question="",
        snapshot=_empty_probe_snapshot(),
        accepted_complex_route=None,
        prior_violations=(),
    )
    assert probe.mode == "provider_probe"
    with pytest.raises((TypeError, ValueError)):
        models.GuardedGenerationRequest(
            mode="provider_probe",
            canonical_question="",
            snapshot=snapshot,
            accepted_complex_route=None,
            prior_violations=(),
        )
    with pytest.raises((TypeError, ValueError)):
        models.GuardedGenerationRequest(
            mode="provider_probe",
            canonical_question="",
            snapshot=_empty_probe_snapshot(),
            accepted_complex_route=None,
            prior_violations=(_violation(),),
        )


def test_clarification_contract_has_only_spans_and_typed_candidates():
    request = factories.valid_clarification_request()
    schema = json.dumps(request.model_json_schema(), sort_keys=True)
    for forbidden in (
        '"message"',
        '"reasoning"',
        '"description"',
        '"raw_value"',
        '"value"',
    ):
        assert forbidden not in schema
    payload = request.model_dump(mode="python")
    payload["ambiguities"][0]["message"] = "Which one?"
    with pytest.raises(ValidationError):
        models.ClarificationRequest.model_validate(payload)


def test_ambiguity_candidate_variants_have_valid_controls():
    candidates = (
        models.ObjectAmbiguityCandidate(kind="object", object_id="table.accounts"),
        models.RelationshipAmbiguityCandidate(
            kind="relationship",
            relationship_id="relationship.transaction_account",
        ),
        models.GovernedLiteralAmbiguityCandidate(
            kind="governed_literal", literal_id="literal.active-status"
        ),
        models.GrainAmbiguityCandidate(
            kind="grain",
            grain="month",
            grouping_columns=(
                models.ColumnRef(table_id="table.transactions", column="txn_date"),
            ),
        ),
        models.OperatorAmbiguityCandidate(
            kind="operator", operator_id="window.period_over_period.v1"
        ),
    )
    adapter = TypeAdapter(models.AmbiguityCandidate)
    assert [adapter.validate_python(item).kind for item in candidates] == [
        "object",
        "relationship",
        "governed_literal",
        "grain",
        "operator",
    ]
    with pytest.raises(ValidationError):
        models.GrainAmbiguityCandidate(kind="grain", grain="month", grouping_columns=())


def test_ambiguity_requires_two_distinct_homogeneous_candidates_and_valid_span():
    first = models.ObjectAmbiguityCandidate(kind="object", object_id="table.accounts")
    second = models.ObjectAmbiguityCandidate(
        kind="object", object_id="table.transactions"
    )
    valid = models.Ambiguity(
        ambiguity_id="target_object", start=5, end=13, candidates=(first, second)
    )
    assert len(valid.candidates) == 2
    invalid_candidates = (
        (first,),
        (first, first),
        (
            first,
            models.RelationshipAmbiguityCandidate(
                kind="relationship",
                relationship_id="relationship.transaction_account",
            ),
        ),
    )
    for candidates in invalid_candidates:
        with pytest.raises(ValidationError):
            models.Ambiguity(
                ambiguity_id="target_object",
                start=5,
                end=13,
                candidates=candidates,
            )
    for start, end in ((-1, 2), (5, 5), (6, 5)):
        with pytest.raises(ValidationError):
            models.Ambiguity(
                ambiguity_id="target_object",
                start=start,
                end=end,
                candidates=(first, second),
            )


def test_clarification_requires_at_least_one_ambiguity():
    with pytest.raises(ValidationError):
        models.ClarificationRequest(outcome="clarification_request", ambiguities=())


def test_local_literal_clarification_is_typed_and_value_free():
    base = _model_payload(factories.valid_response_base())
    response = models.RefusedResponse.model_validate(
        {
            **base,
            "status": "refused",
            "reason": "clarification_required",
            "generation_route": "default_ir",
            "ambiguities": (),
            "literal_needs": (
                {
                    "kind": "literal_need",
                    "issue": "unparseable_question_literal",
                    "expected_type": "integer",
                    "target_column": None,
                    "literal_ref": {
                        "kind": "question",
                        "start": 10,
                        "end": 14,
                        "data_type": "integer",
                    },
                },
            ),
        }
    )
    serialized = response.model_dump_json()
    assert '"literal_needs"' in serialized
    assert '"value"' not in serialized


def test_literal_clarification_issue_allowlist_is_exact():
    issues = {
        "missing_literal",
        "invalid_question_span",
        "unparseable_question_literal",
        "ungrounded_governed_literal",
        "literal_type_mismatch",
        "invented_literal_reference",
    }
    for issue in issues:
        need = models.LiteralClarificationNeed(
            kind="literal_need", issue=issue, expected_type="integer"
        )
        assert need.issue == issue
    with pytest.raises(ValidationError):
        models.LiteralClarificationNeed(
            kind="literal_need", issue="unknown", expected_type="integer"
        )
    with pytest.raises(ValidationError):
        models.LiteralClarificationNeed(
            kind="literal_need",
            issue="missing_literal",
            expected_type="integer",
            raw_value="five",
        )


def test_grounding_refusal_requires_at_least_one_typed_need():
    refusal = models.GroundingRefusal(
        outcome="grounding_refusal", unmet_needs=(_table_need(),)
    )
    assert refusal.unmet_needs[0].kind == "table"
    with pytest.raises(ValidationError):
        models.GroundingRefusal(outcome="grounding_refusal", unmet_needs=())


def test_generation_outcome_union_accepts_each_exact_outcome():
    outcomes = (
        factories.minimal_ir(),
        factories.complex_window_plan(),
        models.GroundingRefusal(
            outcome="grounding_refusal", unmet_needs=(_table_need(),)
        ),
        factories.valid_clarification_request(),
    )
    adapter = TypeAdapter(models.IRGenerationOutcome)
    assert [adapter.validate_python(item).outcome for item in outcomes] == [
        "ir",
        "complex_plan",
        "grounding_refusal",
        "clarification_request",
    ]
    with pytest.raises(ValidationError):
        adapter.validate_python({"outcome": "sql", "sql": "SELECT 1"})


def test_bound_parameter_value_never_serializes():
    parameter = models.BoundParameter(position=1, data_type="string", value="private")
    assert parameter.value == "private"
    assert "private" not in parameter.model_dump_json()
    assert "value" not in parameter.model_dump()


def test_bound_parameter_value_is_excluded_from_nested_serialization():
    compiled = factories.compiled_query()
    assert compiled.parameters[0].value == "London"
    dumped = compiled.model_dump(mode="python")
    serialized = compiled.model_dump_json()
    assert all("value" not in parameter for parameter in dumped["parameters"])
    assert "London" not in serialized
    assert '"value"' not in serialized


def test_bound_parameter_position_and_json_scalar_are_bounded():
    with pytest.raises(ValidationError):
        models.BoundParameter(position=0, data_type="string", value="private")
    with pytest.raises(ValidationError):
        models.BoundParameter(position=1, data_type="decimal", value=math.inf)


def test_sql_artifact_count_and_types_must_agree():
    artifact = factories.sql_artifact()
    assert artifact.parameter_count == len(artifact.parameter_types) == 2
    for field, value in (
        ("parameter_count", 1),
        ("parameter_types", ("string",)),
        ("parameter_count", -1),
    ):
        payload = artifact.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.SQLArtifact.model_validate(payload)


def test_public_sql_artifact_has_no_parameters_or_values():
    artifact = factories.sql_artifact()
    assert set(artifact.model_fields) == {
        "sql",
        "sql_sha256",
        "parameter_count",
        "parameter_types",
        "ir_hash",
        "compiler_version",
        "dialect",
    }
    with pytest.raises(ValidationError):
        models.SQLArtifact.model_validate(
            {
                **artifact.model_dump(),
                "parameters": [
                    {"position": 1, "data_type": "string", "value": "London"}
                ],
            }
        )


def test_budget_contract_defaults_are_exact():
    limits = models.BudgetLimits.defaults()
    assert limits.initial_semantic_call_capacity == 1
    assert limits.planned_semantic_call_capacity == 2
    assert limits.max_transport_attempts_per_semantic_call == 2
    assert limits.provider_timeout_ms_per_attempt == 20_000
    assert limits.max_input_tokens == 32_000
    assert limits.max_output_tokens == 8_000
    assert limits.max_cost_usd == Decimal("0.50")
    assert limits.end_to_end_deadline_ms == 120_000


def test_budget_limits_enforce_finite_positive_and_transport_bounds():
    valid = models.BudgetLimits.defaults().model_dump(mode="python")
    invalid = (
        ("initial_semantic_call_capacity", 2),
        ("planned_semantic_call_capacity", 1),
        ("max_transport_attempts_per_semantic_call", 0),
        ("max_transport_attempts_per_semantic_call", 3),
        ("provider_timeout_ms_per_attempt", 0),
        ("max_input_tokens", 0),
        ("max_output_tokens", 0),
        ("max_cost_usd", Decimal(0)),
        ("max_cost_usd", Decimal("Infinity")),
        ("end_to_end_deadline_ms", 0),
    )
    for field, value in invalid:
        payload = {**valid, field: value}
        with pytest.raises(ValidationError):
            models.BudgetLimits.model_validate(payload)


def test_budget_usage_fields_are_nonnegative_and_capacity_is_typed():
    usage = models.BudgetUsage(
        semantic_call_capacity=1,
        planned_ir_authorized=False,
        semantic_calls=0,
        transport_attempts=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=Decimal(0),
        elapsed_ms=0,
    )
    assert usage.semantic_call_capacity == 1
    for field in (
        "semantic_calls",
        "transport_attempts",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "elapsed_ms",
    ):
        payload = usage.model_dump(mode="python")
        payload[field] = Decimal("-0.01") if field == "cost_usd" else -1
        with pytest.raises(ValidationError):
            models.BudgetUsage.model_validate(payload)
    with pytest.raises(ValidationError):
        models.BudgetUsage.model_validate(
            {**usage.model_dump(), "semantic_call_capacity": 3}
        )


def test_production_request_is_strict_and_has_only_trusted_scope_input():
    request = factories.valid_request()
    assert request.dialect == "duckdb"
    assert request.max_rows == 1000
    assert set(request.model_fields) == {
        "question",
        "authorization_scope",
        "dialect",
        "max_rows",
    }

    advisory = models.GroundingResponse(
        semantic_version="legacy.v1",
        retrieval_mode="lexical_graph",
        question="Show accounts",
        concepts=[],
        tables=[],
        columns=[],
        joins=[],
        grain=[],
        metrics=[],
        filters=[],
        warnings=[],
        classifications=[],
        provenance=[],
        ranking_evidence=[],
    )
    forbidden_inputs = {
        "grounding": {},
        "grounding_response": advisory,
        "prompt_envelope": {"prompt": "arbitrary"},
        "snapshot": factories.valid_snapshot(),
        "authorization_material": {"allow": "everything"},
    }
    for field, value in forbidden_inputs.items():
        payload = request.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.SQLGenerationRequest.model_validate(payload)


def test_ok_requires_concrete_generation_route_and_independent_cache_status():
    payload = _ok_payload()
    payload["generation_route"] = "none"
    payload["cache_status"] = "hit"
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_cache_is_never_a_generation_route():
    payload = _ok_payload()
    payload["generation_route"] = "cache"
    payload["cache_status"] = "hit"
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


@pytest.mark.parametrize("cache_status", ["disabled", "miss", "hit"])
def test_ok_accepts_each_cache_status_without_changing_generation_route(
    cache_status,
):
    payload = _ok_payload()
    payload["cache_status"] = cache_status
    response = TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    assert response.generation_route == "default_ir"
    assert response.cache_status == cache_status


def test_ok_response_enforces_ir_attempt_and_budget_route_coherence():
    complex_ir = _complex_window_ir()
    planned_payload = _ok_payload()
    planned_payload["ir"] = complex_ir
    planned_payload["sql_artifact"] = factories.sql_artifact(
        factories.compiled_query(complex_ir)
    )
    _apply_planned_generation_evidence(planned_payload)
    response = TypeAdapter(models.SQLGenerationResponse).validate_python(
        planned_payload
    )
    assert response.generation_route == "planned_ir"
    assert response.budget_usage.planned_ir_authorized is True
    assert response.attempt_records[-1].stage == "planned_ir"

    default_with_complex_ir = _ok_payload()
    default_with_complex_ir["ir"] = complex_ir
    default_with_complex_ir["sql_artifact"] = factories.sql_artifact(
        factories.compiled_query(complex_ir)
    )
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(
            default_with_complex_ir
        )

    planned_with_default_evidence = _ok_payload()
    planned_with_default_evidence.update(
        generation_route="planned_ir",
        ir=complex_ir,
        sql_artifact=factories.sql_artifact(factories.compiled_query(complex_ir)),
    )
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(
            planned_with_default_evidence
        )

    default_with_planned_evidence = _apply_planned_generation_evidence(_ok_payload())
    default_with_planned_evidence["generation_route"] = "default_ir"
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(
            default_with_planned_evidence
        )

    # Vary attempt records independently of budget usage so attempt coherence is
    # pinned on its own rather than riding along with the budget mismatch above.
    default_base = factories.valid_response_base()
    mixed_evidence = {
        "planned_attempts_with_default_budget": {
            "attempt_records": _planned_attempt_records(),
            "budget_usage": default_base.budget_usage,
        },
        "default_attempts_with_planned_budget": {
            "attempt_records": default_base.attempt_records,
            "budget_usage": _planned_budget_usage(),
        },
    }
    for label, evidence in mixed_evidence.items():
        payload = _ok_payload()
        payload.update(
            generation_route="planned_ir",
            ir=complex_ir,
            sql_artifact=factories.sql_artifact(factories.compiled_query(complex_ir)),
            **evidence,
        )
        try:
            TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
        except ValidationError:
            continue
        pytest.fail(f"planned OK response accepted incoherent evidence: {label}")


def test_ok_requires_snapshot_ir_artifact_result_lineage_and_disclosures():
    required = (
        "snapshot_hash",
        "ir",
        "sql_artifact",
        "result",
        "output_lineage",
        "disclosures",
    )
    for field in required:
        missing = _ok_payload()
        missing.pop(field)
        with pytest.raises(ValidationError):
            TypeAdapter(models.SQLGenerationResponse).validate_python(missing)

        explicit_none = _ok_payload()
        explicit_none[field] = None
        with pytest.raises(ValidationError):
            TypeAdapter(models.SQLGenerationResponse).validate_python(explicit_none)


def test_ok_public_serialization_contains_artifact_not_bound_parameters():
    response = TypeAdapter(models.SQLGenerationResponse).validate_python(_ok_payload())
    dumped = response.model_dump(mode="python")
    assert "sql_artifact" in dumped
    assert "parameters" not in dumped["sql_artifact"]
    assert "London" not in response.model_dump_json()


def test_check_failed_requires_violation_is_nonexecutable_and_never_has_result():
    response = TypeAdapter(models.SQLGenerationResponse).validate_python(
        _check_failed_payload()
    )
    assert response.status == "check_failed"
    assert response.executable is False
    assert not hasattr(response, "result")

    payload = _check_failed_payload()
    payload["violations"] = ()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    payload = _check_failed_payload()
    payload["executable"] = True
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    payload = _check_failed_payload()
    payload["result"] = _result()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_check_failed_allows_optional_value_free_ir_and_artifact():
    payload = _check_failed_payload()
    payload["ir"] = None
    payload["sql_artifact"] = None
    response = TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    assert response.ir is None
    assert response.sql_artifact is None


def test_route_none_requires_genuinely_pre_generation_evidence():
    payload = _pre_generation_check_failed_payload()
    response = TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    assert response.generation_route == "none"
    assert response.snapshot_hash is None
    assert response.ir is None
    assert response.sql_artifact is None
    assert response.attempt_records == ()
    assert response.grounding_usage.object_ids == frozenset()
    assert response.grounding_usage.relationship_ids == frozenset()
    assert response.grounding_usage.governed_literal_ids == frozenset()
    assert response.budget_usage.semantic_calls == 0
    assert response.budget_usage.transport_attempts == 0
    assert response.budget_usage.planned_ir_authorized is False

    default_attempt = factories.valid_response_base().attempt_records
    semantic_call_usage = factories.valid_response_base().budget_usage
    invalid_mutations = (
        ("snapshot_hash", factories.valid_snapshot().snapshot_hash),
        ("attempt_records", default_attempt),
        ("attempt_records", _planned_attempt_records()),
        (
            "attempt_records",
            (
                models.AttemptRecord(
                    stage="provider_transport",
                    ordinal=1,
                    outcome="rejected",
                    latency_ms=1,
                    violation_codes=("provider_transport_failure",),
                    generation_route="default_ir",
                    cache_status="miss",
                ),
            ),
        ),
        ("budget_usage", semantic_call_usage),
        ("budget_usage", _planned_budget_usage()),
        ("grounding_usage", factories.valid_response_base().grounding_usage),
        ("ir", factories.minimal_ir()),
        ("sql_artifact", factories.sql_artifact()),
    )
    for field, value in invalid_mutations:
        invalid = _pre_generation_check_failed_payload()
        invalid[field] = value
        with pytest.raises(ValidationError):
            TypeAdapter(models.SQLGenerationResponse).validate_python(invalid)


def test_route_none_rejects_every_post_generation_refusal():
    for reason in (
        "missing_grounding",
        "policy_disallowed",
        "clarification_required",
        "unsupported_complexity",
    ):
        payload = _refused_payload(reason)
        payload.update(_pre_generation_response_base_payload())
        with pytest.raises(ValidationError):
            TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_refused_response_rejects_sql_artifact():
    payload = _refused_payload("missing_grounding")
    payload["sql_artifact"] = factories.sql_artifact()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_refused_response_rejects_result_ir_and_bound_parameters():
    for field, value in (
        ("result", _result()),
        ("ir", factories.minimal_ir()),
        ("compiled_query", factories.compiled_query()),
        ("parameters", ({"position": 1, "value": "private"},)),
    ):
        payload = _refused_payload("missing_grounding")
        payload[field] = value
        with pytest.raises(ValidationError):
            TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_missing_grounding_requires_typed_unmet_needs_only():
    valid = TypeAdapter(models.SQLGenerationResponse).validate_python(
        _refused_payload("missing_grounding")
    )
    assert valid.unmet_needs[0].kind == "table"
    payload = _refused_payload("missing_grounding")
    payload["unmet_needs"] = ()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    payload = _refused_payload("missing_grounding")
    payload["policy_ids"] = ("policy.sensitive-output",)
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_policy_refusal_requires_policy_ids_and_rejects_unrelated_populations():
    valid = TypeAdapter(models.SQLGenerationResponse).validate_python(
        _refused_payload("policy_disallowed")
    )
    assert valid.policy_ids == ("policy.sensitive-output",)
    assert valid.generation_route == "default_ir"
    assert valid.snapshot_hash is not None
    assert valid.budget_usage.semantic_calls == 1
    assert valid.attempt_records[-1].stage == "default_ir"
    payload = _refused_payload("policy_disallowed")
    payload["policy_ids"] = ()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    payload = _refused_payload("policy_disallowed")
    payload["literal_needs"] = (
        models.LiteralClarificationNeed(
            kind="literal_need", issue="missing_literal", expected_type="string"
        ),
    )
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_unsupported_complexity_requires_allowlisted_operator_ids():
    valid = TypeAdapter(models.SQLGenerationResponse).validate_python(
        _refused_payload("unsupported_complexity")
    )
    assert valid.unsupported_operator_ids == ("window.period_over_period.v1",)
    payload = _refused_payload("unsupported_complexity")
    payload["unsupported_operator_ids"] = ()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)
    payload = _refused_payload("unsupported_complexity")
    payload["unsupported_operator_ids"] = ("window.arbitrary.v1",)
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_clarification_requires_exactly_one_nonempty_source():
    valid = TypeAdapter(models.SQLGenerationResponse).validate_python(
        _refused_payload("clarification_required")
    )
    assert valid.ambiguities
    assert not valid.literal_needs

    payload = _refused_payload("clarification_required")
    payload["ambiguities"] = ()
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)

    payload = _refused_payload("clarification_required")
    payload["literal_needs"] = (
        models.LiteralClarificationNeed(
            kind="literal_need", issue="missing_literal", expected_type="string"
        ),
    )
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_model_clarification_records_default_route_only():
    baseline = TypeAdapter(models.SQLGenerationResponse).validate_python(
        _refused_payload("clarification_required")
    )
    assert baseline.ambiguities
    assert baseline.generation_route == "default_ir"

    for route in ("planned_ir", "none"):
        payload = _refused_payload("clarification_required")
        payload["generation_route"] = route
        with pytest.raises(ValidationError):
            TypeAdapter(models.SQLGenerationResponse).validate_python(payload)

    # Coherent planned evidence must still be rejected while `ambiguities` is
    # populated: only `default_ir` may carry a model-proposed ambiguity, and this
    # case cannot be absorbed by route/attempt/budget coherence validators.
    coherent_planned = _apply_planned_generation_evidence(
        _refused_payload("clarification_required")
    )
    assert coherent_planned["ambiguities"]
    assert coherent_planned["generation_route"] == "planned_ir"
    assert coherent_planned["attempt_records"][-1].stage == "planned_ir"
    assert coherent_planned["budget_usage"].planned_ir_authorized is True
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(coherent_planned)


def test_local_literal_clarification_preserves_default_or_planned_route():
    need = models.LiteralClarificationNeed(
        kind="literal_need",
        issue="missing_literal",
        expected_type="integer",
        target_column=None,
        literal_ref=None,
    )

    default_payload = _refused_payload("clarification_required")
    default_payload["ambiguities"] = ()
    default_payload["literal_needs"] = (need,)
    default_response = TypeAdapter(models.SQLGenerationResponse).validate_python(
        default_payload
    )
    assert default_response.generation_route == "default_ir"
    assert default_response.budget_usage.semantic_call_capacity == 1
    assert default_response.budget_usage.planned_ir_authorized is False
    assert default_response.attempt_records[-1].stage == "default_ir"

    planned_payload = _refused_payload("clarification_required")
    planned_payload["ambiguities"] = ()
    planned_payload["literal_needs"] = (need,)
    _apply_planned_generation_evidence(planned_payload)
    planned_response = TypeAdapter(models.SQLGenerationResponse).validate_python(
        planned_payload
    )
    assert planned_response.generation_route == "planned_ir"
    assert planned_response.budget_usage.semantic_call_capacity == 2
    assert planned_response.budget_usage.planned_ir_authorized is True
    assert planned_response.budget_usage.semantic_calls == 2
    assert planned_response.attempt_records[-1].stage == "planned_ir"

    invalid = _refused_payload("clarification_required")
    invalid["ambiguities"] = ()
    invalid["literal_needs"] = (need,)
    invalid.update(
        generation_route="none",
        snapshot_hash=None,
        attempt_records=(),
        budget_usage=_pre_generation_response_base_payload()["budget_usage"],
    )
    with pytest.raises(ValidationError):
        TypeAdapter(models.SQLGenerationResponse).validate_python(invalid)


def test_unrelated_refusal_reasons_reject_ambiguity_and_literal_populations():
    ambiguity = factories.valid_clarification_request().ambiguities[0]
    literal_need = models.LiteralClarificationNeed(
        kind="literal_need", issue="missing_literal", expected_type="string"
    )
    for reason in (
        "missing_grounding",
        "policy_disallowed",
        "unsupported_complexity",
    ):
        for field, value in (
            ("ambiguities", (ambiguity,)),
            ("literal_needs", (literal_need,)),
        ):
            payload = _refused_payload(reason)
            payload[field] = value
            with pytest.raises(ValidationError):
                TypeAdapter(models.SQLGenerationResponse).validate_python(payload)


def test_refusal_reason_population_matrix_is_exact():
    snapshot = factories.valid_snapshot()
    populations = {
        "unmet_needs": (_table_need(),),
        "policy_ids": ("policy.sensitive-output",),
        "unsupported_operator_ids": ("window.period_over_period.v1",),
        "ambiguities": (
            factories.valid_clarification_request(snapshot=snapshot).ambiguities[0],
        ),
        "literal_needs": (
            models.LiteralClarificationNeed(
                kind="literal_need",
                issue="missing_literal",
                expected_type="string",
            ),
        ),
    }
    valid_population_sets = {
        "missing_grounding": {frozenset({"unmet_needs"})},
        "policy_disallowed": {frozenset({"policy_ids"})},
        "unsupported_complexity": {frozenset({"unsupported_operator_ids"})},
        "clarification_required": {
            frozenset({"ambiguities"}),
            frozenset({"literal_needs"}),
        },
    }
    population_names = tuple(populations)
    adapter = TypeAdapter(models.SQLGenerationResponse)

    for reason, allowed_sets in valid_population_sets.items():
        for mask in range(1 << len(population_names)):
            selected = frozenset(
                name
                for index, name in enumerate(population_names)
                if mask & (1 << index)
            )
            payload = _refused_payload(reason, snapshot=snapshot)
            for name in population_names:
                payload[name] = populations[name] if name in selected else ()
            if selected in allowed_sets:
                response = adapter.validate_python(payload)
                assert response.reason == reason
            else:
                with pytest.raises(ValidationError):
                    adapter.validate_python(payload)


def test_response_union_is_status_discriminated_and_rejects_unknown_status():
    adapter = TypeAdapter(models.SQLGenerationResponse)
    assert adapter.validate_python(_ok_payload()).status == "ok"
    assert adapter.validate_python(_check_failed_payload()).status == "check_failed"
    assert (
        adapter.validate_python(_refused_payload("missing_grounding")).status
        == "refused"
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({**_ok_payload(), "status": "partial"})


def test_response_base_carries_all_required_versions_identities_and_evidence():
    base = factories.valid_response_base()
    assert set(base.model_fields) == {
        "contract_version",
        "semantic_version",
        "policy_version",
        "canonicalization_version",
        "literal_registry_version",
        "ir_contract_version",
        "type_registry_version",
        "prompt_version",
        "router_version",
        "compiler_version",
        "checker_version",
        "dialect",
        "provider",
        "model",
        "model_revision",
        "canonical_question_hash",
        "authorization_scope_hash",
        "snapshot_hash",
        "generation_route",
        "cache_status",
        "grounding_usage",
        "assumptions",
        "attempt_records",
        "budget_usage",
        "violations",
    }


def test_response_base_exact_versions_routes_and_cache_values():
    base = factories.valid_response_base()
    mutations = {
        "contract_version": "008.v2",
        "canonicalization_version": "008.question.v2",
        "literal_registry_version": "008.literal-span.v2",
        "ir_contract_version": "008.ir.v2",
        "type_registry_version": "008.types.v2",
        "dialect": "postgres",
        "generation_route": "cache",
        "cache_status": "warm",
    }
    for field, value in mutations.items():
        payload = base.model_dump(mode="python")
        payload[field] = value
        with pytest.raises(ValidationError):
            models.ResponseBase.model_validate(payload)


def test_factory_helpers_all_return_normally_validated_contracts():
    snapshot = factories.valid_snapshot()
    compiled = factories.compiled_query()
    factory_results = {
        "valid_scope": (factories.valid_scope(), models.AuthorizationScope),
        "valid_snapshot": (snapshot, models.GroundingSnapshot),
        "question_literal_ref": (
            factories.question_literal_ref(factories.canonical_question()),
            models.QuestionLiteralRef,
        ),
        "governed_literal_ref": (
            factories.governed_literal_ref("literal.active-status"),
            models.GovernedLiteralRef,
        ),
        "minimal_ir": (factories.minimal_ir(snapshot), models.RelationalQueryIR),
        "branch_volume_ir": (
            factories.branch_volume_ir(snapshot),
            models.RelationalQueryIR,
        ),
        "relative_growth_ir": (
            factories.relative_growth_ir(snapshot),
            models.RelationalQueryIR,
        ),
        "complex_window_plan": (
            factories.complex_window_plan(snapshot),
            models.ComplexQueryPlan,
        ),
        "valid_clarification_request": (
            factories.valid_clarification_request(snapshot=snapshot),
            models.ClarificationRequest,
        ),
        "sensitive_ir": (
            factories.sensitive_ir(snapshot),
            models.RelationalQueryIR,
        ),
        "compiled_query": (compiled, models.CompiledQuery),
        "sql_artifact": (factories.sql_artifact(compiled), models.SQLArtifact),
        "valid_request": (factories.valid_request(), models.SQLGenerationRequest),
        "valid_response_base": (
            factories.valid_response_base(snapshot),
            models.ResponseBase,
        ),
    }
    for name, (value, expected_type) in factory_results.items():
        assert isinstance(value, expected_type), name
        if name != "compiled_query":
            assert isinstance(
                expected_type.model_validate(value.model_dump()), expected_type
            )

    assert [parameter.value for parameter in compiled.parameters] == ["London", 5]


def test_factory_source_uses_no_construct_bypass_or_route_authority():
    source = inspect.getsource(factories)
    assert "model_construct" not in source
    # Route authority may only be obtained by running the real router, never by
    # touching the module-private token or creation helper in `models`.
    assert "_create_accepted_complex_route" not in source
    assert "_ACCEPTED_COMPLEX_ROUTE_TOKEN" not in source
    if "accepted_complex_route" in factories.__dict__:
        assert "ComplexityRouter" in source


def test_factory_spans_are_exact_unicode_codepoints_and_unique():
    question = factories.canonical_question("  Cafe\u0301\tLondon  ")
    assert question == "Café London"
    ref = factories.question_literal_ref(question, token="London")
    assert question[ref.start : ref.end] == "London"
    assert ref.start == len("Café ")
    with pytest.raises(ValueError, match="exactly once"):
        factories.question_literal_ref(question, token="Paris")
    with pytest.raises(ValueError, match="exactly once"):
        factories.question_literal_ref("London and London", token="London")
    with pytest.raises(ValueError, match="exactly once"):
        factories.question_literal_ref("banana", token="ana")


def test_factory_warning_decisions_reuse_snapshot_hashes_not_copied_prose():
    snapshot = factories.valid_snapshot()
    warning_hashes = {
        warning.warning_hash for item in snapshot.objects for warning in item.warnings
    }
    ir = factories.branch_volume_ir(snapshot)
    assert ir.warning_decisions
    assert {
        decision.warning.warning_hash for decision in ir.warning_decisions
    }.issubset(warning_hashes)
    assert "warning_text" not in _all_mapping_keys(ir)


def test_factory_compiled_query_is_internal_while_public_artifact_is_value_free():
    compiled = factories.compiled_query()
    artifact = factories.sql_artifact(compiled)
    assert [parameter.value for parameter in compiled.parameters] == ["London", 5]
    assert artifact.parameter_count == 2
    assert "London" not in artifact.model_dump_json()
    assert "value" not in _all_mapping_keys(artifact)


def test_factory_codes_preserves_stable_violation_order():
    violations = (_violation("first_code"), _violation("second_code"))
    assert factories.codes(violations) == ("first_code", "second_code")


def test_legacy_advisory_models_keep_exact_representative_shapes_and_dumps():
    semantic = models.SemanticObject(
        id="table.accounts",
        type="table",
        name="Accounts",
        future_extension={"enabled": True},
    )
    assert set(models.SemanticObject.model_fields) == {
        "id",
        "type",
        "name",
        "description",
        "status",
        "aliases",
        "tags",
        "links",
        "provenance",
        "cerebro",
        "body",
        "path",
    }
    assert semantic.model_dump(mode="python") == {
        "id": "table.accounts",
        "type": "table",
        "name": "Accounts",
        "description": "",
        "status": "active",
        "aliases": [],
        "tags": [],
        "links": [],
        "provenance": {},
        "cerebro": {},
        "body": "",
        "path": "",
        "future_extension": {"enabled": True},
    }

    ranking = models.RankedResult(
        id="table.accounts",
        type="table",
        name="Accounts",
        score=1.0,
        evidence=["lexical"],
    )
    advisory = models.GroundingResponse(
        semantic_version="legacy.v1",
        retrieval_mode="lexical_graph",
        question="Show accounts",
        concepts=[],
        tables=[],
        columns=[],
        joins=[],
        grain=[],
        metrics=[],
        filters=[],
        warnings=[],
        classifications=[],
        provenance=[],
        ranking_evidence=[ranking],
    )
    assert set(models.RankedResult.model_fields) == {
        "id",
        "type",
        "name",
        "score",
        "evidence",
    }
    assert set(models.GroundingResponse.model_fields) == {
        "semantic_version",
        "retrieval_mode",
        "question",
        "concepts",
        "tables",
        "columns",
        "joins",
        "grain",
        "metrics",
        "filters",
        "warnings",
        "classifications",
        "provenance",
        "ranking_evidence",
    }
    assert advisory.model_dump(mode="python") == {
        "semantic_version": "legacy.v1",
        "retrieval_mode": "lexical_graph",
        "question": "Show accounts",
        "concepts": [],
        "tables": [],
        "columns": [],
        "joins": [],
        "grain": [],
        "metrics": [],
        "filters": [],
        "warnings": [],
        "classifications": [],
        "provenance": [],
        "ranking_evidence": [
            {
                "id": "table.accounts",
                "type": "table",
                "name": "Accounts",
                "score": 1.0,
                "evidence": ["lexical"],
            }
        ],
    }
    assert models.GroundingResponse.model_config.get("extra") != "forbid"
    assert not hasattr(models, "QueryPlan")


def test_all_task0_evidence_records_keep_exact_fields_and_dumps():
    table = models.SourceTableManifest(
        name="accounts",
        file_name="accounts.csv",
        sha256="a" * 64,
        row_count=2,
    )
    manifest = models.SourceManifest(tables=(table,))
    materialized_table = models.MaterializedTableReceipt(
        table_id="table.accounts",
        source_file_sha256="b" * 64,
        row_count=2,
    )
    materialization = models.MaterializationReceipt(
        source_manifest_sha256="c" * 64,
        bundle_sha256="d" * 64,
        tables=(materialized_table,),
        database_sha256="e" * 64,
        engine="duckdb",
        engine_version="1.5.5",
    )
    capability = models.ProviderCapabilityReceipt(
        provider="organizer",
        model="organizer-model",
        revision="2026-08-27",
        schema_mechanism="json_schema",
    )
    blocker = models.PreflightBlocker(code="missing_api_key", gate="organizer")
    report = models.PreflightReport(
        offline_ready=True,
        data_prerequisites_ready=True,
        organizer_prerequisites_ready=False,
        live_prerequisites_ready=False,
        blockers=(blocker,),
    )

    expected = {
        "SourceTableManifest": (
            {"name", "file_name", "sha256", "row_count"},
            {
                "name": "accounts",
                "file_name": "accounts.csv",
                "sha256": "a" * 64,
                "row_count": 2,
            },
        ),
        "SourceManifest": (
            {"tables"},
            {
                "tables": (
                    {
                        "name": "accounts",
                        "file_name": "accounts.csv",
                        "sha256": "a" * 64,
                        "row_count": 2,
                    },
                )
            },
        ),
        "MaterializedTableReceipt": (
            {"table_id", "source_file_sha256", "row_count"},
            {
                "table_id": "table.accounts",
                "source_file_sha256": "b" * 64,
                "row_count": 2,
            },
        ),
        "MaterializationReceipt": (
            {
                "source_manifest_sha256",
                "bundle_sha256",
                "tables",
                "database_sha256",
                "engine",
                "engine_version",
            },
            {
                "source_manifest_sha256": "c" * 64,
                "bundle_sha256": "d" * 64,
                "tables": (
                    {
                        "table_id": "table.accounts",
                        "source_file_sha256": "b" * 64,
                        "row_count": 2,
                    },
                ),
                "database_sha256": "e" * 64,
                "engine": "duckdb",
                "engine_version": "1.5.5",
            },
        ),
        "ProviderCapabilityReceipt": (
            {"provider", "model", "revision", "schema_mechanism"},
            {
                "provider": "organizer",
                "model": "organizer-model",
                "revision": "2026-08-27",
                "schema_mechanism": "json_schema",
            },
        ),
        "PreflightBlocker": (
            {"code", "gate"},
            {"code": "missing_api_key", "gate": "organizer"},
        ),
        "PreflightReport": (
            {
                "offline_ready",
                "data_prerequisites_ready",
                "organizer_prerequisites_ready",
                "live_prerequisites_ready",
                "blockers",
            },
            {
                "offline_ready": True,
                "data_prerequisites_ready": True,
                "organizer_prerequisites_ready": False,
                "live_prerequisites_ready": False,
                "blockers": ({"code": "missing_api_key", "gate": "organizer"},),
            },
        ),
    }
    records = (
        table,
        manifest,
        materialized_table,
        materialization,
        capability,
        blocker,
        report,
    )
    assert {type(record).__name__ for record in records} == set(expected)
    for record in records:
        fields, dumped = expected[type(record).__name__]
        assert set(record.model_fields) == fields
        assert record.model_dump(mode="python") == dumped
        assert record.model_config == {
            "extra": "forbid",
            "frozen": True,
            "strict": True,
        }
    assert hasattr(models, "Sha256Digest")
