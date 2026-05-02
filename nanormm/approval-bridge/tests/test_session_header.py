"""Tests for the X-Nanoclaw-Session middleware and contextvar."""
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import PlainTextResponse

from approval_bridge.mcp_app import SESSION_ID_VAR, _SessionHeaderMiddleware


def test_session_header_middleware_sets_contextvar():
    """Middleware should extract X-Nanoclaw-Session header and set the contextvar."""
    captured: list[str | None] = []

    async def endpoint(request):
        captured.append(SESSION_ID_VAR.get())
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(_SessionHeaderMiddleware)

    with TestClient(app) as client:
        r = client.get("/", headers={"X-Nanoclaw-Session": "sess-abc"})
        assert r.status_code == 200
    assert captured == ["sess-abc"]


def test_session_header_middleware_absent_yields_none():
    """When X-Nanoclaw-Session is absent, contextvar should be None."""
    captured: list[str | None] = []

    async def endpoint(request):
        captured.append(SESSION_ID_VAR.get())
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(_SessionHeaderMiddleware)

    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
    assert captured == [None]


def test_session_header_resets_after_request():
    """ContextVar must reset after the request, not leak across requests."""
    async def endpoint(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(_SessionHeaderMiddleware)

    with TestClient(app) as client:
        client.get("/", headers={"X-Nanoclaw-Session": "sess-xyz"})
        # After the request, outside the middleware, contextvar should be default
        assert SESSION_ID_VAR.get() is None
