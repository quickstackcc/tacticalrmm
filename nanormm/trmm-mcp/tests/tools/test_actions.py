import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_kill_process_by_pid(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.delete("/agents/uuid-1/processes/9999/").mock(
            return_value=httpx.Response(
                200, json="Process with PID: 9999 was ended successfully"
            )
        )
        client = TrmmClient.from_env()
        result = await kill_process(client=client, agent_id="uuid-1", pid=9999)

    assert "9999" in result
    assert route.called


@pytest.mark.asyncio
async def test_restart_service(trmm_env):
    from trmm_mcp.tools.actions import restart_service
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/services/uuid-1/W3SVC/").mock(
            return_value=httpx.Response(200, json="The service was restarted successfully")
        )
        client = TrmmClient.from_env()
        result = await restart_service(client=client, agent_id="uuid-1", service_name="W3SVC")

    assert "restarted" in result
    assert b'"sv_action":"restart"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_restart_service_url_encodes_name(trmm_env):
    from trmm_mcp.tools.actions import restart_service
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/services/uuid-1/Some%20Service/").mock(
            return_value=httpx.Response(200, json="ok")
        )
        client = TrmmClient.from_env()
        await restart_service(client=client, agent_id="uuid-1", service_name="Some Service")

    assert route.called


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
