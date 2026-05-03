# Plan 3 smoke — results & handoff

**Date:** 2026-04-29 (late)
**Author:** Claude (Opus 4.7) + Jim
**Supersedes:** [2026-04-29-plan3-recon-status.md](./2026-04-29-plan3-recon-status.md) (the "Slack delivery mystery" turned out to be misdiagnosis)
**Plan 4 spec:** [`docs/superpowers/specs/2026-04-30-bridge-side-card-emission.md`](../specs/2026-04-30-bridge-side-card-emission.md)

## TL;DR

Plan 3 read-side and infrastructure are **fully green**. Approve flow is **architecturally blocked** at the agent-driven card emission step. Documented in the Plan 4 spec; work resumes there.

## What works (smoke proven, not just integration-tested)

| Layer | Evidence |
|---|---|
| Slack delivery (events, signing, dispatch) | Real Slack POSTs land at chat-sdk-bridge, return 200 + chunked "ok" |
| Engagement: `engage_mode='mention'` | `chat_sdk_kv` dedupe keys + sessions created on every @recon |
| Sender access gate | Jim is in `agent_group_members`; `unregistered_senders` no longer increments |
| Container spawn | `nanoclaw-v2-recon-*` containers created per session, mounts populated |
| Vertex AI auth (no SA keys, no OneCLI) | Metadata-server token from default Compute SA, granted `roles/aiplatform.user` |
| Model selection | `ANTHROPIC_MODEL=claude-sonnet-4-6` honored end-to-end |
| Bridge gating | kill_process tool call → action stored in redis (`nanormm:pending:act_*`), audit row written to postgres `nanormm_actions` with `policy_decision='human_approval'` |
| Recon understands alerts and chooses correct tool | Three smoke retries: recon parsed agent_id/pid/process_name correctly each time, chose `kill_process` correctly each time |

## What's blocked

Agent-driven approval card emission. Recon (both Sonnet 4.6 and Haiku 4.5) refuses to emit the `nanormm_card` envelope as its final assistant message verbatim, even with extreme prompt strengthening. See the Plan 4 spec for the full diagnosis and recommended fix.

Three audit rows in postgres have `pending_at` set, `approved_at`/`executed_at` NULL — they'll expire harmlessly.

Three redis keys (`nanormm:pending:act_*`) — also TTL'd; will expire.

## Changes made tonight

### Code commits (pushed)

