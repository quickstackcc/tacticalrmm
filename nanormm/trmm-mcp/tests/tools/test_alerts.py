import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_list_alerts_returns_normalized_records(trmm_env):
    from trmm_mcp.tools.alerts import list_alerts
    from trmm_mcp.trmm_client import TrmmClient

    fake_payload = [
        {
            "id": 1,
            "alert_time": "2026-04-27T10:00:00Z",
            "severity": "warning",
            "message": "CPU high",
            "agent": "agent-uuid-1",
            "snoozed": False,
            "resolved": False,
        },
        {
            "id": 2,
            "alert_time": "2026-04-27T11:00:00Z",
            "severity": "error",
            "message": "Disk full",
            "agent": "agent-uuid-2",
            "snoozed": False,
            "resolved": False,
        },
    ]

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/alerts/").mock(return_value=httpx.Response(200, json=fake_payload))
        client = TrmmClient.from_env()
        result = await list_alerts(client=client)

    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_list_alerts_filters_by_status(trmm_env):
    from trmm_mcp.tools.alerts import list_alerts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/alerts/").mock(return_value=httpx.Response(200, json=[]))
        client = TrmmClient.from_env()
        await list_alerts(client=client, status="unresolved")

    sent = route.calls.last.request
    assert sent.url.params["resolved"] == "false"


@pytest.mark.asyncio
async def test_get_alert_returns_full_record(trmm_env):
    from trmm_mcp.tools.alerts import get_alert
    from trmm_mcp.trmm_client import TrmmClient

    payload = {"id": 42, "severity": "error", "message": "x", "agent": "uuid"}

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/alerts/42/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await get_alert(client=client, alert_id=42)

    assert result["id"] == 42
    assert result["severity"] == "error"


@pytest.mark.asyncio
async def test_get_alert_404_propagates(trmm_env):
    from trmm_mcp.exceptions import TrmmNotFoundError
    from trmm_mcp.tools.alerts import get_alert
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/alerts/9999/").mock(return_value=httpx.Response(404))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmNotFoundError):
            await get_alert(client=client, alert_id=9999)


@pytest.mark.asyncio
async def test_search_past_alerts_filters_by_agent_and_since(trmm_env):
    from trmm_mcp.tools.alerts import search_past_alerts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/alerts/").mock(return_value=httpx.Response(200, json=[]))
        client = TrmmClient.from_env()
        await search_past_alerts(client=client, agent_id="abc", since="2026-04-01T00:00:00Z")

    sent = route.calls.last.request
    assert sent.url.params["agent"] == "abc"
    assert sent.url.params["since"] == "2026-04-01T00:00:00Z"
