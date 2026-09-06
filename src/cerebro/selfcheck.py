"""Local, deterministic gates over typed relational IR and typed clarification.

Nothing here contacts a provider, a compiler, or an engine. Validation runs in
ordered phases and returns at the first phase that finds a violation, so a
single defect yields a single stable code instead of a cascade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .models import (
    MAX_EXPRESSION_DEPTH,
    SUPPORTED_COMPLEX_NODE_KINDS,
    Ambiguity,
    CheckViolation,
    ClarificationRequest,
    GroundingRefusal,
    GroundingSnapshot,
    RelationalQueryIR,
)

_TYPE_REGISTRY_VERSION = "008.types.v1"

# Token boundaries for `008.literal-span.v1`. A question span must be exactly
# one token, so a partial word can never become a governed literal.
_TOKEN_BREAK_CHARACTERS = frozenset(" ,;()[]{}?!")

# A governance object is never an answer, so it is never a valid candidate.
_QUERYABLE_OBJECT_TYPES = frozenset({"dataset", "table", "concept", "metric"})
_NUMERIC_TYPES = frozenset({"integer", "decimal"})
_TEMPORAL_TYPES = frozenset({"date", "timestamp"})

_AGGREGATE_FUNCTIONS = frozenset(
    {"count", "sum", "avg", "min", "max", "stddev", "variance"}
)
_COMPARISON_OPERATORS = frozenset({"eq", "neq", "lt", "lte", "gt", "gte"})
_LOGICAL_OPERATORS = frozenset({"and", "or"})
_ARITHMETIC_OPERATORS = frozenset({"add", "subtract", "multiply", "divide"})


@dataclass(frozen=True)
class GroundingIndex:
    """Exact membership for one snapshot. It answers only yes-or-no questions."""

    table_ids: frozenset[str]
    object_ids: frozenset[str]
    columns: dict[tuple[str, str], str]
    column_classifications: dict[tuple[str, str], str]
    object_types: dict[str, str]
    metric_result_types: dict[str, str]
    relationships: dict[str, tuple[tuple[str, str], tuple[str, str]]]
    governed_literals: dict[str, str]
    warning_hashes: frozenset[tuple[str, str]]
    policy_ids: frozenset[str]
    ranked_object_ids: tuple[str, ...] = ()

    @classmethod
    def from_snapshot(cls, snapshot: GroundingSnapshot) -> GroundingIndex:
        if not isinstance(snapshot, GroundingSnapshot):
            raise TypeError("a grounding index requires a frozen snapshot")
        columns: dict[tuple[str, str], str] = {}
        classifications: dict[tuple[str, str], str] = {}
        metrics: dict[str, str] = {}
        relationships: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {}
        warnings: set[tuple[str, str]] = set()
        table_ids: set[str] = set()
        object_types: dict[str, str] = {}
        for item in snapshot.objects:
            object_types[item.object_id] = item.object_type
            if item.object_type == "table":
                table_ids.add(item.object_id)
            for column in item.columns:
                key = (column.ref.table_id, column.ref.column)
                columns[key] = column.data_type
                classifications[key] = column.classification
            for relationship in item.relationships:
                relationships[relationship.relationship_id] = (
                    (relationship.left.table_id, relationship.left.column),
                    (relationship.right.table_id, relationship.right.column),
                )
            for warning in item.warnings:
                warnings.add((warning.object_id, warning.warning_hash))
            if item.object_type == "metric" and item.metric_result_type is not None:
                metrics[item.object_id] = item.metric_result_type
        return cls(
            table_ids=frozenset(table_ids),
            object_ids=frozenset(snapshot.authorized_object_ids),
            columns=columns,
            column_classifications=classifications,
            object_types=object_types,
            metric_result_types=metrics,
            relationships=relationships,
            governed_literals={
                literal.literal_id: literal.data_type
                for literal in snapshot.governed_literals
            },
            warning_hashes=frozenset(warnings),
            policy_ids=frozenset(snapshot.policy_ids),
            ranked_object_ids=tuple(
                item.object_id for item in snapshot.ranking_evidence
            ),
        )

    def column_type(self, table_id: str, column: str) -> str | None:
        return self.columns.get((table_id, column))

    def relationship_tables(self, relationship_id: str) -> tuple[str, str] | None:
        endpoints = self.relationships.get(relationship_id)
        if endpoints is None:
            return None
        return endpoints[0][0], endpoints[1][0]

    def is_graph_connected(self, object_id: str, ranked: set[str]) -> bool:
        """True when the object touches a ranked object through one relationship."""
        for left, right in self.relationships.values():
            tables = {left[0], right[0]}
            if object_id in tables and tables & ranked:
                return True
        return False


class ExpressionTypeRegistry:
    """Version-pinned function and operator signatures over snapshot types."""

    def __init__(self, version: str = _TYPE_REGISTRY_VERSION) -> None:
        if version != _TYPE_REGISTRY_VERSION:
            raise ValueError(f"unsupported type registry version: {version}")
        self.version = version

    @staticmethod
    def is_aggregate(function: str) -> bool:
        return function in _AGGREGATE_FUNCTIONS

    def function_signature(self, function: str) -> tuple[int, int]:
        """Return the inclusive minimum and maximum argument count."""
        if function == "coalesce":
            return 1, 100
        if function == "nullif":
            return 2, 2
        return 1, 1

    def function_result(
        self, function: str, argument_types: list[str | None]
    ) -> str | None:
        first = argument_types[0] if argument_types else None
        if function == "count":
            return "integer"
        if function in {"sum", "avg", "stddev", "variance"}:
            return "decimal"
        if function in {"min", "max", "nullif", "coalesce"}:
            return first
        if function == "date_trunc":
            return first if first in _TEMPORAL_TYPES else None
        return None

    def binary_result(self, operator: str) -> str | None:
        if operator in _COMPARISON_OPERATORS or operator in _LOGICAL_OPERATORS:
            return "boolean"
        if operator in _ARITHMETIC_OPERATORS:
            return "decimal"
        return None

    @staticmethod
    def are_comparable(left: str | None, right: str | None) -> bool:
        if left is None or right is None:
            # An unresolved operand type is reported by the reference phase.
            return True
        if left == right:
            return True
        return left in _NUMERIC_TYPES and right in _NUMERIC_TYPES


@dataclass
class IRValidationResult:
    validated_ir: RelationalQueryIR | None
    violations: tuple[CheckViolation, ...] = ()


@dataclass
class ClarificationDecision:
    reason: str | None = None
    ambiguities: tuple[Ambiguity, ...] = ()
    violation: CheckViolation | None = None


@dataclass
class RefusalDecision:
    reason: str | None = None
    violation: CheckViolation | None = None


@dataclass
class _Findings:
    violations: list[CheckViolation] = field(default_factory=list)

    def add(self, code: str, subject_ids: tuple[str, ...] = ()) -> None:
        violation = CheckViolation(
            code=code, stage="ast_check", subject_ids=subject_ids
        )
        if violation not in self.violations:
            self.violations.append(violation)

    def __bool__(self) -> bool:
        return bool(self.violations)


def _node_inputs(node: Any) -> tuple[str, ...]:
    if node.kind in {"join", "set_operation"}:
        return (node.left_id, node.right_id)
    if node.kind == "scan":
        return ()
    return (node.input_id,)


def _walk_expressions(expression: Any) -> list[Any]:
    """Return the expression and every nested expression, depth-bounded."""
    collected: list[Any] = []
    frontier = [(expression, 1)]
    while frontier:
        current, depth = frontier.pop()
        if depth > MAX_EXPRESSION_DEPTH:
            break
        collected.append(current)
        kind = getattr(current, "kind", None)
        if kind == "function":
            frontier.extend((argument, depth + 1) for argument in current.arguments)
        elif kind == "binary":
            frontier.extend(((current.left, depth + 1), (current.right, depth + 1)))
        elif kind == "in":
            frontier.append((current.expression, depth + 1))
            frontier.extend((value, depth + 1) for value in current.values)
        elif kind == "case":
            for branch in current.branches:
                frontier.extend(((branch.when, depth + 1), (branch.then, depth + 1)))
            frontier.append((current.else_expression, depth + 1))
    return collected


def _node_expressions(node: Any) -> list[Any]:
    kind = node.kind
    if kind == "filter":
        return [node.predicate]
    if kind == "aggregate":
        return [item.expression for item in (*node.group_by, *node.measures)]
    if kind == "project":
        return [item.expression for item in node.outputs]
    if kind == "sort":
        return [key.expression for key in node.keys]
    if kind == "window":
        expressions: list[Any] = []
        for output in node.outputs:
            if output.argument is not None:
                expressions.append(output.argument)
            expressions.extend(output.partition_by)
            expressions.extend(key.expression for key in output.order_by)
        return expressions
    return []


def _literal_refs_of(node: Any) -> list[Any]:
    refs: list[Any] = []
    if node.kind == "limit":
        refs.append(node.count)
    if node.kind == "aggregate" and node.minimum_group_size is not None:
        refs.append(node.minimum_group_size)
    if node.kind == "window":
        refs.extend(
            output.offset for output in node.outputs if output.offset is not None
        )
    for expression in _node_expressions(node):
        for nested in _walk_expressions(expression):
            if getattr(nested, "kind", None) == "literal":
                refs.append(nested.ref)
            elif getattr(nested, "kind", None) == "relative_time":
                refs.append(nested.amount_ref)
    return refs


def _span_is_one_token(canonical_question: str, start: int, end: int) -> bool:
    if start < 0 or end <= start or end > len(canonical_question):
        return False
    token = canonical_question[start:end]
    if not token or token[0] == " " or token[-1] == " ":
        return False
    # A quoted literal is one token including inner spaces; an unquoted token
    # may not contain a break character.
    is_quoted = token[0] == token[-1] and token[0] in "'\""
    if not is_quoted and any(
        character in _TOKEN_BREAK_CHARACTERS for character in token
    ):
        return False
    before = canonical_question[start - 1] if start > 0 else " "
    after = canonical_question[end] if end < len(canonical_question) else " "
    return (before in _TOKEN_BREAK_CHARACTERS or before in "'\"") and (
        after in _TOKEN_BREAK_CHARACTERS or after in "'\""
    )


def _literal_ref_type(
    ref: Any, index: GroundingIndex, canonical_question: str
) -> tuple[str | None, bool]:
    """Return the ref's scalar type and whether it is grounded."""
    if ref.kind == "question":
        if not _span_is_one_token(canonical_question, ref.start, ref.end):
            return ref.data_type, False
        return ref.data_type, True
    declared = index.governed_literals.get(ref.literal_id)
    return declared, declared is not None


