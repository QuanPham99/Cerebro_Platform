from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from .bundle import BundleLoader, BundleValidator
from .enrichment import GenerationProvider, SemanticEnricher, StageCallback, TraceCallback
from .llm import GenerationOutputError
from .models import (
    CatalogSnapshot,
    RelationshipCandidate,
    ReviewRecord,
    SemanticBundle,
    SemanticProposal,
    ValidationIssue,
)
from .paths import DEFAULT_CONFIG, ROOT
from .semantic.compiler import canonical_object_id, metric_formula, normalize_reference
from .settings import ACTIVE_BUNDLE_POINTER
from .source import DuckDBSource
from .upstream import OKFDocument


class DocumentationEnrichmentAgent(Protocol):
    """Optional configured-mode second pass; never part of database-only generation."""

    def enrich(self, snapshot: CatalogSnapshot, proposal: SemanticProposal) -> SemanticProposal:
        ...


class CandidateValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        rendered = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(rendered)

    @property
    def public_message(self) -> str:
        messages = "; ".join(issue.message.rstrip(".") for issue in self.issues)
        return f"Candidate validation failed: {messages}."

    @property
    def details(self) -> dict[str, Any]:
        return {
            "validation_codes": ", ".join(sorted({issue.code for issue in self.issues})),
            "objects": ", ".join(
                sorted({issue.message.split(" ", 1)[0] for issue in self.issues if issue.message})
            ),
        }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unnamed"


def _stable_id(prefix: str, raw: str) -> str:
    return canonical_object_id(prefix, raw)


def _sanitize_trace_payload(value: Any, key: str = "") -> Any:
    sensitive_keys = {"api_key", "credentials", "database_path", "prompt", "raw_response", "source_rows"}
    if key.lower() in sensitive_keys:
        return None
    if isinstance(value, dict):
        return {
            item_key: _sanitize_trace_payload(item_value, item_key)
            for item_key, item_value in value.items()
            if item_key.lower() not in sensitive_keys
        }
    if isinstance(value, list):
        return [_sanitize_trace_payload(item) for item in value]
    if isinstance(value, str) and (value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)):
        return "[omitted]"
    return value


def _normalize_table_targets(
    *,
    object_id: str,
    object_folder: str,
    targets: list[str],
    table_names: set[str],
    missing_code: str,
    invalid_code: str,
) -> tuple[list[str], list[ValidationIssue]]:
    path = f"{object_folder}/{object_id.split('.', 1)[1]}.md"
    issues: list[ValidationIssue] = []
    normalized = [target if target.startswith("table.") else f"table.{target}" for target in targets]
    normalized = sorted(set(normalized))
    if not normalized:
        issues.append(ValidationIssue(code=missing_code, message=f"{object_id} has no table target", path=path))
    for target in normalized:
        if target.removeprefix("table.") not in table_names or not target.startswith("table."):
            issues.append(
                ValidationIssue(
                    code=invalid_code,
                    message=f"{object_id} references unknown table target {target}",
                    path=path,
                )
            )
    return normalized, issues


def _validate_policy_evidence(
    object_id: str,
    evidence: list[str],
    snapshot: CatalogSnapshot,
) -> list[ValidationIssue]:
    known_columns = {
        f"{table.name}.{column.name}"
        for table in snapshot.tables
        for column in table.columns
    }
    references = {
        match.group(0)
        for item in evidence
        for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", item)
    }
    if references and references <= known_columns:
        return []
    return [
        ValidationIssue(
            code="invalid_policy_evidence",
            message=f"{object_id} must cite only real table.column catalog identifiers",
            path=f"policies/{object_id.split('.', 1)[1]}.md",
        )
    ]


