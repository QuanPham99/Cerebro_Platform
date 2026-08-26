# Cerebro Semantic Layer Definition

> **Status:** Final prototype design baseline
>
> **Scope:** PostgreSQL retail-banking semantic foundation grounded in Google OKF v0.2

## What a semantic layer is

A semantic layer is a governed translation between physical database structures and business language.

A database can tell us that `transactions.amount` is a numeric column connected to an account through `account_id`. It does not inherently explain:

- Whether the amount is signed or always positive.
- How credits and debits should be interpreted.
- Whether "customer" means a person, legal party, or account holder.
- Which join path avoids duplicated transactions.
- Whether a balance is current, end-of-day, or averaged.
- Which fields contain personal or financial information.
- Whether a definition was generated, reviewed, or is stale.

The semantic layer records those meanings as an explicit contract:

```text
Business question
       |
       v
Business concepts and definitions
       |
       v
Metrics, grain, dimensions, and policies
       |
       v
Approved relationships and join paths
       |
       v
Physical PostgreSQL tables and columns
```

For Cerebro, the semantic layer is not merely a graph visualization or a collection of AI-generated descriptions. It is versioned knowledge that tells an agent:

1. What the data means.
2. Where that meaning exists physically.
3. How the data can safely be joined.
4. Which assumptions and restrictions apply.
5. Why the information should be trusted.

## What Google Open Knowledge Format provides

Cerebro builds upon Google's maintained [Open Knowledge Format repository](https://github.com/GoogleCloudPlatform/open-knowledge-format) and the [OKF v0.2 specification](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md).

An OKF bundle is a directory of Markdown documents with YAML frontmatter. Google OKF permits domain-specific directory structures, so Cerebro organizes the generated banking bundle as follows:

```text
knowledge/bank-demo/
├── index.md
├── datasets/
│   ├── index.md
│   └── retail_bank.md
├── tables/
│   ├── index.md
│   ├── customers.md
│   ├── accounts.md
│   ├── account_holders.md
│   └── transactions.md
├── concepts/
│   ├── index.md
│   ├── customer.md
│   ├── account.md
│   └── transaction.md
├── relationships/
│   ├── index.md
│   ├── customer_account_ownership.md
│   └── account_transactions.md
├── metrics/
│   ├── index.md
│   └── outgoing_transaction_volume.md
└── policies/
    ├── index.md
    └── restricted_customer_data.md
```

The directory taxonomy is Cerebro's convention; the documents inside it remain valid OKF v0.2 concepts.

| Folder | Semantic purpose |
| --- | --- |
| `datasets/` | Data-source and schema-level knowledge |
| `tables/` | Physical PostgreSQL tables, columns, keys, and grain |
| `concepts/` | Business meanings independent of physical table names |
| `relationships/` | Approved cardinalities and executable SQL join paths |
| `metrics/` | Governed calculations, filters, grains, and aggregation rules |
| `policies/` | Sensitivity classifications and usage restrictions |

The bundle root is the progressive-disclosure entry point for people and agents:

```markdown
---
okf_version: "0.2"
---

# Retail Banking Knowledge Bundle

Semantic knowledge generated from the `bank-demo` PostgreSQL source.

## Contents

- [Retail bank dataset](datasets/retail_bank.md)
- [Physical tables](tables/index.md)
- [Business concepts](concepts/index.md)
- [Approved relationships](relationships/index.md)
- [Metrics](metrics/index.md)
- [Policies](policies/index.md)
```

A simplified table concept could look like this:

```markdown
---
type: PostgreSQL Table
title: Accounts
resource: postgresql://bank/public/accounts
status: stable
tags: [retail-banking, accounts]
generated:
  by: cerebro/openai
  at: 2026-08-26T10:00:00Z
verified:
  - by: human:reviewer
    at: 2026-08-26T11:00:00Z
---

# Accounts

One row represents one retail banking account.

An account can have multiple holders through
[Account Ownership](/relationships/account_ownership.md).
```

Standard Markdown links create graph connections between concepts. OKF v0.2 also defines fields for provenance, verification, trust, freshness, and lifecycle while deliberately avoiding a prescribed database, retrieval engine, or agent runtime.

The normal OKF frontmatter and Markdown body provide portability. Cerebro's additional `cerebro` frontmatter block provides deterministic SQL-grounding details while remaining compatible with the format.

Google's reference implementation currently supplies:

- An agent that produces OKF from BigQuery metadata.
- Optional web-based enrichment.
- OKF parsing and bundle generation.
- Sample bundles and validation tests.
- A Cytoscape-based static graph viewer.
- Trust and freshness metadata handling.

