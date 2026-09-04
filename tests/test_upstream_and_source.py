from pathlib import Path

import pytest

from cerebro.paths import ROOT
from cerebro.source import DuckDBSource, SamplingDisabledError
from cerebro.upstream import OKFDocument, Source


def test_pinned_upstream_metadata_and_document_contract():
    manifest = (ROOT / "knowledge" / "bank-workshop" / "bundle.yaml").read_text()
    assert "ad30107c31c06aec8a7d5636e0d1058118604e6f" in manifest
    document = OKFDocument.parse("---\ntype: concept\nid: test\n---\n\n# Test\n")
    document.validate()
    assert document.frontmatter["type"] == "concept"


def test_duckdb_source_contract_and_counts(bank_source_config):
    source = DuckDBSource(bank_source_config)
    assert isinstance(source, Source)
    snapshot = source.scan()
    assert len(snapshot.tables) == 10
    assert snapshot.column_count == 75
    assert len(snapshot.relationships) == 11
    assert sum(table.primary_key is not None for table in snapshot.tables) == 10
    assert snapshot.row_sampling == "disabled"
    assert len(source.list_concepts()) == 10
    first = source.list_concepts()[0]
    assert source.read_concept(first)["name"] == first.resource
    with pytest.raises(SamplingDisabledError):
        source.sample_rows(first)


def test_missing_source_is_actionable(tmp_path: Path):
    config = tmp_path / "source.yaml"
    config.write_text("name: missing\nversion: 1\ndatabase_path: /does/not/exist.duckdb\n")
    with pytest.raises(FileNotFoundError, match="DuckDB source not found"):
        DuckDBSource(config)
