# Cerebro Semantic Layer Definition

> **Status:** Implemented prototype contract
>
> **Format:** Google Open Knowledge Format v0.2 with Cerebro Semantic Profile v0.1
>
> **Scope:** The checked-in ten-table retail-banking DuckDB dataset

## Purpose

Cerebro's semantic layer is a governed translation between business language and physical data. It records the entities people reason about, the dimensions used to group them, the metrics and rules used to analyze them, the relationships that connect them, and the physical tables and columns that support those meanings.

The runtime follows this direction:

```text
Business question
    -> metric, dimension, or business rule
    -> owning entities and governed relationships
    -> physical tables, columns, and join path
```

This keeps table selection and join inference grounded in reviewed contracts. It also gives every selected object a stable identifier and source metadata that can be returned with grounding evidence.

## Architecture and ownership

```text
Catalog Scan
    -> SemanticInventoryAgent
    -> RelationshipAgent
    -> MetricRuleAgent
    -> Semantic Linker
    -> OKF Compiler
    -> Validator
    -> Human Review
    -> Activation
```

The boundaries are deliberate:

- `DuckDBSource` owns physical discovery and opens the database read-only.
- The three agents are bounded provider wrappers. Each makes exactly one typed call from sanitized catalog metadata and cannot read source rows, compile documents, validate candidates, review, or activate.
- Pydantic models constrain each proposal before compilation.
- The linker derives graph edges from typed contracts.
- The compiler writes a separate candidate bundle with `draft` objects.
- The validator owns physical references, compatible types, links, keys, grain, relationship endpoints, and metric bindings.
- A human owns business approval. Approval creates an immutable reviewed copy, promotes its documents to `stable`, and records the verifier.
- Activation accepts only a validated approved copy whose content still matches its review digest.

Generated output never updates the active semantic layer directly.

## OKF and the Cerebro profile

OKF supplies the portable Markdown and YAML envelope. Cerebro uses the namespaced `cerebro` block for executable semantic contracts.

The root `index.md` contains the OKF version:

```yaml
---
okf_version: "0.2"
---
```

Nested `index.md` files are navigation documents without frontmatter. Semantic documents use normal OKF fields such as `type`, `id`, `title`, `description`, `status`, `links`, `sources`, `generated`, and `verified`.

The bundle manifest declares the profile independently:

```yaml
name: bank-workshop
version: 0.2.0
okf_version: "0.2"
semantic_profile_version: "0.1"
review_state: approved
```

`cerebro.kind` selects the profile contract. The loader also normalizes legacy documents:

- `table` becomes `physical_table` internally.
- `concept` becomes `legacy_concept` internally.
- `name` supplies `title` when needed.
- `active` is accepted as the legacy form of `stable`.
- Unknown OKF types and fields remain available rather than being discarded.

## Bundle structure

```text
knowledge/bank-workshop/
├── index.md
├── bundle.yaml
├── datasets/
├── tables/
├── entities/
├── dimensions/
├── metrics/
├── rules/
├── relationships/
└── policies/
```

The reviewed golden bundle contains:

| Kind | Count | Purpose |
| --- | ---: | --- |
| Dataset | 1 | Groups the physical catalog |
| Physical table | 10 | Records 75 discovered columns and table grain |
| Entity | 10 | Defines business objects and their physical identity |
| Dimension | 11 | Defines governed grouping attributes |
| Metric | 6 | Defines aggregate and ratio measures |
| Business rule | 6 | Defines predicates, classifications, direction, and time behavior |
| Relationship | 11 | Connects semantic entities and physical join endpoints |
| Policy | 1 | Defines handling for sensitive banking data |

Physical table documents contain catalog facts and relationship membership. They do not carry reverse backlinks for every semantic object; the linker derives semantic edges from the objects that own those mappings.

## Profile contracts

### Entity

An entity has one primary physical table, one or more key columns, and a typed grain.

