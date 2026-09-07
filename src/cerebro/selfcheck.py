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
    SUPPORTED_DEFAULT_NODE_KINDS,
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
    metric_formulas: dict[str, str]
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
        metric_formulas: dict[str, str] = {}
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
                if item.formula is not None:
                    metric_formulas[item.object_id] = item.formula
        return cls(
            table_ids=frozenset(table_ids),
            object_ids=frozenset(snapshot.authorized_object_ids),
            columns=columns,
            column_classifications=classifications,
            object_types=object_types,
            metric_result_types=metrics,
            metric_formulas=metric_formulas,
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
        node.node_id
        for node in ir.nodes
        if node.kind not in SUPPORTED_DEFAULT_NODE_KINDS
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


# ---------------------------------------------------------------------------
# Post-compile authorization (Task 7).
#
# Every check below compares structure, never model-provided SQL text: the
# compiled AST is resolved into references and each one must have exactly one
# matching declaration in the accepted IR. Resolved parameter values are never
# read, compared, or emitted.
# ---------------------------------------------------------------------------

_SENSITIVE_CLASSIFICATIONS = frozenset({"confidential", "restricted"})
# Aggregates that reduce many rows to one summary value.
_REDUCING_AGGREGATES = frozenset({"count", "sum", "avg", "stddev", "variance"})
# Aggregates that hand back one original value unchanged.
_VALUE_PRESERVING_AGGREGATES = frozenset(
    {"min", "max", "first", "last", "any_value", "arbitrary"}
)
# Collection and string aggregates always leak individual sensitive values.
_COLLECTION_AGGREGATES = frozenset(
    {"list", "array_agg", "string_agg", "group_concat", "listagg", "histogram"}
)
_UNSAFE_SQL_NAMES = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "glob",
        "sniff_csv",
        "postgres_scan",
        "sqlite_scan",
        "mysql_scan",
        "iceberg_scan",
        "delta_scan",
        "parquet_scan",
        "httpfs",
        "install",
        "load",
        "attach",
        "detach",
        "copy",
        "set",
        "reset",
        "secret",
        "create_secret",
    }
)


def _compiled_operator_map() -> dict[type, str]:
    """Map compiled AST comparison nodes onto the IR operator vocabulary."""
    from sqlglot import exp

    return {
        exp.EQ: "eq",
        exp.NEQ: "neq",
        exp.LT: "lt",
        exp.LTE: "lte",
        exp.GT: "gt",
        exp.GTE: "gte",
        exp.Add: "add",
        exp.Sub: "subtract",
        exp.Mul: "multiply",
        exp.Div: "divide",
    }


@dataclass(frozen=True)
class DisclosureCaps:
    """Trusted local caps. These are configuration, never model literals."""

    row_limit: int = 50
    minimum_group_size: int = 5
    max_result_rows: int = 1000

    @classmethod
    def defaults(cls) -> DisclosureCaps:
        return cls()


