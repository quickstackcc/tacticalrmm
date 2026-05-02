"""Integration test for the /mcp endpoint exposed by the bridge.

Uses the MCP Python SDK's HTTP client to perform a real initialize +
list_tools handshake against the bridge's mounted streamable_http app.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from approval_bridge.app import create_app


def test_mcp_endpoint_responds_to_initialize(bridge_settings):
    """Hit /mcp/ with a JSON-RPC initialize and confirm a 200 + protocol response.

    We don't drive the full MCP client here — we just confirm the route is
    mounted and accepts JSON-RPC POSTs (the streamable-http transport's
    primary entry point).
    """
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        # MCP streamable-http expects POST with JSON-RPC body and the
        # mcp-protocol-version + accept headers per the SDK's HTTP transport.
        r = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.0.0"},
                },
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )
        # SDK returns 200 with either a JSON or SSE body depending on
        # negotiation. Either way, NOT 404 (route mounted) and NOT 500.
        assert r.status_code in (200, 202)


def test_mcp_endpoint_lists_trmm_tools(bridge_settings):
    """After initialize, list_tools should return the trmm-mcp tool surface."""
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        # Initialize first to establish a session
        init = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.0.0"},
                },
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )
        assert init.status_code in (200, 202)

        # Capture session ID from response headers (streamable-http convention)
        session_id = init.headers.get("mcp-session-id")

        # If session_id is required, include it; otherwise the second call
        # is stateless. Try without first.
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        if session_id:
            headers["mcp-session-id"] = session_id

        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert r.status_code == 200

        # Body may be JSON or SSE — handle both
        body_text = r.text
        # Look for one of the known tool names in either parsed JSON or SSE body
        assert "list_alerts" in body_text or "kill_process" in body_text


def test_mcp_endpoint_sets_session_contextvar(bridge_settings, monkeypatch):
    """X-Nanoclaw-Session on /mcp/ requests must reach SESSION_ID_VAR."""
    from approval_bridge.app import create_app
    from approval_bridge.mcp_app import SESSION_ID_VAR

    captured: list[str | None] = []

    # Patch the dispatcher's dispatch so we can see what session_id arrives.
    # We don't actually call any tool — we read the contextvar inside dispatch.
    from trmm_mcp.tools._base import Dispatcher
    orig_dispatch = Dispatcher.dispatch

    async def spying_dispatch(self, name, args, *, summary="", session_id=None):
        captured.append(SESSION_ID_VAR.get())
        return {"status": "executed", "result": None}

    monkeypatch.setattr(Dispatcher, "dispatch", spying_dispatch)

    app = create_app(bridge_settings)
    with TestClient(app) as client:
        # Initialize
        init = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "test", "version": "0"}}},
            headers={"accept": "application/json, text/event-stream",
                     "content-type": "application/json",
                     "x-nanoclaw-session": "session-77"},
        )
        sid = init.headers.get("mcp-session-id")
        h = {"accept": "application/json, text/event-stream",
             "content-type": "application/json",
             "x-nanoclaw-session": "session-77"}
        if sid:
            h["mcp-session-id"] = sid

        # Call any registered tool — list_alerts is auto in conftest's policy.
        client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "list_alerts", "arguments": {}}},
            headers=h,
        )
    assert captured == ["session-77"]
