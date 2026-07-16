from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime, timezone

import psycopg

from . import __version__, db, export, rules, slack
from . import state as state_mod
from .settings import TripwireSettings

log = logging.getLogger("tripwire")

_running = True


def _stop(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def bootstrap(cfg: TripwireSettings, conn: psycopg.Connection) -> dict:
    """First run: seed known IPs from history and start tailing from the
    current max id, so historic rows don't fire a storm of alerts."""
    st = rules.new_state()
    st["_bootstrapping"] = True
    for username, debug_info in db.historic_login_rows(conn, cfg.known_ip_bootstrap_days):
        ip = rules._find_ip(debug_info)
        if ip:
            known = st["known_ips"].setdefault(username, [])
            if ip not in known:
                known.append(ip)
    st.pop("_bootstrapping")
    st["last_id"] = db.max_audit_id(conn)
    log.info(
        "bootstrapped: last_id=%s, known ips for %d users",
        st["last_id"],
        len(st["known_ips"]),
    )
    return st


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = TripwireSettings()  # type: ignore[call-arg]

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    conn: psycopg.Connection | None = None
    st = state_mod.load_state(cfg.state_path)
    fresh = st["last_id"] == 0
    last_heartbeat = time.monotonic()
    processed = 0

    log.info("tripwire-watch %s starting (poll=%ss)", __version__, cfg.poll_seconds)

    while _running:
        try:
            if conn is None or conn.closed:
                conn = db.connect(cfg.db_dsn)
                if fresh:
                    st = bootstrap(cfg, conn)
                    state_mod.save_state(cfg.state_path, st)
                    fresh = False

            now = datetime.now(tz=timezone.utc)
            rows = db.fetch_new_rows(conn, st["last_id"])
            if rows:
                processed += len(rows)
                export.export_rows(cfg.audit_export_path, rows)
                alerts = rules.evaluate(rows, st, now, cfg)
                for alert in rules.apply_dedupe(alerts, st, now, cfg):
                    log.warning("ALERT %s: %s", alert.rule, alert.title)
                    slack.post_alert(cfg.slack_webhook, alert)
                state_mod.save_state(cfg.state_path, st)

            if cfg.heartbeat_hours > 0 and (
                time.monotonic() - last_heartbeat > cfg.heartbeat_hours * 3600
            ):
                slack.post_text(
                    cfg.slack_webhook,
                    f"🟢 tripwire-watch alive — {processed} audit rows processed "
                    f"in the last {cfg.heartbeat_hours}h.",
                )
                last_heartbeat = time.monotonic()
                processed = 0

        except psycopg.Error as exc:
            log.error("db error (will reconnect): %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:  # noqa: BLE001, S110
                pass
            conn = None
        except Exception:  # noqa: BLE001
            log.exception("unexpected error in poll loop")

        # Interruptible sleep.
        for _ in range(cfg.poll_seconds):
            if not _running:
                break
            time.sleep(1)

    log.info("tripwire-watch stopping")
    state_mod.save_state(cfg.state_path, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
