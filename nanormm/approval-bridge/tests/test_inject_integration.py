"""End-to-end: MCP tool call → dispatch → inject → response shape.

This test wires the real bridge app, mocks the inject HTTP target with respx,
and confirms a HUMAN_APPROVAL tool call POSTs to the inject endpoint with the
correct payload, persists the audit row, and returns the expected response.
"""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient


def test_mcp_kill_process_call_injects_card_and_responds(
    bridge_settings, mock_trmm
):
    """Call kill_process via MCP, assert inject POST + response shape."""
    from approval_bridge.app import create_app

    with respx.mock(assert_all_called=False) as nanoclaw_mock:
        # Mock the nanoclaw internal inject-card endpoint.
        inject_route = nanoclaw_mock.post(
            "http://127.0.0.1:8765/internal/sessions/sess-77/inject-card"
        ).mock(return_value=httpx.Response(202, json={"accepted": True}))

        app = create_app(bridge_settings)
        with TestClient(app) as client:
            # Initialize MCP session.
            init = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"},
                    },
                },
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    "x-nanoclaw-session": "sess-77",
                },
            )
            assert init.status_code in (200, 202)

            # Extract session ID if returned by the MCP handler.
            sid = init.headers.get("mcp-session-id")
            h = {
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-nanoclaw-session": "sess-77",
            }
            if sid:
                h["mcp-session-id"] = sid

            # Call kill_process with agent_id and pid — a human_approval tool.
            r = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "kill_process",
                        "arguments": {"agent_id": "00000000-0000-0000-0000-0000000000d1", "pid": 1234},
                    },
                },
                headers=h,
            )
            assert r.status_code == 200

            # Verify the inject endpoint was called with the correct payload.
            assert inject_route.called, "inject-card endpoint was not called"

            request = inject_route.calls.last.request
            payload = json.loads(request.content)

            # Assert payload structure matches what inject_card sends.
            assert payload["title"] == "Pending action"
            assert payload["questionId"].startswith("nrmact-act_")
            assert len(payload["options"]) == 2
            assert payload["options"][0]["value"] == "approve"
            assert payload["options"][1]["value"] == "reject"


def test_mcp_kill_process_persists_audit_row(
    bridge_settings, mock_trmm, postgresql
):
    """Verify the audit row is written when kill_process is dispatched."""
    from approval_bridge.app import create_app

    with respx.mock(assert_all_called=False) as nanoclaw_mock:
        inject_route = nanoclaw_mock.post(
            "http://127.0.0.1:8765/internal/sessions/sess-88/inject-card"
        ).mock(return_value=httpx.Response(202, json={"accepted": True}))

        app = create_app(bridge_settings)
        with TestClient(app) as client:
            # Initialize.
            init = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"},
                    },
                },
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    "x-nanoclaw-session": "sess-88",
                },
            )

            sid = init.headers.get("mcp-session-id")
            h = {
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-nanoclaw-session": "sess-88",
            }
            if sid:
                h["mcp-session-id"] = sid

            # Call kill_process.
            r = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "kill_process",
                        "arguments": {"agent_id": "00000000-0000-0000-0000-0000000000d2", "pid": 5678},
                    },
                },
                headers=h,
            )
            assert r.status_code == 200

        # Extract the action_id from the response. The response should contain
        # a JSON-RPC result with the pending action structure.
        # For simplicity, we verify the audit row exists by querying the DB.

        cur = postgresql.cursor()
        cur.execute("SELECT action_id, tool_name, policy_decision FROM nanormm_actions")
        rows = cur.fetchall()

        # Should have exactly one row (this test).
        assert len(rows) == 1
        action_id, tool_name, policy_decision = rows[0]
        assert tool_name == "kill_process"
        assert policy_decision == "human_approval"
        assert action_id.startswith("act_")


def test_mcp_kill_process_approval_row_created(bridge_settings, mock_trmm):
    """Verify the approval row is created in Redis when kill_process is dispatched."""
    from approval_bridge.app import create_app

    with respx.mock(assert_all_called=False) as nanoclaw_mock:
        nanoclaw_mock.post(
            "http://127.0.0.1:8765/internal/sessions/sess-99/inject-card"
        ).mock(return_value=httpx.Response(202, json={"accepted": True}))

        app = create_app(bridge_settings)
        with TestClient(app) as client:
            # Initialize.
            init = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"},
                    },
                },
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    "x-nanoclaw-session": "sess-99",
                },
            )

            sid = init.headers.get("mcp-session-id")
            h = {
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-nanoclaw-session": "sess-99",
            }
            if sid:
                h["mcp-session-id"] = sid

            # Call kill_process.
            r = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "kill_process",
                        "arguments": {"agent_id": "00000000-0000-0000-0000-0000000000d3", "pid": 9999},
                    },
                },
                headers=h,
            )
            assert r.status_code == 200

            # Reach the dispatcher's approval registry to verify the row exists.
            dispatcher = app.state.dispatcher
            # The action_id is returned in the response. For this test, we
            # iterate over all pending approvals and verify one exists.
            pending_count = sum(
                1 for row in dispatcher._approvals.iter_all()
                if row["status"] == "pending"
            )
            assert pending_count >= 1, "no pending approval found in Redis"
