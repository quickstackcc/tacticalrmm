import pytest
from fastapi.testclient import TestClient
from httpx import Response

from approval_bridge.app import create_app


@pytest.fixture
def client(bridge_settings):
    app = create_app(bridge_settings)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-secret",
        "X-Slack-User-ID": "U123",
        "X-Slack-User-Name": "alice",
    }


def _seed_pending(client, tool_name="kill_process", args=None) -> str:
    """Helper: create a pending action via the dispatcher (bypasses Slack)."""
    import asyncio

    args = args or {"agent_id": "agent-1", "pid": 4123}
    dispatcher = client.app.state.dispatcher
    summary = f"kill PID {args['pid']} on {args['agent_id']}"

    result = asyncio.run(dispatcher.dispatch(tool_name, args, summary=summary))
    assert result["status"] == "pending"
    return result["action_id"]


def test_execute_unknown_token_returns_404(client, auth_headers):
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": "act_doesnotexist"},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json() == {"error": "Action expired or unknown"}


def test_execute_missing_token_returns_422(client, auth_headers):
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 422  # pydantic validation


def test_execute_no_auth_returns_401(client):
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": "act_x"},
    )
    assert r.status_code == 401


def test_execute_happy_path(client, auth_headers, mock_trmm):
    # Mock the TRMM call that kill_process makes.
    mock_trmm.post("/agents/agent-1/processes/kill/").mock(
        return_value=Response(200, json={"ok": True})
    )

    token = _seed_pending(client)
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    assert r.status_code == 200
    body = r.json()
    assert "message" in body
    assert "kill" in body["message"].lower()


def test_execute_rejected_action_returns_409(client, auth_headers):
    token = _seed_pending(client)
    # Pre-reject the action via the registry directly.
    client.app.state.dispatcher._approvals.mark_rejected(token, rejected_by="bob", reason="unsafe")

    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    assert r.status_code == 409
    assert r.json() == {"error": "Action was rejected"}


def test_execute_idempotent_on_slack_retry(client, auth_headers, mock_trmm):
    """If Slack retries the button click, the second POST returns the cached
    result — does not re-execute the TRMM call.
    """
    mock_trmm.post("/agents/agent-1/processes/kill/").mock(
        return_value=Response(200, json={"ok": True})
    )

    token = _seed_pending(client)

    r1 = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json() == r1.json()

    # Verify TRMM only called once
    assert mock_trmm.routes[0].call_count == 1


def test_execute_records_approver_in_audit(client, auth_headers, mock_trmm, postgresql):
    mock_trmm.post("/agents/agent-1/processes/kill/").mock(
        return_value=Response(200, json={"ok": True})
    )
    token = _seed_pending(client)
    client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    cur = postgresql.cursor()
    cur.execute(
        "SELECT approved_by, executed_at FROM nanormm_actions WHERE action_id = %s",
        (token,),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "alice"  # X-Slack-User-Name
    assert row[1] is not None  # executed_at populated


def test_execute_trmm_error_returns_502(client, auth_headers, mock_trmm):
    """If the underlying TRMM call raises TrmmApiError, the bridge maps it to
    HTTP 502 with {error: '<class>: <msg>'}."""
    # TRMM returns an error status — TrmmClient maps non-2xx to TrmmApiError.
    mock_trmm.post("/agents/agent-1/processes/kill/").mock(
        return_value=Response(500, json={"detail": "internal trmm error"})
    )

    token = _seed_pending(client)
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    assert r.status_code == 502
    body = r.json()
    assert "error" in body
    assert "TrmmApiError" in body["error"] or "trmm" in body["error"].lower()
