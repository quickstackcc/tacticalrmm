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
async def test_pending_response_includes_approval_card(envelope_env):
    """Pending response carries a v2-Card-shaped `nanormm_card` envelope."""
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
    action_id = result["action_id"]

    env = result["nanormm_card"]
    assert env["kind"] == "chat-sdk"

    content = env["content"]
    assert content["type"] == "ask_question"
    assert content["questionId"] == f"nrmact-{action_id}"
    assert content["title"] == "Pending action"
    assert content["question"] == summary

    options = content["options"]
    approve = next(o for o in options if o["value"] == "approve")
    reject = next(o for o in options if o["value"] == "reject")
    assert approve["label"] == "Approve"
    assert approve["selectedLabel"] == "✅ Approved"
    assert reject["label"] == "Reject"
    assert reject["selectedLabel"] == "❌ Rejected"


@pytest.mark.asyncio
async def test_auto_response_does_not_include_card(envelope_env):
    registry, dispatcher = envelope_env

    @registry.register(name="list_alerts")
    async def list_alerts() -> list:
        return [{"id": 1}]

    result = await dispatcher.dispatch("list_alerts", {}, summary="")
    assert result["status"] == "executed"
    assert "nanormm_card" not in result
    assert "nanoclaw_action" not in result
