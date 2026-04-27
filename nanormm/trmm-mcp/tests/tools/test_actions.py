import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_kill_process_by_pid(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/processes/kill/").mock(
            return_value=httpx.Response(200, json={"killed": True})
        )
        client = TrmmClient.from_env()
        result = await kill_process(client=client, agent_id="uuid-1", pid=9999)

    assert result == {"killed": True}
    body = route.calls.last.request.content
    assert b'"pid":9999' in body


@pytest.mark.asyncio
async def test_kill_process_by_name(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/processes/kill/").mock(
            return_value=httpx.Response(200, json={"killed": True})
        )
        client = TrmmClient.from_env()
        await kill_process(client=client, agent_id="uuid-1", name="bad.exe")

    body = route.calls.last.request.content
    assert b'"name":"bad.exe"' in body


@pytest.mark.asyncio
async def test_kill_process_requires_pid_or_name(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(ValueError):
        await kill_process(client=client, agent_id="uuid-1")


@pytest.mark.asyncio
async def test_restart_service(trmm_env):
    from trmm_mcp.tools.actions import restart_service
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/services/restart/").mock(
            return_value=httpx.Response(200, json={"status": "restarted"})
        )
        client = TrmmClient.from_env()
        result = await restart_service(
            client=client, agent_id="uuid-1", service_name="W3SVC"
        )
    assert result["status"] == "restarted"
    assert b'"service_name":"W3SVC"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_reboot_agent(trmm_env):
    from trmm_mcp.tools.actions import reboot_agent
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/reboot/").mock(
            return_value=httpx.Response(200, json={"queued": True})
        )
        client = TrmmClient.from_env()
        result = await reboot_agent(client=client, agent_id="uuid-1")
    assert result == {"queued": True}
