# 002 — Bank Source Discovery

## Problem

The workshop bank database has catalog metadata but no database constraints, while business keys and joins are documented separately.

## Goal

Discover the physical schema deterministically and merge declared metadata with explicit provenance, without reading data rows.

## Non-Goals

- Data profiling, value sampling, or PII extraction.
- Schema mutation or query execution for analytics.
- Inferring relationships from row values.

## Functional Requirements

- FR-101: Open `/home/kwan/Documents/Code_Beavers_Txt2Sql_WS/data/workshop.duckdb` read-only.
- FR-102: Enumerate schemas, 10 tables, and 75 columns from catalog views.
- FR-103: Load keys, 11 relationships, classifications, and query rules from `config/bank-source.yaml`.
- FR-104: Emit a normalized snapshot with provenance values `discovered` or `declared` on every fact.
- FR-105: Implement the upstream `Source` interface; `sample_rows` must be disabled.
- FR-106: Expose discovery through `cerebro scan`.

## Acceptance Criteria

- AC-101: Scan returns exactly 10 tables and 75 columns for the reference database.
- AC-102: Output contains 10 declared primary keys and 11 declared foreign-key relationships.
- AC-103: The database connection is read-only and no `SELECT *`, sample, or data row appears in logs/artifacts.
- AC-104: Missing database/config paths fail with actionable messages.

## Edge Cases

- DuckDB is locked, absent, or has drifted.
- Catalog table is empty.
- A declared column or relationship endpoint does not exist.

## Interfaces / Contracts

`DuckDBSource.list_concepts()`, `read_concept()`, and a JSON-serializable `CatalogSnapshot`. CLI writes a snapshot only when `--output` is supplied.

## Constraints

Catalog-only SQL; source database is never copied.

## Assumptions

The `main` schema contains the workshop objects.

## Open Questions

Whether future sources require multi-schema naming is deferred.

## Test Design

| Test | Requirement | Verification |
|---|---|---|
| T-101 | FR-101, FR-102 | Integration scan asserts counts and read-only connection. |
| T-102 | FR-103, FR-104 | Snapshot asserts keys, relationships, provenance. |
| T-103 | FR-105 | Calling `sample_rows` raises `SamplingDisabledError`. |
| T-104 | FR-106 | CLI smoke test scans a fixture database. |
| T-105 | AC-104 | Missing and inconsistent inputs return non-zero errors. |
