from __future__ import annotations

import re
from typing import Any

from .profile import REFERENCE_PREFIXES


def canonical_object_id(prefix: str, value: str) -> str:
    """Canonicalize generated semantic IDs without changing physical identifiers."""
    normalized_prefix = prefix.lower().strip()
    raw = value.strip()
    supplied_prefix, separator, suffix = raw.partition(".")
    if separator and supplied_prefix.lower() == normalized_prefix:
        raw = suffix
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "unnamed"
    return f"{normalized_prefix}.{slug}"


def normalize_reference(kind: str, value: str) -> str:
    prefix = REFERENCE_PREFIXES[kind]
    if kind == "physical_table":
        return value if value.startswith(f"{prefix}.") else f"{prefix}.{value}"
    return canonical_object_id(prefix, value)


def _sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "(" + ", ".join(_sql_value(item) for item in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"


def _predicate_sql(predicate: dict[str, Any]) -> str:
    source = predicate["source"]
    column = f"{str(source['table']).removeprefix('table.')}.{source['column']}"
    operator = predicate["operator"]
    value = predicate.get("value")
    operators = {
        "eq": "=", "neq": "<>", "in": "IN", "not_in": "NOT IN",
        "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
    }
    if operator == "is_null":
        return f"{column} IS NULL"
    if operator == "not_null":
        return f"{column} IS NOT NULL"
    return f"{column} {operators[operator]} {_sql_value(value)}"


def _aggregate_sql(term: dict[str, Any]) -> str:
    aggregation = str(term["aggregation"])
    source = term.get("source")
    expression = "*" if not source else f"{str(source['table']).removeprefix('table.')}.{source['column']}"
    predicates = term.get("predicates", [])
    if predicates:
        condition = " AND ".join(_predicate_sql(item) for item in predicates)
        if aggregation == "count":
            return f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)"
        expression = f"CASE WHEN {condition} THEN {expression} ELSE 0 END"
    if aggregation == "count_distinct":
        return f"COUNT(DISTINCT {expression})"
    return f"{aggregation.upper()}({expression})"


def metric_formula(measure: dict[str, Any]) -> str:
    if measure["kind"] == "aggregate":
        return _aggregate_sql(measure)
    numerator = _aggregate_sql(measure["numerator"])
    denominator = _aggregate_sql(measure["denominator"])
    scale = float(measure.get("scale", 100.0))
    prefix = "" if scale == 1 else f"{scale:g} * "
    return f"{prefix}{numerator} / NULLIF({denominator}, 0)"