def _expression_type(
    expression: Any,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
) -> str | None:
    kind = getattr(expression, "kind", None)
    if kind == "column":
        return index.column_type(expression.ref.table_id, expression.ref.column)
    if kind == "metric":
        return index.metric_result_types.get(expression.metric_id)
    if kind == "literal":
        declared, _ = _literal_ref_type(expression.ref, index, canonical_question)
        return declared
    if kind == "function":
        argument_types = [
            _expression_type(argument, index, registry, canonical_question)
            for argument in expression.arguments
        ]
        return registry.function_result(expression.function, argument_types)
    if kind == "binary":
        return registry.binary_result(expression.operator)
    if kind in {"in", "relative_time"}:
        return "boolean"
    if kind == "case":
        return _expression_type(
            expression.branches[0].then, index, registry, canonical_question
        )
    return None


def _output_scope(node: Any, inputs: list[set[str]]) -> set[str]:
    """Return the qualified names a node makes available to its consumer."""
    kind = node.kind
    if kind == "aggregate":
        return {item.alias for item in (*node.group_by, *node.measures)}
    if kind == "project":
        return {item.alias for item in node.outputs}
    if kind == "window":
        combined = set().union(*inputs) if inputs else set()
        return combined | {output.alias for output in node.outputs}
    if kind == "set_operation":
        return set(inputs[0]) if inputs else set()
    combined = set().union(*inputs) if inputs else set()
    return combined


