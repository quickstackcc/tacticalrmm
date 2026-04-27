import psycopg
import pytest


def _row(dsn: str, action_id: str) -> dict:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT action_id, tool_name, args, policy_decision, approved_by, "
            "approved_at, rejected_by, executed_at, result FROM nanormm_actions "
            "WHERE action_id = %s",
            (action_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = [
        "action_id",
        "tool_name",
        "args",
        "policy_decision",
        "approved_by",
        "approved_at",
        "rejected_by",
        "executed_at",
        "result",
    ]
    return dict(zip(cols, row, strict=False))


def test_record_pending_inserts_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(
        action_id="act_x",
        tool_name="kill_process",
        args={"agent_id": "a1", "pid": 9999},
        policy_decision="human_approval",
    )
    row = _row(audit_dsn, "act_x")
    assert row is not None
    assert row["tool_name"] == "kill_process"
    assert row["args"] == {"agent_id": "a1", "pid": 9999}
    assert row["policy_decision"] == "human_approval"


def test_record_pending_is_idempotent(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(action_id="act_y", tool_name="x", args={}, policy_decision="auto")
    # Second call must not raise (used during MCP server replay on restart)
    log.record_pending(action_id="act_y", tool_name="x", args={}, policy_decision="auto")
    row = _row(audit_dsn, "act_y")
    assert row is not None


def test_record_approval_updates_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(
        action_id="act_z", tool_name="x", args={}, policy_decision="human_approval"
    )
    log.record_approval(action_id="act_z", approved_by="U_SLACK_42")
    row = _row(audit_dsn, "act_z")
    assert row["approved_by"] == "U_SLACK_42"
    assert row["approved_at"] is not None


def test_record_execution_updates_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(action_id="act_e", tool_name="x", args={}, policy_decision="auto")
    log.record_execution(action_id="act_e", result={"ok": True})
    row = _row(audit_dsn, "act_e")
    assert row["executed_at"] is not None
    assert row["result"] == {"ok": True}


def test_record_rejection_updates_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(
        action_id="act_r", tool_name="x", args={}, policy_decision="human_approval"
    )
    log.record_rejection(action_id="act_r", rejected_by="U_X", reason="nope")
    row = _row(audit_dsn, "act_r")
    assert row["rejected_by"] == "U_X"


def test_record_approval_on_missing_action_raises(audit_dsn: str):
    """Audit-trail integrity: UPDATE-on-nothing must raise, not silently succeed."""
    from trmm_mcp.audit import AuditLog
    from trmm_mcp.exceptions import AuditLogError

    log = AuditLog(audit_dsn)
    with pytest.raises(AuditLogError, match="no audit row"):
        log.record_approval(action_id="act_does_not_exist", approved_by="U_X")


def test_record_execution_on_missing_action_raises(audit_dsn: str):
    from trmm_mcp.audit import AuditLog
    from trmm_mcp.exceptions import AuditLogError

    log = AuditLog(audit_dsn)
    with pytest.raises(AuditLogError, match="no audit row"):
        log.record_execution(action_id="act_does_not_exist", result={"x": 1})


def test_record_rejection_on_missing_action_raises(audit_dsn: str):
    from trmm_mcp.audit import AuditLog
    from trmm_mcp.exceptions import AuditLogError

    log = AuditLog(audit_dsn)
    with pytest.raises(AuditLogError, match="no audit row"):
        log.record_rejection(action_id="act_does_not_exist", rejected_by="U_X", reason="x")
