"""Authorization precedes ranking and graph expansion (FR-704)."""
from __future__ import annotations

from .models import SemanticBundle
from .provenance import QueryFault, digest
from .query_models import (
    AuthorizationScope, ColumnRef, GroundingSnapshot, SnapshotColumn,
    SnapshotGovernedLiteral, SnapshotMetadataObject, SnapshotRanking,
    SnapshotRelationship, SnapshotWarning,
)
from .retrieval import SemanticRetriever

TYPES = {"BIGINT": "integer", "INTEGER": "integer", "DOUBLE": "decimal",
         "FLOAT": "decimal", "VARCHAR": "string", "DATE": "date",
         "TIMESTAMP": "timestamp", "BOOLEAN": "boolean"}


def trusted_scope(bundle: SemanticBundle, *, tenant: str, object_ids=None,
                  classifications=("public", "internal", "confidential", "restricted")):
    """Composition-root helper; never called with a model-authored tenant/scope."""
    payload = dict(scope_version="008.scope.v1", tenant_scope_hash=digest(tenant),
                   policy_version="008.policy.v1", allowed_object_ids=frozenset(object_ids if object_ids is not None else (x.id for x in bundle.objects)),
                   allowed_classifications=frozenset(classifications))
    return AuthorizationScope(**payload, authorization_scope_hash=digest(payload))


def verify_snapshot(snapshot: GroundingSnapshot):
    if digest(snapshot, exclude={"snapshot_hash"}) != snapshot.snapshot_hash:
        raise QueryFault("snapshot_integrity_error")


WARNING_CONTROLS = {
    "Balance is a current snapshot, not a transaction flow.": "snapshot_measure",
    "Aggregate each activity log to customer grain before combining; never use a naive UNION of raw events.": "preserve_grain",
    "Loan Officer headcount and loans must each aggregate to branch grain before comparison.": "preserve_grain",
    "Account transactions reach branches through accounts; use both declared joins.": "governed_relationship",
    "Use card transaction grain; do not mix directly with account transactions.": "preserve_grain",
    "Use restricted identity attributes only when necessary; prefer aggregate output.": "bounded_disclosure",
    "Payment events and loans have different grains; aggregate before comparing loan types.": "preserve_grain",
    "Satisfaction score is meaningful only for resolved cases.": "resolved_satisfaction",
    "Amounts are positive; derive inflow/outflow from txn_type.": "positive_volume",
    "Anchor recent periods to MAX(transactions.txn_date).": "data_relative_time",
    "Safe division returns NULL for an empty population.": "informational",
    "Join loans only after preserving payment-event denominator.": "preserve_grain",
    "Direction requires txn_type; amount itself is unsigned.": "positive_volume",
    "Use MAX(txn_date) as the relative-time anchor.": "data_relative_time",
    "Declared relationship; the source DuckDB does not define FK constraints.": "informational",
    "Card-event grain differs from account transactions.": "preserve_grain",
    "Anchor relative time to MAX(txn_date).": "data_relative_time",
    "Amounts are positive; use txn_type for direction.": "positive_volume",
    "Do not UNION raw rows with card_transactions.": "preserve_grain",
}


def warning_control(text: str) -> str:
    return WARNING_CONTROLS.get(text, "unsupported_warning")


class GroundingResolver:
    def __init__(self, bundle: SemanticBundle, top_k: int = 10, depth: int = 2,
                 governed_literals: tuple[SnapshotGovernedLiteral, ...] = ()):
        self.bundle = bundle.model_copy(deep=True)
        self.top_k, self.depth = top_k, depth
        self.governed_literals = governed_literals

    def resolve(self, canonical_question: str, scope: AuthorizationScope, dialect="duckdb"):
        if digest(scope, exclude={"authorization_scope_hash"}) != scope.authorization_scope_hash:
            raise QueryFault("authorization_scope_integrity_error")
        authorized = []
        for obj in self.bundle.objects:
            if obj.id not in scope.allowed_object_ids or obj.status != "active":
                continue
            copy = obj.model_copy(deep=True)
            if obj.type == "table":
                copy.cerebro["columns"] = [c for c in obj.cerebro.get("columns", []) if c.get("classification", "internal") in scope.allowed_classifications]
            authorized.append(copy)
        # A new retriever is built from the authorized subset: forbidden objects
        # never participate in ranking, score fusion or adjacency traversal.
        retriever = SemanticRetriever(self.bundle.model_copy(update={"objects": authorized}))
        ranked = retriever.search(canonical_question, limit=self.top_k)
        ids = retriever.expand([x.id for x in ranked], self.depth)
        objects = []
        for object_id in ids:
            obj = retriever.by_id[object_id]
            columns = tuple(SnapshotColumn(ref=ColumnRef(table_id=obj.id, column=c["name"]),
                data_type=TYPES.get(c["data_type"].upper(), "decimal" if c["data_type"].startswith("DECIMAL") else "string"),
                description=str(c.get("description", "")), classification=c.get("classification", "internal")) for c in obj.cerebro.get("columns", []))
            relationships = ()
            if obj.type == "relationship":
                d = obj.cerebro
                if d["source_table"] not in ids or d["target_table"] not in ids:
                    continue
                relationships = (SnapshotRelationship(relationship_id=obj.id,
                    left=ColumnRef(table_id=d["source_table"], column=d["source_column"]),
                    right=ColumnRef(table_id=d["target_table"], column=d["target_column"])),)
            warnings = tuple(SnapshotWarning(object_id=obj.id, warning_hash=digest(w),
                kind="informational" if warning_control(w) == "informational" else "actionable",
                control_id=warning_control(w)) for w in obj.cerebro.get("warnings", []))
            objects.append(SnapshotMetadataObject(object_id=obj.id, object_type=obj.type,
                description=obj.description, dependencies=tuple(sorted(x for x in obj.links if x in ids)), columns=columns, relationships=relationships, warnings=warnings,
                formula=obj.cerebro.get("formula") if obj.type == "metric" else None,
                metric_result_type="decimal" if obj.type == "metric" else None))
        payload = dict(snapshot_version="008.grounding.v1", semantic_version=self.bundle.version,
            policy_version=scope.policy_version, canonicalization_version="008.question.v1",
            literal_registry_version="008.literal-span.v1", type_registry_version="008.types.v1",
            authorization_scope_hash=scope.authorization_scope_hash,
            retrieval_config_hash=digest({"top_k": self.top_k, "depth": self.depth, "version": "008.retrieval.v1"}),
            dialect=dialect, objects=tuple(objects),
            governed_literals=tuple(x for x in self.governed_literals if x.source_object_id in ids),
            ranking_evidence=tuple(SnapshotRanking(id=x.id, score=x.score) for x in ranked))
        return GroundingSnapshot(**payload, snapshot_hash=digest(payload))
