import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_script_history_returns_recent_runs(trmm_env):
    from trmm_mcp.tools.scripts import script_history
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"id": 1, "script_name": "Disk-Check", "stdout": "OK", "stderr": "", "retcode": 0},
        {"id": 2, "script_name": "Restart-IIS", "stdout": "", "stderr": "denied", "retcode": 1},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/uuid-1/scripthistory/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await script_history(client=client, agent_id="uuid-1", n=20)

    assert len(result) == 2
    assert route.calls.last.request.url.params["limit"] == "20"
