"""Generate a synthetic large OKF bundle to prove graph scale claims.

The real golden bundle (`knowledge/bank-workshop`) has ~72 objects - nowhere near
the "thousands of tables/entities" target the progressive-disclosure graph design
(spec 026) is built for. Nothing in the loader, validator, retriever, or graph API
has ever been exercised at that scale. This generator programmatically emits a
valid OKF bundle (domains, entities, physical tables) sized well past that target,
round-tripping through the real `BundleLoader`/`BundleValidator` unmodified, so
`tests/test_graph_scale.py` can assert the tiered/expand-bounded `/api/graph`
behavior actually stays bounded regardless of total bundle size - not just at the
demo bundle's tiny scale.

Deliberately generates only domain/entity/physical_table objects (skipping
dimensions/metrics/rules/relationships): those three kinds are exactly what the
overview tier and 1-hop entity expansion touch, and they are the kinds that
dominate object count at real banking-schema scale, so this is the proportional
and sufficient subset for a scale test rather than a content-realism exercise.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_DOMAIN_COUNT = 40
DEFAULT_ENTITIES_PER_DOMAIN = 20


def _write(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n" + body + "\n"
    path.write_text(text, encoding="utf-8")


def generate_large_bundle(
    root: Path,
    *,
    domain_count: int = DEFAULT_DOMAIN_COUNT,
    entities_per_domain: int = DEFAULT_ENTITIES_PER_DOMAIN,
) -> dict[str, int]:
    """Write a synthetic bundle under ``root``. Returns the generated object counts."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundle.yaml").write_text(
        yaml.safe_dump({
            "name": "synthetic-large-bundle",
            "version": "0.0.1",
            "generation_mode": "fallback",
            "review_state": "approved",
            "okf_version": "0.2",
            "semantic_profile_version": "0.1",
        }),
        encoding="utf-8",
    )

    for domain_index in range(domain_count):
        domain_id = f"domain.synthetic-{domain_index}"
        _write(
            root / "domains" / f"synthetic-{domain_index}.md",
            {
                "type": "Domain",
                "id": domain_id,
                "title": f"Synthetic Domain {domain_index}",
                "description": f"Generated domain {domain_index} for scale testing.",
                "status": "stable",
                "cerebro": {"kind": "domain", "classification": "internal", "warnings": []},
            },
            f"# Synthetic Domain {domain_index}",
        )

        for entity_index in range(entities_per_domain):
            slug = f"synthetic-{domain_index}-{entity_index}"
            entity_id = f"entity.{slug}"
            table_id = f"table.{slug}"

            _write(
                root / "tables" / f"{slug}.md",
                {
                    "type": "Table",
                    "id": table_id,
                    "title": f"Synthetic Table {slug}",
                    "description": f"Generated physical table for {entity_id}.",
                    "status": "stable",
                    "cerebro": {
                        "kind": "physical_table",
                        "classification": "internal",
                        "columns": [{"name": "id", "data_type": "BIGINT", "nullable": False}],
                    },
                },
                f"# Synthetic Table {slug}",
            )

            _write(
                root / "entities" / f"{slug}.md",
                {
                    "type": "Entity",
                    "id": entity_id,
                    "title": f"Synthetic Entity {slug}",
                    "description": f"Generated entity for scale testing ({slug}).",
                    "status": "stable",
                    "links": [table_id, domain_id],
                    "cerebro": {
                        "kind": "entity",
                        "classification": "internal",
                        "domain": domain_id,
                        "physical_mapping": {"table": table_id, "key": ["id"]},
                        "grain": {"type": "entity", "description": "one row per synthetic record", "key": ["id"]},
                        "warnings": [],
                    },
                },
                f"# Synthetic Entity {slug}",
            )

    total_entities = domain_count * entities_per_domain
    return {
        "domain": domain_count,
        "entity": total_entities,
        "physical_table": total_entities,
        "total": domain_count + 2 * total_entities,
    }