```yaml
type: Entity
id: entity.customer
title: Customer
status: stable
links: [table.customers]
cerebro:
  kind: entity
  classification: restricted
  physical_mapping:
    table: table.customers
    key: [customer_id]
  grain:
    type: entity
    description: one row per bank customer
    key: [customer_id]
```

### Dimension

A dimension belongs to an entity and binds to one or more real columns. It declares its semantic type and compatible metrics.

```yaml
type: Dimension
id: dimension.transaction-channel
title: Transaction Channel
links: [entity.transaction, metric.transaction-volume, table.transactions]
cerebro:
  kind: dimension
  entity: entity.transaction
  physical_mappings:
    - table: table.transactions
      column: channel
  semantic_type: categorical
  compatible_metrics: [metric.transaction-volume]
```

Derived dimensions include a deterministic derivation description. The customer-age dimension, for example, defines completed years from `date_of_birth` at the relevant maximum available date.

### Metric

A metric belongs to an entity and stores a typed measure tree. Aggregate measures support `count`, `count_distinct`, `sum`, `avg`, `min`, and `max`. Ratio measures contain typed numerator and denominator aggregates plus a scale. Column bindings and predicates must resolve to the catalog.

```yaml
type: Metric
id: metric.card-fraud-rate
title: Card Fraud Rate
cerebro:
  kind: metric
  entity: entity.card-transaction
  measure:
    kind: ratio
    numerator:
      kind: aggregate
      aggregation: count
      predicates:
        - source: {table: table.card_transactions, column: is_fraud}
          operator: eq
          value: 1
    denominator:
      kind: aggregate
      aggregation: count
      predicates: []
    scale: 100
  dependencies: [table.card_transactions]
  compatible_dimensions:
    - dimension.merchant-category
    - dimension.card-type
  grain:
    type: aggregate
    description: Requested compatible dimensions
```

The compiler derives a readable SQL expression from the typed measure. That expression is explanatory output; the typed measure remains the semantic source of truth.

The golden metrics are transaction volume, account balance, customer count, card fraud rate, late payment rate, and non-performing loan rate.

### Business rule

A business rule belongs to an entity and declares its rule kind, output type, dependencies, logic, and grain. Rules are definitions for later planning; they are not free-form instructions to execute.

The golden rules define active customer behavior, fraudulent card transactions, late loan payments, non-performing loans, transaction direction, and the maximum-available-date anchor for relative time.

### Relationship

A relationship stores semantic and physical views of the same governed connection:

```yaml
type: Relationship
id: relationship.transaction_account
cerebro:
  kind: relationship
  semantic:
    from: entity.transaction
    to: entity.account
  physical:
    source: {table: table.transactions, column: account_id}
    target: {table: table.accounts, column: account_id}
  cardinality: many-to-one
  join_type:
    default: left
  validation:
    target_unique: not_checked
    source_fk_coverage: not_checked
    fanout: not_checked
```

Catalog constraints and reviewed declarations are allowed evidence. Source-row uniqueness, coverage, and fanout profiling are disabled, so those checks are explicitly stored as `not_checked`.

### Policy

Policies link handling rules to physical tables. The banking policy requires aggregate results and minimization of restricted fields. SQL enforcement remains in the guarded chat runtime; the policy object provides the reviewed semantic evidence for that enforcement.

## Bounded semantic generation

`SemanticEnricher` sanitizes the catalog snapshot once. The interactive Build smoke test invokes two structural agents in order:

1. **`SemanticInventoryAgent`** proposes entities, dimensions, aliases, classifications, table purposes, and reviewable policies.
2. **`RelationshipAgent`** receives the inventory and proposes catalog-bounded physical endpoints plus optional entity endpoints, cardinality, confidence, and evidence.
3. **`MetricRuleAgent`** remains an optional compatibility path, but is not invoked by the smoke test. Its stable `query_semantics` progress stage is reported as skipped because metrics and rules are authored after activation.

The externally visible progress identifiers remain `business_semantics`, `relationship_semantics`, and `query_semantics` for client compatibility. Events identify the responsible wrapper through `details.agent_id` values `semantic_inventory`, `relationship`, and `metric_rule`. The compilation stage remains `compile_okf` and is presented as **Link and compile OKF**.