def _write_doc(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = OKFDocument(frontmatter=frontmatter, body=body.strip() + "\n")
    path.write_text(document.serialize(), encoding="utf-8")


def _candidate_relationships(snapshot: CatalogSnapshot, proposal: SemanticProposal) -> list[tuple[RelationshipCandidate, str]]:
    table_columns = {table.name: {column.name for column in table.columns} for table in snapshot.tables}
    candidates: dict[tuple[str, str, str, str], tuple[RelationshipCandidate, str]] = {}
    for fact in snapshot.relationships:
        candidate = RelationshipCandidate(
            id=fact.id,
            source_table=fact.source_table,
            source_column=fact.source_column,
            target_table=fact.target_table,
            target_column=fact.target_column,
            cardinality=fact.cardinality,
            description=f"Catalog join from {fact.source_table}.{fact.source_column} to {fact.target_table}.{fact.target_column}.",
            confidence=1.0,
            evidence=[fact.provenance.source],
        )
        key = (candidate.source_table, candidate.source_column, candidate.target_table, candidate.target_column)
        candidates[key] = (candidate, fact.provenance.origin)
    for candidate in proposal.relationships.relationships:
        if (
            candidate.source_table not in table_columns
            or candidate.target_table not in table_columns
            or candidate.source_column not in table_columns[candidate.source_table]
            or candidate.target_column not in table_columns[candidate.target_table]
        ):
            raise ValueError(f"Relationship {candidate.id} references an unknown table or column")
        key = (candidate.source_table, candidate.source_column, candidate.target_table, candidate.target_column)
        if key in candidates:
            physical, origin = candidates[key]
            candidates[key] = (
                physical.model_copy(update={
                    "source_entity": candidate.source_entity,
                    "target_entity": candidate.target_entity,
                    "description": candidate.description or physical.description,
                    "evidence": sorted(set([*physical.evidence, *candidate.evidence])),
                }),
                origin,
            )
        else:
            candidates[key] = (candidate, "ai_proposed")
    return [candidates[key] for key in sorted(candidates)]


def compile_candidate_bundle(
    snapshot: CatalogSnapshot,
    proposal: SemanticProposal,
    output: Path | str | None = None,
) -> Path:
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    root = Path(output) if output else ROOT / "knowledge" / "generated" / run_id
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Candidate output already exists and is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    fingerprint_payload = snapshot.model_dump(mode="json")
    fingerprint_payload["database_path"] = "[omitted]"
    source_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    run_id = root.name if output else run_id
    version = f"{snapshot.source_version}+{_slug(run_id)}"
    manifest = {
        "name": snapshot.source_name,
        "version": version,
        "generation_mode": proposal.generation_mode,
        "review_state": "candidate",
        "provider": proposal.provider,
        "model": proposal.model,
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "source_fingerprint": source_fingerprint,
        "row_sampling": snapshot.row_sampling,
        "source_mode": snapshot.source_mode,
        "discovery_evidence": snapshot.discovery_evidence,
        "okf_version": "0.2",
        "semantic_profile_version": "0.1",
    }
    (root / "bundle.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    safe_snapshot = snapshot.model_dump(mode="json")
    safe_snapshot["database_path"] = "[omitted]"
    (root / "snapshot.json").write_text(
        json.dumps(safe_snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_doc(root / "index.md", {"okf_version": "0.2"}, f"# {snapshot.source_name}\n\nGenerated candidate bundle `{run_id}`.")

    relationship_entries = _candidate_relationships(snapshot, proposal)
    table_links: dict[str, set[str]] = {table.name: {f"dataset.{_slug(snapshot.source_name)}"} for table in snapshot.tables}
    for relationship, _ in relationship_entries:
        relationship_id = _stable_id("relationship", relationship.id)
        table_links[relationship.source_table].add(relationship_id)
        table_links[relationship.target_table].add(relationship_id)

    semantic_issues: list[ValidationIssue] = []
    semantic_ids: set[str] = set()
    table_names = set(table_links)

    def register_id(object_id: str, folder: str) -> None:
        if object_id in semantic_ids:
            semantic_issues.append(
                ValidationIssue(
                    code="duplicate_semantic_id",
                    message=f"Duplicate semantic ID: {object_id}",
                    path=f"{folder}/{object_id.split('.', 1)[1]}.md",
                )
            )
        semantic_ids.add(object_id)

    concepts: list[dict[str, Any]] = []
    for raw in proposal.business.concepts:
        object_id = _stable_id("concept", raw.id)
        register_id(object_id, "concepts")
        links, issues = _normalize_table_targets(
            object_id=object_id,
            object_folder="concepts",
            targets=raw.maps_to,
            table_names=table_names,
            missing_code="missing_concept_table_mapping",
            invalid_code="invalid_concept_mapping_target",
        )
        semantic_issues.extend(issues)
        concept = {**raw.model_dump(), "id": object_id, "links": links}
        concepts.append(concept)
        for link in links:
            table_name = link.removeprefix("table.")
            if table_name in table_links:
                table_links[table_name].add(object_id)

    metrics: list[dict[str, Any]] = []
    for raw in proposal.query.measures:
        object_id = _stable_id("metric", raw.id)
        register_id(object_id, "metrics")
        dependencies, issues = _normalize_table_targets(
            object_id=object_id,
            object_folder="metrics",
            targets=raw.dependencies,
            table_names=table_names,
            missing_code="missing_metric_dependency",
            invalid_code="invalid_metric_dependency_target",
        )
        semantic_issues.extend(issues)
        metric = {**raw.model_dump(), "id": object_id, "dependencies": dependencies}
        metrics.append(metric)
        for dependency in dependencies:
            table_name = dependency.removeprefix("table.")
            if table_name in table_links:
                table_links[table_name].add(object_id)

    policies: list[dict[str, Any]] = []
    for raw in proposal.business.policies:
        object_id = _stable_id("policy", raw.id)
        register_id(object_id, "policies")
        applies_to, issues = _normalize_table_targets(
            object_id=object_id,
            object_folder="policies",
            targets=raw.applies_to,
            table_names=table_names,
            missing_code="missing_policy_target",
            invalid_code="invalid_policy_target",
        )
        semantic_issues.extend(issues)
        semantic_issues.extend(_validate_policy_evidence(object_id, raw.evidence, snapshot))
        policy = {**raw.model_dump(), "id": object_id, "applies_to": applies_to}
        policies.append(policy)
        for target in applies_to:
            table_name = target.removeprefix("table.")
            if table_name in table_links:
                table_links[table_name].add(object_id)

    domain_ids = {_stable_id("domain", raw.id) for raw in proposal.business.domains}
    entity_ids = {_stable_id("entity", raw.id) for raw in proposal.business.entities}
    dimension_ids = {_stable_id("dimension", raw.id) for raw in proposal.business.dimensions}
    structured_metric_ids = {_stable_id("metric", raw.id) for raw in proposal.query.structured_measures}
    rule_ids = {_stable_id("rule", raw.id) for raw in proposal.query.rules}
    for raw in proposal.business.domains:
        register_id(_stable_id("domain", raw.id), "domains")
    for raw in proposal.business.entities:
        register_id(_stable_id("entity", raw.id), "entities")
    for raw in proposal.business.dimensions:
        register_id(_stable_id("dimension", raw.id), "dimensions")
    for raw in proposal.query.structured_measures:
        register_id(_stable_id("metric", raw.id), "metrics")
    for raw in proposal.query.rules:
        register_id(_stable_id("rule", raw.id), "rules")

    def reference_index(prefix: str, values: list[Any]) -> dict[str, str]:
        index: dict[str, str] = {}
        for raw in values:
            value = str(raw.id).strip()
            supplied_prefix, separator, suffix = value.partition(".")
            unprefixed = suffix if separator and supplied_prefix.lower() == prefix else value
            canonical = _stable_id(prefix, value)
            for alias in {value, unprefixed, _slug(unprefixed), canonical}:
                index[alias] = canonical
        return index

    domain_id_by_raw = reference_index("domain", proposal.business.domains)
    entity_id_by_raw = reference_index("entity", proposal.business.entities)
    dimension_id_by_raw = reference_index("dimension", proposal.business.dimensions)
    metric_id_by_raw = reference_index("metric", proposal.query.structured_measures)
    rule_id_by_raw = reference_index("rule", proposal.query.rules)

    def semantic_ref(value: str) -> str:
        if value in entity_id_by_raw:
            return entity_id_by_raw[value]
        if value in dimension_id_by_raw:
            return dimension_id_by_raw[value]
        if value in metric_id_by_raw:
            return metric_id_by_raw[value]
        if value in rule_id_by_raw:
            return rule_id_by_raw[value]
        if value in table_names:
            return normalize_reference("physical_table", value)
        prefix, separator, _suffix = value.partition(".")
        if separator:
            kind_by_prefix = {
                "entity": "entity",
                "dimension": "dimension",
                "metric": "metric",
                "rule": "business_rule",
            }
            if prefix.lower() in kind_by_prefix:
                return normalize_reference(kind_by_prefix[prefix.lower()], value)
        return value

    metrics_by_dimension: dict[str, set[str]] = {}
    for raw in proposal.query.structured_measures:
        metric_id = _stable_id("metric", raw.id)
        for value in raw.compatible_dimensions:
            dimension_id = normalize_reference("dimension", value)
            metrics_by_dimension.setdefault(dimension_id, set()).add(metric_id)

    domains: list[dict[str, Any]] = []
    for raw in proposal.business.domains:
        object_id = _stable_id("domain", raw.id)
        domains.append({**raw.model_dump(mode="json"), "id": object_id})

    entities: list[dict[str, Any]] = []
    for raw in proposal.business.entities:
        object_id = _stable_id("entity", raw.id)
        table = normalize_reference("physical_table", raw.physical_mapping.table)
        if table.removeprefix("table.") not in table_names:
            semantic_issues.append(ValidationIssue(
                code="invalid_entity_table", message=f"{object_id} references unknown table {table}",
                path=f"entities/{object_id.split('.', 1)[1]}.md",
            ))
        else:
            columns = {column.name for column in next(item for item in snapshot.tables if item.name == table.removeprefix("table." )).columns}
            for key in raw.physical_mapping.key:
                if key not in columns:
                    semantic_issues.append(ValidationIssue(
                        code="invalid_entity_key", message=f"{object_id} references unknown key {table}.{key}",
                        path=f"entities/{object_id.split('.', 1)[1]}.md",
                    ))
        domain_ref: str | None = None
        if raw.domain:
            domain_ref = domain_id_by_raw.get(raw.domain) or normalize_reference("domain", raw.domain)
            if domain_ref not in domain_ids:
                semantic_issues.append(ValidationIssue(
                    code="invalid_entity_domain_reference",
                    message=f"{object_id} references unknown domain {domain_ref}",
                    path=f"entities/{object_id.split('.', 1)[1]}.md",
                ))
                domain_ref = None
        entities.append({
            **raw.model_dump(mode="json"), "id": object_id,
            "physical_mapping": {"table": table, "key": raw.physical_mapping.key},
            "domain": domain_ref,
        })

    dimensions: list[dict[str, Any]] = []
    for raw in proposal.business.dimensions:
        object_id = _stable_id("dimension", raw.id)
        entity = normalize_reference("entity", raw.entity)
        if entity not in entity_ids:
            semantic_issues.append(ValidationIssue(
                code="invalid_dimension_entity", message=f"{object_id} references unknown entity {entity}",
                path=f"dimensions/{object_id.split('.', 1)[1]}.md",
            ))
        bindings = []
        for binding in raw.physical_mappings:
            table = normalize_reference("physical_table", binding.table)
            table_name = table.removeprefix("table.")
            columns = {column.name for item in snapshot.tables if item.name == table_name for column in item.columns}
            if table_name not in table_names or binding.column not in columns:
                semantic_issues.append(ValidationIssue(
                    code="invalid_dimension_binding",
                    message=f"{object_id} references unknown column {table}.{binding.column}",
                    path=f"dimensions/{object_id.split('.', 1)[1]}.md",
                ))
            bindings.append({"table": table, "column": binding.column})
        compatible_metrics = sorted(metrics_by_dimension.get(object_id, set()))
        proposed_metrics = {normalize_reference("metric", value) for value in raw.compatible_metrics}
        unconfirmed_metrics = sorted(proposed_metrics - set(compatible_metrics))
        warnings = list(raw.warnings)
        if unconfirmed_metrics:
            warnings.append(
                "Semantic linker omitted unconfirmed metric compatibility: "
                + ", ".join(unconfirmed_metrics)
                + "."
            )
        dimensions.append({
            **raw.model_dump(mode="json"), "id": object_id, "entity": entity,
            "physical_mappings": bindings, "compatible_metrics": compatible_metrics,
            "warnings": warnings,
        })

    structured_metrics: list[dict[str, Any]] = []
    for raw in proposal.query.structured_measures:
        object_id = _stable_id("metric", raw.id)
        entity = normalize_reference("entity", raw.entity)
        dependencies = [normalize_reference("physical_table", value) for value in raw.dependencies]
        compatible_dimensions = [normalize_reference("dimension", value) for value in raw.compatible_dimensions]
        time_dimension = normalize_reference("dimension", raw.time_dimension) if raw.time_dimension else None
        if entity not in entity_ids:
            semantic_issues.append(ValidationIssue(code="invalid_metric_entity", message=f"{object_id} references unknown entity {entity}", path=f"metrics/{object_id.split('.', 1)[1]}.md"))
        for target in dependencies:
            if target.removeprefix("table.") not in table_names:
                semantic_issues.append(ValidationIssue(code="invalid_metric_dependency_target", message=f"{object_id} references unknown table {target}", path=f"metrics/{object_id.split('.', 1)[1]}.md"))
        for target in compatible_dimensions + ([time_dimension] if time_dimension else []):
            if target not in dimension_ids:
                semantic_issues.append(ValidationIssue(code="invalid_metric_dimension", message=f"{object_id} references unknown dimension {target}", path=f"metrics/{object_id.split('.', 1)[1]}.md"))
        measure = raw.measure.model_dump(mode="json")
        for term in ([measure] if measure["kind"] == "aggregate" else [measure["numerator"], measure["denominator"]]):
            candidates = ([term.get("source")] if term.get("source") else []) + [item.get("source") for item in term.get("predicates", [])]
            for binding in candidates:
                if not binding:
                    continue
                binding["table"] = normalize_reference("physical_table", str(binding["table"]))
                table_name = str(binding["table"]).removeprefix("table.")
                columns = {column.name for item in snapshot.tables if item.name == table_name for column in item.columns}
                if table_name not in table_names or binding["column"] not in columns:
                    semantic_issues.append(ValidationIssue(code="invalid_metric_binding", message=f"{object_id} references unknown column {binding['table']}.{binding['column']}", path=f"metrics/{object_id.split('.', 1)[1]}.md"))
        structured_metrics.append({
            **raw.model_dump(mode="json"), "id": object_id, "entity": entity,
            "dependencies": dependencies, "compatible_dimensions": compatible_dimensions,
            "time_dimension": time_dimension, "measure": measure,
        })

    rules: list[dict[str, Any]] = []
    all_semantic_ids = entity_ids | dimension_ids | structured_metric_ids | rule_ids | {f"table.{name}" for name in table_names}
    for raw in proposal.query.rules:
        object_id = _stable_id("rule", raw.id)
        entity = normalize_reference("entity", raw.entity)
        dependencies = [semantic_ref(value) for value in raw.dependencies]
        if entity not in entity_ids:
            semantic_issues.append(ValidationIssue(code="invalid_rule_entity", message=f"{object_id} references unknown entity {entity}", path=f"rules/{object_id.split('.', 1)[1]}.md"))
        for target in dependencies:
            if target not in all_semantic_ids:
                semantic_issues.append(ValidationIssue(code="invalid_rule_dependency", message=f"{object_id} references unknown target {target}", path=f"rules/{object_id.split('.', 1)[1]}.md"))
        rules.append({**raw.model_dump(mode="json"), "id": object_id, "entity": entity, "dependencies": dependencies})

    for relationship, _origin in relationship_entries:
        for field, value in (("source_entity", relationship.source_entity), ("target_entity", relationship.target_entity)):
            if value and normalize_reference("entity", value) not in entity_ids:
                semantic_issues.append(ValidationIssue(
                    code="invalid_relationship_entity",
                    message=f"relationship.{_slug(relationship.id)} references unknown {field} {value}",
                    path=f"relationships/{_slug(relationship.id)}.md",
                ))

    policy_tables = []
    for table in snapshot.tables:
        if any(column.classification in {"restricted", "confidential"} for column in table.columns):
            table_links[table.name].add("policy.generated-data-handling")
            policy_tables.append(f"table.{table.name}")
    if policy_tables:
        register_id("policy.generated-data-handling", "policies")

    if semantic_issues:
        raise CandidateValidationError(semantic_issues)

    generated = {"by": f"{proposal.provider}/{proposal.model}", "at": now.isoformat()}

    dataset_id = f"dataset.{_slug(snapshot.source_name)}"
    _write_doc(
        root / "datasets" / f"{_slug(snapshot.source_name)}.md",
        {
            "type": "Dataset",
            "id": dataset_id,
            "name": snapshot.source_name.replace("-", " ").title(),
            "title": snapshot.source_name.replace("-", " ").title(),
            "description": "Catalog-only semantic candidate generated without source-row access.",
            "status": "draft",
            "links": [f"table.{table.name}" for table in snapshot.tables],
            "sources": [{"id": "duckdb-catalog", "resource": "DuckDB catalog", "title": "DuckDB catalog metadata"}],
            "generated": generated,
            "provenance": {"origin": "discovered", "source": "DuckDB catalog", "source_fingerprint": source_fingerprint},
            "cerebro": {"kind": "dataset", "classification": "internal", "schema": snapshot.schema_name},
        },
        f"# {snapshot.source_name}\n\nGenerated from catalog metadata only.",
    )
    for table in snapshot.tables:
        description = proposal.business.table_purposes.get(table.name, table.description)
        classification = proposal.business.classifications.get(
            table.name,
            "confidential" if any(column.classification in {"restricted", "confidential"} for column in table.columns) else "internal",
        )
        _write_doc(
            root / "tables" / f"{table.name}.md",
            {
                "type": "Table",
                "id": f"table.{table.name}",
                "name": table.name.replace("_", " ").title(),
                "title": table.name.replace("_", " ").title(),
                "description": description,
                "status": "draft",
                "aliases": table.aliases,
                "links": sorted(table_links[table.name]),
                "sources": [{"id": "duckdb-catalog", "resource": "DuckDB catalog", "title": "DuckDB catalog metadata"}],
                "generated": generated,
                "provenance": {
                    "origin": "ai_proposed" if table.name in proposal.business.table_purposes else "discovered",
                    "catalog": table.provenance["catalog"].source,
                    "semantics": proposal.provider if table.name in proposal.business.table_purposes else None,
                },
                "cerebro": {
                    "kind": "physical_table",
                    "classification": classification,
                    "physical": {"schema": table.schema_name, "table": table.name},
                    "schema": table.schema_name,
                    "grain": proposal.query.grains.get(table.name, table.grain),
                    "primary_key": table.primary_key,
                    "columns": [column.model_dump(mode="json") for column in table.columns],
                    "warnings": proposal.query.warnings,
                },
            },
            f"# {table.name.replace('_', ' ').title()}\n\n{description}",
        )
    for relationship, origin in relationship_entries:
        object_id = _stable_id("relationship", relationship.id)
        relationship_links = {
            f"table.{relationship.source_table}",
            f"table.{relationship.target_table}",
        }
        if relationship.source_entity and relationship.target_entity:
            relationship_links.update({
                normalize_reference("entity", relationship.source_entity),
                normalize_reference("entity", relationship.target_entity),
            })
        _write_doc(
            root / "relationships" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "Relationship",
                "id": object_id,
                "name": object_id.split(".", 1)[1].replace("-", " ").replace("_", " ").title(),
                "title": object_id.split(".", 1)[1].replace("-", " ").replace("_", " ").title(),
                "description": relationship.description or f"Join {relationship.source_table} to {relationship.target_table}.",
                "status": "draft",
                "links": sorted(relationship_links),
                "sources": [{"resource": relationship.evidence[0] if relationship.evidence else "DuckDB catalog"}],
                "generated": generated,
                "provenance": {"origin": origin, "source": proposal.provider if origin == "ai_proposed" else (relationship.evidence[0] if relationship.evidence else "declared manifest")},
                "cerebro": {
                    "kind": "relationship",
                    "classification": "internal",
                    "edge_type": "physical_fk",
                    "source_table": f"table.{relationship.source_table}",
                    "source_column": relationship.source_column,
                    "target_table": f"table.{relationship.target_table}",
                    "target_column": relationship.target_column,
                    "cardinality": relationship.cardinality,
                    "physical": {
                        "source": {"table": f"table.{relationship.source_table}", "column": relationship.source_column},
                        "target": {"table": f"table.{relationship.target_table}", "column": relationship.target_column},
                    },
                    **({"semantic": {
                        "from": normalize_reference("entity", relationship.source_entity),
                        "to": normalize_reference("entity", relationship.target_entity),
                    }} if relationship.source_entity and relationship.target_entity else {}),
                    **({"semantic_provenance": "ai_proposed"} if relationship.source_entity and origin != "ai_proposed" else {}),
                    "join_type": {"default": "left"},
                    "validation": {"target_unique": "not_checked", "source_fk_coverage": "not_checked", "fanout": "not_checked"},
                    "confidence": relationship.confidence,
                    "evidence": relationship.evidence,
                    "warnings": (
                        ["AI-proposed relationship; review before activation."]
                        if origin == "ai_proposed"
                        else ["AI-proposed semantic endpoints; physical relationship is declared."]
                        if relationship.source_entity and relationship.target_entity
                        else []
                    ),
                },
            },
            f"# {object_id}\n\n{relationship.description or 'Generated relationship candidate.'}",
        )
    for concept in concepts:
        object_id = str(concept["id"])
        name = str(concept.get("name") or object_id.split(".", 1)[1].replace("-", " ").title())
        description = str(concept.get("description") or "Generated business concept.")
        _write_doc(
            root / "concepts" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "concept", "id": object_id, "name": name, "description": description,
                "status": "draft", "aliases": concept.get("aliases", []), "links": concept["links"],
                "generated": generated,
                "provenance": {"origin": "ai_proposed", "source": proposal.provider},
                "cerebro": {"classification": concept.get("classification", "internal"), "maps_to": concept["links"]},
            },
            f"# {name}\n\n{description}",
        )
    for metric in metrics:
        object_id = str(metric["id"])
        name = str(metric.get("name") or object_id.split(".", 1)[1].replace("-", " ").title())
        description = str(metric.get("description") or "Generated governed metric.")
        _write_doc(
            root / "metrics" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "metric", "id": object_id, "name": name, "description": description,
                "status": "draft", "links": metric["dependencies"],
                "generated": generated,
                "provenance": {"origin": "ai_proposed", "source": proposal.provider},
                    "cerebro": {
                        "classification": metric.get("classification", "internal"),
                        "dependencies": metric["dependencies"], "formula": metric["formula"],
                        "metric_result_type": "decimal",
                        "filters": metric.get("filters", []), "grain": metric.get("grain", "aggregate"),
                    "warnings": metric.get("warnings", []),
                },
            },
            f"# {name}\n\n{description}\n\nFormula: `{metric['formula']}`",
        )
    for policy in policies:
        object_id = str(policy["id"])
        name = str(policy["name"])
        description = str(policy["description"])
        _write_doc(
            root / "policies" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "policy", "id": object_id, "name": name, "description": description,
                "status": "draft", "links": policy["applies_to"],
                "generated": generated,
                "provenance": {"origin": "ai_proposed", "source": proposal.provider},
                "cerebro": {
                    "classification": policy["classification"],
                    "applies_to": policy["applies_to"], "rule": policy["rule"],
                    "confidence": policy["confidence"], "evidence": policy["evidence"],
                    "warnings": policy["warnings"],
                },
            },
            f"# {name}\n\n{description}\n\nRule: {policy['rule']}",
        )
    if policy_tables:
        _write_doc(
            root / "policies" / "generated-data-handling.md",
            {
                "type": "policy", "id": "policy.generated-data-handling", "name": "Generated data handling",
                "description": "Restricted data is blocked and confidential data requires aggregation.",
                "status": "draft", "links": policy_tables,
                "generated": generated,
                "provenance": {"origin": "derived", "source": "catalog classifications"},
                "cerebro": {"classification": "restricted", "applies_to": policy_tables, "warnings": ["Never expose raw restricted fields."]},
            },
            "# Generated data handling\n\nRestricted fields must never be returned to a model.",
        )
    for domain in domains:
        object_id = str(domain["id"])
        name = str(domain["name"])
        description = str(domain["description"])
        _write_doc(
            root / "domains" / f"{object_id.split('.', 1)[1]}.md",
            {
                "type": "Domain", "id": object_id, "name": name, "title": name,
                "description": description, "status": "draft",
                "generated": generated,
                "provenance": {"origin": "ai_proposed", "source": proposal.provider},
                "cerebro": {
                    "kind": "domain", "classification": domain["classification"],
                    "owner": domain.get("owner"), "warnings": domain.get("warnings", []),
                },
            },
            f"# {name}\n\n{description}",
        )
    for entity in entities:
        object_id = str(entity["id"])
        table = str(entity["physical_mapping"]["table"])
        links = [table] + ([entity["domain"]] if entity.get("domain") else [])
        _write_doc(root / "entities" / f"{object_id.split('.', 1)[1]}.md", {
            "type": "Entity", "id": object_id, "name": entity["name"], "title": entity["name"],
            "description": entity["description"], "status": "draft", "aliases": entity.get("aliases", []),
            "links": links, "generated": generated,
            "provenance": {"origin": "ai_proposed", "source": proposal.provider},
            "cerebro": {
                "kind": "entity", "classification": entity["classification"],
                "physical_mapping": entity["physical_mapping"], "grain": entity["grain"],
                "domain": entity.get("domain"),
                "warnings": entity.get("warnings", []),
            },
        }, f"# {entity['name']}\n\n{entity['description']}")
    for dimension in dimensions:
        object_id = str(dimension["id"])
        links = sorted({dimension["entity"], *[item["table"] for item in dimension["physical_mappings"]], *dimension["compatible_metrics"]})
        _write_doc(root / "dimensions" / f"{object_id.split('.', 1)[1]}.md", {
            "type": "Dimension", "id": object_id, "name": dimension["name"], "title": dimension["name"],
            "description": dimension["description"], "status": "draft", "links": links,
            "generated": generated, "provenance": {"origin": "ai_proposed", "source": proposal.provider},
            "cerebro": {
                "kind": "dimension", "classification": dimension["classification"],
                "entity": dimension["entity"], "physical_mappings": dimension["physical_mappings"],
                "semantic_type": dimension["semantic_type"], "derivation": dimension.get("derivation"),
                "compatible_metrics": dimension["compatible_metrics"], "warnings": dimension.get("warnings", []),
            },
        }, f"# {dimension['name']}\n\n{dimension['description']}")
        for metric in structured_metrics:
            object_id = str(metric["id"])
            links = sorted({metric["entity"], *metric["dependencies"], *metric["compatible_dimensions"], *([metric["time_dimension"]] if metric.get("time_dimension") else [])})
            formula = metric_formula(metric["measure"])
            metric_result_type = (
                "integer"
                if metric["measure"]["kind"] == "aggregate"
                and metric["measure"]["aggregation"] in {"count", "count_distinct"}
                else "decimal"
            )
            _write_doc(root / "metrics" / f"{object_id.split('.', 1)[1]}.md", {
            "type": "Metric", "id": object_id, "name": metric["name"], "title": metric["name"],
            "description": metric["description"], "status": "draft", "links": links,
            "generated": generated, "provenance": {"origin": "ai_proposed", "source": proposal.provider},
            "cerebro": {
                "kind": "metric", "classification": metric["classification"], "entity": metric["entity"],
                    "measure": metric["measure"], "dependencies": metric["dependencies"],
                    "formula": formula, "metric_result_type": metric_result_type,
                    "filters": [], "grain": metric["grain"],
                "compatible_dimensions": metric["compatible_dimensions"],
                "time_dimension": metric.get("time_dimension"),
                "relative_time_anchor": metric.get("relative_time_anchor"),
                "warnings": metric.get("warnings", []),
            },
        }, f"# {metric['name']}\n\n{metric['description']}\n\nFormula: `{formula}`")
    for rule in rules:
        object_id = str(rule["id"])
        links = sorted({rule["entity"], *rule["dependencies"]})
        _write_doc(root / "rules" / f"{object_id.split('.', 1)[1]}.md", {
            "type": "Business Rule", "id": object_id, "name": rule["name"], "title": rule["name"],
            "description": rule["description"], "status": "draft", "links": links,
            "generated": generated, "provenance": {"origin": "ai_proposed", "source": proposal.provider},
            "cerebro": {
                "kind": "business_rule", "classification": rule["classification"], "entity": rule["entity"],
                "rule_kind": rule["rule_kind"], "output_type": rule["output_type"],
                "dependencies": rule["dependencies"], "logic": rule["logic"], "grain": rule["grain"],
                "warnings": rule.get("warnings", []),
            },
        }, f"# {rule['name']}\n\n{rule['description']}\n\n{rule['logic']}")
    for folder in ("datasets", "tables", "relationships", "concepts", "domains", "entities", "dimensions", "metrics", "rules", "policies"):
        if (root / folder).is_dir():
            (root / folder / "index.md").write_text(f"# {folder.title()}\n", encoding="utf-8")
    report = BundleValidator().validate(BundleLoader().load(root))
    if not report.valid:
        raise CandidateValidationError(report.issues)
    return root