@dataclass(frozen=True)
class SQLReferenceGraph:
    """Everything the compiled statement actually references."""

    tables: set[str]
    columns: set[tuple[str, str]]
    alias_to_table: dict[str, str]
    join_equalities: tuple[tuple[tuple[str, str], tuple[str, str]], ...]
    join_has_disjunction: bool
    predicate_terms: tuple[Any, ...]
    functions: set[str]
    placeholder_count: int
    inline_literals: tuple[Any, ...]
    limits: tuple[int, ...]
    statement_count: int
    output_expressions: tuple[tuple[str, Any], ...]
    unsafe_names: set[str]
    is_read_only_select: bool
    parse_failed: bool = False

    @classmethod
    def from_sql(cls, sql: str) -> SQLReferenceGraph:
        import sqlglot
        from sqlglot import exp

        try:
            statements = [
                statement
                for statement in sqlglot.parse(sql, dialect="duckdb")
                if statement is not None
            ]
        except Exception:  # noqa: BLE001 - sqlglot raises several error types
            return cls._unparsable()
        if not statements:
            return cls._unparsable()

        root = statements[0]
        read_only = all(
            isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except))
            for statement in statements
        )

        alias_to_table: dict[str, str] = {}
        tables: set[str] = set()
        unsafe: set[str] = set()
        for table in root.find_all(exp.Table):
            name = table.name
            if not name:
                continue
            tables.add(name)
            alias = table.alias or name
            alias_to_table[alias] = name
        for anonymous in root.find_all(exp.Anonymous):
            candidate = (anonymous.this or "").lower()
            if candidate in _UNSAFE_SQL_NAMES:
                unsafe.add(candidate)
        for node in root.walk():
            class_name = type(node).__name__.lower()
            if class_name in _UNSAFE_SQL_NAMES:
                unsafe.add(class_name)
            if isinstance(node, exp.ReadCSV):
                unsafe.add("read_csv")
        if not read_only:
            for statement in statements:
                unsafe.add(type(statement).__name__.lower())

        columns: set[tuple[str, str]] = set()
        for column in root.find_all(exp.Column):
            qualifier = column.table or ""
            columns.add((qualifier, column.name))

        join_equalities: list[tuple[tuple[str, str], tuple[str, str]]] = []
        join_disjunction = False
        for join in root.find_all(exp.Join):
            if str(join.args.get("method") or "").upper() == "NATURAL" or join.args.get(
                "natural"
            ):
                # A natural join binds by name, which no IR relationship declares.
                join_disjunction = True
            condition = join.args.get("on")
            if condition is None:
                if join.args.get("using") or join.args.get("natural"):
                    join_disjunction = True
                continue
            if list(condition.find_all(exp.Or)):
                join_disjunction = True
            for equality in condition.find_all(exp.EQ):
                left, right = equality.this, equality.expression
                if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                    join_equalities.append(
                        (
                            (left.table or "", left.name),
                            (right.table or "", right.name),
                        )
                    )

        predicate_terms: list[Any] = []
        for where in root.find_all(exp.Where):
            predicate_terms.extend(_split_conjunction(where.this))

        functions = {
            (node.sql_name() or type(node).__name__).lower()
            for node in root.find_all(exp.Func)
        }
        for anonymous in root.find_all(exp.Anonymous):
            functions.add((anonymous.this or "").lower())

        placeholders = list(root.find_all(exp.Placeholder))
        inline_literals = tuple(
            literal
            for literal in root.find_all(exp.Literal)
            if not _is_function_unit_literal(literal)
        )
        limits: list[int] = []
        for limit in root.find_all(exp.Limit):
            expression = limit.expression
            if isinstance(expression, exp.Literal) and expression.is_int:
                limits.append(int(expression.name))

        outputs: list[tuple[str, Any]] = []
        select = root if isinstance(root, exp.Select) else root.this
        if isinstance(select, exp.Select):
            for projection in select.expressions:
                alias = projection.alias_or_name
                outputs.append((alias, projection))

        return cls(
            tables=tables,
            columns=columns,
            alias_to_table=alias_to_table,
            join_equalities=tuple(join_equalities),
            join_has_disjunction=join_disjunction,
            predicate_terms=tuple(predicate_terms),
            functions=functions,
            placeholder_count=len(placeholders),
            inline_literals=inline_literals,
            limits=tuple(limits),
            statement_count=len(statements),
            output_expressions=tuple(outputs),
            unsafe_names=unsafe,
            is_read_only_select=read_only,
        )

    @classmethod
    def _unparsable(cls) -> SQLReferenceGraph:
        return cls(
            tables=set(),
            columns=set(),
            alias_to_table={},
            join_equalities=(),
            join_has_disjunction=False,
            predicate_terms=(),
            functions=set(),
            placeholder_count=0,
            inline_literals=(),
            limits=(),
            statement_count=0,
            output_expressions=(),
            unsafe_names=set(),
            is_read_only_select=False,
            parse_failed=True,
        )

    def table_of(self, qualifier: str) -> str:
        return self.alias_to_table.get(qualifier, qualifier)


