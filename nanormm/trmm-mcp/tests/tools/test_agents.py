import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_list_agents_returns_inventory(trmm_env):
    from trmm_mcp.tools.agents import list_agents
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"agent_id": "uuid-1", "hostname": "DC01", "online": True, "client": "Acme"},
        {"agent_id": "uuid-2", "hostname": "WS-42", "online": False, "client": "Acme"},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await list_agents(client=client)

    assert len(result) == 2
    assert result[0]["hostname"] == "DC01"


@pytest.mark.asyncio
async def test_list_agents_filters(trmm_env):
    from trmm_mcp.tools.agents import list_agents
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/").mock(return_value=httpx.Response(200, json=[]))
        client = TrmmClient.from_env()
        await list_agents(client=client, online=True, client_id=5, site_id=12)

    sent = route.calls.last.request
    assert sent.url.params["online"] == "true"
    assert sent.url.params["client"] == "5"
    assert sent.url.params["site"] == "12"


@pytest.mark.asyncio
async def test_get_agent_returns_full_record(trmm_env):
    from trmm_mcp.tools.agents import get_agent
    from trmm_mcp.trmm_client import TrmmClient

    payload = {
        "agent_id": "uuid-1",
        "hostname": "DC01",
        "operating_system": "Windows Server 2022",
        "online": True,
        "last_seen": "2026-04-27T11:50:00Z",
    }
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await get_agent(client=client, agent_id="uuid-1")

    assert result["hostname"] == "DC01"
    assert result["operating_system"] == "Windows Server 2022"
