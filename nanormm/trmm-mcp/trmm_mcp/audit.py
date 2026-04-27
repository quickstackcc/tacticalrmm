import json
from contextlib import contextmanager
from typing import Any

import psycopg

from .exceptions import AuditLogError


class AuditLog:
    """
    Persistent record of every gated write action.

    `record_pending` is idempotent (ON CONFLICT DO NOTHING) so the MCP
    server can replay pending creations on restart without producing
    duplicate audit rows. The three update methods (`record_approval`,
    `record_rejection`, `record_execution`) require a pre-existing
    pending row — they raise `AuditLogError` if the row is missing,
    rather than silently no-op'ing, because audit-trail integrity
    depends on either writing or failing loudly.

    Per-call `psycopg.connect()` is intentional: audit writes are
    low-frequency (one per gated tool invocation) and not worth a
    pool's state surface at this scale.
    """

    def __init__(self, dsn: str):
        self._dsn = dsn

    def record_pending(
        self,
        *,
        action_id: str,
        tool_name: str,
        args: dict[str, Any],
        policy_decision: str,
        summary: str = "",
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO nanormm_actions (action_id, tool_name, args, summary, policy_decision)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (action_id) DO NOTHING
                """,
                (action_id, tool_name, json.dumps(args), summary, policy_decision),
            )

    def record_approval(self, *, action_id: str, approved_by: str) -> None:
        self._update_one(
            action_id,
            """
            UPDATE nanormm_actions
               SET approved_by = %s, approved_at = now()
             WHERE action_id = %s
            """,
            (approved_by, action_id),
        )

    def record_rejection(self, *, action_id: str, rejected_by: str, reason: str = "") -> None:
        self._update_one(
            action_id,
            """
            UPDATE nanormm_actions
               SET rejected_by = %s, rejected_at = now(), reject_reason = %s
             WHERE action_id = %s
            """,
            (rejected_by, reason, action_id),
        )

    def record_execution(self, *, action_id: str, result: Any) -> None:
        self._update_one(
            action_id,
            """
            UPDATE nanormm_actions
               SET executed_at = now(), result = %s::jsonb
             WHERE action_id = %s
            """,
            (json.dumps(result), action_id),
        )

    def _update_one(self, action_id: str, sql: str, params: tuple) -> None:
        with self._cursor() as cur:
            cur.execute(sql, params)
            if cur.rowcount == 0:
                raise AuditLogError(
                    f"no audit row for action_id {action_id!r}; "
                    f"record_pending must be called first"
                )

    @contextmanager
    def _cursor(self):
        try:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                yield cur
        except psycopg.Error as e:
            raise AuditLogError(f"audit log database error: {e}") from e