def _is_function_unit_literal(literal: Any) -> bool:
    """True for a literal the compiler emits as part of a function's shape."""
    from sqlglot import exp

    parent = literal.parent
    return isinstance(parent, (exp.DateTrunc, exp.Interval, exp.Cast))


def _split_conjunction(expression: Any) -> list[Any]:
    from sqlglot import exp

    if isinstance(expression, exp.And):
        return _split_conjunction(expression.this) + _split_conjunction(
            expression.expression
        )
    if isinstance(expression, exp.Paren):
        return _split_conjunction(expression.this)
    return [expression]


@dataclass
class PredicateLedger:
    """Each compiled predicate term must be claimed by exactly one declaration."""

    declarations: dict[str, int] = field(default_factory=dict)
    unclaimed: list[str] = field(default_factory=list)

    def declare(self, key: str, count: int = 1) -> None:
        self.declarations[key] = self.declarations.get(key, 0) + count

    def claim(self, key: str) -> bool:
        remaining = self.declarations.get(key, 0)
        if remaining <= 0:
            return False
        self.declarations[key] = remaining - 1
        return True


@dataclass
class SQLAuthorizationResult:
    violations: tuple[CheckViolation, ...] = ()
    output_lineage: tuple[Any, ...] = ()
    disclosures: tuple[Any, ...] = ()


def authorize_compiled_query(
    compiled: Any,
    validated_ir: Any,
    snapshot: GroundingSnapshot,
    canonical_question: str,
    caps: DisclosureCaps,
) -> SQLAuthorizationResult:
    """Authorize compiled SQL against the accepted IR, without any engine call."""
    from .models import DisclosureRecord, OutputLineage

    index = GroundingIndex.from_snapshot(snapshot)
    findings = _Findings()
    graph = SQLReferenceGraph.from_sql(compiled.sql)
    ir = validated_ir.ir

    if graph.parse_failed:
        findings.add("unparsable_sql")
        return SQLAuthorizationResult(violations=tuple(findings.violations))
    if graph.statement_count != 1:
        findings.add("non_select_statement")
    if not graph.is_read_only_select:
        findings.add("non_select_statement")
    if graph.unsafe_names:
        findings.add("unsafe_sql_source", tuple(sorted(graph.unsafe_names)))
    if graph.join_has_disjunction:
        findings.add("unsafe_join_condition")

    _authorize_sources(ir, index, graph, findings)
    _authorize_relationships(ir, index, graph, findings)
    _authorize_parameters(ir, compiled, graph, index, findings)
    _authorize_predicates(ir, index, graph, findings)
    _authorize_limit(ir, graph, caps, findings)
    _authorize_metric_roots(ir, snapshot, graph, findings)

    lineage, disclosures = _derive_lineage_and_disclosures(
        ir, index, graph, caps, findings, OutputLineage, DisclosureRecord
    )
    if findings:
        # Lineage and disclosures are evidence of an authorized query only.
        return SQLAuthorizationResult(violations=tuple(findings.violations))
    return SQLAuthorizationResult(
        violations=(), output_lineage=tuple(lineage), disclosures=tuple(disclosures)
    )


def _ir_scanned_tables(ir: Any) -> set[str]:
    return {_physical_name(node.table_id) for node in ir.nodes if node.kind == "scan"}


def _physical_name(table_id: str) -> str:
    return table_id.split(".", 1)[1] if table_id.startswith("table.") else table_id


def _ir_column_references(ir: Any) -> set[tuple[str, str]]:
    references: set[tuple[str, str]] = set()
    for node in ir.nodes:
        for expression in _node_expressions(node):
            for nested in _walk_expressions(expression):
                if getattr(nested, "kind", None) == "column":
                    references.add(
                        (_physical_name(nested.ref.table_id), nested.ref.column)
                    )
                elif getattr(nested, "kind", None) == "relative_time":
                    references.add(
                        (
                            _physical_name(nested.date_column.table_id),
                            nested.date_column.column,
                        )
                    )
        if node.kind == "join":
            continue
    for disclosure in ir.requested_disclosures:
        for column in disclosure.source_columns:
            references.add((_physical_name(column.table_id), column.column))
    for assumption in ir.assumptions:
        for column in _assumption_columns(assumption):
            references.add((_physical_name(column.table_id), column.column))
    return references


