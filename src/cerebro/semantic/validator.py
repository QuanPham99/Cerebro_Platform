from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..models import (
    BusinessRuleCandidate,
    DimensionCandidate,
    DomainCandidate,
    EntityCandidate,
    SemanticObject,
    StructuredMetricCandidate,
    ValidationIssue,
    normalize_profile_kind,
)


def _issue(obj: SemanticObject, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, path=obj.path)


def _binding_issues(
    obj: SemanticObject,
    bindings: object,
    tables: dict[str, SemanticObject],
    table_columns: dict[str, set[str]],
    prefix: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    values = bindings if isinstance(bindings, list) else [bindings]
    if not values or values == [None]:
        return [_issue(obj, f"missing_{prefix}_binding", f"{obj.id} has no physical binding")]
    for binding in values:
        if not isinstance(binding, dict):
            issues.append(_issue(obj, f"invalid_{prefix}_binding", f"{obj.id} has an invalid physical binding"))
            continue
        table = str(binding.get("table", ""))
        column = str(binding.get("column", ""))
        if table not in tables:
            issues.append(_issue(obj, f"missing_{prefix}_table", f"{obj.id} references missing table {table}"))
        elif column and column not in table_columns[table]:
            issues.append(_issue(obj, f"undeclared_{prefix}_column", f"{obj.id} references missing {table}.{column}"))
    return issues


def _measure_bindings(measure: object) -> list[dict[str, Any]]:
    if not isinstance(measure, dict):
        return []
    terms = [measure] if measure.get("kind") == "aggregate" else [measure.get("numerator"), measure.get("denominator")]
    bindings: list[dict[str, Any]] = []
    for term in terms:
        if not isinstance(term, dict):
            continue
        if isinstance(term.get("source"), dict):
            bindings.append(term["source"])
        for predicate in term.get("predicates", []):
            if isinstance(predicate, dict) and isinstance(predicate.get("source"), dict):
                bindings.append(predicate["source"])
    return bindings


def _require_refs(
    obj: SemanticObject,
    values: object,
    by_id: dict[str, SemanticObject],
    allowed: set[str],
    code: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    refs = values if isinstance(values, list) else [values]
    for ref in refs:
        target = by_id.get(str(ref))
        if target is None or target.profile_kind not in allowed:
            issues.append(_issue(obj, code, f"{obj.id} references invalid target {ref}"))
    return issues


def validate_profile_object(
    obj: SemanticObject,
    by_id: dict[str, SemanticObject],
    tables: dict[str, SemanticObject],
    table_columns: dict[str, set[str]],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    spec = obj.cerebro
    kind = obj.profile_kind
    expected_kind = normalize_profile_kind(obj.type)
    if expected_kind != "generic" and expected_kind != kind:
        issues.append(_issue(obj, "profile_kind_mismatch", f"{obj.id} type and cerebro.kind disagree"))

    prefixes = {
        "domain": "domain.", "dataset": "dataset.", "physical_table": "table.", "entity": "entity.",
        "dimension": "dimension.", "metric": "metric.", "business_rule": "rule.",
        "relationship": "relationship.", "policy": "policy.", "legacy_concept": "concept.",
    }
    if kind in prefixes and not obj.id.startswith(prefixes[kind]):
        issues.append(_issue(obj, "invalid_profile_id", f"{obj.id} has the wrong prefix for {kind}"))

    if kind == "domain":
        try:
            DomainCandidate.model_validate({
                "id": obj.id, "name": obj.name, "description": obj.description,
                "classification": spec.get("classification"), "owner": spec.get("owner"),
                "warnings": spec.get("warnings", []),
            })
        except ValidationError:
            issues.append(_issue(obj, "invalid_domain_contract", f"{obj.id} does not match the typed domain contract"))

    if kind == "entity":
        try:
            EntityCandidate.model_validate({
                "id": obj.id, "name": obj.name, "description": obj.description,
                "aliases": obj.aliases, "classification": spec.get("classification"),
                "physical_mapping": spec.get("physical_mapping"), "grain": spec.get("grain"),
                "warnings": spec.get("warnings", []),
            })
        except ValidationError:
            issues.append(_issue(obj, "invalid_entity_contract", f"{obj.id} does not match the typed entity contract"))
        mapping = spec.get("physical_mapping")
        issues.extend(_binding_issues(obj, mapping, tables, table_columns, "entity"))
        if isinstance(mapping, dict):
            table = str(mapping.get("table", ""))
            keys = mapping.get("key", [])
            if not isinstance(keys, list) or not keys:
                issues.append(_issue(obj, "missing_entity_key", f"{obj.id} has no entity key"))
            elif table in table_columns:
                for key in keys:
                    if str(key) not in table_columns[table]:
                        issues.append(_issue(obj, "undeclared_entity_key", f"{obj.id} references missing key {table}.{key}"))
        if not isinstance(spec.get("grain"), dict):
            issues.append(_issue(obj, "missing_entity_grain", f"{obj.id} has no typed grain"))
        if spec.get("domain"):
            issues.extend(_require_refs(obj, spec.get("domain"), by_id, {"domain"}, "invalid_entity_domain"))

    elif kind == "dimension":
        try:
            DimensionCandidate.model_validate({
                "id": obj.id, "name": obj.name, "description": obj.description,
                "entity": spec.get("entity"), "physical_mappings": spec.get("physical_mappings"),
                "semantic_type": spec.get("semantic_type"), "derivation": spec.get("derivation"),
                "compatible_metrics": spec.get("compatible_metrics", []),
                "classification": spec.get("classification"), "warnings": spec.get("warnings", []),
            })
        except ValidationError:
            issues.append(_issue(obj, "invalid_dimension_contract", f"{obj.id} does not match the typed dimension contract"))
        issues.extend(_require_refs(obj, spec.get("entity"), by_id, {"entity"}, "invalid_dimension_entity"))
        issues.extend(_binding_issues(obj, spec.get("physical_mappings"), tables, table_columns, "dimension"))
        issues.extend(_require_refs(obj, spec.get("compatible_metrics", []), by_id, {"metric"}, "invalid_dimension_metric"))

    elif kind == "metric" and spec.get("measure"):
        try:
            StructuredMetricCandidate.model_validate({
                "id": obj.id, "name": obj.name, "description": obj.description,
                "entity": spec.get("entity"), "measure": spec.get("measure"),
                "dependencies": spec.get("dependencies"), "grain": spec.get("grain"),
                "compatible_dimensions": spec.get("compatible_dimensions", []),
                "time_dimension": spec.get("time_dimension"),
                "relative_time_anchor": spec.get("relative_time_anchor"),
                "classification": spec.get("classification"), "warnings": spec.get("warnings", []),
            })
        except ValidationError:
            issues.append(_issue(obj, "invalid_metric_contract", f"{obj.id} does not match the typed metric contract"))
        issues.extend(_require_refs(obj, spec.get("entity"), by_id, {"entity"}, "invalid_metric_entity"))
        if not spec.get("dependencies"):
            issues.append(_issue(obj, "missing_metric_dependency", f"{obj.id} has no physical dependency"))
        issues.extend(_require_refs(obj, spec.get("dependencies", []), by_id, {"physical_table"}, "invalid_metric_dependency_target"))
        issues.extend(_require_refs(obj, spec.get("compatible_dimensions", []), by_id, {"dimension"}, "invalid_metric_dimension"))
        if spec.get("time_dimension"):
            issues.extend(_require_refs(obj, spec["time_dimension"], by_id, {"dimension"}, "invalid_metric_time_dimension"))
        bindings = _measure_bindings(spec.get("measure"))
        issues.extend(_binding_issues(obj, bindings, tables, table_columns, "metric"))
        if not isinstance(spec.get("grain"), dict):
            issues.append(_issue(obj, "missing_metric_grain", f"{obj.id} has no typed grain"))

    elif kind == "business_rule":
        try:
            BusinessRuleCandidate.model_validate({
                "id": obj.id, "name": obj.name, "description": obj.description,
                "entity": spec.get("entity"), "rule_kind": spec.get("rule_kind"),
                "output_type": spec.get("output_type"), "dependencies": spec.get("dependencies"),
                "logic": spec.get("logic"), "grain": spec.get("grain"),
                "classification": spec.get("classification"), "warnings": spec.get("warnings", []),
            })
        except ValidationError:
            issues.append(_issue(obj, "invalid_rule_contract", f"{obj.id} does not match the typed business-rule contract"))
        if not spec.get("dependencies"):
            issues.append(_issue(obj, "missing_rule_dependency", f"{obj.id} has no semantic dependency"))
        issues.extend(_require_refs(obj, spec.get("entity"), by_id, {"entity"}, "invalid_rule_entity"))
        issues.extend(_require_refs(
            obj,
            spec.get("dependencies", []),
            by_id,
            {"entity", "dimension", "metric", "business_rule", "physical_table"},
            "invalid_rule_dependency",
        ))
        if not str(spec.get("logic", "")).strip():
            issues.append(_issue(obj, "missing_rule_logic", f"{obj.id} has no rule logic"))
        if not isinstance(spec.get("grain"), dict):
            issues.append(_issue(obj, "missing_rule_grain", f"{obj.id} has no typed grain"))

    elif kind == "relationship":
        semantic = spec.get("semantic")
        if semantic is not None:
            if not isinstance(semantic, dict):
                issues.append(_issue(obj, "invalid_relationship_semantics", f"{obj.id} has invalid semantic endpoints"))
            else:
                issues.extend(_require_refs(
                    obj,
                    [semantic.get("from"), semantic.get("to")],
                    by_id,
                    {"entity"},
                    "invalid_relationship_entity",
                ))
        physical = spec.get("physical", {})
        if isinstance(physical, dict):
            source = physical.get("source", {})
            target = physical.get("target", {})
            expected_links = {
                str(source.get("table", "")) if isinstance(source, dict) else "",
                str(target.get("table", "")) if isinstance(target, dict) else "",
            }
            if isinstance(semantic, dict):
                expected_links.update({str(semantic.get("from", "")), str(semantic.get("to", ""))})
            expected_links.discard("")
            if expected_links and set(obj.links) != expected_links:
                issues.append(_issue(obj, "semantic_links_mismatch", f"{obj.id} links do not match its relationship profile"))

    if kind in {"entity", "dimension", "metric", "business_rule"}:
        expected_links: set[str] = set()
        if kind == "entity":
            expected_links.add(str(spec.get("physical_mapping", {}).get("table", "")))
            if spec.get("domain"):
                expected_links.add(str(spec.get("domain", "")))
        elif kind == "dimension":
            expected_links.add(str(spec.get("entity", "")))
            expected_links.update(str(item.get("table", "")) for item in spec.get("physical_mappings", []) if isinstance(item, dict))
            expected_links.update(str(item) for item in spec.get("compatible_metrics", []))
        elif kind == "metric":
            expected_links.add(str(spec.get("entity", "")))
            expected_links.update(str(item) for item in spec.get("dependencies", []))
            expected_links.update(str(item) for item in spec.get("compatible_dimensions", []))
            if spec.get("time_dimension"):
                expected_links.add(str(spec["time_dimension"]))
        else:
            expected_links.add(str(spec.get("entity", "")))
            expected_links.update(str(item) for item in spec.get("dependencies", []))
        expected_links.discard("")
        if set(obj.links) != expected_links:
            issues.append(_issue(obj, "semantic_links_mismatch", f"{obj.id} links do not match its semantic profile"))
    return issues
