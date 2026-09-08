"""The Text-to-SQL execution surface, deliberately kept out of `api.py`.

`api.py` is advisory grounding metadata and nothing else, and a test asserts
that. Execution lives here instead, behind a router that only exists once an
operator has supplied a trusted `AuthorizationScope` on the command line. A
server started without that scope has no execution route at all, so the safe
configuration is the default rather than something to remember.

The request body carries a question and a row cap. It never carries a scope:
a caller that could name its own scope would be granting itself authority,
which is the exact thing the whole authorization-first design prevents.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .evaluation import AgentRuntime
from .models import SQLGenerationRequest

_log = logging.getLogger("cerebro.agent_api")

#: A demo-friendly ceiling. The compiler still enforces its own effective cap,
#: and a lower default keeps one careless question from returning a huge page.
DEFAULT_MAX_ROWS = 100


class AgentAskRequest(BaseModel):
    """Everything a caller may say. Notably absent: any authority claim."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
    max_rows: int = Field(default=DEFAULT_MAX_ROWS, ge=1, le=1_000)


def agent_status(runtime: Any) -> str:
    return "ready" if isinstance(runtime, AgentRuntime) else "disabled"


def create_agent_router(runtime: AgentRuntime) -> APIRouter:
    """Expose one question endpoint bound to one pre-authorized runtime."""
    if not isinstance(runtime, AgentRuntime):
        raise TypeError("the agent surface requires a composed AgentRuntime")

    router = APIRouter(prefix="/api/agent", tags=["agent"])

    @router.get("/scope")
    async def scope() -> dict:
        """Publish what this server is allowed to see, never how to change it."""
        authorized = runtime.authorization_scope
        return {
            "policy_version": authorized.policy_version,
            "authorization_scope_hash": authorized.authorization_scope_hash,
            "allowed_object_ids": sorted(authorized.allowed_object_ids),
            "allowed_classifications": sorted(authorized.allowed_classifications),
            "provider": getattr(runtime.provider, "provider", "unknown"),
            "model": getattr(runtime.provider, "model", "unknown"),
            "dialect": "duckdb",
            "max_rows_default": DEFAULT_MAX_ROWS,
        }

    @router.post("/ask")
    async def ask(payload: AgentAskRequest) -> JSONResponse:
        try:
            request = SQLGenerationRequest(
                question=payload.question,
                authorization_scope=runtime.authorization_scope,
                dialect="duckdb",
                max_rows=payload.max_rows,
            )
            # The composed DuckDB connection belongs to the server thread;
            # moving this call to a generic worker can deadlock that connection.
            response = runtime.agent.run(request)
        except Exception:  # noqa: BLE001 - sanitized to a stable code
            # Only a stable code crosses this boundary. An engine or provider
            # message can carry data, and this response is shown in a browser.
            _log.exception("agent request failed")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "code": "agent_request_failed"},
            )
        # Serializing through JSON honours every `exclude` on the contract, so
        # resolved literals and bound parameter values cannot reach the client.
        return JSONResponse(content=json.loads(response.model_dump_json()))

    return router


def attach_agent(app: Any, runtime: AgentRuntime) -> None:
    """Mount the execution surface and record it for health reporting."""
    from fastapi.routing import APIRoute
    from starlette.routing import Mount

    app.state.agent_runtime = runtime
    app.include_router(create_agent_router(runtime))

    # The built UI is mounted at "/" as a catch-all, and Starlette matches routes
    # in order. A router added afterwards would be shadowed by it and answer 405
    # from the static files handler, so the catch-all is moved back to last.
    routes = app.router.routes
    api_fallback = [
        route
        for route in routes
        if isinstance(route, APIRoute) and route.path == "/api/{unknown_path:path}"
    ]
    catch_all = [
        route for route in routes if isinstance(route, Mount) and route.path == ""
    ]
    for route in [*api_fallback, *catch_all]:
        routes.remove(route)
    for route in [*api_fallback, *catch_all]:
        routes.append(route)
