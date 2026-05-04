import pytest

# Schemas for the synthetic test tools used throughout this file. Production
# tools live in trmm_mcp.tools._schemas.SCHEMAS; tests inject their own dict
# via the Dispatcher's `schemas=` parameter so the strict validator (which
# requires a registered schema for every dispatched tool) accepts them.
_TEST_SCHEMAS = {
    "always_auto": {
        "schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
    },
    "always_gated": {
        "schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
    },
    "always_forbidden": {
        "schema": {"type": "object", "properties": {}},
    },
}


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
    dispatcher = Dispatcher(
        registry=registry,
        policy=pol,
        approvals=approvals,
        audit=audit,
        schemas=_TEST_SCHEMAS,
    )
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

    result = await dispatcher.dispatch("always_gated", {"x": 5}, summary="Multiply 5 by 10")
    assert result["status"] == "pending"
    assert result["action_id"].startswith("act_")
    assert result["summary"] == "Multiply 5 by 10"
    assert "nanormm_card" not in result

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


@pytest.mark.asyncio
async def test_recover_executes_approved_actions_on_startup(registry_env, fake_redis):
    registry, dispatcher = registry_env

    captured = []

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> dict:
        captured.append(x)
        return {"got": x}

    # Simulate: action created, approved, but server crashed before executing
    a1 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 1}, summary="")
    a2 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 2}, summary="")
    dispatcher._approvals.mark_approved(a1, approved_by="U_X")
    dispatcher._approvals.mark_approved(a2, approved_by="U_X")

    recovered = await dispatcher.recover()
    assert set(recovered) == {a1, a2}
    assert sorted(captured) == [1, 2]


@pytest.mark.asyncio
async def test_recover_skips_pending_and_rejected(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x

    a1 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 1}, summary="")
    _a2 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 2}, summary="")
    a3 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 3}, summary="")
    dispatcher._approvals.mark_approved(a1, approved_by="U_X")
    # _a2 stays pending
    dispatcher._approvals.mark_rejected(a3, rejected_by="U_X", reason="no")

    recovered = await dispatcher.recover()
    assert recovered == [a1]


