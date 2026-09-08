from __future__ import annotations

import re


SEMANTIC_PROFILE_VERSION = "0.1"

PROFILE_KINDS = frozenset({
    "dataset",
    "physical_table",
    "entity",
    "dimension",
    "metric",
    "business_rule",
    "relationship",
    "policy",
    "legacy_concept",
})

TYPE_ALIASES = {
    "table": "physical_table",
    "physical_table": "physical_table",
    "concept": "legacy_concept",
    "business_rule": "business_rule",
}

REFERENCE_PREFIXES = {
    "dataset": "dataset",
    "physical_table": "table",
    "entity": "entity",
    "dimension": "dimension",
    "metric": "metric",
    "business_rule": "rule",
    "relationship": "relationship",
    "policy": "policy",
}


def normalize_profile_kind(value: object, cerebro: object = None) -> str:
    """Resolve permissive OKF types to one stable Cerebro profile kind."""
    raw = str(cerebro["kind"]) if isinstance(cerebro, dict) and cerebro.get("kind") else str(value or "")
    normalized = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    resolved = TYPE_ALIASES.get(normalized, normalized)
    return resolved if resolved in PROFILE_KINDS else "generic"