def _output_arity(node: Any, arities: dict[str, int]) -> int:
    kind = node.kind
    if kind == "aggregate":
        return len(node.group_by) + len(node.measures)
    if kind == "project":
        return len(node.outputs)
    if kind in {"join", "set_operation"}:
        return arities.get(node.left_id, 0)
    if kind == "scan":
        return 0
    return arities.get(node.input_id, 0)


def _output_types(
    node: Any,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    inherited: dict[str, list[str | None]],
) -> list[str | None]:
    kind = node.kind
    if kind == "aggregate":
        return [
            _expression_type(item.expression, index, registry, canonical_question)
            for item in (*node.group_by, *node.measures)
        ]
    if kind == "project":
        return [
            _expression_type(item.expression, index, registry, canonical_question)
            for item in node.outputs
        ]
    if kind in {"join", "set_operation"}:
        return inherited.get(node.left_id, [])
    if kind == "scan":
        return []
    return inherited.get(node.input_id, [])


def validate_ir(
    ir: RelationalQueryIR,
    snapshot: GroundingSnapshot,
    canonical_question: str,
    generation_route: str,
) -> IRValidationResult:
    """Validate one IR against its snapshot, question, and generation route."""
    if not isinstance(ir, RelationalQueryIR):
        raise TypeError("IR validation requires a validated RelationalQueryIR")
    index = GroundingIndex.from_snapshot(snapshot)
    registry = ExpressionTypeRegistry()

    for phase in (
        _phase_shape,
        _phase_route,
        _phase_references,
        _phase_connectivity,
        _phase_dataflow,
        _phase_types,
    ):
        findings = _Findings()
        phase(ir, index, registry, canonical_question, generation_route, findings)
        if findings:
            return IRValidationResult(
                validated_ir=None, violations=tuple(findings.violations)
            )
    return IRValidationResult(validated_ir=ir, violations=())


