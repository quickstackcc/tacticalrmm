from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import tuple_row

from .models import AuditRow

FETCH_SQL = """
SELECT id, entry_time, username, action, object_type, agent_id, message, debug_info
FROM logs_auditlog
WHERE id > %s
ORDER BY id
LIMIT 500
"""

BOOTSTRAP_SQL = """
SELECT username, debug_info
FROM logs_auditlog
WHERE action = 'login' AND entry_time >= %s
"""


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, row_factory=tuple_row)


def fetch_new_rows(conn: psycopg.Connection, last_id: int) -> list[AuditRow]:
    with conn.cursor() as cur:
        cur.execute(FETCH_SQL, (last_id,))
        return [
            AuditRow(
                id=r[0],
                entry_time=r[1] if r[1].tzinfo else r[1].replace(tzinfo=timezone.utc),
                username=r[2],
                action=r[3],
                object_type=r[4],
                agent_id=r[5],
                message=r[6],
                debug_info=r[7],
            )
            for r in cur.fetchall()
        ]


def max_audit_id(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM logs_auditlog")
        return int(cur.fetchone()[0])


def historic_login_rows(conn: psycopg.Connection, days: int) -> list[tuple[str, dict | None]]:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP_SQL, (since,))
        return [(r[0], r[1]) for r in cur.fetchall()]
