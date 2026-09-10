from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import duckdb
import httpx

from cerebro import api
from cerebro.settings import Settings


def make_settings(
    database: Path,
    *,
    basic_auth_user: str | None = None,
    basic_auth_password: str | None = None,
) -> Settings:
    return Settings(
        database_path=database,
        database_schema="main",
        llm_base_url="https://llm.example.invalid/v1",
        llm_api_key="basic-auth-secret-marker",
        llm_model="deployment-model",
        llm_response_mode="auto",
        embedding_model=None,
        llm_provider_id="greennode-compatible",
        llm_provider_name="GreenNode-compatible",
        basic_auth_user=basic_auth_user,
        basic_auth_password=basic_auth_password,
    )


def make_database(path: Path) -> Path:
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE basic_auth_probe (id INTEGER)")
    return path


def make_spa(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html><title>Cerebro</title>", encoding="utf-8")
    return path


def make_app(
    tmp_path: Path,
    monkeypatch,
    *,
    basic_auth_user: str | None = None,
    basic_auth_password: str | None = None,
):
    settings = make_settings(
        make_database(tmp_path / "auth.duckdb"),
        basic_auth_user=basic_auth_user,
        basic_auth_password=basic_auth_password,
    )
    monkeypatch.setattr(api.Settings, "from_environment", lambda: settings)
    return api.create_app(
        generation_output_root=tmp_path / "generated",
        reviewed_output_root=tmp_path / "reviewed",
        web_dist_path=make_spa(tmp_path / "dist"),
    )


def request(app, path: str, *, auth: tuple[str, str] | None = None, with_lifespan: bool = False) -> httpx.Response:
    async def call() -> httpx.Response:
        headers = {}
        if auth is not None:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            if with_lifespan:
                async with app.router.lifespan_context(app):
                    return await client.get(path, headers=headers)
            return await client.get(path, headers=headers)

    return asyncio.run(call())


def test_disabled_by_default_allows_every_route(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)

    assert request(app, "/").status_code == 200
    assert request(app, "/api/health").status_code == 200
    assert request(app, "/api/health/ready").status_code in {200, 503}


def test_enabled_rejects_missing_and_wrong_credentials(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, basic_auth_user="ops", basic_auth_password="s3cret")

    no_credentials = request(app, "/")
    assert no_credentials.status_code == 401
    assert no_credentials.headers["www-authenticate"] == 'Basic realm="Cerebro"'

    wrong_credentials = request(app, "/", auth=("ops", "wrong"))
    assert wrong_credentials.status_code == 401


def test_enabled_accepts_correct_credentials_on_static_and_mcp_routes(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, basic_auth_user="ops", basic_auth_password="s3cret")

    static_page = request(app, "/", auth=("ops", "s3cret"))
    assert static_page.status_code == 200

    # The mounted MCP app requires its own protocol handshake, so a bare GET
    # legitimately fails downstream — the point here is only that authorized
    # requests are let past the auth gate (never a 401) to reach it at all.
    mcp_route = request(app, "/mcp/", auth=("ops", "s3cret"), with_lifespan=True)
    assert mcp_route.status_code != 401
    mcp_route_unauthorized = request(app, "/mcp/")
    assert mcp_route_unauthorized.status_code == 401


def test_health_endpoints_stay_unauthenticated_even_when_enabled(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, basic_auth_user="ops", basic_auth_password="s3cret")

    assert request(app, "/api/health").status_code == 200
    assert request(app, "/api/health/ready").status_code in {200, 503}
