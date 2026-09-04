from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from cerebro.bundle import load_validated_bundle
from cerebro.paths import DEFAULT_BUNDLE, DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def disable_live_llm_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep local .env credentials from turning unit tests into live API calls."""
    monkeypatch.setenv("CEREBRO_LLM_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")


@pytest.fixture()
def bank_database(tmp_path: Path) -> Path:
    path = tmp_path / "bank.duckdb"
    bundle = load_validated_bundle(DEFAULT_BUNDLE)
    connection = duckdb.connect(str(path))
    try:
        for table in [item for item in bundle.objects if item.type == "table"]:
            columns = table.cerebro.get("columns", [])
            definition = ", ".join(f'"{column["name"]}" {column["data_type"]}' for column in columns)
            connection.execute(f'CREATE TABLE "{table.id.removeprefix("table.")}" ({definition})')
        connection.execute("INSERT INTO customers (customer_id, gender) VALUES (1, 'Female'), (2, 'Male'), (3, 'Female')")
        connection.execute("INSERT INTO accounts (account_id, customer_id, balance) VALUES (1, 1, 100.0), (2, 2, 300.0)")
    finally:
        connection.close()
    return path


@pytest.fixture()
def bank_source_config(tmp_path: Path, bank_database: Path) -> Path:
    payload = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["database_path"] = str(bank_database)
    path = tmp_path / "bank-source.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path