def _authorize_sources(
    ir: Any, index: GroundingIndex, graph: SQLReferenceGraph, findings: _Findings
) -> None:
    declared_tables = _ir_scanned_tables(ir)
    extra_tables = graph.tables - declared_tables
    if extra_tables:
        findings.add("ungrounded_sql_reference", tuple(sorted(extra_tables)))

    declared_columns = _ir_column_references(ir)
    # Relationship endpoints are declared by the IR join, not by an expression.
    for node in ir.nodes:
        if node.kind != "join":
            continue
        endpoints = index.relationships.get(node.relationship_id)
        if endpoints is None:
            continue
        for table_id, column in endpoints:
            declared_columns.add((_physical_name(table_id), column))
    # Metric formulas expand governed columns the IR references only by metric.
    for node in ir.nodes:
        for expression in _node_expressions(node):
            for nested in _walk_expressions(expression):
                if getattr(nested, "kind", None) != "metric":
                    continue
                declared_columns |= _metric_formula_columns(index, nested.metric_id)

    output_aliases = {alias for alias, _ in graph.output_expressions}
    for qualifier, column in graph.columns:
        table = graph.table_of(qualifier)
        if not qualifier and column in output_aliases:
            # A reference to an inner projection alias, not a physical column.
            continue
        if (table, column) in declared_columns:
            continue
        if not qualifier and any(column == name for _, name in declared_columns):
            continue
        findings.add("ungrounded_sql_reference", (f"{table}.{column}",))


def _metric_formula_columns(
    index: GroundingIndex, metric_id: str
) -> set[tuple[str, str]]:
    """Return the columns a governed metric formula may legitimately expand to."""
    columns: set[tuple[str, str]] = set()
    for table_id, column in index.columns:
        columns.add((_physical_name(table_id), column))
    return columns if metric_id in index.metric_result_types else set()


def _authorize_relationships(
    ir: Any, index: GroundingIndex, graph: SQLReferenceGraph, findings: _Findings
) -> None:
    declared: list[frozenset[tuple[str, str]]] = []
    for node in ir.nodes:
        if node.kind != "join":
            continue
        endpoints = index.relationships.get(node.relationship_id)
        if endpoints is None:
            findings.add("ungrounded_sql_reference", (node.relationship_id,))
            continue
        declared.append(
            frozenset(
                {
                    (_physical_name(endpoints[0][0]), endpoints[0][1]),
                    (_physical_name(endpoints[1][0]), endpoints[1][1]),
                }
            )
        )

    remaining = list(declared)
    for left, right in graph.join_equalities:
        observed = frozenset(
            {
                (graph.table_of(left[0]), left[1]),
                (graph.table_of(right[0]), right[1]),
            }
        )
        if observed in remaining:
            remaining.remove(observed)
            continue
        findings.add(
            "relationship_mismatch", tuple(sorted(f"{t}.{c}" for t, c in observed))
        )
    if remaining:
        findings.add("relationship_mismatch", ("missing_declared_join",))


def _ir_literal_types(ir: Any, index: GroundingIndex) -> list[str]:
    """Return the declared scalar types of IR literal refs, in traversal order."""
    types: list[str] = []
    for node in ir.nodes:
        for expression in _node_expressions(node):
            for nested in _walk_expressions(expression):
                if getattr(nested, "kind", None) == "literal":
                    types.append(_ref_declared_type(nested.ref, index))
                elif getattr(nested, "kind", None) == "relative_time":
                    types.append("integer")
        if node.kind == "limit":
            types.append("integer")
        if node.kind == "aggregate" and node.minimum_group_size is not None:
            types.append("integer")
    return types


def _ref_declared_type(ref: Any, index: GroundingIndex) -> str:
    if ref.kind == "question":
        return ref.data_type
    return index.governed_literals.get(ref.literal_id, "string")


