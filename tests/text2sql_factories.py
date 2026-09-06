from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from importlib import import_module
from typing import Any

models = import_module("cerebro.models")

SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64
SHA256_D = "d" * 64

_DEFAULT_OBJECT_IDS = frozenset(
    {
        "metric.transaction-volume",
        "policy.sensitive-output",
        "relationship.account_branch",
        "relationship.transaction_account",
        "table.accounts",
        "table.branches",
        "table.transactions",
    }
)


def valid_scope(
    allowed_object_ids: Iterable[str] | None = None,
    policy_version: str = "policy.v1",
):
    """Return a normally validated immutable authorization scope."""
    object_ids = (
        _DEFAULT_OBJECT_IDS
        if allowed_object_ids is None
        else frozenset(allowed_object_ids)
    )
    return models.AuthorizationScope(
        scope_version="008.scope.v1",
        tenant_scope_hash=SHA256_A,
        policy_version=policy_version,
        allowed_object_ids=object_ids,
        allowed_classifications=frozenset(
            {"restricted", "public", "confidential", "internal"}
        ),
        authorization_scope_hash=SHA256_B,
    )


def canonical_question(text: str = "Show accounts in London") -> str:
    """Apply the versioned NFC/Unicode-whitespace canonicalization used by tests."""
    normalized = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", normalized.strip(), flags=re.UNICODE)


def _unique_codepoint_span(question: str, token: str) -> tuple[int, int]:
    if not token:
        raise ValueError("token must be nonempty")
    occurrences = [
        match.start() for match in re.finditer(f"(?={re.escape(token)})", question)
    ]
    if len(occurrences) != 1:
        raise ValueError(
            f"token must occur exactly once in the canonical question: {token!r}"
        )
    start = occurrences[0]
    return start, start + len(token)


def question_literal_ref(
    question: str,
    token: str = "London",
    data_type: str = "string",
):
    """Create a ref from one exact, unique Unicode code-point span."""
    canonical = canonical_question(question)
    start, end = _unique_codepoint_span(canonical, token)
    return models.QuestionLiteralRef(
        kind="question",
        start=start,
        end=end,
        data_type=data_type,
    )


def governed_literal_ref(literal_id: str):
    return models.GovernedLiteralRef(kind="governed", literal_id=literal_id)


def _column(
    table_id: str,
    name: str,
    data_type: str,
    classification: str = "internal",
    description: str = "Governed column metadata.",
):
    return models.SnapshotColumn(
        ref=models.ColumnRef(table_id=table_id, column=name),
        data_type=data_type,
        description=description,
        classification=classification,
    )