class _StubInjectClient:
    """Records calls; raises if instructed."""

    def __init__(self, raise_exc: Exception | None = None):
        self.calls: list[dict] = []
        self.raise_exc = raise_exc

    async def inject_card(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc


@pytest.fixture
def gated_env_with_inject(fake_redis, audit_dsn, tmp_path):
    """Same as registry_env but Dispatcher has an InjectClient stub wired."""
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
  always_gated: human_approval
"""
    )
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    inject = _StubInjectClient()
    dispatcher = Dispatcher(
        registry=registry,
        policy=pol,
        approvals=approvals,
        audit=audit,
        inject_client=inject,
        schemas=_TEST_SCHEMAS,
    )
    return registry, dispatcher, inject


@pytest.mark.asyncio
async def test_gated_tool_with_inject_calls_inject(gated_env_with_inject):
    registry, dispatcher, inject = gated_env_with_inject

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x * 10

    result = await dispatcher.dispatch(
        "always_gated", {"x": 5}, summary="Multiply 5 by 10", session_id="sess-99"
    )
    assert result["status"] == "pending"
    assert result["action_id"].startswith("act_")
    assert result["summary"] == "Multiply 5 by 10"
    assert "nanormm_card" not in result

    assert len(inject.calls) == 1
    call = inject.calls[0]
    assert call["session_id"] == "sess-99"
    assert call["question_id"] == f"nrmact-{result['action_id']}"
    assert call["title"] == "Pending action"
    assert call["question"] == "Multiply 5 by 10"
    assert call["options"] == [
        {"label": "Approve", "selectedLabel": "✅ Approved", "value": "approve"},
        {"label": "Reject", "selectedLabel": "❌ Rejected", "value": "reject"},
    ]


@pytest.mark.asyncio
async def test_gated_tool_with_inject_missing_session_id_raises(gated_env_with_inject):
    from trmm_mcp.exceptions import PolicyError

    registry, dispatcher, _inject = gated_env_with_inject

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    with pytest.raises(PolicyError, match="session_id"):
        await dispatcher.dispatch("always_gated", {}, summary="x", session_id=None)


@pytest.mark.asyncio
async def test_gated_tool_inject_failure_raises(gated_env_with_inject):
    import httpx

    registry, dispatcher, inject = gated_env_with_inject
    inject.raise_exc = httpx.ConnectError("nanoclaw down")

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    with pytest.raises(httpx.ConnectError):
        await dispatcher.dispatch(
            "always_gated", {}, summary="x", session_id="sess-99"
        )


@pytest.mark.asyncio
async def test_gated_tool_no_inject_client_works(registry_env):
    """When inject_client=None (stdio mode), HUMAN_APPROVAL still works
    and returns plain pending response without nanormm_card."""
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    result = await dispatcher.dispatch("always_gated", {}, summary="x")
    assert result["status"] == "pending"
    assert "nanormm_card" not in result


@pytest.mark.asyncio
async def test_dispatch_rejects_wrong_arg_type(registry_env):
    """Schema validation runs before policy lookup; mismatched type is rejected."""
    from trmm_mcp.exceptions import SchemaValidationError

    registry, dispatcher = registry_env

    @registry.register(name="always_auto")
    async def my_tool(x: int) -> int:  # noqa: ARG001
        raise AssertionError("must not execute when validation fails")

    with pytest.raises(SchemaValidationError, match="always_auto"):
        await dispatcher.dispatch("always_auto", {"x": "not-an-integer"})


@pytest.mark.asyncio
async def test_dispatch_rejects_unknown_field(registry_env):
    """Strict mode: extra fields are rejected (additionalProperties: false)."""
    from trmm_mcp.exceptions import SchemaValidationError

    registry, dispatcher = registry_env

    @registry.register(name="always_auto")
    async def my_tool(x: int) -> int:  # noqa: ARG001
        raise AssertionError("must not execute when validation fails")

    with pytest.raises(SchemaValidationError):
        await dispatcher.dispatch("always_auto", {"x": 1, "smuggled_field": "evil"})


@pytest.mark.asyncio
async def test_dispatch_rejects_tool_with_no_schema(fake_redis, audit_dsn, tmp_path):
    """If a tool is registered but has no schema entry, dispatch refuses it."""
    from trmm_mcp.approvals import ApprovalRegistry
    from trmm_mcp.audit import AuditLog
    from trmm_mcp.exceptions import SchemaValidationError
    from trmm_mcp.policy import Policy
    from trmm_mcp.tools._base import Dispatcher, ToolRegistry

    pol_path = tmp_path / "p.yaml"
    pol_path.write_text("version: 1\ndefault: auto\ntools: {}\n")
    pol = Policy.load(pol_path)
    registry = ToolRegistry()

    @registry.register(name="rogue_tool")
    async def rogue() -> int:
        raise AssertionError("must not execute")

    dispatcher = Dispatcher(
        registry=registry,
        policy=pol,
        approvals=ApprovalRegistry(fake_redis, ttl_seconds=60),
        audit=AuditLog(audit_dsn),
        schemas={},  # explicitly empty: no tool has a schema
    )

    with pytest.raises(SchemaValidationError, match="no schema"):
        await dispatcher.dispatch("rogue_tool", {})


@pytest.mark.asyncio
async def test_dispatch_validation_runs_before_audit_write(registry_env, audit_dsn):
    """A validation failure must NOT create an audit row or pending approval."""
    import psycopg

    from trmm_mcp.exceptions import SchemaValidationError

    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:  # noqa: ARG001
        raise AssertionError("must not execute")

    with pytest.raises(SchemaValidationError):
        await dispatcher.dispatch("always_gated", {"x": "bad"}, summary="x")

    with psycopg.connect(audit_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM nanormm_actions")
        (count,) = cur.fetchone()
    assert count == 0

    # And no pending row landed in Redis either.
    assert list(dispatcher._approvals.iter_all()) == []