@dataclass(frozen=True)
class GenerationResult:
    output: Path
    bundle: SemanticBundle
    proposal: SemanticProposal
    snapshot: CatalogSnapshot


def run_generation_workflow(
    config_path: Path | str = DEFAULT_CONFIG,
    database_path: Path | str | None = None,
    output: Path | str | None = None,
    provider: GenerationProvider | None = None,
    on_stage: StageCallback | None = None,
    on_trace: TraceCallback | None = None,
    source_mode: str = "configured",
    database_schema: str | None = None,
    documentation_agent: DocumentationEnrichmentAgent | None = None,
    include_query_semantics: bool = True,
) -> GenerationResult:
    if source_mode not in {"configured", "database_only"}:
        raise ValueError("source_mode must be configured or database_only")
    current_stage = "source_check"

    def emit(stage: str, status: str, summary: str, details: dict[str, Any] | None = None) -> None:
        nonlocal current_stage
        current_stage = stage
        if on_stage:
            on_stage(stage, status, summary, details or {})

    def trace(
        stage: str,
        status: str,
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
    ) -> None:
        if on_trace:
            on_trace(
                stage,
                status,
                _sanitize_trace_payload(input_payload) if input_payload is not None else None,
                _sanitize_trace_payload(output_payload) if output_payload is not None else None,
            )

    try:
        trace(
            "source_check",
            "started",
            {
                "source_mode": source_mode,
                "schema": database_schema or "configured default",
                "access": "read-only DuckDB",
            },
        )
        emit("source_check", "started", f"Checking the {source_mode.replace('_', '-')} read-only DuckDB source.")
        source = DuckDBSource(
            config_path,
            database_path,
            source_mode=source_mode,  # type: ignore[arg-type]
            schema=database_schema,
        )
        trace("source_check", "completed", output_payload={"reachable": True, "access": "read-only"})
        emit("source_check", "completed", "The DuckDB source is reachable.")

        trace(
            "catalog_scan",
            "started",
            {"source_mode": source_mode, "read": "catalog metadata only", "rows_read": 0},
        )
        emit("catalog_scan", "started", "Reading tables, columns, native comments, and supported catalog constraints.")
        snapshot = source.scan()
        trace(
            "catalog_scan",
            "completed",
            output_payload=snapshot.model_dump(mode="json", exclude={"database_path"}),
        )
        emit(
            "catalog_scan",
            "completed",
            f"Discovered {len(snapshot.tables)} tables, {snapshot.column_count} columns, and {len(snapshot.relationships)} catalog relationships.",
            {
                "tables": len(snapshot.tables),
                "columns": snapshot.column_count,
                "declared_relationships": len(snapshot.relationships),
                "row_sampling": snapshot.row_sampling,
                "rows_read": snapshot.discovery_evidence.get("rows_read", 0),
                "config_loaded": snapshot.discovery_evidence.get("config_loaded", False),
            },
        )

        proposal = SemanticEnricher(provider).enrich(
            snapshot,
            on_stage=emit,
            on_trace=trace,
            include_query_semantics=include_query_semantics,
        )
        if documentation_agent is not None:
            if source_mode == "database_only":
                raise ValueError("Documentation enrichment is disabled in database-only mode")
            proposal = documentation_agent.enrich(snapshot, proposal)

        trace("compile_okf", "started", proposal.model_dump(mode="json"))
        emit("compile_okf", "started", "Linking semantic proposals and compiling OKF documents.")
        candidate_path = compile_candidate_bundle(snapshot, proposal, output)
        compiled = BundleLoader().load(candidate_path)
        compiled_counts: dict[str, int] = {}
        for obj in compiled.objects:
            compiled_counts[obj.profile_kind] = compiled_counts.get(obj.profile_kind, 0) + 1
        compiled_summary = {
            "name": compiled.name,
            "version": compiled.version,
            "counts": compiled_counts,
            "object_ids": [obj.id for obj in compiled.objects],
            "review_state": compiled.review_state,
            "empty_categories": [
                kind for kind in ("metric", "business_rule") if compiled_counts.get(kind, 0) == 0
            ],
            "linker_warnings": sorted({
                str(warning)
                for obj in compiled.objects
                for warning in obj.cerebro.get("warnings", [])
                if str(warning).startswith("Semantic linker ")
            }),
        }
        trace("compile_okf", "completed", output_payload=compiled_summary)
        emit(
            "compile_okf",
            "completed",
            f"Compiled {len(compiled.objects)} OKF semantic objects.",
            {"objects": len(compiled.objects)},
        )

        trace("validate_candidate", "started", compiled_summary)
        emit("validate_candidate", "started", "Validating OKF documents, links, joins, metrics, and provenance.")
        report = BundleValidator().validate(compiled)
        if not report.valid:
            raise ValueError("Generated candidate did not pass semantic contract validation")
        validation_output = {"valid": True, "objects": report.document_count, "issues": []}
        trace("validate_candidate", "completed", output_payload=validation_output)
        emit(
            "validate_candidate",
            "completed",
            f"Validated {report.document_count} semantic objects with no contract errors.",
            {"objects": report.document_count, "issues": 0},
        )
        trace("candidate_ready", "completed", validation_output, compiled_summary)
        emit(
            "candidate_ready",
            "completed",
            "Candidate is ready to preview. It is not active and is not available to downstream agents.",
            {"review_state": "candidate"},
        )
        return GenerationResult(candidate_path, compiled, proposal, snapshot)
    except CandidateValidationError as exc:
        trace(current_stage, "failed", output_payload={"error": exc.public_message, **exc.details})
        emit(current_stage, "failed", exc.public_message, exc.details)
        raise
    except GenerationOutputError as exc:
        trace(
            current_stage,
            "failed",
            output_payload={"error": exc.public_message, "schema": exc.schema_name},
        )
        emit(current_stage, "failed", exc.public_message, {"schema": exc.schema_name})
        raise
    except Exception:
        trace(current_stage, "failed", output_payload={"error": "This stage failed. Inspect the server log for details."})
        emit(current_stage, "failed", "This stage failed. Inspect the server or CLI log for details.")
        raise


