# Cerebro UI and OKF v0.2 Guide

This guide explains how the Cerebro workspace projects the reviewed
`knowledge/bank-workshop` OKF v0.2 bundle and how to trace business meaning to
physical data without changing or activating semantic definitions.

## Start locally

From the repository root:

```bash
./scripts/dev.sh
```

Open <http://127.0.0.1:5173>. The FastAPI and MCP service runs at
<http://127.0.0.1:8000>.

## Workspace layout

- The left rail selects the Semantic Constellation or Text-to-SQL workspace and
  can collapse independently.
- In the semantic workspace, layer presets and per-kind filters control the
  graph projection. Search matches titles, stable IDs, descriptions, and
  aliases.
- The center canvas supports pan, zoom, fit, reset, selection, and keyboard
  navigation. Its legend explains the active edge grammar.
- The details rail shows the selected object's typed contract and can collapse
  independently. The graph resizes after either rail changes.

Filters and selection never edit the bundle. Build candidates also remain
separate until validation, human review, and explicit activation.

## Semantic Profile v0.1 on OKF v0.2

The bundle uses OKF v0.2 documents plus Cerebro's Semantic Profile v0.1.
`cerebro.kind` is the canonical application kind; raw OKF `type` remains visible
for compatibility.

| Layer | Kinds | Purpose |
| --- | --- | --- |
| Physical | Dataset, Physical table | Source scope, columns, keys, and grain |
| Semantic | Entity, Dimension, Relationship, Business rule | Business identity, slicing, joins, and governed conditions |
| Metrics | Metric | Typed measures, dependencies, compatible dimensions, and time semantics |
| Governance | Policy | Classification and usage constraints |
| Compatibility | Legacy concept, Generic | Older bundles and unknown OKF types |

Columns stay inside physical-table details instead of becoming graph nodes.

## Edge grammar

| Edge | Direction | Meaning |
| --- | --- | --- |
| `physical_fk` | Physical source to target | Approved join with explicit one/many endpoints |
| `relationship_endpoint` | Relationship to member | Membership in a governed join contract |
| `entity_table_mapping` | Entity to physical table | Identity-to-storage binding |
| `dimension_entity` | Dimension to entity | Entity sliced by the dimension |
| `dimension_table_mapping` | Dimension to physical table | Column binding for the dimension |
| `semantic_relationship` | Relationship between entities | Business direction of the join |
| `metric_entity` | Metric to entity | Entity grain governed by the metric |
| `metric_dimension` | Metric to dimension | Compatible grouping or time dimension |
| `metric_dependency` | Metric to dependency | Physical inputs required by the measure |
| `rule_entity` / `rule_dependency` | Rule to governed inputs | Business rule coverage |
| `policy_coverage` | Policy to governed object | Policy applicability |

Physical cardinality remains distinct from semantic direction. Relationship
membership edges are intentionally arrowless.

## Trace an example

For “Card fraud rate by card type”:

1. Select **Metrics**, then `metric.card-fraud-rate`.
2. Follow its compatible dimension to `dimension.card-type` and its entity to
   `entity.card-transaction`.
3. Inspect the metric's typed ratio measure and `table.card_transactions`
   dependency.
4. Follow `relationship.card_transaction_card` to `entity.card` and
   `table.cards` when the query needs card attributes.
5. Inspect `policy.sensitive-banking-data` before using the result.

Use **Open OKF source** to compare any graph object with its Markdown
frontmatter.

## API equivalents

```bash
curl http://127.0.0.1:8000/api/bundles/active
curl http://127.0.0.1:8000/api/graph
curl http://127.0.0.1:8000/api/concepts/metric.card-fraud-rate
```

The graph and grounding APIs expose metadata only. SQL execution is a separate,
authorization-bound surface.

Vietnamese: [ui-and-okf-guide.vi.md](ui-and-okf-guide.vi.md).
