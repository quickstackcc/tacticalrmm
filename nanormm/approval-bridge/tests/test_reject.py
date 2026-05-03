import pytest
from fastapi.testclient import TestClient
from httpx import Response

from approval_bridge.app import create_app


@pytest.fixture
def client(bridge_settings):
    app = create_app(bridge_settings)
    # Detach inject_client so _seed_pending can dispatch directly without
    # threading session_id through the MCP middleware (these tests target
    # the /api/nanoclaw/actions/reject/ path, not the inject flow).
    app.state.dispatcher._inject = None
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-secret",
        "X-Slack-User-ID": "U999",
        "X-Slack-User-Name": "carol",
    }


def _seed_pending(client) -> str:
    """Helper: create a pending action via the dispatcher (bypasses Slack)."""
    import asyncio

    dispatcher = client.app.state.dispatcher
    result = asyncio.run(
        dispatcher.dispatch(
            "kill_process",
            {"agent_id": "agent-1", "pid": 4123},
            summary="kill PID 4123 on agent-1",
        )
    )
    assert result["status"] == "pending"
    return result["action_id"]


def test_reject_unknown_token_returns_404(client, auth_headers):
    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": "act_doesnotexist"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_reject_no_auth_returns_401(client):
    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": "x"},
    )
    assert r.status_code == 401


def test_reject_happy_path(client, auth_headers, postgresql):
    token = _seed_pending(client)

    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": token, "reason": "unsafe in prod"},
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert "message" in r.json()

    cur = postgresql.cursor()
    cur.execute(
        "SELECT rejected_by, reject_reason FROM nanormm_actions WHERE action_id = %s",
        (token,),
    )
    row = cur.fetchone()
    assert row[0] == "carol"
    assert row[1] == "unsafe in prod"


def test_reject_already_executed_returns_409(client, auth_headers, mock_trmm):
    mock_trmm.post("/agents/agent-1/processes/kill/").mock(
        return_value=Response(200, json={"ok": True})
    )
    token = _seed_pending(client)
    # Execute first
    client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )
    # Now try to reject — should 409
    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": token},
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_reject_idempotent_on_double_call(client, auth_headers):
    """Re-rejecting an already-rejected action returns 200 with the same
    summary — no 409, no double-write."""
    token = _seed_pending(client)

    r1 = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": token, "reason": "first"},
        headers=auth_headers,
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": token, "reason": "second"},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json() == r1.json()  # Same message, idempotent