class ReviewConflictError(RuntimeError):
    pass


class ReviewValidationError(ValueError):
    pass


class ActivationError(ValueError):
    pass


def bundle_digest(bundle_path: Path | str) -> str:
    root = Path(bundle_path).resolve()
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "approval.json"):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _review_payload(record: ReviewRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _review_actor(reviewer: str) -> str:
    return f"human:{_slug(reviewer)}"


def _promote_reviewed_documents(root: Path, reviewer: str, reviewed_at: str) -> None:
    verification = {"by": _review_actor(reviewer), "at": reviewed_at}
    for path in sorted(root.rglob("*.md")):
        if path.name in {"index.md", "log.md"}:
            continue
        document = OKFDocument.parse(path.read_text(encoding="utf-8"))
        frontmatter = dict(document.frontmatter)
        frontmatter["status"] = "stable"
        verified = frontmatter.get("verified", [])
        if isinstance(verified, dict):
            verified = [verified]
        if not isinstance(verified, list):
            verified = []
        if verification not in verified:
            verified.append(verification)
        frontmatter["verified"] = verified
        path.write_text(OKFDocument(frontmatter=frontmatter, body=document.body).serialize(), encoding="utf-8")


def review_bundle(
    bundle_path: Path | str,
    *,
    reviewer: str,
    decision: str,
    comment: str = "",
    acknowledge_ai_risk: bool = False,
    reviewed_root: Path | str = ROOT / "knowledge" / "reviewed",
) -> ReviewRecord:
    candidate_path = Path(bundle_path).resolve()
    reviewer = reviewer.strip()
    comment = comment.strip()
    if not reviewer:
        raise ReviewValidationError("Reviewer name is required")
    if decision not in {"approve", "reject"}:
        raise ReviewValidationError("Decision must be approve or reject")
    if decision == "approve" and not acknowledge_ai_risk:
        raise ReviewValidationError("Approval requires explicit AI-risk acknowledgement")
    if decision == "reject" and not comment:
        raise ReviewValidationError("Rejection requires a comment")

    existing_path = candidate_path / "review.json"
    requested = {
        "decision": decision,
        "reviewer": reviewer,
        "comment": comment,
        "acknowledge_ai_risk": acknowledge_ai_risk,
    }
    if existing_path.is_file():
        existing = ReviewRecord.model_validate_json(existing_path.read_text(encoding="utf-8"))
        comparable = {
            "decision": existing.decision,
            "reviewer": existing.reviewer,
            "comment": existing.comment,
            "acknowledge_ai_risk": existing.acknowledge_ai_risk,
        }
        if comparable == requested:
            return existing
        raise ReviewConflictError("Candidate already has a different review decision")

    bundle = BundleLoader().load(candidate_path)
    report = BundleValidator().validate(bundle)
    if not report.valid:
        raise ReviewValidationError("Cannot review invalid candidate")
    if bundle.review_state != "candidate":
        raise ReviewValidationError(f"Expected candidate review state, found {bundle.review_state}")
    candidate_digest = bundle_digest(candidate_path)
    run_id = candidate_path.name
    reviewed_path: Path | None = None
    reviewed_digest: str | None = None

    if decision == "approve":
        reviewed_at = datetime.now(timezone.utc).isoformat()
        reviewed_parent = Path(reviewed_root).resolve()
        reviewed_parent.mkdir(parents=True, exist_ok=True)
        reviewed_path = reviewed_parent / run_id
        if reviewed_path.exists():
            raise ReviewConflictError(f"Reviewed bundle already exists: {reviewed_path}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=reviewed_parent))
        try:
            for source in candidate_path.iterdir():
                if source.name == "review.json":
                    continue
                destination = temporary / source.name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            manifest_path = temporary / "bundle.yaml"
            metadata = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            metadata["review_state"] = "approved"
            manifest_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
            _promote_reviewed_documents(temporary, reviewer, reviewed_at)
            approved = BundleLoader().load(temporary)
            approved_report = BundleValidator().validate(approved)
            if not approved_report.valid:
                raise ReviewValidationError("Approved copy failed validation")
            reviewed_digest = bundle_digest(temporary)
            record = ReviewRecord(
                run_id=run_id,
                decision="approve",
                reviewer=reviewer,
                comment=comment,
                acknowledge_ai_risk=True,
                reviewed_at=reviewed_at,
                candidate_digest=candidate_digest,
                reviewed_bundle=str(reviewed_path),
                reviewed_digest=reviewed_digest,
            )
            (temporary / "approval.json").write_text(
                json.dumps(_review_payload(record), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.replace(reviewed_path)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
    else:
        record = ReviewRecord(
            run_id=run_id,
            decision="reject",
            reviewer=reviewer,
            comment=comment,
            acknowledge_ai_risk=acknowledge_ai_risk,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            candidate_digest=candidate_digest,
        )
    existing_path.write_text(json.dumps(_review_payload(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def activate_bundle(
    bundle_path: Path | str,
    *,
    trusted_bundle_path: Path | str | None = None,
    active_pointer: Path | str | None = None,
) -> dict[str, Any]:
    path = Path(bundle_path).resolve()
    bundle = BundleLoader().load(path)
    report = BundleValidator().validate(bundle)
    if not report.valid:
        raise ActivationError("Cannot activate invalid bundle: " + "; ".join(issue.message for issue in report.issues))
    if bundle.review_state != "approved":
        raise ActivationError("Only an approved reviewed bundle can be activated")
    trusted_path = Path(trusted_bundle_path).resolve() if trusted_bundle_path is not None else None
    if path != trusted_path:
        receipt_path = path / "approval.json"
        if not receipt_path.is_file():
            raise ActivationError("Approved bundle is missing approval.json")
        receipt = ReviewRecord.model_validate_json(receipt_path.read_text(encoding="utf-8"))
        if receipt.decision != "approve" or receipt.reviewed_digest != bundle_digest(path):
            raise ActivationError("Reviewed bundle digest does not match its approval receipt")
    pointer = Path(active_pointer) if active_pointer is not None else ACTIVE_BUNDLE_POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "path": str(path),
        "name": bundle.name,
        "version": bundle.version,
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(pointer)
    return payload
