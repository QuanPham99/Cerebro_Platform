from __future__ import annotations

from typing import Any


def profile_edges(obj: Any) -> list[tuple[str, str, str]]:
    """Return `(target, edge_type, label)` edges for one profile object."""
    spec = obj.cerebro
    kind = obj.profile_kind
    if kind == "dataset":
        return [(str(target), "semantic_mapping", "contains") for target in obj.links]
    if kind == "legacy_concept":
        return [(str(target), "semantic_mapping", "maps to") for target in spec.get("maps_to", obj.links)]
    if kind == "entity":
        mapping = spec.get("physical_mapping", {})
        edges = [(str(mapping.get("table", "")), "entity_mapping", "maps to")]
        if spec.get("domain"):
            edges.append((str(spec.get("domain", "")), "domain_membership", "belongs to"))
        return edges
    if kind == "dimension":
        edges = [(str(spec.get("entity", "")), "dimension_entity", "describes")]
        edges.extend(
            (str(binding.get("table", "")), "dimension_binding", "backed by")
            for binding in spec.get("physical_mappings", [])
        )
        return edges
    if kind == "metric":
        if spec.get("measure"):
            edges = [(str(spec.get("entity", "")), "metric_entity", "measures")]
            edges.extend((str(value), "metric_dimension", "compatible with") for value in spec.get("compatible_dimensions", []))
            edges.extend((str(value), "metric_dependency", "depends on") for value in spec.get("dependencies", []))
            return edges
        return [(str(target), "metric_dependency", "depends on") for target in spec.get("dependencies", obj.links)]
    if kind == "business_rule":
        edges = [(str(spec.get("entity", "")), "rule_entity", "classifies")]
        edges.extend((str(value), "rule_dependency", "depends on") for value in spec.get("dependencies", []))
        return edges
    if kind == "policy":
        return [(str(target), "policy_coverage", "applies to") for target in spec.get("applies_to", obj.links)]
    return []