def _phase_shape(
    ir: RelationalQueryIR,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    generation_route: str,
    findings: _Findings,
) -> None:
    node_ids = [node.node_id for node in ir.nodes]
    duplicates = sorted({item for item in node_ids if node_ids.count(item) > 1})
    if duplicates:
        findings.add("duplicate_ir_node", tuple(duplicates))
        return

    by_id = {node.node_id: node for node in ir.nodes}
    if ir.root_node_id not in by_id:
        findings.add("invalid_ir_root", (ir.root_node_id,))
        return

    for node in ir.nodes:
        missing = [input_id for input_id in _node_inputs(node) if input_id not in by_id]
        if missing:
            findings.add("invalid_node_arity", (node.node_id,))
            return

    if _has_node_cycle(ir, by_id):
        findings.add("cyclic_ir", tuple(sorted(node_ids)))
        return

    reachable = _reachable_from_root(ir, by_id)
    orphans = sorted(set(node_ids) - reachable)
    if orphans:
        findings.add("orphan_ir_node", tuple(orphans))


def _phase_route(
    ir: RelationalQueryIR,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    generation_route: str,
    findings: _Findings,
) -> None:
    if generation_route == "planned_ir":
        return
    complex_nodes = sorted(
        node.node_id for node in ir.nodes if node.kind in SUPPORTED_COMPLEX_NODE_KINDS
    )
    if complex_nodes:
        findings.add("complex_node_requires_planned_route", tuple(complex_nodes))


