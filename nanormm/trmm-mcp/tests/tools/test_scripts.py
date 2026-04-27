import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_script_history_returns_agent_history(trmm_env):
    from trmm_mcp.tools.scripts import script_history
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"id": 1, "type": "script_run", "command": "", "script_name": "Disk-Check"},
        {"id": 2, "type": "cmd_run", "command": "ipconfig"},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/history/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await script_history(client=client, agent_id="uuid-1", n=20)

    assert len(result) == 2
    assert result[0]["type"] == "script_run"


@pytest.mark.asyncio
async def test_run_script_on_agent_by_id(trmm_env):
    from trmm_mcp.tools.scripts import run_script_on_agent
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/scripts/run/").mock(
            return_value=httpx.Response(200, json={"run_id": 555})
        )
        client = TrmmClient.from_env()
        result = await run_script_on_agent(
            client=client, agent_id="uuid-1", script_id=42, args=["--verbose"]
        )
    assert result == {"run_id": 555}
    body = route.calls.last.request.content
    assert b'"script_id":42' in body
    assert b'"--verbose"' in body


@pytest.mark.asyncio
async def test_run_script_rejects_script_body_kwarg(trmm_env):
    """Defensive: agent must never be able to ship script source."""
    from trmm_mcp.tools.scripts import run_script_on_agent
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(TypeError):
        # script_body is not a parameter; this should fail at call time
        await run_script_on_agent(
            client=client, agent_id="uuid-1", script_id=42, script_body="rm -rf /"
        )


@pytest.mark.asyncio
async def test_run_inline_command(trmm_env):
    from trmm_mcp.tools.scripts import run_inline_command
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/cmd/").mock(
            return_value=httpx.Response(200, json={"stdout": "hi", "retcode": 0})
        )
        client = TrmmClient.from_env()
        result = await run_inline_command(  # noqa: S604 - shell is TRMM API field, not subprocess
            client=client, agent_id="uuid-1", shell="cmd", command="echo hi"
        )
    assert result["stdout"] == "hi"
    body = route.calls.last.request.content
    assert b'"shell":"cmd"' in body
    assert b'"command":"echo hi"' in body


@pytest.mark.asyncio
async def test_run_inline_command_validates_shell(trmm_env):
    from trmm_mcp.tools.scripts import run_inline_command
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(ValueError):
        await run_inline_command(  # noqa: S604 - shell is TRMM API field, not subprocess
            client=client, agent_id="uuid-1", shell="brainfuck", command="x"
        )
