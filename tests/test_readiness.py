from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import duckdb
import httpx
import yaml

from cerebro import api
from cerebro.paths import DEFAULT_BUNDLE
from cerebro.settings import Settings


def deployment_settings(database: Path | None, *, configured: bool = True) -> Settings:
    return Settings(
        database_path=database,
        database_schema="main",
        llm_base_url="https://llm.example.invalid/v1",
        llm_api_key="readiness-secret-marker" if configured else None,
        llm_model="deployment-model" if configured else None,
        llm_response_mode="auto",
        embedding_model=None,
        llm_provider_id="greennode-compatible",
        llm_provider_name="GreenNode-compatible",
    )


def make_database(path: Path) -> Path:
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE readiness_probe (id INTEGER)")
    return path


def make_spa(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html><title>Cerebro</title>", encoding="utf-8")
    return path


def make_app(
    tmp_path: Path,
    monkeypatch,
    *,
    database: Path | None,
    web_dist: Path,
    configured: bool = True,
    bundle_path: Path = DEFAULT_BUNDLE,
):
    monkeypatch.setattr(api.Settings, "from_environment", lambda: deployment_settings(database, configured=configured))
    return api.create_app(
        bundle_path=bundle_path,
        generation_output_root=tmp_path / "generated",
        reviewed_output_root=tmp_path / "reviewed",
        web_dist_path=web_dist,
    )


def get_ready(app) -> httpx.Response:
    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get("/api/health/ready")

    return asyncio.run(request())


def test_readiness_succeeds_without_live_model_call(tmp_path: Path, monkeypatch):
    provider_calls = 0

    def provider():
        nonlocal provider_calls
        provider_calls += 1
        return None

    monkeypatch.setattr(api, "provider_from_environment", provider)
    app = make_app(
        tmp_path,
        monkeypatch,
        database=make_database(tmp_path / "ready.duckdb"),
        web_dist=make_spa(tmp_path / "dist"),
    )
    startup_provider_calls = provider_calls

    response = get_ready(app)

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert set(response.json()["components"]) == {
        "active_bundle",
        "golden_bundle",
        "web_ui",
        "database",
        "llm",
    }
    assert all(component["status"] == "ok" for component in response.json()["components"].values())
    assert provider_calls == startup_provider_calls


def test_readiness_rejects_missing_and_unreadable_database(tmp_path: Path, monkeypatch):
    spa = make_spa(tmp_path / "dist")
    missing = make_app(tmp_path, monkeypatch, database=tmp_path / "missing.duckdb", web_dist=spa)
    missing_response = get_ready(missing)
    assert missing_response.status_code == 503
    assert missing_response.json()["components"]["database"] == {
        "status": "error",
        "configured": True,
    }

    invalid_path = tmp_path / "invalid.duckdb"
    invalid_path.write_text("not a DuckDB database", encoding="utf-8")
    invalid = make_app(tmp_path, monkeypatch, database=invalid_path, web_dist=spa)
    invalid_response = get_ready(invalid)
    assert invalid_response.status_code == 503
    assert invalid_response.json()["components"]["database"]["status"] == "error"


def test_readiness_rejects_missing_spa(tmp_path: Path, monkeypatch):
    app = make_app(
        tmp_path,
        monkeypatch,
        database=make_database(tmp_path / "ready.duckdb"),
        web_dist=tmp_path / "missing-dist",
    )

    response = get_ready(app)

    assert response.status_code == 503
    assert response.json()["components"]["web_ui"] == {"status": "error"}


def test_readiness_revalidates_active_bundle(tmp_path: Path, monkeypatch):
    active = tmp_path / "active"
    shutil.copytree(DEFAULT_BUNDLE, active)
    app = make_app(
        tmp_path,
        monkeypatch,
        database=make_database(tmp_path / "ready.duckdb"),
        web_dist=make_spa(tmp_path / "dist"),
        bundle_path=active,
    )
    manifest_path = active / "bundle.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["review_state"] = "candidate"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    response = get_ready(app)

    assert response.status_code == 503
    assert response.json()["components"]["active_bundle"] == {"status": "error"}


def test_readiness_revalidates_golden_bundle(tmp_path: Path, monkeypatch):
    golden = tmp_path / "golden"
    active = tmp_path / "active"
    shutil.copytree(DEFAULT_BUNDLE, golden)
    shutil.copytree(DEFAULT_BUNDLE, active)
    monkeypatch.setattr(api, "DEFAULT_BUNDLE", golden)
    app = make_app(
        tmp_path,
        monkeypatch,
        database=make_database(tmp_path / "ready.duckdb"),
        web_dist=make_spa(tmp_path / "dist"),
        bundle_path=active,
    )
    (golden / "bundle.yaml").write_text("not: [valid", encoding="utf-8")

    response = get_ready(app)

    assert response.status_code == 503
    assert response.json()["components"]["golden_bundle"] == {"status": "error"}


def test_readiness_requires_llm_and_redacts_secrets(tmp_path: Path, monkeypatch):
    app = make_app(
        tmp_path,
        monkeypatch,
        database=make_database(tmp_path / "ready.duckdb"),
        web_dist=make_spa(tmp_path / "dist"),
        configured=False,
    )

    response = get_ready(app)

    assert response.status_code == 503
    assert response.json()["components"]["llm"] == {
        "status": "error",
        "configured": False,
    }
    assert "readiness-secret-marker" not in response.text
    assert str(tmp_path) not in response.text
