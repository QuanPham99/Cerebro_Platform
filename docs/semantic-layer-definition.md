# Cerebro Semantic Layer Definition

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

An OKF bundle is a directory of Markdown documents with YAML frontmatter:

```text
bank/
|-- index.md
|-- datasets/
|   `-- retail_bank.md
|-- tables/
|   |-- customers.md
|   |-- accounts.md
|   `-- transactions.md
|-- concepts/
|   |-- customer.md
|   `-- account.md
`-- metrics/
    `-- transaction_volume.md
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
