import json
from typing import Any

import psycopg


class AuditLog:
    """Postgres-backed write log for nanormm tool actions.

    Each action gets a single row keyed on `action_id`. The row is created in
    `record_pending` (idempotent on conflict) and updated as the action progresses
    through approval, execution, or rejection.
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
    ) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nanormm_actions (action_id, tool_name, args, policy_decision)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (action_id) DO NOTHING
                """,
                (action_id, tool_name, json.dumps(args), policy_decision),
            )
            conn.commit()

    def record_approval(self, *, action_id: str, approved_by: str) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nanormm_actions
                   SET approved_by = %s, approved_at = now()
                 WHERE action_id = %s
                """,
                (approved_by, action_id),
            )
            conn.commit()

    def record_rejection(
        self, *, action_id: str, rejected_by: str, reason: str = ""
    ) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nanormm_actions
                   SET rejected_by = %s, rejected_at = now(), reject_reason = %s
                 WHERE action_id = %s
                """,
                (rejected_by, reason, action_id),
            )
            conn.commit()

    def record_execution(self, *, action_id: str, result: Any) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nanormm_actions
                   SET executed_at = now(), result = %s::jsonb
                 WHERE action_id = %s
                """,
                (json.dumps(result), action_id),
            )
            conn.commit()
