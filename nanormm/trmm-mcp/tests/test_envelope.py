import pytest

from trmm_mcp.approvals import ApprovalRegistry
from trmm_mcp.audit import AuditLog
from trmm_mcp.policy import Policy
from trmm_mcp.tools._base import Dispatcher, ToolRegistry


@pytest.fixture
def envelope_env(fake_redis, audit_dsn, tmp_path):
    """Dispatcher wired with one human_approval and one auto tool."""
    pol_path = tmp_path / "p.yaml"
    pol_path.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
  kill_process: human_approval
"""
    )
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    dispatcher = Dispatcher(registry=registry, policy=pol, approvals=approvals, audit=audit)
    return registry, dispatcher


@pytest.mark.asyncio
async def test_pending_response_includes_nanoclaw_action_envelope(envelope_env):
    """Envelope shape matches nanoclaw's parseActionResponse expectations."""
    registry, dispatcher = envelope_env

    @registry.register(name="kill_process")
    async def kill_process(*, agent_id: str, pid: int) -> dict:
        return {"ok": True}

    summary = "Kill PID 4123 on agent DC01"
    result = await dispatcher.dispatch(
        "kill_process",
        {"agent_id": "DC01", "pid": 4123},
        summary=summary,
    )

    assert result["status"] == "pending"
    assert "action_id" in result
    assert "nanoclaw_action" in result

    env = result["nanoclaw_action"]
    assert env["preview"] == summary

    blocks = env["slack_blocks"]
    assert isinstance(blocks, list)
    # Must contain a section block with the summary text
    sections = [b for b in blocks if b.get("type") == "section"]
    assert any(summary in str(b) for b in sections)

    # Must contain an actions block with confirm + cancel buttons
    actions_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions_blocks) == 1
    elements = actions_blocks[0]["elements"]

    confirm = next(e for e in elements if e["action_id"] == "nanoclaw_confirm")
    assert confirm["value"] == result["action_id"]
    assert confirm.get("style") == "primary"

    cancel = next(e for e in elements if e["action_id"] == "nanoclaw_cancel")
    assert cancel.get("style") == "danger"


@pytest.mark.asyncio
async def test_auto_response_does_not_include_envelope(envelope_env):
    registry, dispatcher = envelope_env

    @registry.register(name="list_alerts")
    async def list_alerts() -> list:
        return [{"id": 1}]

    result = await dispatcher.dispatch("list_alerts", {}, summary="")
    assert result["status"] == "executed"
    assert "nanoclaw_action" not in result