def _authorize_parameters(
    ir: Any,
    compiled: Any,
    graph: SQLReferenceGraph,
    index: GroundingIndex,
    findings: _Findings,
) -> None:
    parameters = list(compiled.parameters)
    if graph.placeholder_count != len(parameters):
        findings.add("compiled_parameter_mismatch", ("placeholder_count",))
        return
    positions = [item.position for item in parameters]
    if positions != list(range(1, len(parameters) + 1)):
        findings.add("compiled_parameter_mismatch", ("position_order",))
        return
    # Compare declared scalar types positionally. Values are never read.
    expected = _ir_literal_types(ir, index)
    actual = [item.data_type for item in parameters]
    if len(expected) == len(actual) and expected != actual:
        findings.add("compiled_parameter_mismatch", ("type_order",))


def _authorize_predicates(
    ir: Any, index: GroundingIndex, graph: SQLReferenceGraph, findings: _Findings
) -> None:
    from sqlglot import exp

    ledger = PredicateLedger()
    for node in ir.nodes:
        if node.kind == "filter":
            for term in _ir_predicate_terms(node.predicate):
                # One data-relative window is a closed interval: it renders as
                # exactly two bound comparisons against the same scalar anchor,
                # so it authorizes two compiled terms rather than one.
                ledger.declare(term, 2 if term == "relative_time" else 1)
        if node.kind == "aggregate" and node.minimum_group_size is not None:
            ledger.declare("minimum_group_guard")

    for term in graph.predicate_terms:
        key = _compiled_predicate_key(term, graph, index)
        if key is None:
            findings.add("undeclared_filter")
            continue
        if key == "relationship":
            continue
        if not ledger.claim(key):
            findings.add("undeclared_filter")

    # An inline constant in a predicate is a compiler defect: every model literal
    # must arrive as a bound parameter.
    for literal in graph.inline_literals:
        parent = literal.parent
        if isinstance(parent, exp.Limit):
            continue
        if _literal_is_inside_predicate(literal):
            findings.add("undeclared_literal")


def _literal_is_inside_predicate(literal: Any) -> bool:
    from sqlglot import exp

    node = literal
    while node is not None:
        if isinstance(node, (exp.Where, exp.Join)):
            return True
        if isinstance(node, exp.Select):
            return False
        node = node.parent
    return False


def _ir_predicate_terms(predicate: Any) -> list[str]:
    """Flatten an IR predicate into conjunction terms keyed by their shape."""
    kind = getattr(predicate, "kind", None)
    if kind == "binary" and predicate.operator == "and":
        return _ir_predicate_terms(predicate.left) + _ir_predicate_terms(
            predicate.right
        )
    return [_ir_predicate_key(predicate)]


def _ir_predicate_key(predicate: Any) -> str:
    kind = getattr(predicate, "kind", None)
    if kind == "relative_time":
        return "relative_time"
    if kind == "binary":
        left = _operand_shape(predicate.left)
        right = _operand_shape(predicate.right)
        return f"binary:{predicate.operator}:{left}:{right}"
    if kind == "in":
        return f"in:{_operand_shape(predicate.expression)}"
    if kind == "case":
        return "case"
    return f"other:{kind}"


def _operand_shape(expression: Any) -> str:
    kind = getattr(expression, "kind", None)
    if kind == "column":
        return (
            f"column:{_physical_name(expression.ref.table_id)}.{expression.ref.column}"
        )
    if kind == "literal":
        return "literal"
    if kind == "metric":
        return f"metric:{expression.metric_id}"
    if kind == "function":
        return f"function:{expression.function}"
    return f"other:{kind}"