def valid_snapshot(scope=None, governed_literals: Iterable[Any] = ()):
    """Return a compact immutable snapshot covering shared IR fixtures."""
    scope = scope or valid_scope()
    transaction_account = models.SnapshotRelationship(
        relationship_id="relationship.transaction_account",
        left=models.ColumnRef(table_id="table.transactions", column="account_id"),
        right=models.ColumnRef(table_id="table.accounts", column="account_id"),
    )
    account_branch = models.SnapshotRelationship(
        relationship_id="relationship.account_branch",
        left=models.ColumnRef(table_id="table.accounts", column="branch_id"),
        right=models.ColumnRef(table_id="table.branches", column="branch_id"),
    )
    warning_text = "Transaction volume is a positive amount aggregate."
    volume_warning = models.SnapshotWarning(
        object_id="metric.transaction-volume",
        warning_hash=hashlib.sha256(warning_text.encode("utf-8")).hexdigest(),
        kind="actionable",
        control_id="control.positive_transaction_volume",
    )

    objects = (
        models.SnapshotMetadataObject(
            object_id="table.accounts",
            object_type="table",
            description="Governed account metadata.",
            columns=(
                _column("table.accounts", "account_id", "integer"),
                _column("table.accounts", "branch_id", "integer"),
                _column("table.accounts", "city", "string", "confidential"),
                _column(
                    "table.accounts",
                    "customer_name",
                    "string",
                    "confidential",
                ),
                _column("table.accounts", "status", "string"),
            ),
            relationships=(transaction_account, account_branch),
        ),
        models.SnapshotMetadataObject(
            object_id="table.branches",
            object_type="table",
            description="Governed branch metadata.",
            columns=(
                _column("table.branches", "branch_id", "integer"),
                _column("table.branches", "branch_name", "string"),
                _column("table.branches", "city", "string"),
            ),
            relationships=(account_branch,),
        ),
        models.SnapshotMetadataObject(
            object_id="table.transactions",
            object_type="table",
            description="Governed transaction metadata.",
            columns=(
                _column("table.transactions", "transaction_id", "integer"),
                _column("table.transactions", "account_id", "integer"),
                _column("table.transactions", "amount", "decimal", "confidential"),
                _column("table.transactions", "txn_date", "date"),
                _column("table.transactions", "txn_type", "string"),
            ),
            relationships=(transaction_account,),
        ),
        models.SnapshotMetadataObject(
            object_id="metric.transaction-volume",
            object_type="metric",
            description="Positive governed transaction volume.",
            formula="sum(table.transactions.amount)",
            metric_result_type="decimal",
            warnings=(volume_warning,),
        ),
        models.SnapshotMetadataObject(
            object_id="relationship.transaction_account",
            object_type="relationship",
            description="Transactions belong to accounts.",
            relationships=(transaction_account,),
        ),
        models.SnapshotMetadataObject(
            object_id="relationship.account_branch",
            object_type="relationship",
            description="Accounts belong to branches.",
            relationships=(account_branch,),
        ),
        models.SnapshotMetadataObject(
            object_id="policy.sensitive-output",
            object_type="policy",
            description="Sensitive output must be bounded and disclosed.",
        ),
    )

    scope_ids = frozenset(scope.allowed_object_ids)
    relationship_dependencies = {
        "relationship.transaction_account": frozenset(
            {
                "relationship.transaction_account",
                "table.transactions",
                "table.accounts",
            }
        ),
        "relationship.account_branch": frozenset(
            {
                "relationship.account_branch",
                "table.accounts",
                "table.branches",
            }
        ),
    }
    object_dependencies = {
        "metric.transaction-volume": frozenset(
            {"metric.transaction-volume", "table.transactions"}
        ),
        **relationship_dependencies,
    }
    visible_relationship_ids = frozenset(
        relationship_id
        for relationship_id, dependencies in relationship_dependencies.items()
        if dependencies.issubset(scope_ids)
    )

    visible_objects = []
    for item in objects:
        dependencies = object_dependencies.get(
            item.object_id, frozenset({item.object_id})
        )
        if not dependencies.issubset(scope_ids):
            continue
        payload = item.model_dump(mode="python")
        payload["relationships"] = tuple(
            relationship
            for relationship in item.relationships
            if relationship.relationship_id in visible_relationship_ids
        )
        visible_objects.append(models.SnapshotMetadataObject.model_validate(payload))
    visible_objects = tuple(visible_objects)
    visible_object_ids = frozenset(item.object_id for item in visible_objects)

    validated_literals = tuple(
        models.SnapshotGovernedLiteral.model_validate(literal)
        for literal in governed_literals
    )
    visible_literals = tuple(
        literal
        for literal in validated_literals
        if literal.source_object_id in visible_object_ids
    )

    return models.GroundingSnapshot(
        snapshot_version="008.grounding.v1",
        semantic_version="semantic.v1",
        policy_version=scope.policy_version,
        canonicalization_version="008.question.v1",
        literal_registry_version="008.literal-span.v1",
        type_registry_version="008.types.v1",
        authorization_scope_hash=scope.authorization_scope_hash,
        retrieval_config_hash=SHA256_C,
        dialect="duckdb",
        objects=visible_objects,
        governed_literals=visible_literals,
        ranking_evidence=(),
        authorized_object_ids=visible_object_ids,
        policy_ids=frozenset(
            item.object_id for item in visible_objects if item.object_type == "policy"
        ),
        dialect_capabilities=models.DialectCapabilities(
            parameter_style="qmark",
            supports_window=True,
            supports_set_operations=True,
        ),
        snapshot_hash=SHA256_D,
    )