When no model is configured, fallback generation emits only the discovered dataset, physical tables, and catalog or declared relationships. It does not invent entities, dimensions, metrics, rules, or policies. A configured smoke run may propose entities, dimensions, relationships, and policies, but still emits no metrics or business rules.

## Post-activation definition authoring

Metrics and business rules are governed semantic objects, but they are not guessed during the raw-database smoke test. Once a structural graph is approved and activated, the Definition composer lets a user describe one definition in natural language or start from a compact typed form. Natural-language translation receives approved graph metadata only and never source rows.

Saving a definition forks the active bundle into an `authored` candidate revision. The form validates entity ownership, table and column bindings, dimensions, dependencies, measure structure, and rule logic before the draft graph is shown. The active graph and `bank-workshop` v0.2.0 golden graph are never mutated. The authored revision must be reviewed, copied immutably, and activated through the same explicit gates as a generated candidate.

## Deterministic validation

A candidate cannot reach review when it contains:

- duplicate semantic IDs;
- missing or incompatible semantic targets;
- invented tables, columns, entity keys, metric bindings, or join endpoints;
- invalid classifications, cardinalities, grain contracts, or relationship semantics;
- links that disagree with the typed profile;
- empty metric dependencies or rule logic;
- policy targets outside the physical catalog.

Generated candidates remain `draft` with `ai_proposed` provenance. Approval preserves that origin, records human verification on every reviewed document, promotes the reviewed copy to `stable`, and computes the review digest after promotion.

## Progressive retrieval

Grounding does not expand the complete graph. It performs three bounded steps:

1. Rank typed semantic candidates with lexical retrieval, optional embeddings, and reciprocal-rank fusion.
2. Select the best metric, dimension, rule, entity, relationship, or policy seeds for the question and follow only their declared mappings.
3. Add the shortest governed relationship paths between the selected entities and then add the required physical tables and columns.

The grounding response includes `entities`, `dimensions`, `metrics`, `rules`, legacy `concepts`, physical `tables`, `joins`, columns, grain, warnings, classifications, provenance, and ranking evidence. Existing HTTP routes and MCP tool names remain compatible.

## Profile-aware graph projection

The implemented graph projects entities, dimensions, metrics, business rules, policies, physical bindings, and governed relationships from the golden, active, generated-candidate, or authored-revision bundle. Backend `profile_kind` is the canonical presentation and filtering key; raw OKF `type` remains available in the inspector. Every graph uses one shared visual schema, including amber/yellow hexagons for metrics and green octagons for business rules. Canonical entity-to-entity semantic relationships carry direction and cardinality, while smaller relationship nodes preserve audit and source navigation. Physical joins retain source-to-target arrows plus explicit endpoint cardinality labels.

The UI exposes All, Physical, Semantic, Metrics, and Governance layer presets, per-kind checkboxes, grouped keyboard navigation, search, focused-path fading, source links, and profile-specific inspection. The inspector also shows status, source records, generated metadata, verification records, provenance, and legacy `active` status as `stable`.

## Evaluation

`evaluation/semantic-questions.yaml` contains 30 typed semantic cases. Each case records expected objects by kind so metric, dimension, rule, entity, table, and relationship resolution can be assessed independently. `evaluation/golden-questions.yaml` retains ten earlier banking intents mapped to the new profile.

`compare_semantic_oracle` reports the full precision and recall profile. `compare_structural_oracle` scores physical tables, entities, dimensions, relationships, and policies for smoke runs while explicitly marking metrics and business rules as deferred. Golden semantics are never included in generation prompts.

Run the release checks with:

```bash
cerebro validate
cerebro evaluate
pytest -q
```

## Current boundary

Semantic query-planner decomposition, deterministic physical planning, SQL-architecture migration, and source-row relationship profiling remain separate follow-up work. The longer-term Text-to-SQL multi-agent topology is a target, not current builder behavior. The current runtime consumes the upgraded grounding contract while retaining its existing query-plan and SQL interfaces.
