from __future__ import annotations

import json
import asyncio
from pathlib import Path

import pytest
import httpx
import duckdb

import cerebro.generation as generation
import cerebro.source as source_module
from cerebro.enrichment import GenerationProvider
from cerebro.evaluation import compare_relationship_oracle
from cerebro.api import create_app, create_mcp_server
from cerebro.bundle import load_validated_bundle
from cerebro.cli import build_parser, main
from cerebro.generation import (
    ActivationError,
    ReviewConflictError,
    activate_bundle,
    review_bundle,
    run_generation_workflow,
)
from cerebro.models import BusinessSemantics, QuerySemantics, RelationshipSemantics
from cerebro.source import DuckDBSource
from cerebro.retrieval import SemanticRetriever
from cerebro.paths import DEFAULT_BUNDLE


class CapturingProvider(GenerationProvider):
    name = "deterministic-test-provider"
    model = "typed-fixture"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, _schema_name, prompt, output_model):
        self.prompts.append(prompt)
        if output_model is BusinessSemantics:
            return BusinessSemantics(
                table_purposes={"accounts": "Customer-held deposit accounts."},
                concepts=[],
                classifications={"accounts": "confidential"},
            )
        if output_model is RelationshipSemantics:
            return RelationshipSemantics(relationships=[])
        if output_model is QuerySemantics:
            return QuerySemantics(
                grains={}, dimensions=[], measures=[], joins=[], guidance=[], warnings=[]
            )
        raise AssertionError(output_model)


def test_database_only_discovery_uses_only_catalog_and_ignores_config(
    bank_database: Path, monkeypatch: pytest.MonkeyPatch
):
    queries: list[str] = []
    original_connect = DuckDBSource._connect

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.connection.close()

        def execute(self, sql, parameters=None):
            queries.append(" ".join(sql.lower().split()))
            return self.connection.execute(sql, parameters or [])

    monkeypatch.setattr(
        source_module,
        "load_source_config",
        lambda _path: (_ for _ in ()).throw(AssertionError("config must not load")),
    )
    monkeypatch.setattr(
        DuckDBSource,
        "_connect",
        lambda self: ConnectionProxy(original_connect(self)),
    )

    snapshot = DuckDBSource(
        Path("/not/read.yaml"), bank_database, source_mode="database_only", schema="main"
    ).scan()

    assert len(snapshot.tables) == 10
    assert snapshot.column_count == 75
    assert snapshot.relationships == []
    assert sum(table.primary_key is not None for table in snapshot.tables) == 0
    assert snapshot.discovery_evidence == {
        "config_loaded": False,
        "web_enrichment": "disabled",
        "row_sampling": "disabled",
        "rows_read": 0,
        "catalog_queries": 3,
        "native_comments": 0,
        "primary_keys": 0,
        "foreign_keys": 0,
    }
    assert len(queries) == 3
    assert all(
        any(catalog in query for catalog in ("duckdb_tables()", "duckdb_columns()", "duckdb_constraints()"))
        for query in queries
    )


def test_database_only_model_inputs_and_snapshot_are_sealed(bank_database: Path, tmp_path: Path):
    provider = CapturingProvider()
    output = tmp_path / "raw-run"
    result = run_generation_workflow(
        config_path=tmp_path / "contains-configured-secret.yaml",
        database_path=bank_database,
        output=output,
        provider=provider,
        source_mode="database_only",
        database_schema="main",
    )

    joined = "\n".join(provider.prompts)
    assert str(bank_database) not in joined
    assert "database_path" not in joined
    assert "configured-secret" not in joined
    assert "http://" not in joined and "https://" not in joined
    assert "checked-in-golden-bundle" not in joined
    assert result.snapshot.relationships == []
    persisted = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
    assert persisted["database_path"] == "[omitted]"
    assert persisted["discovery_evidence"]["rows_read"] == 0
    assert persisted["discovery_evidence"]["config_loaded"] is False


def test_database_only_discovers_native_comments_and_supported_constraints(tmp_path: Path):
    database = tmp_path / "native.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parents(id))"
        )
        connection.execute("COMMENT ON TABLE parents IS 'Native parent comment'")
        connection.execute("COMMENT ON COLUMN children.parent_id IS 'Native FK comment'")
    finally:
        connection.close()

    snapshot = DuckDBSource(
        tmp_path / "unused.yaml", database, source_mode="database_only", schema="main"
    ).scan()
    tables = {table.name: table for table in snapshot.tables}
    assert tables["parents"].description == "Native parent comment"
    assert tables["children"].primary_key == "id"
    assert next(column for column in tables["children"].columns if column.name == "parent_id").description == "Native FK comment"
    assert len(snapshot.relationships) == 1
    relationship = snapshot.relationships[0]
    assert (relationship.source_table, relationship.source_column) == ("children", "parent_id")
    assert (relationship.target_table, relationship.target_column) == ("parents", "id")
    assert relationship.provenance.origin == "discovered"


