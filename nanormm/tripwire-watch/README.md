# tripwire-watch

Standalone security-tripwire daemon for QSRMM. Tails TRMM's `logs_auditlog`
table (read-only) and posts alerts to a Slack incoming webhook when any of the
Phase 3 tripwire conditions from `docs/quickstack/admin-hardening.md` fire:

| Rule | Severity | Condition |
|---|---|---|
| `bulk_exec` | critical | scripts/commands hit > 3 distinct agents within 60s |
| `bulk_action` | critical | any TRMM bulk-action use |
| `isolation_keyword` | warning | exec message matches network-isolation keywords |
| `script_change` | critical | any script-library add/modify/delete |
| `user_added` | critical | new TRMM user created |
| `role_change` | critical | role added/modified |
| `login_new_ip` | warning | successful login from an IP never seen for that user |
| `failed_login_burst` | warning | ≥ 3 failed logins in 10 min |
| `offhours_session` | critical | remote session between 10pm–6am America/Chicago |

Design notes:

- **Standalone by intent** — no dependency on trmm-mcp/approval-bridge, no
  TRMM code modification (survives TRMM upgrades untouched), delivery via a
  plain Slack incoming webhook rather than nanoclaw.
- First run bootstraps `known_ips` from 90 days of login history and starts
  tailing from the current max audit id (no alert storm on deploy).
- State (`last_id`, IP sets, rolling windows, dedupe map) persists to
  `/var/lib/nanormm/tripwire-state.json` with atomic writes.
- Repeated identical alerts are suppressed for 10 minutes.
- Daily heartbeat message so a dead tripwire is itself detectable.

## Coverage gaps (accepted)

- Sessions opened directly at mesh.quickstack.cc (not via TRMM) don't create
  `remote_session` audit rows; mitigated by Mesh `force2factor` + YubiKeys.
- Non-US logins never reach the audit log (nginx geo-block 403s them first).
- Agent "isolation" has no native TRMM action; keyword matching on exec
  messages is best-effort.

## Deploy

```
rsync to /opt/nanormm/tripwire-watch
python3.11 -m venv .venv && .venv/bin/pip install -e .
# /etc/nanormm/tripwire.env: TRIPWIRE_DB_DSN, TRIPWIRE_SLACK_WEBHOOK
sudo cp systemd/tripwire-watch.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tripwire-watch
```

DB role (read-only, single table):

```sql
CREATE ROLE tripwire_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE tacticalrmm TO tripwire_ro;
GRANT USAGE ON SCHEMA public TO tripwire_ro;
GRANT SELECT ON logs_auditlog TO tripwire_ro;
```
