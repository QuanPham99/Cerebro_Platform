"""Build the demo DuckDB from CSVs using DDL derived from the OKF bundle.

Build-time tooling. The bundle is the single source of column names, order, and
types, so the physical schema cannot drift from the semantic contract.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import tempfile
from pathlib import Path

import duckdb

from cerebro.bundle import load_validated_bundle
from cerebro.models import SemanticBundle
from cerebro.paths import DEFAULT_BUNDLE, ROOT
from cerebro.provenance import digest, sha256_file, bundle_digest
from cerebro.query_models import SourceManifest, MaterializationReceipt, MaterializedTableEvidence
from sqlglot import exp


class LoadError(RuntimeError):
    """Raised when source CSVs diverge from the bundle declaration."""


def _tables(bundle: SemanticBundle):
    for obj in sorted(bundle.objects, key=lambda item: item.id):
        if obj.type == "table":
            yield obj.id.split(".", 1)[1], obj.cerebro.get("columns", [])


def columns_from_bundle(bundle: SemanticBundle) -> dict[str, list[str]]:
    return {table: [column["name"] for column in columns] for table, columns in _tables(bundle)}


def ddl_from_bundle(bundle: SemanticBundle) -> dict[str, str]:
    statements = {}
    for table, columns in _tables(bundle):
        fields = ", ".join(f'"{column["name"]}" {column["data_type"]}' for column in columns)
        statements[table] = f'CREATE TABLE "{table}" ({fields})'
    return statements


def create_schema(db_path: Path, bundle: SemanticBundle) -> dict[str, int]:
    """Atomic synthetic schema construction; never evidence of real materialization."""
    db_path = Path(db_path)
    if db_path.exists():
        raise LoadError("schema_only_target_exists")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=db_path.parent, prefix=".load-") as staging:
        temporary = Path(staging) / "database.duckdb"
        with duckdb.connect(str(temporary)) as connection:
            for statement in ddl_from_bundle(bundle).values():
                connection.execute(statement)
        os.replace(temporary, db_path)
    return {table: 0 for table in columns_from_bundle(bundle)}


def _load_csvs(csv_dir: Path, db_path: Path, bundle: SemanticBundle,
              manifest: SourceManifest | None = None) -> dict[str, int]:
    """FR-700/701: preflight, isolated load, verify, then atomic replacement."""
    csv_dir, db_path = Path(csv_dir), Path(db_path)
    expected = columns_from_bundle(bundle)
    actual = {p.name for p in csv_dir.iterdir() if p.is_file() and p.suffix == ".csv"} if csv_dir.is_dir() else set()
    if actual != {f"{name}.csv" for name in expected}:
        missing = sorted({f"{name}.csv" for name in expected} - actual)
        raise LoadError("source_file_set_mismatch:" + ",".join(missing))
    files = {}
    for name, header in expected.items():
        path = csv_dir / f"{name}.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            if next(csv.reader(handle), []) != header:
                raise LoadError(f"{name}: source_header_mismatch")
        files[name] = sha256_file(path)
    if manifest is not None:
        entries = {t.name: t for t in manifest.tables}
        if len(entries) != len(manifest.tables) or set(entries) != set(expected):
            raise LoadError("manifest_table_set_mismatch")
        if any(entries[n].sha256 != h for n, h in files.items()):
            raise LoadError("source_checksum_mismatch")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    receipt = None
    with tempfile.TemporaryDirectory(dir=db_path.parent, prefix=".load-") as staging:
        staging = Path(staging)
        temporary = staging / "database.duckdb"
        with duckdb.connect(str(temporary)) as connection:
            for name, statement in ddl_from_bundle(bundle).items():
                source = staging / f"{name}.csv"
                shutil.copyfile(csv_dir / f"{name}.csv", source)
                if sha256_file(source) != files[name]:
                    raise LoadError("source_changed_during_materialization")
                connection.execute(statement)
                table = exp.to_identifier(name, quoted=True).sql(dialect="duckdb")
                try:
                    connection.execute(f"INSERT INTO {table} SELECT * FROM read_csv(?, header=true, all_varchar=true, strict_mode=true)", [str(source)])
                except duckdb.Error:
                    raise LoadError(f"{name}: source_cast_failed") from None
                counts[name] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if manifest is not None and counts[name] != entries[name].row_count:
                    raise LoadError("source_row_count_mismatch")
            actual_tables = connection.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_type='BASE TABLE'").fetchone()[0]
            if actual_tables != len(expected):
                raise LoadError("materialized_table_set_mismatch")
        if manifest is not None:
            table_objects = bundle.by_id()
            tables = tuple(MaterializedTableEvidence(table_id="table." + t.name, sha256=t.sha256,
                row_count=counts[t.name], columns_sha256=digest(table_objects["table." + t.name].cerebro["columns"])) for t in manifest.tables)
            payload = dict(manifest_sha256=digest(manifest), bundle_sha256=bundle_digest(bundle),
                database_sha256=sha256_file(temporary), engine_version=duckdb.__version__,
                source_kind=manifest.source_kind, tables=tables, version="008.materialization.v1")
            receipt = MaterializationReceipt(**payload, receipt_sha256=digest(payload))
        os.replace(temporary, db_path)
    return counts, receipt


def load_csvs(csv_dir: Path, db_path: Path, bundle: SemanticBundle, manifest: SourceManifest | None = None) -> dict[str, int]:
    return _load_csvs(csv_dir, db_path, bundle, manifest)[0]


def materialize(csv_dir: Path, db_path: Path, bundle: SemanticBundle, manifest: SourceManifest) -> MaterializationReceipt:
    return _load_csvs(csv_dir, db_path, bundle, manifest)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the demo DuckDB from CSVs")
    parser.add_argument("--csv-dir", type=Path, default=ROOT / "archive")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "workshop.duckdb")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Create the declared schema with no rows; use before archive/*.csv is available",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    if args.schema_only:
        counts = create_schema(args.database, bundle)
    elif args.manifest:
        from cerebro.provenance import atomic_json
        manifest = SourceManifest.model_validate_json(args.manifest.read_text())
        receipt = materialize(args.csv_dir, args.database, bundle, manifest)
        if args.receipt: atomic_json(args.receipt, receipt)
        counts = {t.table_id[6:]: t.row_count for t in receipt.tables}
    else:
        if args.receipt: parser.error("--receipt requires --manifest")
        counts = load_csvs(args.csv_dir, args.database, bundle)
    for table, rows in counts.items():
        print(f"{table:<20} {rows:>10,} rows")
    print(f"total{' ' * 15} {sum(counts.values()):>10,} rows -> {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
