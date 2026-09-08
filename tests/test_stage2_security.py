"""Adversarial acceptance for 008 AC-700/701/702/712/716/717."""
import csv
from pathlib import Path

import duckdb
import pytest

from cerebro.bundle import load_validated_bundle
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.provenance import sha256_file
from scripts.load_duckdb import create_schema, load_csvs, columns_from_bundle


@pytest.mark.parametrize("failure", ["missing", "header", "cast"])
def test_atomic_loader_preserves_existing_target_after_late_failure(tmp_path, failure):
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    db = tmp_path / "existing.duckdb"
    create_schema(db, bundle)
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO accounts (account_id) VALUES (999)")
    con.close()
    sample = {"BIGINT": "1", "VARCHAR": "x", "DOUBLE": "1.5", "DATE": "2026-01-01"}
    for obj in bundle.objects:
        if obj.type != "table":
            continue
        name = obj.id.split(".", 1)[1]
        columns = obj.cerebro["columns"]
        with (tmp_path / f"{name}.csv").open("w") as handle:
            writer = csv.writer(handle)
            writer.writerow([c["name"] for c in columns])
            writer.writerow([sample[c["data_type"]] for c in columns])
    late = tmp_path / "transactions.csv"
    if failure == "missing":
        late.unlink()
    elif failure == "header":
        late.write_text("bad_header\n1\n")
    else:
        late.write_text(",".join(columns_from_bundle(bundle)["transactions"]) + "\nnot-an-integer,1,2026-01-01,1,x,x\n")
    before = sha256_file(db)
    with pytest.raises(Exception):
        load_csvs(tmp_path, db, bundle)
    assert sha256_file(db) == before


def test_extra_csv_is_rejected_before_target_creation(tmp_path):
    (tmp_path / "unexpected.csv").write_text("x\n1\n")
    target = tmp_path / "new.duckdb"
    with pytest.raises(Exception):
        load_csvs(tmp_path, target, load_validated_bundle(DEFAULT_BUNDLE))
    assert not target.exists()
