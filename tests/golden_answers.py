"""Deterministic golden IR outcomes for the offline reference run.

Every answer here is a typed, value-free `IRGenerationOutcome`. No SQL string,
no resolved literal, and no expected result value is stored: those belong to the
compiler and executor assertions, because a fixture that carried them would be
asserting its own output instead of the production compiler's.

This module never imports `cerebro.enrichment`. The enrichment flow builds the
semantic bundle and has no authority over query generation.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cerebro.evaluation import ReferenceQuestion, load_reference_questions
from cerebro.models import (
    AggregateNode,
    BinaryExpression,
    ColumnExpression,
    ColumnRef,
    ComplexPlanStep,
    ComplexQueryPlan,
    FilterNode,
    FunctionExpression,
    GuardedGenerationRequest,
    JoinNode,
    LimitNode,
    LiteralExpression,
    MetricExpression,
    NamedExpression,
    ProjectNode,
    QuestionLiteralRef,
    RelationalQueryIR,
    RelativeTimeExpression,
    RequestedDisclosure,
    ScanNode,
    SortKey,
    SortNode,
    WindowExpression,
    WindowNode,
)
from cerebro.provenance import canonical_question_sha256
from cerebro.text2sql_provider import (
    ProviderGeneration,
    ProviderRejected,
    ProviderUsage,
    TransportAttempt,
)

#: The single explicit fixture scope the offline reference runs under.
REFERENCE_OBJECT_IDS: tuple[str, ...] = (
    "metric.card-fraud-rate",
    "metric.transaction-volume",
    "policy.sensitive-banking-data",
    "relationship.account_branch",
    "relationship.card_transaction_card",
    "relationship.transaction_account",
    "table.accounts",
    "table.branches",
    "table.card_transactions",
    "table.cards",
    "table.customers",
    "table.transactions",
)

REFERENCE_POLICY_VERSION = "policy.reference.v1"
REFERENCE_TENANT_SCOPE_HASH = "1" * 64

#: Governed metric result types the read-only workshop bundle does not declare.
#:
#: `knowledge/bank-workshop/` is read-only input owned by another workstream and
#: none of its metric cards author `metric_result_type`, which `GroundingResolver`
#: requires and refuses to infer from a formula. The reference run therefore
#: materializes a local copy of the bundle and authors the declaration there.
#: Nothing in the governed bundle is modified.
MISSING_METRIC_RESULT_TYPES: Mapping[str, str] = {
    "metric.card-fraud-rate": "decimal",
    "metric.late-payment-rate": "decimal",
    "metric.non-performing-loan-rate": "decimal",
    "metric.transaction-volume": "decimal",
}
_DEFAULT_METRIC_RESULT_TYPE = "decimal"

_FORMULA_LINE = re.compile(r"^(?P<indent>\s*)formula:\s", re.MULTILINE)


def materialize_reference_bundle(source: Path | str, destination: Path | str) -> Path:
    """Copy an OKF bundle and author the metric result types it omits.

    This is fixture materialization, not a bundle edit: the source tree is only
    read. It exists so the offline reference can exercise metric fidelity while
    the governed bundle's missing declaration is resolved upstream.
    """
    source_root = Path(source)
    target = Path(destination)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source_root, target)
    for card in sorted((target / "metrics").glob("*.md")):
        text = card.read_text(encoding="utf-8")
        if "metric_result_type:" in text:
            continue
        match = _FORMULA_LINE.search(text)
        if match is None:
            continue
        indent = match.group("indent")
        identifier = _declared_id(text)
        declared = MISSING_METRIC_RESULT_TYPES.get(
            identifier, _DEFAULT_METRIC_RESULT_TYPE
        )
        insertion = f"{indent}metric_result_type: {declared}\n"
        card.write_text(
            text[: match.start()] + insertion + text[match.start() :],
            encoding="utf-8",
        )
    return target


def _declared_id(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("id:"):
            return line.split(":", 1)[1].strip()
    return ""


# --- span helpers -----------------------------------------------------------


def question_span(canonical_question: str, token: str, data_type: str):
    """Derive one `QuestionLiteralRef` from an exact code-point offset."""
    occurrences = [
        match.start()
        for match in re.finditer(f"(?={re.escape(token)})", canonical_question)
    ]
    if len(occurrences) != 1:
        raise ProviderRejected(
            f"token must occur exactly once in the canonical question: {token!r}"
        )
    start = occurrences[0]
    return QuestionLiteralRef(
        kind="question", start=start, end=start + len(token), data_type=data_type
    )


def _column(table_id: str, column: str) -> ColumnExpression:
    return ColumnExpression(
        kind="column", ref=ColumnRef(table_id=table_id, column=column)
    )


# --- golden outcomes --------------------------------------------------------


def metric_fidelity_ir(request: GuardedGenerationRequest) -> RelationalQueryIR:
    """Card fraud rate per card type: one governed metric over a declared join."""
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="aggregate_fraud_rate",
        nodes=(
            ScanNode(
                kind="scan",
                node_id="scan_card_transactions",
                table_id="table.card_transactions",
            ),
            ScanNode(kind="scan", node_id="scan_cards", table_id="table.cards"),
            JoinNode(
                kind="join",
                node_id="join_card_transaction_card",
                left_id="scan_card_transactions",
                right_id="scan_cards",
                relationship_id="relationship.card_transaction_card",
                join_type="inner",
            ),
            AggregateNode(
                kind="aggregate",
                node_id="aggregate_fraud_rate",
                input_id="join_card_transaction_card",
                group_by=(
                    NamedExpression(
                        alias="card_type",
                        expression=_column("table.cards", "card_type"),
                    ),
                ),
                measures=(
                    NamedExpression(
                        alias="card_fraud_rate",
                        expression=MetricExpression(
                            kind="metric", metric_id="metric.card-fraud-rate"
                        ),
                    ),
                ),
            ),
        ),
    )


def two_hop_join_ir(request: GuardedGenerationRequest) -> RelationalQueryIR:
    """Transaction volume per branch across two declared relationships."""
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="aggregate_branch_volume",
        nodes=(
            ScanNode(
                kind="scan",
                node_id="scan_transactions",
                table_id="table.transactions",
            ),
            ScanNode(kind="scan", node_id="scan_accounts", table_id="table.accounts"),
            ScanNode(kind="scan", node_id="scan_branches", table_id="table.branches"),
            JoinNode(
                kind="join",
                node_id="join_transaction_account",
                left_id="scan_transactions",
                right_id="scan_accounts",
                relationship_id="relationship.transaction_account",
                join_type="inner",
            ),
            JoinNode(
                kind="join",
                node_id="join_account_branch",
                left_id="join_transaction_account",
                right_id="scan_branches",
                relationship_id="relationship.account_branch",
                join_type="inner",
            ),
            AggregateNode(
                kind="aggregate",
                node_id="aggregate_branch_volume",
                input_id="join_account_branch",
                group_by=(
                    NamedExpression(
                        alias="branch_name",
                        expression=_column("table.branches", "branch_name"),
                    ),
                ),
                measures=(
                    NamedExpression(
                        alias="transaction_volume",
                        expression=MetricExpression(
                            kind="metric", metric_id="metric.transaction-volume"
                        ),
                    ),
                ),
            ),
        ),
    )


def relative_time_plan(request: GuardedGenerationRequest) -> ComplexQueryPlan:
    """The first scripted call escalates: growth needs period-over-period."""
    return ComplexQueryPlan(
        outcome="complex_plan",
        plan_version="008.complex-plan.v1",
        operator_ids=("window.period_over_period.v1",),
        steps=(
            ComplexPlanStep(
                step_id="monthly_growth",
                operator_id="window.period_over_period.v1",
                depends_on=(),
                input_object_ids=(
                    "table.transactions",
                    "metric.transaction-volume",
                ),
                output_names=("txn_date", "transaction_volume", "growth"),
            ),
        ),
        expected_outputs=("txn_date", "transaction_volume", "growth"),
    )


def relative_time_ir(request: GuardedGenerationRequest) -> RelationalQueryIR:
    """The conforming fallback IR: `LAG` over the monthly governed metric."""
    amount_ref = question_span(request.canonical_question, "24", "integer")
    month = FunctionExpression(
        kind="function",
        function="date_trunc",
        arguments=(_column("table.transactions", "txn_date"),),
    )
    volume = MetricExpression(kind="metric", metric_id="metric.transaction-volume")
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="window_monthly_growth",
        nodes=(
            ScanNode(
                kind="scan",
                node_id="scan_transactions",
                table_id="table.transactions",
            ),
            FilterNode(
                kind="filter",
                node_id="filter_recent_months",
                input_id="scan_transactions",
                predicate=RelativeTimeExpression(
                    kind="relative_time",
                    date_column=ColumnRef(
                        table_id="table.transactions", column="txn_date"
                    ),
                    anchor="data_max",
                    amount_ref=amount_ref,
                    unit="month",
                    lower_inclusive=True,
                    upper_inclusive=True,
                ),
            ),
            AggregateNode(
                kind="aggregate",
                node_id="aggregate_monthly_volume",
                input_id="filter_recent_months",
                # `008.ir.v1` expresses a post-aggregate reference by the
                # underlying column name, so the truncated grouping key keeps
                # the `txn_date` alias the window below must order by.
                group_by=(NamedExpression(alias="txn_date", expression=month),),
                measures=(
                    NamedExpression(alias="transaction_volume", expression=volume),
                ),
            ),
            WindowNode(
                kind="window",
                node_id="window_monthly_growth",
                input_id="aggregate_monthly_volume",
                outputs=(
                    WindowExpression(
                        alias="growth",
                        function="lag",
                        argument=volume,
                        partition_by=(),
                        order_by=(
                            SortKey(expression=month, direction="asc", nulls="last"),
                        ),
                    ),
                ),
            ),
        ),
    )


def bounded_disclosure_ir(request: GuardedGenerationRequest) -> RelationalQueryIR:
    """A bounded sensitive projection: every source disclosed, cap from the text."""
    limit_ref = question_span(request.canonical_question, "five", "integer")
    name = ColumnRef(table_id="table.customers", column="name")
    income = ColumnRef(table_id="table.customers", column="annual_income")
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="limit_top_incomes",
        nodes=(
            ScanNode(kind="scan", node_id="scan_customers", table_id="table.customers"),
            ProjectNode(
                kind="project",
                node_id="project_customer_income",
                input_id="scan_customers",
                outputs=(
                    NamedExpression(
                        alias="name",
                        expression=ColumnExpression(kind="column", ref=name),
                    ),
                    NamedExpression(
                        alias="annual_income",
                        expression=ColumnExpression(kind="column", ref=income),
                    ),
                ),
            ),
            SortNode(
                kind="sort",
                node_id="sort_by_income",
                input_id="project_customer_income",
                keys=(
                    SortKey(
                        expression=ColumnExpression(kind="column", ref=income),
                        direction="desc",
                        nulls="last",
                    ),
                ),
            ),
            LimitNode(
                kind="limit",
                node_id="limit_top_incomes",
                input_id="sort_by_income",
                count=limit_ref,
            ),
        ),
        requested_disclosures=(
            RequestedDisclosure(source_columns=(name, income), limit_ref=limit_ref),
        ),
    )


def zero_row_ir(request: GuardedGenerationRequest) -> RelationalQueryIR:
    """An explicit `0` from the question text, never inferred from prose."""
    threshold = question_span(request.canonical_question, "0", "decimal")
    return RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="project_card_txn_ids",
        nodes=(
            ScanNode(
                kind="scan",
                node_id="scan_card_transactions",
                table_id="table.card_transactions",
            ),
            FilterNode(
                kind="filter",
                node_id="filter_negative_amount",
                input_id="scan_card_transactions",
                predicate=BinaryExpression(
                    kind="binary",
                    operator="lt",
                    left=_column("table.card_transactions", "amount"),
                    right=LiteralExpression(kind="literal", ref=threshold),
                ),
            ),
            ProjectNode(
                kind="project",
                node_id="project_card_txn_ids",
                input_id="filter_negative_amount",
                outputs=(
                    NamedExpression(
                        alias="card_txn_id",
                        expression=_column("table.card_transactions", "card_txn_id"),
                    ),
                ),
            ),
        ),
    )


GoldenBuilder = Callable[[GuardedGenerationRequest], Any]

#: Scripted outcomes per case, in call order. `relative-time` scripts exactly
#: two: the escalating plan, then its conforming fallback IR.
GOLDEN_OUTCOMES: Mapping[str, tuple[GoldenBuilder, ...]] = {
    "metric-fidelity": (metric_fidelity_ir,),
    "two-hop-join": (two_hop_join_ir,),
    "relative-time": (relative_time_plan, relative_time_ir),
    "bounded-disclosure": (bounded_disclosure_ir,),
    "zero-row": (zero_row_ir,),
}

EXPECTED_ROUTE: Mapping[str, str] = {
    case_id: "planned_ir" if case_id == "relative-time" else "default_ir"
    for case_id in GOLDEN_OUTCOMES
}
EXPECTED_SEMANTIC_CALLS: Mapping[str, int] = {
    case_id: len(builders) for case_id, builders in GOLDEN_OUTCOMES.items()
}


@dataclass
class _Script:
    case_id: str
    remaining: list[GoldenBuilder]


class GoldenProvider:
    """A deterministic provider that answers by canonical-question identity.

    It structurally implements `Text2SQLGenerationProvider`, so orchestration
    consumes it through exactly the same boundary as the live organizer. It holds
    no SQL, no resolved literal, and no expected result.
    """

    provider = "golden"
    model = "golden-answers"
    model_revision = "008.golden.v1"
    schema_mechanism = "json_schema"

    def __init__(
        self,
        questions: Sequence[ReferenceQuestion] | None = None,
        outcomes: Mapping[str, tuple[GoldenBuilder, ...]] | None = None,
    ) -> None:
        cases = (
            tuple(questions) if questions is not None else load_reference_questions()
        )
        table = dict(outcomes if outcomes is not None else GOLDEN_OUTCOMES)
        self._scripts: dict[str, _Script] = {}
        for case in cases:
            builders = table.get(case.id)
            if not builders:
                raise KeyError(f"no golden outcome is scripted for {case.id}")
            self._scripts[case.canonical_question_hash] = _Script(
                case_id=case.id, remaining=list(builders)
            )
        self.calls: list[tuple[str, str]] = []

    @property
    def semantic_calls(self) -> int:
        return len(self.calls)

    def calls_for(self, case_id: str) -> tuple[str, ...]:
        return tuple(mode for scripted, mode in self.calls if scripted == case_id)

    def generate(self, request, output_adapter):
        if type(request) is not GuardedGenerationRequest:
            raise ProviderRejected("the golden provider accepts only a guarded request")
        script = self._scripts.get(
            canonical_question_sha256(request.canonical_question)
        )
        if script is None:
            raise ProviderRejected("no golden outcome exists for this question")
        if not script.remaining:
            raise ProviderRejected(
                f"{script.case_id} requested more calls than are scripted"
            )
        builder = script.remaining.pop(0)
        self.calls.append((script.case_id, request.mode))
        outcome = builder(request)
        # Round-tripping through the wire adapter proves the fixture is a typed,
        # serializable outcome rather than a privileged in-process object.
        output = output_adapter.validate_python(outcome.model_dump(mode="json"))
        return ProviderGeneration(
            output=output,
            transport_attempts=(
                TransportAttempt(ordinal=1, outcome="accepted", latency_ms=0),
            ),
            usage=ProviderUsage(),
        )
