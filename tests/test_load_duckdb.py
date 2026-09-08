from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.paths import DEFAULT_BUNDLE
from scripts.load_duckdb import (
    LoadError,
    columns_from_bundle,
    create_schema,
    ddl_from_bundle,
    load_csvs,
)

SAMPLE = {
    "BIGINT": "1",
    "VARCHAR": "x",
    "DOUBLE": "1.5",
    "DATE": "2020-01-01",
}


def _write_fixture_csvs(directory: Path, bundle) -> None:
    """One header row plus one data row per table, driven by the bundle itself."""
    for table, names in columns_from_bundle(bundle).items():
        declared = {
            column["name"]: column["data_type"]
            for obj in bundle.objects
            if obj.id == f"table.{table}"
            for column in obj.cerebro["columns"]
        }
        path = directory / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            writer.writerow([SAMPLE[declared[name]] for name in names])


def test_ddl_covers_every_declared_table_and_column():
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    ddl = ddl_from_bundle(bundle)
    assert len(ddl) == 10
    assert sum(len(names) for names in columns_from_bundle(bundle).values()) == 75


def test_load_applies_declared_types_not_inferred_types(tmp_path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    _write_fixture_csvs(tmp_path, bundle)
    db_path = tmp_path / "workshop.duckdb"
    counts = load_csvs(tmp_path, db_path, bundle)
    assert counts["transactions"] == 1
    connection = duckdb.connect(str(db_path), read_only=True)
    actual = dict(
        connection.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'transactions'"
        ).fetchall()
    )
    connection.close()
    assert actual["txn_date"] == "DATE"
    assert actual["amount"] == "DOUBLE"


def test_header_divergence_fails_closed(tmp_path):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    _write_fixture_csvs(tmp_path, bundle)
    path = tmp_path / "transactions.csv"
    rows = list(csv.reader(path.open(newline="", encoding="utf-8")))
    rows[0][0] = "renamed_column"
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    with pytest.raises(LoadError, match="transactions"):
        load_csvs(tmp_path, tmp_path / "bad.duckdb", bundle)


def test_schema_only_build_matches_bundle_without_csvs(tmp_path):
    """Deviation from plan Task 1: lets the pipeline run before real CSVs exist.

    The declared schema alone is enough for catalog discovery (spec 002) and for
    EXPLAIN validation (FR-714), so the demo is not blocked on proprietary data.
    """
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    db_path = tmp_path / "empty.duckdb"
    counts = create_schema(db_path, bundle)

    assert set(counts) == set(columns_from_bundle(bundle))
    assert all(rows == 0 for rows in counts.values())

    connection = duckdb.connect(str(db_path), read_only=True)
    tables = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchone()[0]
    columns = connection.execute("SELECT COUNT(*) FROM information_schema.columns").fetchone()[0]
    amount_type = connection.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'transactions' AND column_name = 'amount'"
    ).fetchone()[0]
    connection.close()

    assert tables == 10
    assert columns == 75
    assert amount_type == "DOUBLE"