`quickstack-cc/nanormm-nanoclaw:main`:
- `e2f73c1` — container-runner: forward Vertex AI env vars
- `37ab659` — container-runner: pass `ANTHROPIC_MODEL` (corrected from `ANTHROPIC_DEFAULT_MODEL` — that name isn't read by Claude Code)
- `b81ba5b` — agent-runner: disallow `mcp__nanoclaw__ask_user_question` (forces agent down the envelope path)

### Local repo state (qsrmm — NOT committed)

- `docs/quickstack/migration-nanoclaw-dedicated-sa.md` — runbook for migrating off the default Compute SA
- `docs/superpowers/specs/2026-04-30-bridge-side-card-emission.md` — Plan 4 spec stub
- `docs/superpowers/handoffs/2026-04-29-plan3-smoke-results.md` — this file
- `CLAUDE.md` — present in repo root, untracked from earlier work

### VM state changes (NOT in git)

| Change | Path / Resource |
|---|---|
| `engage_mode='pattern' '@recon'` → `engage_mode='mention'` | `/opt/nanoclaw/data/v2.db` `messaging_group_agents.mga-1777499979498-zaecbl` |
| Added `slack:U0A7G93HG2V` (Jim) to recon agent group | `/opt/nanoclaw/data/v2.db` `agent_group_members` |
| `ANTHROPIC_DEFAULT_MODEL=claude-haiku-4-5` → `ANTHROPIC_MODEL=claude-sonnet-4-6` | `/opt/nanoclaw/.env` and `/opt/nanoclaw/data/env/env` |
| Sed-patched `ANTHROPIC_DEFAULT_MODEL` → `ANTHROPIC_MODEL` (twice) | `/opt/nanoclaw/dist/container-runner.js` (matches commit `37ab659`) |
| Sed-added `'mcp__nanoclaw__ask_user_question'` to `SDK_DISALLOWED_TOOLS` | `/opt/nanoclaw/container/agent-runner/src/providers/claude.ts` (matches commit `b81ba5b`) |
| Recon system prompt: 1.5KB → 6.8KB strengthened version | `/opt/nanoclaw/groups/recon/CLAUDE.local.md` (backups `.bak.*` retained) |

### GCP IAM changes

- Granted `roles/aiplatform.user` to `serviceAccount:536483581971-compute@developer.gserviceaccount.com` on project `qsrmm-494222`. (Cleanup runbook: `docs/quickstack/migration-nanoclaw-dedicated-sa.md`.)

## Things to validate next session

1. **Plan 4 implementation** per the spec. Most direct route to closing Plan 3.
2. **Merge `feat/nanormm-recon` worktree to develop locally** once Plan 4 lands. Branch is at `b5b41647` (nothing changed since the handoff date — all tonight's changes were in the fork or on the VM, not in this repo).
3. **Push develop to origin** with explicit user authorization once #1 + #2 are done.
4. **Mark Plan 3 Task 15 closed** in the plan doc once the smoke is green end-to-end via Plan 4.
5. **Optionally run the dedicated-SA migration runbook** (`docs/quickstack/migration-nanoclaw-dedicated-sa.md`) to back out the broad `roles/aiplatform.user` grant on the default Compute SA. Deferable; not blocking.

## Operational state at end of session

- Nanoclaw is **running** on Sonnet 4.6 (per Jim's stated preference)
- approval-bridge is **running** since 21:50 UTC, healthy
- TRMM is **running** as always (separate, independent of nanoclaw)
- 3 stale redis pending keys + 3 stale postgres audit rows from tonight's failed smoke retries — will expire/can be cleaned manually if desired:
  ```sql
  DELETE FROM nanormm_actions WHERE executed_at IS NULL AND rejected_at IS NULL AND pending_at < NOW() - INTERVAL '1 hour';
  ```
  (Skip; harmless. Redis TTL handles its side.)

## Reference: useful one-liners next session

```bash
# IAP SSH
gcloud compute ssh rmm --project=qsrmm-494222 --zone=us-central1-a --tunnel-through-iap

# Watch nanoclaw + bridge live
sudo journalctl -u nanoclaw -u approval-bridge -f -o cat

# Tail recent activity (already-loaded /tmp/dump_rows.py from /home/jim/.../qsrmm if needed)
sudo -u nanoclaw python3 /tmp/dump_rows.py /opt/nanoclaw/data/v2.db

# Postgres audit
sudo bash -c 'export PGPASSWORD=$(cat /home/nanormm/.pg_password); sudo -u nanormm -E psql -h localhost -U nanormm -d nanormm'

# Redis pending actions
redis-cli -n 11 KEYS "nanormm:*"
```

## Reference: file locations on VM

| What | Path |
|---|---|
| Nanoclaw orchestrator (running) | `/opt/nanoclaw/dist/index.js` (systemd unit) |
| Agent-runner source (mounted into containers) | `/opt/nanoclaw/container/agent-runner/src/` |
| Recon system prompt | `/opt/nanoclaw/groups/recon/CLAUDE.local.md` |
| Nanoclaw central DB | `/opt/nanoclaw/data/v2.db` |
| Per-session state DBs | `/opt/nanoclaw/data/v2-sessions/<agent-group>/<session>/{inbound,outbound}.db` |
| Approval-bridge venv | `/opt/nanormm/approval-bridge/.venv` |
| trmm-mcp + bridge source (rsync'd from worktree) | `/opt/nanormm/qsrmm/nanormm/{trmm-mcp,approval-bridge}/` |
| Bridge env | `/etc/nanormm/bridge.env` |
| Postgres audit DB | `nanormm` database, role `nanormm`, password at `/home/nanormm/.pg_password` |
| Redis | DB index 11 |