def _phase_references(
    ir: RelationalQueryIR,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    generation_route: str,
    findings: _Findings,
) -> None:
    for node in ir.nodes:
        if node.kind == "scan":
            if node.table_id not in index.table_ids:
                findings.add("ungrounded_ir_reference", (node.table_id,))
            continue
        if node.kind == "join" and node.relationship_id not in index.relationships:
            findings.add("ungrounded_ir_reference", (node.relationship_id,))
        for expression in _node_expressions(node):
            for nested in _walk_expressions(expression):
                _check_expression_reference(nested, index, canonical_question, findings)
        for ref in _literal_refs_of(node):
            _check_literal_reference(ref, index, canonical_question, findings)

    for decision in ir.warning_decisions:
        pair = (decision.warning.object_id, decision.warning.warning_hash)
        if pair not in index.warning_hashes:
            findings.add("ungrounded_ir_reference", (decision.warning.object_id,))

    for assumption in ir.assumptions:
        for column in _assumption_columns(assumption):
            if (column.table_id, column.column) not in index.columns:
                findings.add(
                    "ungrounded_ir_reference",
                    (f"{column.table_id}.{column.column}",),
                )
        for table_id in getattr(assumption, "source_table_ids", ()):
            if table_id not in index.table_ids:
                findings.add("ungrounded_ir_reference", (table_id,))
        for ref in _assumption_refs(assumption):
            _check_literal_reference(ref, index, canonical_question, findings)

    for disclosure in ir.requested_disclosures:
        for column in disclosure.source_columns:
            if (column.table_id, column.column) not in index.columns:
                findings.add(
                    "ungrounded_ir_reference",
                    (f"{column.table_id}.{column.column}",),
                )
        _check_literal_reference(
            disclosure.limit_ref, index, canonical_question, findings
        )


def _assumption_columns(assumption: Any) -> tuple[Any, ...]:
    columns = []
    if getattr(assumption, "column", None) is not None:
        columns.append(assumption.column)
    columns.extend(getattr(assumption, "grouping_columns", ()))
    return tuple(columns)


def _assumption_refs(assumption: Any) -> tuple[Any, ...]:
    return (
        *getattr(assumption, "inflow_refs", ()),
        *getattr(assumption, "outflow_refs", ()),
        *getattr(assumption, "literal_refs", ()),
        *getattr(assumption, "physical_operands", ()),
    )


def _check_expression_reference(
    expression: Any,
    index: GroundingIndex,
    canonical_question: str,
    findings: _Findings,
) -> None:
    kind = getattr(expression, "kind", None)
    if kind == "column":
        key = (expression.ref.table_id, expression.ref.column)
        if key not in index.columns:
            findings.add("ungrounded_ir_reference", (f"{key[0]}.{key[1]}",))
    elif kind == "metric":
        if expression.metric_id not in index.metric_result_types:
            findings.add("ungrounded_ir_reference", (expression.metric_id,))
    elif kind == "relative_time":
        key = (expression.date_column.table_id, expression.date_column.column)
        if key not in index.columns:
            findings.add("ungrounded_ir_reference", (f"{key[0]}.{key[1]}",))


def _check_literal_reference(
    ref: Any,
    index: GroundingIndex,
    canonical_question: str,
    findings: _Findings,
) -> None:
    _, grounded = _literal_ref_type(ref, index, canonical_question)
    if grounded:
        return
    subject = (
        ref.literal_id
        if ref.kind == "governed"
        else f"question_span_{ref.start}_{ref.end}"
    )
    findings.add("ungrounded_ir_reference", (subject,))