It does not supply:

- A PostgreSQL semantic scanner.
- A complete business semantic model.
- Typed SQL join contracts.
- Hybrid semantic retrieval.
- A Text-to-SQL retrieval agent.
- A review-and-publish web application.
- A production knowledge-serving API.

Cerebro adds these capabilities around the portable OKF contract.

## Four-layer ownership: Google and Cerebro

Google provides the OKF foundation and proof-of-concept production and visualization tools. It does not provide a complete four-layer semantic system.

| Layer | Google currently provides | Cerebro must develop |
| --- | --- | --- |
| **1. Physical** | BigQuery source abstraction, metadata reading, concept listing, and optional row sampling | PostgreSQL scanning, normalized snapshots, and deterministic PK/FK extraction |
| **2. Business concepts** | Gemini/Google ADK enrichment that writes general OKF documents from BigQuery metadata and optional web sources | Banking concepts, aliases, classifications, OpenAI integration, and human review |
| **3. Query semantics** | Extensible OKF documents, Markdown links, provenance, trust, and lifecycle fields | Structured grain, dimensions, measures, join contracts, cardinalities, warnings, and validators |
| **4. Retrieval** | Static Cytoscape viewer, basic title/ID/tag search, type filtering, and backlinks | Hybrid search, typed graph expansion, active versions, policy filtering, MCP tools, and grounding responses |

### What Cerebro reuses directly

- OKF v0.2 document and bundle conventions.
- Markdown with YAML frontmatter.
- Concept paths and progressive `index.md` navigation.
- Standard provenance, generation, verification, trust, freshness, and lifecycle fields.
- Bundle parsing, writing, path handling, and relevant validation tests.
- Markdown-link graph extraction and Cytoscape viewer concepts.
- Example bundles as conformance and visualization references.

### What Cerebro adapts

- Replace the BigQuery source with a normalized PostgreSQL scanner.
- Replace Google-specific model invocation with a provider-neutral interface whose first adapter uses the OpenAI Responses API.
- Replace general enrichment prompts with retail-banking semantic instructions.
- Disable row sampling and web enrichment for the metadata-only prototype.
- Replace the static viewer with a React-based graph review workspace.
- Preserve standard OKF fields while adding the namespaced `cerebro` query-semantics contract.

### What Cerebro builds as new functionality

- PostgreSQL schema snapshots and physical metadata models.
- Banking concept, classification, relationship, and metric proposal schemas.
- Deterministic physical-reference, grain, join, and link validation.
- Human review, editing, approval, rejection, and publication.
- Full-text, vector, and typed-graph retrieval projections.
- MCP tools that return Text-to-SQL grounding packages.
- Golden banking questions for retrieval evaluation.

The implementation boundary is:

```text
Google
  OKF format
  + BigQuery/Gemini reference producer
  + static graph viewer

Cerebro
  PostgreSQL ingestion
  + banking business semantics
  + deterministic query contracts
  + human review
  + hybrid retrieval
  + MCP grounding
```

## How Cerebro maps the semantic layer

Cerebro maps knowledge through four connected levels.

### 1. Physical layer

The physical layer is discovered deterministically from PostgreSQL:

```text
public.customers
  customer_id UUID PK
  full_name TEXT
  date_of_birth DATE
  risk_rating TEXT

public.accounts
  account_id UUID PK
  product_code TEXT
  current_balance NUMERIC

public.account_holders
  customer_id UUID FK -> customers.customer_id
  account_id UUID FK -> accounts.account_id

public.transactions
  transaction_id UUID PK
  account_id UUID FK -> accounts.account_id
  amount NUMERIC
  direction TEXT
  booked_at TIMESTAMP
```

The scanner records database identifiers, data types, primary keys, foreign keys, constraints, and comments. An LLM is not allowed to invent this technical metadata.

### 2. Business concept layer

The enrichment model proposes mappings between physical objects and business ideas:

| Business concept | Physical mapping | Meaning |
| --- | --- | --- |
| Customer | `customers` | A retail banking party recognized by the bank |
| Account | `accounts` | A deposit or transactional account |
| Account holder | `account_holders` | Association between customers and accounts |
| Transaction | `transactions` | A booked movement affecting one account |

These mappings remain proposals until reviewed and approved by a person.

### 3. Query semantics layer

Text-to-SQL requires more deterministic structure than ordinary Markdown links. Cerebro keeps each document valid OKF and adds a namespaced `cerebro` frontmatter block:

```yaml
cerebro:
  physical:
    source: bank-demo
    schema: public
    table: transactions
    snapshot: snapshot-2026-08-26

  grain:
    description: One row per booked account transaction
    key:
      - transaction_id

  dimensions:
    - column: booked_at
      semantic_type: booking_timestamp
    - column: direction
      semantic_type: transaction_direction

  measures:
    - column: amount
      aggregation: sum
      currency_column: currency_code

  relationships:
    - target: tables/accounts
      type: many_to_one
      join:
        - from: account_id
          to: account_id
      approved: true

  classifications:
    - target: account_id
      sensitivity: financial_identifier

  guidance:
    - Treat credits and debits according to the direction column.
    - Do not join customers directly to transactions.
```

This produces a governed join path:

```text
Customer
   `-- account_holders
          `-- Account
                 `-- Transaction
```

It prevents a query agent from inventing invalid joins such as:

```sql
customers.customer_id = transactions.account_id
```

The mapping can also warn that joint accounts may cause one transaction to be attributed to multiple customers.

#### Business concept example

`concepts/customer.md` separates the business meaning of a customer from the physical `customers` table:

```markdown
---
type: Business Concept
title: Customer
description: A retail banking party recognized by the bank.
status: stable
tags: [retail-banking, party, customer]
generated:
  by: cerebro/openai
  at: 2026-08-26T10:00:00Z
verified:
  - by: human:reviewer
    at: 2026-08-26T11:00:00Z

cerebro:
  mappings:
    primary:
      table: tables/customers
      key: [customer_id]

  aliases:
    - client
    - account holder
    - retail customer

  classifications:
    - target: full_name
      sensitivity: pii
    - target: date_of_birth
      sensitivity: restricted

  relationships:
    - target: concepts/account
      via: relationships/customer_account_ownership
      type: many_to_many
---

# Customer

A customer is an individual retail-banking party.

A customer can hold multiple [Accounts](/concepts/account.md), and an account
can be jointly held by multiple customers.

The approved connection is described by
[Customer Account Ownership](/relationships/customer_account_ownership.md).
```

This separation allows a business concept to map to multiple physical sources in a future version without changing how users ask questions.

#### Approved relationship example

`relationships/customer_account_ownership.md` converts a graph connection into an executable and reviewable join contract:

```markdown
---
type: Semantic Relationship
title: Customer Account Ownership
description: Approved relationship connecting customers to their accounts.
status: stable
tags: [ownership, approved-join]

cerebro:
  source: tables/customers
  target: tables/accounts
  cardinality: many_to_many

  bridge:
    table: tables/account_holders

  joins:
    - left:
        table: customers
        column: customer_id
      right:
        table: account_holders
        column: customer_id

    - left:
        table: account_holders
        column: account_id
      right:
        table: accounts
        column: account_id

  warnings:
    - Joint accounts can cause one transaction to be attributed to multiple customers.
---

# Customer Account Ownership

Customers and accounts have a many-to-many relationship through the
[Account Holders table](/tables/account_holders.md).
```

#### Simple metric example

`metrics/outgoing_transaction_volume.md` demonstrates how a governed calculation links business language to query behavior:

```markdown
---
type: Metric
title: Outgoing Transaction Volume
description: Total value of booked debit transactions.
status: stable
tags: [transaction, debit, volume]

cerebro:
  source: tables/transactions
  expression: SUM(transactions.amount)
  grain: Query-dependent
  filter:
    column: transactions.direction
    operator: equals
    value: DEBIT
  time_column: transactions.booked_at
  allowed_dimensions:
    - concepts/customer
    - concepts/account
  warnings:
    - Customer-level grouping can duplicate values for jointly owned accounts.
---

# Outgoing Transaction Volume

The sum of transaction amounts where `direction = 'DEBIT'`.