def test_review_is_idempotent_conflict_safe_and_activation_checks_digest(
    bank_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidate = run_generation_workflow(
        config_path=tmp_path / "unused.yaml",
        database_path=bank_database,
        output=tmp_path / "run-approval",
        provider=CapturingProvider(),
        source_mode="database_only",
        database_schema="main",
    ).output
    reviewed_root = tmp_path / "reviewed"
    record = review_bundle(
        candidate,
        reviewer="Data Owner",
        decision="approve",
        comment="Catalog-bounded proposal accepted.",
        acknowledge_ai_risk=True,
        reviewed_root=reviewed_root,
    )
    repeated = review_bundle(
        candidate,
        reviewer="Data Owner",
        decision="approve",
        comment="Catalog-bounded proposal accepted.",
        acknowledge_ai_risk=True,
        reviewed_root=reviewed_root,
    )
    assert repeated == record
    assert record.reviewed_bundle == str((reviewed_root / candidate.name).resolve())
    assert json.loads((Path(record.reviewed_bundle) / "approval.json").read_text())["reviewer"] == "Data Owner"
    assert "ai_proposed" in (Path(record.reviewed_bundle) / "tables" / "accounts.md").read_text()
    with pytest.raises(ReviewConflictError):
        review_bundle(
            candidate,
            reviewer="Data Owner",
            decision="reject",
            comment="Changed mind.",
            reviewed_root=reviewed_root,
        )

    pointer = tmp_path / "active.json"
    monkeypatch.setattr(generation, "ACTIVE_BUNDLE_POINTER", pointer)
    payload = activate_bundle(Path(record.reviewed_bundle))
    assert payload["path"] == record.reviewed_bundle
    assert pointer.is_file()

    table = Path(record.reviewed_bundle) / "tables" / "accounts.md"
    table.write_text(table.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")
    with pytest.raises(ActivationError, match="digest"):
        activate_bundle(Path(record.reviewed_bundle))


def test_rejection_requires_comment_and_never_creates_reviewed_bundle(bank_database: Path, tmp_path: Path):
    candidate = run_generation_workflow(
        config_path=tmp_path / "unused.yaml",
        database_path=bank_database,
        output=tmp_path / "run-rejection",
        source_mode="database_only",
        database_schema="main",
    ).output
    with pytest.raises(ValueError, match="comment"):
        review_bundle(candidate, reviewer="Owner", decision="reject", reviewed_root=tmp_path / "reviewed")
    record = review_bundle(
        candidate,
        reviewer="Owner",
        decision="reject",
        comment="Relationship evidence is insufficient.",
        reviewed_root=tmp_path / "reviewed",
    )
    assert record.decision == "reject"
    assert record.reviewed_bundle is None
    with pytest.raises(ActivationError):
        activate_bundle(candidate)


def test_database_only_http_review_and_activation_swap(
    bank_database: Path, bank_source_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CEREBRO_DATABASE_PATH", "")
    monkeypatch.setattr(generation, "ACTIVE_BUNDLE_POINTER", tmp_path / "active.json")
    app = create_app(
        source_config=bank_source_config,
        generation_output_root=tmp_path / "generated",
        reviewed_output_root=tmp_path / "reviewed",
    )

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/api/generation/runs", json={"source_mode": "database_only"}
            )
            assert started.status_code == 202
            run_id = started.json()["id"]
            for _ in range(200):
                run = (await client.get(f"/api/generation/runs/{run_id}")).json()
                if run["status"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.02)
            assert run["status"] == "succeeded", run
            assert run["source_mode"] == "database_only"
            assert run["candidate"]["discovery_evidence"]["rows_read"] == 0

            snapshot = await client.get(f"/api/generation/runs/{run_id}/snapshot")
            assert snapshot.status_code == 200
            assert snapshot.json()["database_path"] == "[omitted]"
            assert snapshot.json()["relationships"] == []
            document = await client.get(
                f"/api/generation/runs/{run_id}/documents/tables/accounts.md"
            )
            assert document.status_code == 200
            assert "source_column" not in document.text
            assert (
                await client.get(f"/api/generation/runs/{run_id}/documents/bundle.yaml")
            ).status_code == 404

            blocked = await client.post(f"/api/generation/runs/{run_id}/activate")
            assert blocked.status_code == 409
            invalid = await client.post(
                f"/api/generation/runs/{run_id}/reviews",
                json={
                    "decision": "approve",
                    "reviewer": "Data Owner",
                    "comment": "",
                    "acknowledge_ai_risk": False,
                },
            )
            assert invalid.status_code == 422
            review_payload = {
                "decision": "approve",
                "reviewer": "Data Owner",
                "comment": "Database-only candidate accepted.",
                "acknowledge_ai_risk": True,
            }
            approved = await client.post(
                f"/api/generation/runs/{run_id}/reviews", json=review_payload
            )
            assert approved.status_code == 200
            repeated = await client.post(
                f"/api/generation/runs/{run_id}/reviews", json=review_payload
            )
            assert repeated.json() == approved.json()
            conflict = await client.post(
                f"/api/generation/runs/{run_id}/reviews",
                json={
                    "decision": "reject",
                    "reviewer": "Data Owner",
                    "comment": "No.",
                    "acknowledge_ai_risk": False,
                },
            )
            assert conflict.status_code == 409

            activated = await client.post(f"/api/generation/runs/{run_id}/activate")
            assert activated.status_code == 200
            active = (await client.get("/api/bundles/active")).json()
            assert active["version"] == run["candidate"]["version"]
            assert active["review_state"] == "approved"
            assert app.state.bundle.version == active["version"]
            assert (await client.get("/api/graph")).json()["version"] == active["version"]
            assert (await client.get("/api/concepts/table.accounts")).status_code == 200
            active_document = await client.get("/knowledge/tables/accounts.md")
            assert active_document.status_code == 200
            chat = await client.post("/api/chat", json={"message": "customer count", "history": []})
            assert chat.json()["semantic_version"] == active["version"]

    asyncio.run(exercise())


def test_mcp_retriever_is_resolved_at_call_time():
    first = SemanticRetriever(load_validated_bundle(DEFAULT_BUNDLE))
    holder = {"retriever": first}
    server = create_mcp_server(lambda: holder["retriever"])
    changed_bundle = first.bundle.model_copy(update={"version": "swapped-version"}, deep=True)
    holder["retriever"] = SemanticRetriever(changed_bundle)

    async def exercise():
        result = await server.call_tool(
            "expand_neighborhood", {"concept_ids": ["table.accounts"], "depth": 0}
        )
        assert json.loads(result[0].text)["semantic_version"] == "swapped-version"

    asyncio.run(exercise())


def test_checked_in_semantics_are_a_post_generation_oracle_only(bank_database: Path, tmp_path: Path):
    output = run_generation_workflow(
        config_path=tmp_path / "unused.yaml",
        database_path=bank_database,
        output=tmp_path / "oracle-run",
        source_mode="database_only",
        database_schema="main",
    ).output
    report = compare_relationship_oracle(output)
    assert report["precision"] == 0.0
    assert report["recall"] == 0.0
    assert len(report["missing"]) == 11
    assert report["invented"] == []


def test_cli_source_mode_review_and_activation(
    bank_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    parsed = build_parser().parse_args(["generate", "--source-mode", "database-only"])
    assert parsed.source_mode == "database-only"
    candidate = run_generation_workflow(
        config_path=tmp_path / "unused.yaml",
        database_path=bank_database,
        output=tmp_path / "cli-run",
        source_mode="database_only",
        database_schema="main",
    ).output
    reviewed_root = tmp_path / "reviewed"
    assert main([
        "review",
        "--bundle", str(candidate),
        "--reviewer", "CLI Owner",
        "--comment", "Accepted from terminal.",
        "--acknowledge-ai-risk",
        "--reviewed-root", str(reviewed_root),
    ]) == 0
    record = json.loads(capsys.readouterr().out)
    pointer = tmp_path / "cli-active.json"
    monkeypatch.setattr(generation, "ACTIVE_BUNDLE_POINTER", pointer)
    assert main(["activate", "--bundle", record["reviewed_bundle"]]) == 0
    assert json.loads(capsys.readouterr().out)["path"] == record["reviewed_bundle"]
    assert pointer.is_file()