def _compiled_predicate_key(
    term: Any, graph: SQLReferenceGraph, index: GroundingIndex
) -> str | None:
    from sqlglot import exp

    if isinstance(term, exp.And):  # pragma: no cover - already split
        return None
    if isinstance(term, exp.In):
        operand = term.this
        if isinstance(operand, exp.Column):
            return f"in:column:{graph.table_of(operand.table or '')}.{operand.name}"
        return None
    if isinstance(term, exp.Not):
        return _compiled_predicate_key(term.this, graph, index)

    operator = _compiled_operator_map().get(type(term))
    if operator is None:
        return None
    left, right = term.this, term.expression
    left_shape = _compiled_operand_shape(left, graph)
    right_shape = _compiled_operand_shape(right, graph)
    if left_shape is None or right_shape is None:
        return None
    # A data-relative anchor renders as a scalar MAX subquery on both sides.
    if "subquery_max" in {left_shape, right_shape}:
        return "relative_time"
    if left_shape.startswith("column:") and right_shape.startswith("column:"):
        left_pair = tuple(left_shape.split(":", 1)[1].split("."))
        right_pair = tuple(right_shape.split(":", 1)[1].split("."))
        for endpoints in index.relationships.values():
            declared = {
                (_physical_name(endpoints[0][0]), endpoints[0][1]),
                (_physical_name(endpoints[1][0]), endpoints[1][1]),
            }
            if {left_pair, right_pair} == declared:
                return "relationship"
    return f"binary:{operator}:{left_shape}:{right_shape}"


def _compiled_operand_shape(node: Any, graph: SQLReferenceGraph) -> str | None:
    from sqlglot import exp

    if isinstance(node, exp.Column):
        return f"column:{graph.table_of(node.table or '')}.{node.name}"
    if isinstance(node, exp.Placeholder):
        return "literal"
    if isinstance(node, exp.Paren):
        return _compiled_operand_shape(node.this, graph)
    if isinstance(node, exp.Subquery):
        return "subquery_max" if list(node.find_all(exp.Max)) else "subquery"
    if isinstance(node, (exp.Sub, exp.Add)):
        inner = _compiled_operand_shape(node.this, graph)
        return inner if inner == "subquery_max" else "arithmetic"
    if isinstance(node, exp.Literal):
        return "constant"
    if isinstance(node, exp.Func):
        return f"function:{(node.sql_name() or type(node).__name__).lower()}"
    return None


def _authorize_limit(
    ir: Any, graph: SQLReferenceGraph, caps: DisclosureCaps, findings: _Findings
) -> None:
    sensitive_output = bool(ir.requested_disclosures)
    inline_limits = list(graph.limits)
    if sensitive_output and not inline_limits and graph.placeholder_count == 0:
        findings.add("compiled_limit_mismatch", ("missing_limit",))
    for value in inline_limits:
        if value > caps.max_result_rows:
            findings.add("compiled_limit_mismatch", ("above_result_cap",))
        elif sensitive_output and value > caps.row_limit:
            findings.add("compiled_limit_mismatch", ("above_policy_cap",))


def _authorize_metric_roots(
    ir: Any, snapshot: GroundingSnapshot, graph: SQLReferenceGraph, findings: _Findings
) -> None:
    import sqlglot
    from sqlglot import exp

    metric_outputs: dict[str, str] = {}
    for node in ir.nodes:
        if node.kind not in {"aggregate", "project"}:
            continue
        items = (
            (*node.group_by, *node.measures)
            if node.kind == "aggregate"
            else node.outputs
        )
        for item in items:
            if getattr(item.expression, "kind", None) == "metric":
                metric_outputs[item.alias] = item.expression.metric_id

    if not metric_outputs:
        return
    formulas = {
        item.object_id: item.formula
        for item in snapshot.objects
        if item.object_type == "metric" and item.formula is not None
    }
    for alias, metric_id in metric_outputs.items():
        formula = formulas.get(metric_id)
        rendered = dict(graph.output_expressions).get(alias)
        if formula is None or rendered is None:
            findings.add("metric_root_mismatch", (metric_id,))
            continue
        try:
            expected = sqlglot.parse_one(formula, dialect="duckdb")
        except Exception:  # noqa: BLE001 - sqlglot raises several error types
            findings.add("metric_root_mismatch", (metric_id,))
            continue
        actual = rendered.this if isinstance(rendered, exp.Alias) else rendered
        if not _root_shapes_match(expected, actual):
            findings.add("metric_root_mismatch", (metric_id,))


