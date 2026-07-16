from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AuditRow:
    """One row of TRMM's logs_auditlog, as consumed by the rule engine."""

    id: int
    entry_time: datetime  # timezone-aware UTC
    username: str
    action: str
    object_type: str
    agent_id: str | None = None
    message: str | None = None
    debug_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class Alert:
    rule: str
    severity: str  # "critical" | "warning"
    title: str
    detail: str
    # Dedupe key: alerts with the same key are suppressed within dedupe_seconds.
    key: str = field(default="")

    @property
    def dedupe_key(self) -> str:
        return f"{self.rule}:{self.key}" if self.key else self.rule