def _phase_connectivity(
    ir: RelationalQueryIR,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    generation_route: str,
    findings: _Findings,
) -> None:
    scanned = {node.table_id for node in ir.nodes if node.kind == "scan"}
    if len(scanned) <= 1:
        return
    # Every physical table must be reachable through declared relationships that
    # the IR actually joins on, so a cross product cannot appear by omission.
    joined_tables: dict[str, set[str]] = {table: set() for table in scanned}
    for node in ir.nodes:
        if node.kind != "join":
            continue
        endpoints = index.relationship_tables(node.relationship_id)
        if endpoints is None:
            continue
        left, right = endpoints
        joined_tables.setdefault(left, set()).add(right)
        joined_tables.setdefault(right, set()).add(left)

    start = next(iter(sorted(scanned)))
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for neighbor in joined_tables.get(current, set()):
            if neighbor not in seen:
                seen.add(neighbor)
                frontier.append(neighbor)
    if not scanned <= seen:
        findings.add("disconnected_ir", tuple(sorted(scanned - seen)))


def _phase_dataflow(
    ir: RelationalQueryIR,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    generation_route: str,
    findings: _Findings,
) -> None:
    by_id = {node.node_id: node for node in ir.nodes}
    order = _topological_order(ir, by_id)
    scopes: dict[str, set[str]] = {}
    for node_id in order:
        node = by_id[node_id]
        if node.kind == "scan":
            scopes[node_id] = {
                f"{table}.{column}"
                for (table, column) in index.columns
                if table == node.table_id
            }
            continue
        inputs = [scopes.get(input_id, set()) for input_id in _node_inputs(node)]
        available = set().union(*inputs) if inputs else set()
        for expression in _node_expressions(node):
            for nested in _walk_expressions(expression):
                if getattr(nested, "kind", None) != "column":
                    continue
                qualified = f"{nested.ref.table_id}.{nested.ref.column}"
                if qualified not in available and nested.ref.column not in available:
                    findings.add("ungrounded_ir_reference", (qualified,))
        aliases = _declared_aliases(node)
        duplicates = sorted({item for item in aliases if aliases.count(item) > 1})
        if duplicates:
            findings.add("duplicate_output_alias", tuple(duplicates))
        scopes[node_id] = _output_scope(node, inputs)


def _declared_aliases(node: Any) -> list[str]:
    if node.kind == "aggregate":
        return [item.alias for item in (*node.group_by, *node.measures)]
    if node.kind == "project":
        return [item.alias for item in node.outputs]
    if node.kind == "window":
        return [output.alias for output in node.outputs]
    return []


def _phase_types(
    ir: RelationalQueryIR,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    generation_route: str,
    findings: _Findings,
) -> None:
    by_id = {node.node_id: node for node in ir.nodes}
    arities: dict[str, int] = {}
    types: dict[str, list[str | None]] = {}

    for node_id in _topological_order(ir, by_id):
        node = by_id[node_id]
        _check_node_types(node, index, registry, canonical_question, findings)
        arities[node_id] = _output_arity(node, arities)
        types[node_id] = _output_types(node, index, registry, canonical_question, types)
        if node.kind == "set_operation":
            left_arity = arities.get(node.left_id, 0)
            right_arity = arities.get(node.right_id, 0)
            if left_arity != right_arity:
                findings.add("set_output_arity_mismatch", (node.node_id,))
                continue
            left_types = types.get(node.left_id, [])
            right_types = types.get(node.right_id, [])
            for position, (left, right) in enumerate(zip(left_types, right_types)):
                if not registry.are_comparable(left, right):
                    findings.add("set_output_type_mismatch", (node.node_id,))
                    break


def _check_node_types(
    node: Any,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    findings: _Findings,
) -> None:
    kind = node.kind

    if kind == "filter":
        _check_predicate(node.predicate, index, registry, canonical_question, findings)
        if _contains_aggregate(node.predicate, registry):
            findings.add("invalid_aggregate_placement", (node.node_id,))

    if kind == "aggregate":
        for item in node.group_by:
            if _contains_aggregate(item.expression, registry):
                findings.add("invalid_aggregate_placement", (node.node_id,))
        for item in node.measures:
            if _has_nested_aggregate(item.expression, registry):
                findings.add("nested_aggregate_or_window", (node.node_id,))

    if kind in {"project", "sort"}:
        for expression in _node_expressions(node):
            if _contains_aggregate(expression, registry):
                # An aggregate outside an aggregate node has no grouping scope.
                findings.add("invalid_aggregate_placement", (node.node_id,))

    for expression in _node_expressions(node):
        _check_expression_types(
            expression, index, registry, canonical_question, findings
        )


