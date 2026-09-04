from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import duckdb
import yaml

from .models import CatalogSnapshot, ColumnFact, Provenance, RelationshipFact, TableFact
from .paths import DEFAULT_CONFIG
from .upstream import ConceptRef, Source


class SamplingDisabledError(RuntimeError):
    pass


def load_source_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Source configuration not found: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Source configuration must be a YAML mapping: {config_path}")
    data["_config_path"] = str(config_path)
    return data


class DuckDBSource(Source):
    """Catalog-only adapter for Google OKF's Source contract."""

    name = "duckdb"

    def __init__(
        self,
        config_path: Path | str = DEFAULT_CONFIG,
        database_path: Path | str | None = None,
        *,
        source_mode: Literal["configured", "database_only"] = "configured",
        schema: str | None = None,
    ):
        self.source_mode = source_mode
        self.config = load_source_config(config_path) if source_mode == "configured" else {}
        environment_path = os.getenv("CEREBRO_DATABASE_PATH")
        configured_path = database_path or (environment_path.strip() if environment_path and environment_path.strip() else None)
        if configured_path is None and source_mode == "configured":
            configured_path = self.config.get("database_path")
        if configured_path is None:
            raise FileNotFoundError("DuckDB source path is required")
        self.database_path = Path(os.path.expandvars(str(configured_path))).expanduser()
        if not self.database_path.exists():
            raise FileNotFoundError(f"DuckDB source not found: {self.database_path}")
        self.schema = schema or os.getenv("CEREBRO_DATABASE_SCHEMA") or self.config.get("schema", "main")
        self._snapshot: CatalogSnapshot | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path), read_only=True)

    def scan(self) -> CatalogSnapshot:
        if self.source_mode == "database_only":
            return self._scan_database_only()
        declared_tables = self.config.get("tables", {})
        classifications = self.config.get("classifications", {})
        classified = {
            field: level
            for level, fields in classifications.items()
            for field in fields
        }
        discovered = Provenance(origin="discovered", source="DuckDB information_schema")
        declared = Provenance(origin="declared", source=self.config["_config_path"])
        tables: list[TableFact] = []
        with self._connect() as connection:
            table_rows = connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = ? AND table_type = 'BASE TABLE' ORDER BY table_name",
                [self.schema],
            ).fetchall()
            for (table_name,) in table_rows:
                column_rows = connection.execute(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
                    [self.schema, table_name],
                ).fetchall()
                declaration = declared_tables.get(table_name, {})
                columns = [
                    ColumnFact(
                        name=name,
                        data_type=data_type,
                        nullable=(nullable == "YES"),
                        classification=classified.get(f"{table_name}.{name}", "internal"),
                        provenance=discovered,
                    )
                    for name, data_type, nullable in column_rows
                ]
                tables.append(
                    TableFact(
                        name=table_name,
                        schema_name=self.schema,
                        description=declaration.get("description", ""),
                        grain=declaration.get("grain", ""),
                        aliases=declaration.get("aliases", []),
                        primary_key=declaration.get("primary_key"),
                        columns=columns,
                        provenance={"catalog": discovered, "semantics": declared},
                    )
                )
        relationships = [
            RelationshipFact(**item, provenance=declared)
            for item in self.config.get("relationships", [])
        ]
        snapshot = CatalogSnapshot(
            source_mode="configured",
            source_name=self.config["name"],
            source_version=str(self.config["version"]),
            database_path=str(self.database_path),
            schema_name=self.schema,
            tables=tables,
            relationships=relationships,
            rules=self.config.get("rules", []),
            schema_reference=Provenance(
                origin="declared",
                source=str(self.config.get("schema_reference_path", "not configured")),
            ),
            discovery_evidence={
                "config_loaded": True,
                "web_enrichment": "disabled",
                "row_sampling": "disabled",
                "rows_read": 0,
                "catalog_queries": 2,
            },
        )
        self._validate_expected(snapshot)
        self._snapshot = snapshot
        return snapshot

    def _scan_database_only(self) -> CatalogSnapshot:
        discovered = Provenance(origin="discovered", source="DuckDB catalog")
        with self._connect() as connection:
            table_rows = connection.execute(
                "SELECT table_name, coalesce(comment, '') FROM duckdb_tables() "
                "WHERE schema_name = ? AND NOT internal AND NOT temporary ORDER BY table_name",
                [self.schema],
            ).fetchall()
            column_rows = connection.execute(
                "SELECT table_name, column_name, data_type, is_nullable, coalesce(comment, '') "
                "FROM duckdb_columns() WHERE schema_name = ? AND NOT internal "
                "ORDER BY table_name, column_index",
                [self.schema],
            ).fetchall()
            constraint_rows = connection.execute(
                "SELECT table_name, constraint_type, constraint_column_names, referenced_table, "
                "referenced_column_names, constraint_name FROM duckdb_constraints() "
                "WHERE schema_name = ? ORDER BY table_name, constraint_index",
                [self.schema],
            ).fetchall()

        columns_by_table: dict[str, list[ColumnFact]] = {name: [] for name, _ in table_rows}
        for table_name, name, data_type, nullable, comment in column_rows:
            if table_name in columns_by_table:
                columns_by_table[table_name].append(
                    ColumnFact(
                        name=name,
                        data_type=data_type,
                        nullable=bool(nullable),
                        description=comment or "",
                        provenance=discovered,
                    )
                )
        primary_keys: dict[str, str] = {}
        relationships: list[RelationshipFact] = []
        for table_name, constraint_type, columns, referenced_table, referenced_columns, constraint_name in constraint_rows:
            columns = list(columns or [])
            referenced_columns = list(referenced_columns or [])
            if constraint_type == "PRIMARY KEY" and len(columns) == 1:
                primary_keys[table_name] = columns[0]
            elif constraint_type == "FOREIGN KEY" and len(columns) == 1 and len(referenced_columns) == 1 and referenced_table:
                relationships.append(
                    RelationshipFact(
                        id=str(constraint_name or f"fk_{table_name}_{columns[0]}_{referenced_table}_{referenced_columns[0]}"),
                        source_table=table_name,
                        source_column=columns[0],
                        target_table=referenced_table,
                        target_column=referenced_columns[0],
                        cardinality="many-to-one",
                        provenance=discovered,
                    )
                )
        tables = [
            TableFact(
                name=name,
                schema_name=self.schema,
                description=comment or "",
                primary_key=primary_keys.get(name),
                columns=columns_by_table[name],
                provenance={"catalog": discovered},
            )
            for name, comment in table_rows
        ]
        catalog_payload = {
            "schema_name": self.schema,
            "tables": [table.model_dump(mode="json") for table in tables],
            "relationships": [item.model_dump(mode="json") for item in relationships],
        }
        fingerprint = hashlib.sha256(
            json.dumps(catalog_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snapshot = CatalogSnapshot(
            source_mode="database_only",
            source_name=self.database_path.stem,
            source_version=fingerprint[:16],
            database_path=str(self.database_path),
            schema_name=self.schema,
            tables=tables,
            relationships=relationships,
            rules=[],
            schema_reference=discovered,
            discovery_evidence={
                "config_loaded": False,
                "web_enrichment": "disabled",
                "row_sampling": "disabled",
                "rows_read": 0,
                "catalog_queries": 3,
                "native_comments": sum(bool(table.description) for table in tables)
                + sum(bool(column.description) for table in tables for column in table.columns),
                "primary_keys": len(primary_keys),
                "foreign_keys": len(relationships),
            },
        )
        self._snapshot = snapshot
        return snapshot

    def _validate_expected(self, snapshot: CatalogSnapshot) -> None:
        expected_tables = int(self.config.get("expected_table_count", len(snapshot.tables)))
        expected_columns = int(self.config.get("expected_column_count", snapshot.column_count))
        if len(snapshot.tables) != expected_tables or snapshot.column_count != expected_columns:
            raise ValueError(
                "Source schema drift: expected "
                f"{expected_tables} tables/{expected_columns} columns, found "
                f"{len(snapshot.tables)} tables/{snapshot.column_count} columns"
            )
        table_map = {table.name: table for table in snapshot.tables}
        for relationship in snapshot.relationships:
            for table_name, column_name in (
                (relationship.source_table, relationship.source_column),
                (relationship.target_table, relationship.target_column),
            ):
                table = table_map.get(table_name)
                if table is None or column_name not in {column.name for column in table.columns}:
                    raise ValueError(
                        f"Declared relationship {relationship.id} references missing "
                        f"column {table_name}.{column_name}"
                    )

    def list_concepts(self) -> list[ConceptRef]:
        snapshot = self._snapshot or self.scan()
        return [
            ConceptRef(id=(snapshot.schema_name, table.name), type="table", resource=table.name)
            for table in snapshot.tables
        ]

    def read_concept(self, ref: ConceptRef) -> dict[str, Any]:
        snapshot = self._snapshot or self.scan()
        for table in snapshot.tables:
            if ref.id == (snapshot.schema_name, table.name):
                return table.model_dump(mode="json")
        raise KeyError(ref.id_str)

    def sample_rows(self, ref: ConceptRef, n: int = 5) -> None:
        raise SamplingDisabledError(
            f"Row sampling is disabled for source {self.config.get('name', self.database_path.stem)} ({ref.id_str})"
        )
