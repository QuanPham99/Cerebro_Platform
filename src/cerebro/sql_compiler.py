"""Local literal resolution and deterministic IR-to-parameterized-SQL compilation.

The compiler accepts only `ValidatedIR`, recomputes every binding hash before it
emits anything, and builds SQL through `sqlglot.exp` nodes rather than string
concatenation. Resolved literal values stay in compiler-local memory and reach
the executor only as positional `BoundParameter` entries.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date as _Date
from datetime import datetime as _DateTime
from typing import Any

import sqlglot
from sqlglot import exp

from .models import (
    BoundParameter,
    CompiledQuery,
    GroundingSnapshot,
    SQLArtifact,
    ValidatedIR,
    relational_ir_sha256,
)
from .provenance import canonical_question_sha256

_COMPILER_VERSION = "008.compiler.v1"
_DIALECT = "duckdb"

_TOKEN_BREAK_CHARACTERS = frozenset(" ,;()[]{}?!")
_INTEGER_TEXT = re.compile(r"^[+-]?[0-9]+$")
_DECIMAL_TEXT = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$")
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$")

# Exact cardinal registry for `008.literal-span.v1`: single-token words only.
_CARDINALS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_AGGREGATE_BUILDERS: dict[str, type[exp.Expression]] = {
    "count": exp.Count,
    "sum": exp.Sum,
    "avg": exp.Avg,
    "min": exp.Min,
    "max": exp.Max,
    "stddev": exp.Stddev,
    "variance": exp.Variance,
}
_BINARY_BUILDERS: dict[str, type[exp.Expression]] = {
    "eq": exp.EQ,
    "neq": exp.NEQ,
    "lt": exp.LT,
    "lte": exp.LTE,
    "gt": exp.GT,
    "gte": exp.GTE,
    "and": exp.And,
    "or": exp.Or,
    "add": exp.Add,
    "subtract": exp.Sub,
    "multiply": exp.Mul,
    "divide": exp.Div,
}
_WINDOW_BUILDERS: dict[str, type[exp.Expression]] = {
    "lag": exp.Lag,
    "lead": exp.Lead,
    "row_number": exp.RowNumber,
    "rank": exp.Anonymous,
    "dense_rank": exp.Anonymous,
}
_UNIT_TO_INTERVAL = {
    "day": "DAY",
    "week": "WEEK",
    "month": "MONTH",
    "quarter": "QUARTER",
    "year": "YEAR",
}
# Positive-only operands: a non-positive value here is a semantic bound failure.
_POSITIVE_BOUND_ROLES = frozenset({"limit", "relative_amount", "minimum_group_size"})


class CompilerError(Exception):
    """A sanitized compilation failure. It carries no value, question, or SQL."""

    def __init__(self, code: str, node_id: str = "") -> None:
        super().__init__(f"{code} at {node_id}" if node_id else code)
        self.code = code
        self.node_id = node_id


@dataclass(frozen=True)
class ResolvedLiteral:
    """A compiler-local resolved constant. It never enters a public model."""

    data_type: str
    value: Any


def compiler_version() -> str:
    """Return the version that changes with rendering or literal semantics."""
    return _COMPILER_VERSION


def _span_token(canonical_question: str, start: int, end: int) -> str:
    if start < 0 or end <= start or end > len(canonical_question):
        raise CompilerError("invalid_literal_reference")
    token = canonical_question[start:end]
    if not token or token[0] == " " or token[-1] == " ":
        raise CompilerError("invalid_literal_reference")
    is_quoted = len(token) >= 2 and token[0] == token[-1] and token[0] in "'\""
    if not is_quoted and any(
        character in _TOKEN_BREAK_CHARACTERS for character in token
    ):
        raise CompilerError("invalid_literal_reference")
    before = canonical_question[start - 1] if start > 0 else " "
    after = canonical_question[end] if end < len(canonical_question) else " "
    if before not in _TOKEN_BREAK_CHARACTERS and before not in "'\"":
        raise CompilerError("invalid_literal_reference")
    if after not in _TOKEN_BREAK_CHARACTERS and after not in "'\"":
        raise CompilerError("invalid_literal_reference")
    return token[1:-1] if is_quoted else token


def _parse_scalar(text: str, data_type: str) -> Any:
    if data_type == "string":
        return unicodedata.normalize("NFC", text)
    if data_type == "integer":
        if _INTEGER_TEXT.match(text):
            return int(text)
        cardinal = _CARDINALS.get(text.lower())
        if cardinal is None:
            raise CompilerError("invalid_literal_type")
        return cardinal
    if data_type == "decimal":
        if not _DECIMAL_TEXT.match(text):
            raise CompilerError("invalid_literal_type")
        return float(text)
    if data_type == "boolean":
        lowered = text.lower()
        if lowered not in {"true", "false"}:
            raise CompilerError("invalid_literal_type")
        return lowered == "true"
    if data_type == "date":
        if not _DATE_TEXT.match(text):
            raise CompilerError("invalid_literal_type")
        try:
            _Date.fromisoformat(text)
        except ValueError as error:
            raise CompilerError("invalid_literal_type") from error
        return text
    if data_type == "timestamp":
        if not _TIMESTAMP_TEXT.match(text):
            raise CompilerError("invalid_literal_type")
        try:
            parsed = _DateTime.fromisoformat(text)
        except ValueError as error:
            raise CompilerError("invalid_literal_type") from error
        if parsed.tzinfo is not None:
            raise CompilerError("invalid_literal_type")
        return text
    raise CompilerError("invalid_literal_type")


class LiteralResolver:
    """Resolve one literal ref without ever exposing the value publicly."""

    def resolve(
        self,
        ref: Any,
        canonical_question: str,
        snapshot: GroundingSnapshot,
        expected_type: str,
        *,
        role: str = "operand",
    ) -> ResolvedLiteral:
        if ref.kind == "question":
            if ref.data_type != expected_type:
                raise CompilerError("invalid_literal_type")
            token = _span_token(canonical_question, ref.start, ref.end)
            value = _parse_scalar(token, expected_type)
        elif ref.kind == "governed":
            declared = {
                literal.literal_id: literal for literal in snapshot.governed_literals
            }.get(ref.literal_id)
            if declared is None:
                raise CompilerError("ungrounded_literal_reference")
            if declared.data_type != expected_type:
                raise CompilerError("invalid_literal_type")
            value = declared.value
        else:  # pragma: no cover - the contract admits no other ref kind
            raise CompilerError("ungrounded_literal_reference")

        if role in _POSITIVE_BOUND_ROLES and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
        ):
            raise CompilerError("invalid_literal_bound")
        return ResolvedLiteral(data_type=expected_type, value=value)


class _CompilationState:
    """Deterministic alias allocation and positional parameter accumulation."""

    def __init__(self) -> None:
        self.table_aliases: dict[str, str] = {}
        self.alias_by_table: dict[str, str] = {}
        self.parameters: list[BoundParameter] = []

    def alias_for(self, node_id: str, table_id: str = "") -> str:
        if node_id not in self.table_aliases:
            alias = f"t{len(self.table_aliases)}"
            self.table_aliases[node_id] = alias
            # The first scan of a table owns the alias that unqualified column
            # references resolve to, which keeps rendering deterministic.
            if table_id and table_id not in self.alias_by_table:
                self.alias_by_table[table_id] = alias
        return self.table_aliases[node_id]

    def bind(self, resolved: ResolvedLiteral) -> exp.Expression:
        self.parameters.append(
            BoundParameter(
                position=len(self.parameters) + 1,
                data_type=resolved.data_type,
                value=resolved.value,
            )
        )
        return exp.Placeholder()


class DialectCompiler:
    """Compile one validated IR into deterministic parameterized DuckDB SQL."""

    def __init__(self, dialect: str = _DIALECT) -> None:
        if dialect != _DIALECT:
            raise CompilerError("unsupported_dialect")
        self.dialect = dialect
        self._resolver = LiteralResolver()

    def compile(
        self,
        validated_ir: ValidatedIR,
        snapshot: GroundingSnapshot,
        canonical_question: str,
        max_rows: int,
    ) -> CompiledQuery:
        if not isinstance(validated_ir, ValidatedIR):
            raise CompilerError("validated_ir_integrity_error")
        self._check_integrity(validated_ir, snapshot, canonical_question)

        ir = validated_ir.ir
        by_id = {node.node_id: node for node in ir.nodes}
        if validated_ir.generation_route != "planned_ir":
            for node in ir.nodes:
                if node.kind in {"window", "set_operation"}:
                    raise CompilerError(
                        "complex_node_requires_planned_route", node.node_id
                    )

        state = _CompilationState()
        query = self._compile_node(
            ir.root_node_id, by_id, snapshot, canonical_question, state
        )
        query = self._apply_effective_limit(
            query, ir, by_id, snapshot, canonical_question, state, max_rows
        )
        sql = query.sql(dialect=self.dialect)
        return CompiledQuery(
            sql=sql,
            parameters=tuple(state.parameters),
            ir_hash=relational_ir_sha256(ir),
            compiler_version=_COMPILER_VERSION,
            dialect=_DIALECT,
        )

    # -- integrity -------------------------------------------------------
    def _check_integrity(
        self,
        validated_ir: ValidatedIR,
        snapshot: GroundingSnapshot,
        canonical_question: str,
    ) -> None:
        try:
            recomputed_ir = relational_ir_sha256(validated_ir.ir)
        except TypeError as error:
            raise CompilerError("validated_ir_integrity_error") from error
        if recomputed_ir != validated_ir.ir_hash:
            raise CompilerError("validated_ir_integrity_error")
        if snapshot.snapshot_hash != validated_ir.snapshot_hash:
            raise CompilerError("validated_ir_integrity_error")
        try:
            question_hash = canonical_question_sha256(canonical_question)
        except ValueError as error:
            raise CompilerError("validated_ir_integrity_error") from error
        if question_hash != validated_ir.canonical_question_hash:
            raise CompilerError("validated_ir_integrity_error")
        if validated_ir.generation_route == "planned_ir":
            if validated_ir.accepted_complex_plan_hash is None:
                raise CompilerError("validated_ir_integrity_error")
        elif validated_ir.accepted_complex_plan_hash is not None:
            raise CompilerError("validated_ir_integrity_error")

    # -- node compilation -------------------------------------------------
    def _compile_node(
        self,
        node_id: str,
        by_id: dict[str, Any],
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
    ) -> exp.Expression:
        node = by_id.get(node_id)
        if node is None:
            raise CompilerError("orphan_compiler_node", node_id)
        kind = getattr(node, "kind", "")

        if kind == "set_operation":
            left = self._compile_node(
                node.left_id, by_id, snapshot, canonical_question, state
            )
            right = self._compile_node(
                node.right_id, by_id, snapshot, canonical_question, state
            )
            builder = {
                "union": exp.Union,
                "intersect": exp.Intersect,
                "except": exp.Except,
            }[node.operator]
            return builder(this=left, expression=right, distinct=not node.all)

        if kind not in {
            "scan",
            "join",
            "filter",
            "aggregate",
            "project",
            "sort",
            "limit",
            "window",
        }:
            raise CompilerError(
                "unsupported_compiler_node", getattr(node, "node_id", "")
            )

        pipeline = self._linear_pipeline(node_id, by_id)
        return self._compile_pipeline(
            pipeline, by_id, snapshot, canonical_question, state
        )

    @staticmethod
    def _linear_pipeline(node_id: str, by_id: dict[str, Any]) -> list[Any]:
        """Return the chain from the root down to its scan/join subtree."""
        chain: list[Any] = []
        current = node_id
        while True:
            node = by_id.get(current)
            if node is None:
                raise CompilerError("orphan_compiler_node", current)
            chain.append(node)
            if node.kind in {"scan", "join", "set_operation"}:
                break
            current = node.input_id
        chain.reverse()
        return chain

    def _compile_pipeline(
        self,
        pipeline: list[Any],
        by_id: dict[str, Any],
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
    ) -> exp.Expression:
        base = pipeline[0]
        if base.kind == "set_operation":
            inner = self._compile_node(
                base.node_id, by_id, snapshot, canonical_question, state
            )
            select = exp.select("*").from_(exp.Subquery(this=inner, alias="q0"))
        else:
            select = self._compile_source(base, by_id, snapshot, state)

        projections: list[exp.Expression] = []
        group_keys: list[exp.Expression] = []
        for node in pipeline[1:]:
            kind = node.kind
            if kind == "filter":
                select = select.where(
                    self._expression(
                        node.predicate, snapshot, canonical_question, state
                    )
                )
            elif kind == "aggregate":
                projections = []
                group_keys = []
                for item in node.group_by:
                    rendered = self._expression(
                        item.expression, snapshot, canonical_question, state
                    )
                    group_keys.append(rendered)
                    projections.append(exp.alias_(rendered, item.alias))
                for item in node.measures:
                    projections.append(
                        exp.alias_(
                            self._expression(
                                item.expression, snapshot, canonical_question, state
                            ),
                            item.alias,
                        )
                    )
            elif kind == "project":
                projections = [
                    exp.alias_(
                        self._expression(
                            item.expression, snapshot, canonical_question, state
                        ),
                        item.alias,
                    )
                    for item in node.outputs
                ]
            elif kind == "window":
                projections = list(projections) + [
                    exp.alias_(
                        self._window_expression(
                            output, snapshot, canonical_question, state
                        ),
                        output.alias,
                    )
                    for output in node.outputs
                ]
            elif kind == "sort":
                select = select.order_by(
                    *[
                        self._sort_key(key, snapshot, canonical_question, state)
                        for key in node.keys
                    ]
                )
            elif kind == "limit":
                continue
            else:  # pragma: no cover - the allowlist above is exhaustive
                raise CompilerError("unsupported_compiler_node", node.node_id)

        if projections:
            select = select.select(*projections, append=False)
        if group_keys:
            select = select.group_by(*group_keys)
        return select

    def _compile_source(
        self,
        base: Any,
        by_id: dict[str, Any],
        snapshot: GroundingSnapshot,
        state: _CompilationState,
    ) -> exp.Select:
        if base.kind == "scan":
            alias = state.alias_for(base.node_id, base.table_id)
            return exp.select().from_(
                exp.Table(
                    this=exp.to_identifier(_table_name(base.table_id)), alias=alias
                )
            )

        # A join chain: the deepest left side becomes FROM, then each join adds
        # its right-side scan with the exact predicate its relationship declares.
        chain: list[Any] = []
        current = base
        while current.kind == "join":
            chain.append(current)
            left = by_id.get(current.left_id)
            if left is None:
                raise CompilerError("orphan_compiler_node", current.left_id)
            current = left
        if current.kind != "scan":
            raise CompilerError("unsupported_compiler_node", current.node_id)
        chain.reverse()

        select = exp.select().from_(
            exp.Table(
                this=exp.to_identifier(_table_name(current.table_id)),
                alias=state.alias_for(current.node_id, current.table_id),
            )
        )
        for join_node in chain:
            right = by_id.get(join_node.right_id)
            if right is None:
                raise CompilerError("orphan_compiler_node", join_node.right_id)
            if right.kind != "scan":
                raise CompilerError("unsupported_compiler_node", right.node_id)
            state.alias_for(right.node_id, right.table_id)
            select = select.join(
                exp.Table(
                    this=exp.to_identifier(_table_name(right.table_id)),
                    alias=state.table_aliases[right.node_id],
                ),
                on=self._join_condition(join_node.relationship_id, snapshot, state),
                join_type=join_node.join_type,
            )
        return select

    def _join_condition(
        self,
        relationship_id: str,
        snapshot: GroundingSnapshot,
        state: _CompilationState,
    ) -> exp.Expression:
        for item in snapshot.objects:
            for relationship in item.relationships:
                if relationship.relationship_id != relationship_id:
                    continue
                # The predicate comes only from the snapshot relationship, so a
                # model cannot invent a join condition.
                return exp.EQ(
                    this=self._qualified(relationship.left, state),
                    expression=self._qualified(relationship.right, state),
                )
        raise CompilerError("ungrounded_relationship_reference", relationship_id)

    @staticmethod
    def _qualified(ref: Any, state: _CompilationState) -> exp.Column:
        alias = state.alias_by_table.get(ref.table_id)
        if alias is None:
            raise CompilerError("ungrounded_ir_reference", ref.table_id)
        return exp.column(ref.column, table=alias)

    # -- expressions ------------------------------------------------------
    def _expression(
        self,
        expression: Any,
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
        *,
        expected_type: str | None = None,
    ) -> exp.Expression:
        kind = getattr(expression, "kind", None)
        if kind == "column":
            return self._column(expression.ref, state)
        if kind == "metric":
            return self._metric(expression.metric_id, snapshot, state)
        if kind == "literal":
            resolved = self._resolver.resolve(
                expression.ref,
                canonical_question,
                snapshot,
                expected_type or _ref_type(expression.ref, snapshot),
            )
            return state.bind(resolved)
        if kind == "function":
            return self._function(expression, snapshot, canonical_question, state)
        if kind == "binary":
            builder = _BINARY_BUILDERS[expression.operator]
            left = self._expression(
                expression.left, snapshot, canonical_question, state
            )
            right = self._expression(
                expression.right,
                snapshot,
                canonical_question,
                state,
                expected_type=_operand_type(expression.left, snapshot),
            )
            return builder(this=left, expression=right)
        if kind == "in":
            operand = self._expression(
                expression.expression, snapshot, canonical_question, state
            )
            values = [
                self._expression(
                    value,
                    snapshot,
                    canonical_question,
                    state,
                    expected_type=_operand_type(expression.expression, snapshot),
                )
                for value in expression.values
            ]
            rendered: exp.Expression = exp.In(this=operand, expressions=values)
            return exp.Not(this=rendered) if expression.negated else rendered
        if kind == "case":
            case = exp.Case()
            for branch in expression.branches:
                case = case.when(
                    self._expression(branch.when, snapshot, canonical_question, state),
                    self._expression(branch.then, snapshot, canonical_question, state),
                )
            return case.else_(
                self._expression(
                    expression.else_expression, snapshot, canonical_question, state
                )
            )
        if kind == "relative_time":
            return self._relative_time(expression, snapshot, canonical_question, state)
        raise CompilerError("unsupported_compiler_node")

    def _column(self, ref: Any, state: _CompilationState) -> exp.Column:
        alias = state.alias_by_table.get(ref.table_id)
        if alias is not None:
            return exp.column(ref.column, table=alias)
        # A projected alias from an inner scope has no table qualifier.
        return exp.column(ref.column)

    def _metric(
        self, metric_id: str, snapshot: GroundingSnapshot, state: _CompilationState
    ) -> exp.Expression:
        metric = next(
            (item for item in snapshot.objects if item.object_id == metric_id), None
        )
        if metric is None or metric.formula is None:
            raise CompilerError("ungrounded_metric_reference", metric_id)
        try:
            parsed = sqlglot.parse_one(metric.formula, dialect=self.dialect)
        except Exception as error:
            raise CompilerError("invalid_metric_formula", metric_id) from error
        # The formula is governed text: only its column references are rewritten
        # onto this query's aliases, never its operators or structure.
        for column in list(parsed.find_all(exp.Column)):
            # A governed formula may qualify a column as `table.<name>.<column>`,
            # which parses into catalog and db parts. Requalify onto this query's
            # alias and clear the leftovers, otherwise the engine sees
            # `"table".t0.amount` and cannot bind it.
            candidates = [
                part for part in (column.table, column.db, column.catalog) if part
            ]
            alias = None
            for candidate in candidates:
                alias = state.alias_by_table.get(
                    f"table.{candidate}"
                ) or state.alias_by_table.get(candidate)
                if alias is not None:
                    break
            if alias is None:
                continue
            column.set("catalog", None)
            column.set("db", None)
            column.set("table", exp.to_identifier(alias))
        return parsed

    def _function(
        self,
        expression: Any,
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
    ) -> exp.Expression:
        name = expression.function
        arguments = [
            self._expression(argument, snapshot, canonical_question, state)
            for argument in expression.arguments
        ]
        if name in _AGGREGATE_BUILDERS:
            if not arguments:
                # Only `count` reaches here with no argument; the type registry
                # rejects every other aggregate without one.
                return exp.Count(this=exp.Star())
            return _AGGREGATE_BUILDERS[name](this=arguments[0])
        if name == "coalesce":
            return exp.Coalesce(this=arguments[0], expressions=arguments[1:])
        if name == "nullif":
            return exp.Nullif(this=arguments[0], expression=arguments[1])
        if name == "date_trunc":
            return exp.DateTrunc(unit=exp.Literal.string("month"), this=arguments[0])
        raise CompilerError("unsupported_compiler_node")

    def _relative_time(
        self,
        expression: Any,
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
    ) -> exp.Expression:
        resolved = self._resolver.resolve(
            expression.amount_ref,
            canonical_question,
            snapshot,
            "integer",
            role="relative_amount",
        )
        column = self._column(expression.date_column, state)
        # Data-relative by construction: the anchor is MAX(column) over the same
        # table, never a wall-clock function, so a stale corpus cannot silently
        # shift results. It must be a scalar subquery because an engine rejects a
        # bare aggregate inside WHERE.
        anchor = exp.Subquery(
            this=exp.select(
                exp.Max(this=exp.column(expression.date_column.column))
            ).from_(
                exp.Table(
                    this=exp.to_identifier(_table_name(expression.date_column.table_id))
                )
            )
        )
        lower = exp.Sub(
            this=anchor,
            expression=exp.Interval(
                this=state.bind(resolved),
                unit=exp.var(_UNIT_TO_INTERVAL[expression.unit]),
            ),
        )
        left_operator = exp.GTE if expression.lower_inclusive else exp.GT
        right_operator = exp.LTE if expression.upper_inclusive else exp.LT
        return exp.And(
            this=left_operator(this=column, expression=lower),
            expression=right_operator(this=column.copy(), expression=anchor.copy()),
        )

    def _window_expression(
        self,
        output: Any,
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
    ) -> exp.Expression:
        builder = _WINDOW_BUILDERS[output.function]
        argument = (
            self._expression(output.argument, snapshot, canonical_question, state)
            if output.argument is not None
            else None
        )
        if builder is exp.Anonymous:
            function: exp.Expression = exp.Anonymous(this=output.function.upper())
        elif argument is None:
            function = builder()
        else:
            function = builder(this=argument)
        return exp.Window(
            this=function,
            partition_by=[
                self._expression(item, snapshot, canonical_question, state)
                for item in output.partition_by
            ],
            order=exp.Order(
                expressions=[
                    self._sort_key(key, snapshot, canonical_question, state)
                    for key in output.order_by
                ]
            )
            if output.order_by
            else None,
        )

    def _sort_key(
        self,
        key: Any,
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
    ) -> exp.Ordered:
        return exp.Ordered(
            this=self._expression(key.expression, snapshot, canonical_question, state),
            desc=key.direction == "desc",
            nulls_first=key.nulls == "first",
        )

    # -- row limit --------------------------------------------------------
    def _apply_effective_limit(
        self,
        query: exp.Expression,
        ir: Any,
        by_id: dict[str, Any],
        snapshot: GroundingSnapshot,
        canonical_question: str,
        state: _CompilationState,
        max_rows: int,
    ) -> exp.Expression:
        if max_rows <= 0:
            raise CompilerError("invalid_row_cap")
        declared: int | None = None
        for node in ir.nodes:
            if node.kind != "limit":
                continue
            resolved = self._resolver.resolve(
                node.count, canonical_question, snapshot, "integer", role="limit"
            )
            declared = (
                int(resolved.value)
                if declared is None
                else min(declared, int(resolved.value))
            )
        # The effective cap is always the tightest of the trusted request cap and
        # any resolved IR limit, so omitting a limit cannot widen a disclosure.
        if declared is None:
            # A trusted deployment cap is local configuration, not a model
            # literal, so it renders inline rather than as a bound parameter.
            return query.limit(exp.Literal.number(max_rows))
        effective = min(max_rows, declared)
        placeholder = state.bind(ResolvedLiteral(data_type="integer", value=effective))
        return query.limit(placeholder)


def _table_name(table_id: str) -> str:
    return table_id.split(".", 1)[1] if table_id.startswith("table.") else table_id


def _ref_type(ref: Any, snapshot: GroundingSnapshot) -> str:
    if ref.kind == "question":
        return ref.data_type
    for literal in snapshot.governed_literals:
        if literal.literal_id == ref.literal_id:
            return literal.data_type
    raise CompilerError("ungrounded_literal_reference")


def _operand_type(expression: Any, snapshot: GroundingSnapshot) -> str | None:
    kind = getattr(expression, "kind", None)
    if kind == "column":
        for item in snapshot.objects:
            for column in item.columns:
                if (
                    column.ref.table_id == expression.ref.table_id
                    and column.ref.column == expression.ref.column
                ):
                    return column.data_type
    if kind == "metric":
        for item in snapshot.objects:
            if item.object_id == expression.metric_id:
                return item.metric_result_type
    return None


def to_sql_artifact(compiled: CompiledQuery) -> SQLArtifact:
    """Copy SQL, hashes, and parameter shape, but never a parameter value."""
    import hashlib

    return SQLArtifact(
        sql=compiled.sql,
        sql_sha256=hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest(),
        parameter_count=len(compiled.parameters),
        parameter_types=tuple(item.data_type for item in compiled.parameters),
        ir_hash=compiled.ir_hash,
        compiler_version=compiled.compiler_version,
        dialect=compiled.dialect,
    )