def _check_predicate(
    expression: Any,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    findings: _Findings,
) -> None:
    resolved = _expression_type(expression, index, registry, canonical_question)
    if resolved != "boolean":
        findings.add("non_boolean_filter", ())


def _check_expression_types(
    expression: Any,
    index: GroundingIndex,
    registry: ExpressionTypeRegistry,
    canonical_question: str,
    findings: _Findings,
) -> None:
    for nested in _walk_expressions(expression):
        kind = getattr(nested, "kind", None)
        if kind == "function":
            minimum, maximum = registry.function_signature(nested.function)
            if not minimum <= len(nested.arguments) <= maximum:
                findings.add("invalid_function_signature", (nested.function,))
            if nested.function == "date_trunc":
                argument_type = (
                    _expression_type(
                        nested.arguments[0], index, registry, canonical_question
                    )
                    if nested.arguments
                    else None
                )
                if argument_type not in _TEMPORAL_TYPES:
                    findings.add("invalid_function_signature", (nested.function,))
        elif kind == "binary":
            left = _expression_type(nested.left, index, registry, canonical_question)
            right = _expression_type(nested.right, index, registry, canonical_question)
            if nested.operator in _LOGICAL_OPERATORS:
                if left != "boolean" or right != "boolean":
                    findings.add("non_boolean_filter", ())
            elif not registry.are_comparable(left, right):
                findings.add("invalid_function_signature", (nested.operator,))
        elif kind == "in":
            operand = _expression_type(
                nested.expression, index, registry, canonical_question
            )
            for value in nested.values:
                value_type = _expression_type(
                    value, index, registry, canonical_question
                )
                if not registry.are_comparable(operand, value_type):
                    findings.add("invalid_function_signature", ("in",))
                    break
        elif kind == "case":
            for branch in nested.branches:
                condition = _expression_type(
                    branch.when, index, registry, canonical_question
                )
                if condition != "boolean":
                    findings.add("non_boolean_filter", ())
                    break


def _contains_aggregate(expression: Any, registry: ExpressionTypeRegistry) -> bool:
    return any(
        getattr(nested, "kind", None) == "function"
        and registry.is_aggregate(nested.function)
        for nested in _walk_expressions(expression)
    )


def _has_nested_aggregate(expression: Any, registry: ExpressionTypeRegistry) -> bool:
    for nested in _walk_expressions(expression):
        if getattr(nested, "kind", None) != "function":
            continue
        if not registry.is_aggregate(nested.function):
            continue
        for argument in nested.arguments:
            if _contains_aggregate(argument, registry):
                return True
    return False