def _root_shapes_match(expected: Any, actual: Any) -> bool:
    """Compare operator trees, ignoring alias qualifiers the compiler assigns."""
    from sqlglot import exp

    if type(expected) is not type(actual):
        return False
    if isinstance(expected, exp.Column):
        return expected.name == actual.name
    if isinstance(expected, exp.Literal):
        return expected.name == actual.name
    expected_children = [
        child for child in expected.args.values() if isinstance(child, exp.Expression)
    ]
    actual_children = [
        child for child in actual.args.values() if isinstance(child, exp.Expression)
    ]
    if len(expected_children) != len(actual_children):
        return False
    return all(
        _root_shapes_match(left, right)
        for left, right in zip(expected_children, actual_children)
    )


def _derive_lineage_and_disclosures(
    ir: Any,
    index: GroundingIndex,
    graph: SQLReferenceGraph,
    caps: DisclosureCaps,
    findings: _Findings,
    lineage_model: Any,
    disclosure_model: Any,
) -> tuple[list[Any], list[Any]]:
    from .models import ColumnRef

    outputs = _root_output_items(ir)
    lineage: list[Any] = []
    disclosures: list[Any] = []
    declared_disclosures = {
        column
        for disclosure in ir.requested_disclosures
        for column in disclosure.source_columns
    }
    resolved_cap = _resolved_disclosure_cap(ir, graph, caps)

    for alias, expression in outputs:
        if expression is None:
            lineage.append(lineage_model(output_name=alias, classification="public"))
            continue
        sources = _expression_source_columns(expression)
        metrics = tuple(
            sorted(
                nested.metric_id
                for nested in _walk_expressions(expression)
                if getattr(nested, "kind", None) == "metric"
            )
        )
        if metrics:
            for metric_id in metrics:
                sources |= _metric_source_columns(index, metric_id)
        classification = _strictest_classification(index, sources)
        lineage.append(
            lineage_model(
                output_name=alias,
                source_columns=tuple(
                    sorted(sources, key=lambda ref: (ref.table_id, ref.column))
                ),
                metric_ids=metrics,
                classification=classification,
            )
        )
        if classification not in _SENSITIVE_CLASSIFICATIONS:
            continue

        aggregate = _outermost_aggregate(expression) or _metric_formula_root(
            index, expression
        )
        if aggregate in _COLLECTION_AGGREGATES:
            findings.add("sensitive_collection_aggregate", (alias,))
            continue
        if aggregate in _REDUCING_AGGREGATES:
            if aggregate != "count" and not _has_minimum_group_guard(ir):
                findings.add("missing_minimum_group_size", (alias,))
            continue
        # Value-preserving output: every contributing source must be disclosed
        # and the effective cap must be finite and within policy.
        if not sources or not sources <= declared_disclosures:
            findings.add("unbounded_sensitive_projection", (alias,))
            continue
        if resolved_cap is None or resolved_cap > caps.row_limit:
            findings.add("compiled_limit_mismatch", (alias,))
            continue
        disclosures.append(
            disclosure_model(
                output_name=alias,
                source_columns=tuple(
                    sorted(sources, key=lambda ref: (ref.table_id, ref.column))
                ),
                classification=classification,
                row_limit=resolved_cap,
            )
        )
    del ColumnRef
    return lineage, disclosures


def _root_output_items(ir: Any) -> list[tuple[str, Any]]:
    by_id = {node.node_id: node for node in ir.nodes}
    order = _topological_order(ir, by_id)
    windows: list[tuple[str, Any]] = []
    for node_id in reversed(order):
        node = by_id[node_id]
        if node.kind == "window":
            # A window output's lineage is the lineage of the value it carries.
            # An ordering-only function such as `row_number` carries no source
            # value, so it is reported with no provenance rather than omitted.
            windows = [
                (output.alias, output.argument) for output in node.outputs
            ] + windows
            continue
        if node.kind == "project":
            return [(item.alias, item.expression) for item in node.outputs] + windows
        if node.kind == "aggregate":
            return [
                (item.alias, item.expression)
                for item in (*node.group_by, *node.measures)
            ] + windows
    return windows


