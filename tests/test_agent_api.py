"""The chat execution surface: opt-in, scope-bound, and value-free.

Every case runs offline against `GoldenProvider`, so the endpoint contract is
proved without a credential or a live call.
"""

from __future__ import annotations

import asyncio
import json

import golden_answers as golden
import httpx
import pytest

from cerebro.agent_api import attach_agent, create_agent_router
from cerebro.api import create_app
from cerebro.evaluation import build_agent, build_authorization_scope
from cerebro.paths import DEFAULT_BUNDLE

SUPPORTED_QUESTION = "What is transaction volume by branch?"


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    import duckdb
    from test_baseline_evaluation import _SCHEMA

    path = tmp_path_factory.mktemp("agent-api") / "agent.duckdb"
    connection = duckdb.connect(str(path))
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.executemany(
        "INSERT INTO branches VALUES (?, ?, ?, ?, ?, ?)",
        [(i, f"Branch {i}", "Delhi", "DL", "2020-01-01", f"IFSC{i:04d}") for i in (1, 2)],
    )
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(i, i, i % 2 + 1, "SAVINGS", 10.0 * i, "2021-07-01", "ACTIVE") for i in range(1, 7)],
    )
    connection.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(i, i % 6 + 1, "2026-03-05", "DEPOSIT", 10.0 * i, "ATM", "G") for i in range(1, 13)],
    )
    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (i, f"Customer {i}", "F", "1990-01-01", "Delhi", "DL", 900 + i, "c@e.invalid", "Eng", 500 + i, "2021-06-01", 700)
            for i in range(1, 7)
        ],
    )
    connection.executemany(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(i, i, i, "CREDIT", "2022-01-01", "2030-01-01", 1, "A") for i in range(1, 7)],
    )
    connection.executemany(
        "INSERT INTO card_transactions VALUES (?, ?, ?, ?, ?, ?)",
        [(i, i % 6 + 1, "2026-03-01", "G", 5.0 * i, 0) for i in range(1, 7)],
    )
    connection.close()
    return str(path)


@pytest.fixture(scope="module")
def scope():
    return build_authorization_scope(
        allowed_object_ids=golden.REFERENCE_OBJECT_IDS,
        policy_version=golden.REFERENCE_POLICY_VERSION,
        tenant_scope_hash=golden.REFERENCE_TENANT_SCOPE_HASH,
    )


@pytest.fixture()
def runtime(database, scope):
    built = build_agent(
        database, "scripted", scope, golden.GoldenProvider(), bundle_path=DEFAULT_BUNDLE
    )
    yield built
    built.close()


def _call(app, method: str, path: str, body: dict | None = None):
    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            if method == "GET":
                return await client.get(path)
            return await client.post(path, json=body)

    return asyncio.run(exercise())


# --- opt-in surface ---------------------------------------------------------


def test_grounding_only_server_has_no_execution_route():
    """Forgetting the scope flag must not expose query execution."""
    app = create_app(DEFAULT_BUNDLE)
    paths = {route.path for route in app.routes}
    assert not {path for path in paths if path.startswith("/api/agent")}
    assert _call(app, "GET", "/api/health").json()["agent"] == "disabled"
    # The built UI answers as a catch-all, so the point is only that nothing
    # executes: the exact rejection status belongs to whatever handles the path.
    assert _call(app, "POST", "/api/agent/ask", {"question": "x"}).status_code != 200


def test_attached_agent_is_reported_as_ready(runtime):
    app = create_app(DEFAULT_BUNDLE)
    attach_agent(app, runtime)
    assert _call(app, "GET", "/api/health").json()["agent"] == "ready"


def test_router_requires_a_composed_runtime():
    for candidate in (None, {"agent": "x"}, "runtime"):
        with pytest.raises(TypeError):
            create_agent_router(candidate)


# --- the question endpoint --------------------------------------------------


@pytest.fixture()
def app_with_agent(runtime):
    app = create_app(DEFAULT_BUNDLE)
    attach_agent(app, runtime)
    return app


def test_supported_question_returns_sql_rows_and_lineage(app_with_agent):
    response = _call(app_with_agent, "POST", "/api/agent/ask", {"question": SUPPORTED_QUESTION})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", body.get("violations")
    assert body["sql_artifact"]["sql"].startswith("SELECT")
    assert body["result"]["row_count"] >= 1
    assert body["output_lineage"]
    assert body["generation_route"] == "default_ir"
    assert body["budget_usage"]["semantic_calls"] == 1
    # The graph highlight depends on these being real object identifiers.
    assert "table.transactions" in body["grounding_usage"]["object_ids"]
    assert body["grounding_usage"]["relationship_ids"]


def test_response_carries_no_resolved_value_or_question_text(app_with_agent):
    body = _call(
        app_with_agent,
        "POST",
        "/api/agent/ask",
        {"question": "Which five customers have the highest annual income?"},
    ).json()
    assert body["status"] == "ok", body.get("violations")
    serialized = json.dumps(body)
    # Rows are the answer and must be present; parameters and IR values must not.
    assert '"parameters"' not in serialized
    assert '"intent"' not in serialized
    assert '"value"' not in json.dumps(body["ir"])
    assert body["sql_artifact"]["parameter_count"] == 1
    assert body["disclosures"]


def test_unsupported_question_returns_a_typed_outcome_not_a_crash(app_with_agent):
    body = _call(
        app_with_agent, "POST", "/api/agent/ask", {"question": "Who won the league?"}
    ).json()
    assert body["status"] in {"refused", "check_failed"}
    if body["status"] == "refused":
        assert body["reason"]
    else:
        assert body["violations"]


def test_request_body_cannot_carry_an_authorization_scope(app_with_agent):
    """A caller that could name its own scope would be granting itself authority."""
    response = _call(
        app_with_agent,
        "POST",
        "/api/agent/ask",
        {
            "question": SUPPORTED_QUESTION,
            "authorization_scope": {"allowed_object_ids": ["table.customers"]},
        },
    )
    assert response.status_code == 422


def test_row_cap_is_bounded(app_with_agent):
    for bad in (0, -1, 5000):
        response = _call(
            app_with_agent,
            "POST",
            "/api/agent/ask",
            {"question": SUPPORTED_QUESTION, "max_rows": bad},
        )
        assert response.status_code == 422


def test_scope_endpoint_publishes_what_the_server_may_see(app_with_agent, scope):
    body = _call(app_with_agent, "GET", "/api/agent/scope").json()
    assert body["authorization_scope_hash"] == scope.authorization_scope_hash
    assert "table.transactions" in body["allowed_object_ids"]
    assert body["dialect"] == "duckdb"


def test_agent_route_is_not_shadowed_by_the_built_ui(runtime, tmp_path, monkeypatch):
    """Starlette matches in order, so a root catch-all must stay last."""
    from starlette.routing import Mount

    import cerebro.api as api_module

    dist = tmp_path / "apps" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<title>Cerebro</title>", encoding="utf-8")
    monkeypatch.setattr(api_module, "ROOT", tmp_path)

    app = api_module.create_app(DEFAULT_BUNDLE)
    attach_agent(app, runtime)
    assert isinstance(app.router.routes[-1], Mount)

    assert _call(app, "GET", "/").status_code == 200
    assert _call(app, "GET", "/api/agent/scope").status_code == 200
    assert (
        _call(app, "POST", "/api/agent/ask", {"question": SUPPORTED_QUESTION}).json()[
            "status"
        ]
        == "ok"
    )