def _has_node_cycle(ir: RelationalQueryIR, by_id: dict[str, Any]) -> bool:
    visiting: set[str] = set()
    settled: set[str] = set()

    def walk(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in settled or node_id not in by_id:
            return False
        visiting.add(node_id)
        for input_id in _node_inputs(by_id[node_id]):
            if walk(input_id):
                return True
        visiting.discard(node_id)
        settled.add(node_id)
        return False

    return any(walk(node.node_id) for node in ir.nodes)


def _reachable_from_root(ir: RelationalQueryIR, by_id: dict[str, Any]) -> set[str]:
    reachable: set[str] = set()
    frontier = [ir.root_node_id]
    while frontier:
        node_id = frontier.pop()
        if node_id in reachable or node_id not in by_id:
            continue
        reachable.add(node_id)
        frontier.extend(_node_inputs(by_id[node_id]))
    return reachable


def _topological_order(ir: RelationalQueryIR, by_id: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    settled: set[str] = set()

    def walk(node_id: str) -> None:
        if node_id in settled or node_id not in by_id:
            return
        settled.add(node_id)
        for input_id in _node_inputs(by_id[node_id]):
            walk(input_id)
        ordered.append(node_id)

    walk(ir.root_node_id)
    for node in ir.nodes:
        walk(node.node_id)
    return ordered


def validate_clarification(
    request: Any,
    canonical_question: str,
    snapshot: GroundingSnapshot,
) -> ClarificationDecision:
    """Validate a model-proposed clarification entirely locally."""
    index = GroundingIndex.from_snapshot(snapshot)
    invalid = ClarificationDecision(
        violation=CheckViolation(
            code="invalid_clarification_request",
            stage="clarification",
            subject_ids=(),
        )
    )

    if isinstance(request, ClarificationRequest):
        validated = request
    else:
        try:
            validated = ClarificationRequest.model_validate(request)
        except ValidationError:
            # A payload the strict contract rejects is an invalid request, not a
            # crash: too few candidates and duplicates arrive this way.
            return invalid

    ranked = set(index.ranked_object_ids)
    for ambiguity in validated.ambiguities:
        if not _span_is_one_token(canonical_question, ambiguity.start, ambiguity.end):
            return invalid
        kinds = {candidate.kind for candidate in ambiguity.candidates}
        if len(kinds) != 1:
            return invalid
        payloads = [
            candidate.model_dump(mode="json") for candidate in ambiguity.candidates
        ]
        if len(payloads) < 2:
            return invalid
        for position, payload in enumerate(payloads):
            if payload in payloads[position + 1 :]:
                return invalid
        for candidate in ambiguity.candidates:
            if not _candidate_is_relevant(candidate, index, ranked):
                return invalid

    return ClarificationDecision(
        reason="clarification_required", ambiguities=validated.ambiguities
    )


def _candidate_is_relevant(
    candidate: Any, index: GroundingIndex, ranked: set[str]
) -> bool:
    kind = candidate.kind
    if kind == "object":
        if candidate.object_id not in index.object_ids:
            return False
        # A governance object is never an answer to a question, so offering a
        # policy as a disambiguation choice is irrelevant by construction.
        if index.object_types.get(candidate.object_id) not in _QUERYABLE_OBJECT_TYPES:
            return False
        if not ranked:
            # With no ranking evidence, snapshot membership plus a queryable
            # type is the strongest deterministic relevance signal available.
            return True
        return candidate.object_id in ranked or index.is_graph_connected(
            candidate.object_id, ranked
        )
    if kind == "relationship":
        return candidate.relationship_id in index.relationships
    if kind == "governed_literal":
        return candidate.literal_id in index.governed_literals
    if kind == "grain":
        return all(
            (column.table_id, column.column) in index.columns
            for column in candidate.grouping_columns
        )
    if kind == "operator":
        from .complexity import COMPLEX_OPERATOR_ALLOWLIST

        return candidate.operator_id in COMPLEX_OPERATOR_ALLOWLIST
    return False


def check_grounding_refusal(
    refusal: GroundingRefusal, snapshot: GroundingSnapshot
) -> RefusalDecision:
    """Accept a missing-grounding claim only after proving local absence."""
    index = GroundingIndex.from_snapshot(snapshot)
    present: list[str] = []
    for need in refusal.unmet_needs:
        if need.kind == "table" and need.object_id in index.table_ids:
            present.append(need.object_id)
        elif need.kind == "metric" and need.metric_id in index.metric_result_types:
            present.append(need.metric_id)
        elif (
            need.kind == "relationship" and need.relationship_id in index.relationships
        ):
            present.append(need.relationship_id)
        elif (
            need.kind == "column"
            and (need.ref.table_id, need.ref.column) in index.columns
        ):
            present.append(f"{need.ref.table_id}.{need.ref.column}")
    if present:
        return RefusalDecision(
            violation=CheckViolation(
                code="false_missing_grounding",
                stage="snapshot",
                subject_ids=tuple(sorted(present)),
            )
        )
    return RefusalDecision(reason="missing_grounding")
