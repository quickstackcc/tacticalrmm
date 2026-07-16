"""Pure rule engine: (new audit rows, mutable state, now) -> alerts.

State is a plain JSON-serializable dict so the daemon can persist it across
restarts. All timestamps in state are ISO-8601 UTC strings.

Rules implemented (docs/quickstack/admin-hardening.md, Phase 3 tripwires):
  bulk_exec          — script/command execution on > N distinct agents within window
  bulk_action        — any use of TRMM's bulk-action feature
  isolation_keyword  — exec whose message matches network-isolation keywords
  script_change      — any add/modify/delete of a script-library object
  user_added         — new TRMM user created
  role_change        — role added or modified (privilege escalation vector)
  login_new_ip       — successful login from an IP not previously seen for that user
  failed_login_burst — >= N failed logins within window
  offhours_session   — remote session opened between offhours_start and offhours_end local
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import Alert, AuditRow
from .settings import TripwireSettings

EXEC_ACTIONS = {"execute_script", "execute_command"}

ISOLATION_KEYWORDS = (
    "netsh advfirewall",
    "netsh interface",
    "disable-netadapter",
    "new-netfirewallrule",
    "set-netfirewallprofile",
    "route delete",
    "nmcli networking off",
)


def new_state() -> dict[str, Any]:
    return {
        "last_id": 0,
        "known_ips": {},  # username -> [ip, ...]
        "exec_events": [],  # [[iso_ts, agent_id], ...] trailing window
        "failed_logins": [],  # [iso_ts, ...] trailing window
        "last_alert": {},  # dedupe_key -> iso_ts
    }


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _find_ip(obj: Any) -> str | None:
    """Recursively find an 'ip' key in a debug_info-like structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "ip" and isinstance(v, str) and v:
                return v
            found = _find_ip(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_ip(item)
            if found:
                return found
    return None


def _prune(events: list, cutoff: datetime) -> list:
    return [e for e in events if _parse(e[0] if isinstance(e, list) else e) >= cutoff]


def _in_offhours(dt_utc: datetime, cfg: TripwireSettings) -> bool:
    local = dt_utc.astimezone(ZoneInfo(cfg.tz))
    if cfg.offhours_start > cfg.offhours_end:  # window crosses midnight (22 -> 6)
        return local.hour >= cfg.offhours_start or local.hour < cfg.offhours_end
    return cfg.offhours_start <= local.hour < cfg.offhours_end


def evaluate(
    rows: list[AuditRow],
    state: dict[str, Any],
    now: datetime,
    cfg: TripwireSettings,
) -> list[Alert]:
    """Evaluate new rows against tripwire rules. Mutates state; returns raw alerts
    (dedupe is applied by the caller via apply_dedupe)."""
    alerts: list[Alert] = []

    for row in rows:
        state["last_id"] = max(state["last_id"], row.id)

        # --- execution-shaped rows ---------------------------------------
        if row.action in EXEC_ACTIONS:
            state["exec_events"].append([_iso(row.entry_time), row.agent_id or "?"])
            msg = (row.message or "").lower()
            for kw in ISOLATION_KEYWORDS:
                if kw in msg:
                    alerts.append(
                        Alert(
                            rule="isolation_keyword",
                            severity="warning",
                            title=f"Possible endpoint-isolation command by {row.username}",
                            detail=f"Matched keyword '{kw}' in: {row.message}",
                            key=kw,
                        )
                    )
                    break

        if row.action == "bulk_action":
            alerts.append(
                Alert(
                    rule="bulk_action",
                    severity="critical",
                    title=f"Bulk action executed by {row.username}",
                    detail=row.message or "(no message)",
                )
            )

        # --- object changes ----------------------------------------------
        if row.object_type == "script" and row.action in {"add", "modify", "delete"}:
            alerts.append(
                Alert(
                    rule="script_change",
                    severity="critical",
                    title=f"Script library {row.action} by {row.username}",
                    detail=row.message or "(no message)",
                    key=row.message or str(row.id),
                )
            )

        if row.object_type == "user" and row.action == "add":
            alerts.append(
                Alert(
                    rule="user_added",
                    severity="critical",
                    title=f"New TRMM user created by {row.username}",
                    detail=row.message or "(no message)",
                    key=str(row.id),
                )
            )

        if row.object_type == "role" and row.action in {"add", "modify"}:
            alerts.append(
                Alert(
                    rule="role_change",
                    severity="critical",
                    title=f"Role {row.action} by {row.username}",
                    detail=row.message or "(no message)",
                    key=row.message or str(row.id),
                )
            )

        # --- logins --------------------------------------------------------
        if row.action == "login":
            ip = _find_ip(row.debug_info)
            if ip:
                known = state["known_ips"].setdefault(row.username, [])
                if ip not in known:
                    known.append(ip)
                    # Bootstrapped states pre-seed known_ips; a genuinely new
                    # IP after bootstrap is alert-worthy.
                    if not state.get("_bootstrapping"):
                        alerts.append(
                            Alert(
                                rule="login_new_ip",
                                severity="warning",
                                title=f"Login from new IP for {row.username}",
                                detail=f"IP {ip} not previously seen for this user.",
                                key=f"{row.username}:{ip}",
                            )
                        )

        if row.action == "failed_login":
            state["failed_logins"].append(_iso(row.entry_time))

        # --- remote sessions ------------------------------------------------
        if row.action == "remote_session" and _in_offhours(row.entry_time, cfg):
            alerts.append(
                Alert(
                    rule="offhours_session",
                    severity="critical",
                    title=f"Off-hours remote session by {row.username}",
                    detail=(
                        f"Agent {row.agent_id or '?'} at "
                        f"{row.entry_time.astimezone(ZoneInfo(cfg.tz)):%Y-%m-%d %H:%M %Z}. "
                        f"{row.message or ''}"
                    ),
                    key=row.agent_id or "?",
                )
            )

    # --- windowed aggregates ---------------------------------------------
    bulk_cutoff = now - timedelta(seconds=cfg.bulk_window_seconds)
    state["exec_events"] = _prune(state["exec_events"], bulk_cutoff)
    distinct_agents = {agent for _, agent in state["exec_events"]}
    if len(distinct_agents) > cfg.bulk_agent_threshold:
        alerts.append(
            Alert(
                rule="bulk_exec",
                severity="critical",
                title=f"Scripts/commands hit {len(distinct_agents)} agents in "
                f"{cfg.bulk_window_seconds}s",
                detail=f"Agents: {', '.join(sorted(distinct_agents))}",
            )
        )

    fail_cutoff = now - timedelta(seconds=cfg.failed_login_window_seconds)
    state["failed_logins"] = _prune(state["failed_logins"], fail_cutoff)
    if len(state["failed_logins"]) >= cfg.failed_login_threshold:
        alerts.append(
            Alert(
                rule="failed_login_burst",
                severity="warning",
                title=f"{len(state['failed_logins'])} failed logins in "
                f"{cfg.failed_login_window_seconds // 60} min",
                detail="Check logs_auditlog failed_login rows for usernames/IPs.",
            )
        )

    return alerts


def apply_dedupe(
    alerts: list[Alert], state: dict[str, Any], now: datetime, cfg: TripwireSettings
) -> list[Alert]:
    """Drop alerts whose dedupe_key fired within dedupe_seconds."""
    out: list[Alert] = []
    for alert in alerts:
        last = state["last_alert"].get(alert.dedupe_key)
        if last and (now - _parse(last)).total_seconds() < cfg.dedupe_seconds:
            continue
        state["last_alert"][alert.dedupe_key] = _iso(now)
        out.append(alert)
    # Keep the dedupe map from growing without bound.
    horizon = now - timedelta(seconds=cfg.dedupe_seconds * 10)
    state["last_alert"] = {
        k: v for k, v in state["last_alert"].items() if _parse(v) >= horizon
    }
    return out
