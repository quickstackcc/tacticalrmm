from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import AuditRow

log = logging.getLogger("tripwire")


def export_rows(path: str, rows: list[AuditRow]) -> None:
    """Append audit rows as JSON lines for the Ops Agent to ship off-VM.

    Best-effort: an export failure must never stall alerting, so errors are
    logged and swallowed. The file is plain JSONL; rotation is handled by
    logrotate (copytruncate) so we can keep a simple append-only handle.
    """
    if not path or not rows:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            for r in rows:
                f.write(
                    json.dumps(
                        {
                            "audit_id": r.id,
                            "time": r.entry_time.isoformat(),
                            "username": r.username,
                            "action": r.action,
                            "object_type": r.object_type,
                            "agent_id": r.agent_id,
                            "message": r.message,
                            "debug_info": r.debug_info,
                        },
                        default=str,
                    )
                    + "\n"
                )
    except OSError as exc:
        log.error("audit export failed (continuing): %s", exc)
