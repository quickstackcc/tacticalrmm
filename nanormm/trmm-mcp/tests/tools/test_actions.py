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
        result = await restart_service(client=client, agent_id="uuid-1", service_name="W3SVC")
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


@pytest.mark.asyncio
async def test_collect_artifacts_known_set(trmm_env):
    from trmm_mcp.tools.actions import collect_artifacts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/artifacts/collect/").mock(
            return_value=httpx.Response(202, json={"job_id": "j1"})
        )
        client = TrmmClient.from_env()
        result = await collect_artifacts(
            client=client, agent_id="uuid-1", artifact_set="event_logs"
        )
    assert result["job_id"] == "j1"
    assert b'"artifact_set":"event_logs"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_collect_artifacts_rejects_unknown_set(trmm_env):
    from trmm_mcp.tools.actions import collect_artifacts
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(ValueError):
        await collect_artifacts(client=client, agent_id="uuid-1", artifact_set="my_made_up_set")


@pytest.mark.asyncio
async def test_isolate_host(trmm_env):
    from trmm_mcp.tools.actions import isolate_host
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/isolate/").mock(
            return_value=httpx.Response(200, json={"isolated": True})
        )
        client = TrmmClient.from_env()
        result = await isolate_host(client=client, agent_id="uuid-1")
    assert result == {"isolated": True}


@pytest.mark.asyncio
async def test_unisolate_host(trmm_env):
    from trmm_mcp.tools.actions import unisolate_host
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/unisolate/").mock(
            return_value=httpx.Response(200, json={"isolated": False})
        )
        client = TrmmClient.from_env()
        result = await unisolate_host(client=client, agent_id="uuid-1")
    assert result == {"isolated": False}


@pytest.mark.asyncio
async def test_disable_account(trmm_env):
    from trmm_mcp.tools.actions import disable_account
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/accounts/disable/").mock(
            return_value=httpx.Response(200, json={"disabled": True})
        )
        client = TrmmClient.from_env()
        result = await disable_account(client=client, agent_id="uuid-1", username="bad-actor")
    assert result == {"disabled": True}
    assert b'"username":"bad-actor"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_pause_scheduled_task(trmm_env):
    from trmm_mcp.tools.actions import pause_scheduled_task
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/tasks/77/pause/").mock(
            return_value=httpx.Response(200, json={"paused": True})
        )
        client = TrmmClient.from_env()
        result = await pause_scheduled_task(client=client, agent_id="uuid-1", task_id=77)
    assert result == {"paused": True}
