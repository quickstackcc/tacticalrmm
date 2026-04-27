import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_list_alerts_returns_dashboard_payload(trmm_env):
    from trmm_mcp.tools.alerts import list_alerts
    from trmm_mcp.trmm_client import TrmmClient

    fake_payload = {
        "alerts_count": 2,
        "alerts": [
            {"id": 1, "severity": "warning", "message": "CPU high", "agent": "uuid-1"},
            {"id": 2, "severity": "error", "message": "Disk full", "agent": "uuid-2"},
        ],
    }
    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/").mock(return_value=httpx.Response(200, json=fake_payload))
        client = TrmmClient.from_env()
        result = await list_alerts(client=client)

    assert result["alerts_count"] == 2
    assert len(result["alerts"]) == 2
    # No filters → default top=25
    assert b'"top":25' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_list_alerts_filters_by_status(trmm_env):
    from trmm_mcp.tools.alerts import list_alerts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/").mock(
            return_value=httpx.Response(200, json={"alerts_count": 0, "alerts": []})
        )
        client = TrmmClient.from_env()
        await list_alerts(client=client, status="unresolved")

    body = route.calls.last.request.content
    assert b'"resolvedFilter":false' in body


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
async def test_search_past_alerts_uses_time_filter(trmm_env):
    from trmm_mcp.tools.alerts import search_past_alerts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/").mock(
            return_value=httpx.Response(200, json={"alerts_count": 0, "alerts": []})
        )
        client = TrmmClient.from_env()
        await search_past_alerts(client=client, agent_id="abc", since="2026-04-01T00:00:00Z")

    body = route.calls.last.request.content
    # timeFilter is TRMM's days-back integer, not the original ISO string
    assert b'"timeFilter":' in body
    # Should be a positive integer (days since 2026-04-01 → at least 1)
    import json as _json

    sent = _json.loads(body)
    assert isinstance(sent["timeFilter"], int)
    assert sent["timeFilter"] >= 1


@pytest.mark.asyncio
async def test_acknowledge_alert_patches_with_note(trmm_env):
    from trmm_mcp.tools.alerts import acknowledge_alert
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/42/").mock(
            return_value=httpx.Response(200, json={"id": 42, "resolved": True})
        )
        client = TrmmClient.from_env()
        result = await acknowledge_alert(client=client, alert_id=42, note="handled via nanormm")

    assert result["resolved"] is True
    body = route.calls.last.request.content
    assert b'"resolved":true' in body
    assert b"handled via nanormm" in body
