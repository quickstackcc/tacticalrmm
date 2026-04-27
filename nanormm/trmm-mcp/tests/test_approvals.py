import pytest


def test_create_returns_action_id_and_persists(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    action_id = reg.create(
        tool_name="kill_process",
        args={"agent_id": "a1", "pid": 9999},
        summary="Kill PID 9999 on a1",
    )
    assert action_id.startswith("act_")
    pending = reg.get(action_id)
    assert pending is not None
    assert pending["tool_name"] == "kill_process"
    assert pending["args"] == {"agent_id": "a1", "pid": 9999}
    assert pending["status"] == "pending"
    assert pending["summary"] == "Kill PID 9999 on a1"


def test_get_returns_none_for_unknown_id(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    assert reg.get("act_nope") is None


def test_mark_approved_changes_status_and_records_approver(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_approved(aid, approved_by="U_SLACK_123")
    p = reg.get(aid)
    assert p["status"] == "approved"
    assert p["approved_by"] == "U_SLACK_123"


def test_mark_rejected_changes_status(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_rejected(aid, rejected_by="U_SLACK_123", reason="not now")
    p = reg.get(aid)
    assert p["status"] == "rejected"
    assert p["rejected_by"] == "U_SLACK_123"
    assert p["reject_reason"] == "not now"


def test_mark_executed_records_result(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_approved(aid, approved_by="U_X")
    reg.mark_executed(aid, result={"killed": True})
    p = reg.get(aid)
    assert p["status"] == "executed"
    assert p["result"] == {"killed": True}


def test_ttl_set_on_create(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=60)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    ttl = fake_redis.ttl(f"nanormm:pending:{aid}")
    assert 0 < ttl <= 60


def test_mark_approved_unknown_action_raises(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry
    from trmm_mcp.exceptions import ApprovalError

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    with pytest.raises(ApprovalError):
        reg.mark_approved("act_nope", approved_by="x")


def test_mark_approved_already_executed_is_idempotent(fake_redis):
    """Re-approving an executed action should not change state — guards against replay."""
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_approved(aid, approved_by="U_A")
    reg.mark_executed(aid, result={"ok": True})
    # Second approval attempt should be a no-op
    reg.mark_approved(aid, approved_by="U_B")
    p = reg.get(aid)
    assert p["status"] == "executed"  # unchanged
    assert p["approved_by"] == "U_A"  # unchanged


def test_action_id_is_unique(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    ids = {reg.create(tool_name="x", args={}, summary="y") for _ in range(50)}
    assert len(ids) == 50
