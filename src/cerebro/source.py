from __future__ import annotations

from pathlib import Path
from typing import Any

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

    def __init__(self, config_path: Path | str = DEFAULT_CONFIG):
        self.config = load_source_config(config_path)
        self.database_path = Path(self.config["database_path"])
        if not self.database_path.exists():
            raise FileNotFoundError(f"DuckDB source not found: {self.database_path}")
        self.schema = self.config.get("schema", "main")
        self._snapshot: CatalogSnapshot | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path), read_only=True)

    def scan(self) -> CatalogSnapshot:
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
        )
        self._validate_expected(snapshot)
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
            f"Row sampling is disabled for source {self.config['name']} ({ref.id_str})"
        )
