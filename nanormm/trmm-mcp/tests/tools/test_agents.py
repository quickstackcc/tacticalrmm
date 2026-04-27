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


@pytest.mark.asyncio
async def test_agent_recent_checks_returns_list(trmm_env):
    from trmm_mcp.tools.agents import agent_recent_checks
    from trmm_mcp.trmm_client import TrmmClient

    payload = [{"id": 1, "name": "CPU", "status": "passing"}]
    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/uuid-1/checks/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await agent_recent_checks(client=client, agent_id="uuid-1", n=20)

    assert len(result) == 1
    assert route.calls.last.request.url.params["limit"] == "20"


@pytest.mark.asyncio
async def test_agent_recent_tasks_returns_list(trmm_env):
    from trmm_mcp.tools.agents import agent_recent_tasks
    from trmm_mcp.trmm_client import TrmmClient

    payload = [{"id": 1, "name": "Backup", "last_run_status": "success"}]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/tasks/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await agent_recent_tasks(client=client, agent_id="uuid-1", n=20)

    assert result[0]["name"] == "Backup"


@pytest.mark.asyncio
async def test_agent_patch_state_returns_categorized_kbs(trmm_env):
    from trmm_mcp.tools.agents import agent_patch_state
    from trmm_mcp.trmm_client import TrmmClient

    payload = {
        "installed": ["KB1"],
        "missing": ["KB2", "KB3"],
        "failed": [],
        "pending_reboot": ["KB4"],
    }
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/winupdates/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await agent_patch_state(client=client, agent_id="uuid-1")

    assert result["missing"] == ["KB2", "KB3"]


@pytest.mark.asyncio
async def test_agent_running_processes_returns_list(trmm_env):
    from trmm_mcp.tools.agents import agent_running_processes
    from trmm_mcp.trmm_client import TrmmClient

    payload = [{"pid": 1234, "name": "explorer.exe", "cpu": 0.1, "mem_mb": 50.2}]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/processes/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await agent_running_processes(client=client, agent_id="uuid-1")

    assert result[0]["pid"] == 1234