def minimal_ir(snapshot=None):
    snapshot = snapshot or valid_snapshot()
    assert "table.accounts" in snapshot.authorized_object_ids
    account_id = models.ColumnExpression(
        kind="column",
        ref=models.ColumnRef(table_id="table.accounts", column="account_id"),
    )
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="project_accounts",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="scan_accounts", table_id="table.accounts"
            ),
            models.ProjectNode(
                kind="project",
                node_id="project_accounts",
                input_id="scan_accounts",
                outputs=(
                    models.NamedExpression(alias="account_id", expression=account_id),
                ),
            ),
        ),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(),
    )


def branch_volume_ir(snapshot=None):
    snapshot = snapshot or valid_snapshot()
    warning = next(
        warning
        for item in snapshot.objects
        if item.object_id == "metric.transaction-volume"
        for warning in item.warnings
    )
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="aggregate_branch_volume",
        nodes=(
            models.ScanNode(
                kind="scan",
                node_id="scan_transactions",
                table_id="table.transactions",
            ),
            models.ScanNode(
                kind="scan", node_id="scan_accounts", table_id="table.accounts"
            ),
            models.ScanNode(
                kind="scan", node_id="scan_branches", table_id="table.branches"
            ),
            models.JoinNode(
                kind="join",
                node_id="join_transaction_account",
                left_id="scan_transactions",
                right_id="scan_accounts",
                relationship_id="relationship.transaction_account",
                join_type="inner",
            ),
            models.JoinNode(
                kind="join",
                node_id="join_account_branch",
                left_id="join_transaction_account",
                right_id="scan_branches",
                relationship_id="relationship.account_branch",
                join_type="inner",
            ),
            models.AggregateNode(
                kind="aggregate",
                node_id="aggregate_branch_volume",
                input_id="join_account_branch",
                group_by=(
                    models.NamedExpression(
                        alias="branch_name",
                        expression=models.ColumnExpression(
                            kind="column",
                            ref=models.ColumnRef(
                                table_id="table.branches", column="branch_name"
                            ),
                        ),
                    ),
                ),
                measures=(
                    models.NamedExpression(
                        alias="transaction_volume",
                        expression=models.MetricExpression(
                            kind="metric",
                            metric_id="metric.transaction-volume",
                        ),
                    ),
                ),
            ),
        ),
        warning_decisions=(
            models.WarningDecision(
                warning=models.WarningRef(
                    object_id=warning.object_id,
                    warning_hash=warning.warning_hash,
                ),
                control_id=warning.control_id,
                decision="applied",
            ),
        ),
        assumptions=(),
        requested_disclosures=(),
    )


