import pytest


@pytest.fixture
def registry_env(fake_redis, audit_dsn, tmp_path):
    """Wire policy + approvals + audit into a Dispatcher."""
    from trmm_mcp.approvals import ApprovalRegistry
    from trmm_mcp.audit import AuditLog
    from trmm_mcp.policy import Policy
    from trmm_mcp.tools._base import Dispatcher, ToolRegistry

    pol_path = tmp_path / "p.yaml"
    pol_path.write_text(
        """
version: 1
default: human_approval
tools:
  always_auto: auto
  always_gated: human_approval
  always_forbidden: forbidden
"""
    )
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    dispatcher = Dispatcher(registry=registry, policy=pol, approvals=approvals, audit=audit)
    return registry, dispatcher


@pytest.mark.asyncio
async def test_auto_tool_executes_immediately(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_auto")
    async def my_tool(x: int) -> int:
        return x + 1

    result = await dispatcher.dispatch("always_auto", {"x": 5})
    assert result == {"status": "executed", "result": 6}


@pytest.mark.asyncio
async def test_gated_tool_returns_pending_and_writes_audit(registry_env, audit_dsn):
    import psycopg
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x * 10

    result = await dispatcher.dispatch(
        "always_gated", {"x": 5}, summary="Multiply 5 by 10"
    )
    assert result["status"] == "pending"
    assert result["action_id"].startswith("act_")
    assert result["summary"] == "Multiply 5 by 10"

    with psycopg.connect(audit_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tool_name, policy_decision, summary FROM nanormm_actions")
        row = cur.fetchone()
    assert row == ("always_gated", "human_approval", "Multiply 5 by 10")


@pytest.mark.asyncio
async def test_forbidden_tool_returns_denied(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_forbidden")
    async def my_forbidden() -> None:
        raise AssertionError("must not execute")

    result = await dispatcher.dispatch("always_forbidden", {})
    assert result["status"] == "denied"
    assert "forbidden" in result["reason"].lower()


@pytest.mark.asyncio
async def test_unknown_tool_raises(registry_env):
    from trmm_mcp.exceptions import PolicyError
    registry, dispatcher = registry_env

    with pytest.raises(PolicyError):
        await dispatcher.dispatch("nonexistent", {})


@pytest.mark.asyncio
async def test_resume_executes_approved_action(registry_env, fake_redis):
    registry, dispatcher = registry_env

    captured = {}

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> dict:
        captured["x"] = x
        return {"x_was": x}

    result = await dispatcher.dispatch("always_gated", {"x": 7}, summary="seven")
    aid = result["action_id"]

    # External approval (would normally come from approval-bridge)
    dispatcher._approvals.mark_approved(aid, approved_by="U_TEST")

    # Resume execution
    final = await dispatcher.resume(aid)
    assert final == {"status": "executed", "result": {"x_was": 7}}
    assert captured == {"x": 7}


@pytest.mark.asyncio
async def test_resume_rejects_unapproved_action(registry_env):
    from trmm_mcp.exceptions import ApprovalError
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    result = await dispatcher.dispatch("always_gated", {}, summary="x")
    with pytest.raises(ApprovalError):
        await dispatcher.resume(result["action_id"])  # still pending, not approved


@pytest.mark.asyncio
async def test_register_lists_all_tool_names(registry_env):
    registry, _ = registry_env

    @registry.register(name="always_auto")
    async def a():
        return None

    @registry.register(name="always_gated")
    async def b():
        return None

    assert set(registry.tool_names()) == {"always_auto", "always_gated"}