def _expression_source_columns(expression: Any) -> set[Any]:
    from .models import ColumnRef

    sources: set[Any] = set()
    for nested in _walk_expressions(expression):
        if getattr(nested, "kind", None) == "column":
            sources.add(
                ColumnRef(table_id=nested.ref.table_id, column=nested.ref.column)
            )
        elif getattr(nested, "kind", None) == "relative_time":
            sources.add(
                ColumnRef(
                    table_id=nested.date_column.table_id,
                    column=nested.date_column.column,
                )
            )
    return sources


def _all_sensitive_columns(index: GroundingIndex) -> set[Any]:
    from .models import ColumnRef

    return {
        ColumnRef(table_id=table_id, column=column)
        for (table_id, column) in index.columns
        if index.column_classifications.get((table_id, column))
        in _SENSITIVE_CLASSIFICATIONS
    }


def _metric_source_columns(index: GroundingIndex, metric_id: str) -> set[Any]:
    """Return the snapshot columns one governed metric formula actually reads.

    Lineage must name the columns the formula touches, not every sensitive
    column in the snapshot: an unrelated restricted column in a neighbouring
    table would otherwise over-classify every metric and demand guards the
    query does not need. When the formula is absent, unparsable, or references a
    column this snapshot cannot resolve, the conservative whole-snapshot
    sensitive set is used instead, because an unresolved reference is exactly
    the case where under-reporting would be unsafe.
    """
    from .models import ColumnRef

    if metric_id not in index.metric_result_types:
        return set()
    formula = index.metric_formulas.get(metric_id)
    if not formula:
        return _all_sensitive_columns(index)

    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse_one(formula, dialect="duckdb")
    except Exception:  # noqa: BLE001 - sqlglot raises several error types
        return _all_sensitive_columns(index)

    resolved: set[tuple[str, str]] = set()
    for column in parsed.find_all(exp.Column):
        name = column.name
        matched: tuple[str, str] | None = None
        for part in (column.table, column.db, column.catalog):
            if not part:
                continue
            for table_id in (f"table.{part}", part):
                if (table_id, name) in index.columns:
                    matched = (table_id, name)
                    break
            if matched is not None:
                break
        if matched is None:
            return _all_sensitive_columns(index)
        resolved.add(matched)
    return {
        ColumnRef(table_id=table_id, column=column) for table_id, column in resolved
    }


def _strictest_classification(index: GroundingIndex, sources: set[Any]) -> str:
    order = ["public", "internal", "confidential", "restricted"]
    strictest = "public"
    for ref in sources:
        classification = index.column_classifications.get(
            (ref.table_id, ref.column), "internal"
        )
        if order.index(classification) > order.index(strictest):
            strictest = classification
    return strictest


def _metric_formula_root(index: GroundingIndex, expression: Any) -> str | None:
    """Return the aggregate at the root of a governed metric formula."""
    if getattr(expression, "kind", None) != "metric":
        return None
    formula = index.metric_formulas.get(expression.metric_id)
    if not formula:
        return None
    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse_one(formula, dialect="duckdb")
    except Exception:  # noqa: BLE001 - sqlglot raises several error types
        return None
    for node in parsed.walk():
        if isinstance(node, exp.AggFunc):
            return (node.sql_name() or type(node).__name__).lower()
    return None


def _outermost_aggregate(expression: Any) -> str | None:
    kind = getattr(expression, "kind", None)
    if kind == "function":
        return expression.function.lower()
    return None


def _has_minimum_group_guard(ir: Any) -> bool:
    return any(
        node.kind == "aggregate" and node.minimum_group_size is not None
        for node in ir.nodes
    )


def _resolved_disclosure_cap(
    ir: Any, graph: SQLReferenceGraph, caps: DisclosureCaps
) -> int | None:
    if graph.limits:
        return min(graph.limits)
    if any(node.kind == "limit" for node in ir.nodes):
        # The limit is a bound parameter; the compiler already tightened it to
        # the trusted cap, so the policy cap is the authoritative upper bound.
        return caps.row_limit
    return None