def relative_growth_ir(
    snapshot=None,
    question: str = "Show transaction growth over the last 24 months",
):
    snapshot = snapshot or valid_snapshot()
    amount_ref = question_literal_ref(question, token="24", data_type="integer")
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="aggregate_monthly_volume",
        nodes=(
            models.ScanNode(
                kind="scan",
                node_id="scan_transactions",
                table_id="table.transactions",
            ),
            models.FilterNode(
                kind="filter",
                node_id="filter_relative_time",
                input_id="scan_transactions",
                predicate=models.RelativeTimeExpression(
                    kind="relative_time",
                    date_column=models.ColumnRef(
                        table_id="table.transactions", column="txn_date"
                    ),
                    anchor="data_max",
                    amount_ref=amount_ref,
                    unit="month",
                    lower_inclusive=True,
                    upper_inclusive=True,
                ),
            ),
            models.AggregateNode(
                kind="aggregate",
                node_id="aggregate_monthly_volume",
                input_id="filter_relative_time",
                group_by=(
                    models.NamedExpression(
                        alias="txn_month",
                        expression=models.FunctionExpression(
                            kind="function",
                            function="date_trunc",
                            arguments=(
                                models.ColumnExpression(
                                    kind="column",
                                    ref=models.ColumnRef(
                                        table_id="table.transactions",
                                        column="txn_date",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
                measures=(
                    models.NamedExpression(
                        alias="transaction_volume",
                        expression=models.MetricExpression(
                            kind="metric",
                            metric_id="metric.transaction-volume",
                        ),
                    ),
                ),
            ),
        ),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(),
    )


def complex_window_plan(snapshot=None):
    snapshot = snapshot or valid_snapshot()
    assert "metric.transaction-volume" in snapshot.authorized_object_ids
    return models.ComplexQueryPlan(
        outcome="complex_plan",
        plan_version="008.complex-plan.v1",
        operator_ids=("window.period_over_period.v1",),
        steps=(
            models.ComplexPlanStep(
                step_id="period_growth",
                operator_id="window.period_over_period.v1",
                depends_on=(),
                input_object_ids=(
                    "table.transactions",
                    "metric.transaction-volume",
                ),
                output_names=("txn_month", "transaction_volume", "growth"),
            ),
        ),
        expected_outputs=("txn_month", "transaction_volume", "growth"),
    )


def valid_clarification_request(
    question: str = "Show accounts in London",
    snapshot=None,
):
    snapshot = snapshot or valid_snapshot()
    canonical = canonical_question(question)
    token = "accounts"
    start, end = _unique_codepoint_span(canonical, token)
    assert {"table.accounts", "table.transactions"}.issubset(
        snapshot.authorized_object_ids
    )
    return models.ClarificationRequest(
        outcome="clarification_request",
        ambiguities=(
            models.Ambiguity(
                ambiguity_id="target_object",
                start=start,
                end=end,
                candidates=(
                    models.ObjectAmbiguityCandidate(
                        kind="object", object_id="table.accounts"
                    ),
                    models.ObjectAmbiguityCandidate(
                        kind="object", object_id="table.transactions"
                    ),
                ),
            ),
        ),
    )


def sensitive_ir(
    snapshot=None,
    question: str = "Which five customer names are in London",
):
    snapshot = snapshot or valid_snapshot()
    assert "table.accounts" in snapshot.authorized_object_ids
    city_ref = question_literal_ref(question, token="London", data_type="string")
    limit_ref = question_literal_ref(question, token="five", data_type="integer")
    source_column = models.ColumnRef(table_id="table.accounts", column="customer_name")
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="limit_sensitive_accounts",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="scan_accounts", table_id="table.accounts"
            ),
            models.FilterNode(
                kind="filter",
                node_id="filter_city",
                input_id="scan_accounts",
                predicate=models.BinaryExpression(
                    kind="binary",
                    operator="eq",
                    left=models.ColumnExpression(
                        kind="column",
                        ref=models.ColumnRef(table_id="table.accounts", column="city"),
                    ),
                    right=models.LiteralExpression(kind="literal", ref=city_ref),
                ),
            ),
            models.ProjectNode(
                kind="project",
                node_id="project_customer_names",
                input_id="filter_city",
                outputs=(
                    models.NamedExpression(
                        alias="customer_name",
                        expression=models.ColumnExpression(
                            kind="column", ref=source_column
                        ),
                    ),
                ),
            ),
            models.LimitNode(
                kind="limit",
                node_id="limit_sensitive_accounts",
                input_id="project_customer_names",
                count=limit_ref,
            ),
        ),
        warning_decisions=(),
        assumptions=(),
        requested_disclosures=(
            models.RequestedDisclosure(
                source_columns=(source_column,),
                limit_ref=limit_ref,
            ),
        ),
    )


def compiled_query(ir=None):
    ir = ir or sensitive_ir()
    return models.CompiledQuery(
        sql=('SELECT "customer_name" FROM "accounts" WHERE "city" = ? LIMIT ?'),
        parameters=(
            models.BoundParameter(position=1, data_type="string", value="London"),
            models.BoundParameter(position=2, data_type="integer", value=5),
        ),
        ir_hash=SHA256_A,
        compiler_version="compiler.v1",
        dialect="duckdb",
    )


def sql_artifact(compiled=None):
    compiled = compiled or compiled_query()
    return models.SQLArtifact(
        sql=compiled.sql,
        sql_sha256=hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest(),
        parameter_count=len(compiled.parameters),
        parameter_types=tuple(parameter.data_type for parameter in compiled.parameters),
        ir_hash=compiled.ir_hash,
        compiler_version=compiled.compiler_version,
        dialect=compiled.dialect,
    )


def valid_request(scope=None):
    return models.SQLGenerationRequest(
        question="Show accounts in London",
        authorization_scope=scope or valid_scope(),
        dialect="duckdb",
        max_rows=1000,
    )


def valid_response_base(snapshot=None):
    snapshot = snapshot or valid_snapshot()
    return models.ResponseBase(
        contract_version="008.v3",
        semantic_version=snapshot.semantic_version,
        policy_version=snapshot.policy_version,
        canonicalization_version=snapshot.canonicalization_version,
        literal_registry_version=snapshot.literal_registry_version,
        ir_contract_version="008.ir.v1",
        type_registry_version=snapshot.type_registry_version,
        prompt_version="prompt.v1",
        router_version="router.v1",
        compiler_version="compiler.v1",
        checker_version="checker.v1",
        dialect="duckdb",
        provider="organizer",
        model="organizer-model",
        model_revision="2026-08-27",
        canonical_question_hash=SHA256_A,
        authorization_scope_hash=snapshot.authorization_scope_hash,
        snapshot_hash=snapshot.snapshot_hash,
        generation_route="default_ir",
        cache_status="miss",
        grounding_usage=models.GroundingUsage(
            object_ids=frozenset({"table.accounts"}),
            relationship_ids=frozenset(),
            governed_literal_ids=frozenset(),
        ),
        assumptions=(),
        attempt_records=(
            models.AttemptRecord(
                stage="default_ir",
                ordinal=1,
                outcome="accepted",
                latency_ms=1,
                violation_codes=(),
                generation_route="default_ir",
                cache_status="miss",
            ),
        ),
        budget_usage=models.BudgetUsage(
            semantic_call_capacity=1,
            planned_ir_authorized=False,
            semantic_calls=1,
            transport_attempts=1,
            input_tokens=10,
            output_tokens=10,
            cost_usd="0.01",
            elapsed_ms=1,
        ),
        violations=(),
    )


def codes(violations: Iterable[Any]) -> tuple[str, ...]:
    return tuple(violation.code for violation in violations)


def accepted_complex_route(snapshot=None, plan=None):
    """Mint planned-route authority the only legitimate way: through the router.

    The router owns acceptance, so no test helper constructs capability fields
    or touches the module-private token and factory in `models`.
    """
    from cerebro.complexity import ComplexityRouter

    snapshot = snapshot or valid_snapshot()
    plan = plan or complex_window_plan(snapshot)
    decision = ComplexityRouter().validate(plan, snapshot)
    if decision.accepted is None:
        raise ValueError("the router refused a plan a factory expected to accept")
    return decision.accepted


def foreign_accepted_route(snapshot=None):
    """Return authority bound to a different snapshot than the one supplied."""
    snapshot = snapshot or valid_snapshot()
    other = snapshot.model_copy(update={"snapshot_hash": "f" * 64})
    return accepted_complex_route(other)


def validated_ir(
    ir=None, snapshot=None, question=None, *, generation_route="default_ir"
):
    """Wrap an IR as `ValidatedIR` with genuinely derived binding hashes."""
    from cerebro.models import relational_ir_sha256
    from cerebro.provenance import canonical_question_sha256, canonicalize_question

    snapshot = snapshot or valid_snapshot()
    ir = ir if ir is not None else minimal_ir(snapshot)
    question = canonicalize_question(
        question if question is not None else canonical_question()
    )
    plan_hash = None
    if generation_route == "planned_ir":
        plan_hash = models.complex_plan_sha256(complex_window_plan(snapshot))
    return models.ValidatedIR(
        ir=ir,
        ir_hash=relational_ir_sha256(ir),
        snapshot_hash=snapshot.snapshot_hash,
        canonical_question_hash=canonical_question_sha256(question),
        generation_route=generation_route,
        accepted_complex_plan_hash=plan_hash,
    )


def filtered_account_ir(snapshot=None, *, city_ref=None, column="city"):
    """Scan, filter one column against a literal ref, then project the key."""
    snapshot = snapshot or valid_snapshot()
    if city_ref is None:
        city_ref = question_literal_ref(canonical_question(), token="London")
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="project_accounts",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="scan_accounts", table_id="table.accounts"
            ),
            models.FilterNode(
                kind="filter",
                node_id="filter_accounts",
                input_id="scan_accounts",
                predicate=models.BinaryExpression(
                    kind="binary",
                    operator="eq",
                    left=models.ColumnExpression(
                        kind="column",
                        ref=models.ColumnRef(table_id="table.accounts", column=column),
                    ),
                    right=models.LiteralExpression(kind="literal", ref=city_ref),
                ),
            ),
            models.ProjectNode(
                kind="project",
                node_id="project_accounts",
                input_id="filter_accounts",
                outputs=(
                    models.NamedExpression(
                        alias="account_id",
                        expression=models.ColumnExpression(
                            kind="column",
                            ref=models.ColumnRef(
                                table_id="table.accounts", column="account_id"
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def complex_node_ir(snapshot=None, kind="window"):
    """Return the smallest valid IR that requires the planned route."""
    snapshot = snapshot or valid_snapshot()
    account_id = models.ColumnExpression(
        kind="column",
        ref=models.ColumnRef(table_id="table.accounts", column="account_id"),
    )
    if kind == "window":
        return models.RelationalQueryIR(
            outcome="ir",
            ir_version="008.ir.v1",
            root_node_id="window_accounts",
            nodes=(
                models.ScanNode(
                    kind="scan", node_id="scan_accounts", table_id="table.accounts"
                ),
                models.ProjectNode(
                    kind="project",
                    node_id="project_accounts",
                    input_id="scan_accounts",
                    outputs=(
                        models.NamedExpression(
                            alias="account_id", expression=account_id
                        ),
                    ),
                ),
                models.WindowNode(
                    kind="window",
                    node_id="window_accounts",
                    input_id="project_accounts",
                    outputs=(
                        models.WindowExpression(
                            alias="row_position",
                            function="row_number",
                            partition_by=(),
                            order_by=(
                                models.SortKey(
                                    expression=account_id,
                                    direction="asc",
                                    nulls="last",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    return models.RelationalQueryIR(
        outcome="ir",
        ir_version="008.ir.v1",
        root_node_id="union_accounts",
        nodes=(
            models.ScanNode(
                kind="scan", node_id="scan_left", table_id="table.accounts"
            ),
            models.ScanNode(
                kind="scan", node_id="scan_right", table_id="table.accounts"
            ),
            models.ProjectNode(
                kind="project",
                node_id="project_left",
                input_id="scan_left",
                outputs=(
                    models.NamedExpression(alias="account_id", expression=account_id),
                ),
            ),
            models.ProjectNode(
                kind="project",
                node_id="project_right",
                input_id="scan_right",
                outputs=(
                    models.NamedExpression(alias="account_id", expression=account_id),
                ),
            ),
            models.SetOperationNode(
                kind="set_operation",
                node_id="union_accounts",
                left_id="project_left",
                right_id="project_right",
                operator="union",
                all=False,
            ),
        ),
    )