Use [Customer Account Ownership](/relationships/customer_account_ownership.md)
when analyzing this metric by customer.
```

This is a simple demonstration metric. Complex metric authoring and validation remain outside the five-day prototype.

### 4. Retrieval layer

Published OKF concepts are projected into three retrieval views:

- PostgreSQL full-text search for exact identifiers, aliases, and banking terminology.
- pgvector embeddings for semantic similarity.
- Typed graph edges for approved joins and connected business concepts.

For the question:

> Which customers had the largest outgoing transaction volume?

Cerebro's MCP retrieval tool should return a grounding package similar to:

```json
{
  "semantic_version": "bank-2026-08-26-01",
  "concepts": [
    "concepts/customer",
    "concepts/account",
    "concepts/transaction"
  ],
  "tables": [
    "tables/customers",
    "tables/account_holders",
    "tables/accounts",
    "tables/transactions"
  ],
  "join_path": [
    "customers.customer_id = account_holders.customer_id",
    "account_holders.account_id = accounts.account_id",
    "accounts.account_id = transactions.account_id"
  ],
  "filters": [
    "transactions.direction = 'DEBIT'"
  ],
  "grain": "customer",
  "warnings": [
    "Joint accounts can attribute one transaction to multiple customers."
  ],
  "provenance": [
    "snapshot-2026-08-26",
    "human-reviewed"
  ]
}
```

The Text-to-SQL team consumes this grounding package and generates SQL from it. Its agent should not need to parse the whole knowledge graph or guess relationship meanings.

## Agent allocation

Cerebro should not create one agent for every layer. Layers 1 and 4 are deterministic system responsibilities; only Layers 2 and 3 require semantic judgment.

| Layer | Component | LLM agent? | Reason |
| --- | --- | --- | --- |
| Physical | `PostgreSQLScanner` | No | Database metadata is ground truth and must not be invented |
| Business concepts | `SemanticEnrichmentAgent` | Yes | Definitions, aliases, and conceptual mappings require semantic judgment |
| Query semantics | `SemanticEnrichmentAgent` plus `OKFValidator` | Partly | The agent proposes grain and guidance; deterministic code verifies physical claims |
| Retrieval | `SemanticRetriever` and MCP server | No | Ranking, graph traversal, filtering, and response construction should be reproducible |

For the prototype, one bounded semantic-enrichment agent handles two structured stages:

```text
Stage 1: Business enrichment
  concepts
  definitions
  aliases
  table purpose
  classifications

Stage 2: Query enrichment
  grain
  dimensions and measures
  joins derived from discovered keys
  query guidance
  ambiguity and fan-out warnings
```

The stages may use separate prompts, but they share one workflow and one structured output contract. The agent can propose semantics but cannot publish them directly.

The complete control flow is:

```text
PostgreSQLScanner
  deterministic metadata
        |
        v
SchemaSnapshot
        |
        v
SemanticEnrichmentAgent
  proposes business and query semantics
        |
        v
OKFValidator
  verifies schema references, joins, links, and required fields
        |
        v
Human reviewer
  corrects and approves business meaning
        |
        v
BundlePublisher
  activates an immutable reviewed bundle
        |
        v
SemanticRetriever + MCP
  supplies grounding to the Text-to-SQL team
```

A future production version may split concept, relationship, metric, and policy enrichment into specialist agents. That split should follow semantic responsibilities and measured workflow needs rather than mirroring the four architectural layers.

## Complete semantic mapping example

The full interpretation of a business question can be traced from language to physical SQL inputs:

```text
Question:
"Which customers had the largest outgoing transaction volume?"
                         |
                         v
Metric:
Outgoing Transaction Volume
SUM(transactions.amount)
WHERE direction = 'DEBIT'
                         |
                         v
Business concepts:
Customer -> Account -> Transaction
                         |
                         v
Approved semantic relationship:
Customer <-> Account through account_holders
                         |
                         v
Physical join path:
customers.customer_id
    = account_holders.customer_id

account_holders.account_id
    = accounts.account_id

accounts.account_id
    = transactions.account_id
                         |
                         v
Policy and warning:
Customer PII is restricted.
Joint ownership may duplicate transaction attribution.
```

The generated OKF bundle is therefore simultaneously:

- A human-readable knowledge repository.
- A machine-readable semantic contract.
- A navigable knowledge graph.
- A grounding source for Text-to-SQL agents.

## Five-day prototype mapping

The prototype proves one central claim:

> A mock PostgreSQL banking schema can be transformed into reviewed, visual, machine-retrievable knowledge that reliably grounds a Text-to-SQL agent.

```text
PostgreSQL metadata
        |
        v
Deterministic schema scan
        |
        v
Technical OKF concepts
        |
        v
OpenAI structured enrichment
        |
        v
Business definitions and proposed semantics
        |
        v
Human review and publication
        |
        v
Full-text, vector, and typed-graph indexes
        |
        v
MCP grounding context for Text-to-SQL
```

The five-day version focuses on tables, concepts, grain, joins, classifications, graph exploration, and retrieval. Complex metric authoring, schema-change automation, authentication, document crawling, and production governance remain later phases.
