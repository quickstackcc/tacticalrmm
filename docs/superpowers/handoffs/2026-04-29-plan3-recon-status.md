# Plan 3 (recon end-to-end) — handoff status

**Date:** 2026-04-29
**Author:** Claude (Opus 4.7) + Jim
**Plan:** [`docs/superpowers/plans/2026-04-29-nanormm-recon.md`](../plans/2026-04-29-nanormm-recon.md) (with v2 pivot note)
**Spec:** [`docs/superpowers/specs/2026-04-29-nanormm-recon-design.md`](../specs/2026-04-29-nanormm-recon-design.md)

## TL;DR

All 14 of 15 plan tasks are operationally complete. **Smoke (Task 15) is blocked on Slack event-delivery silently failing despite a verified URL, subscribed events, and reinstalled bot token.** Slack POSTs reach nginx (HTTP 200), but nanoclaw shows zero inbound activity in the journal. Need a fresh debugging session to instrument the chat-sdk-bridge webhook handler.

## What works

| Component | Verified |
|---|---|
| `approval-bridge` systemd service on VM | `/healthz` → 200, `/mcp/` initialize → 200 with serverInfo `trmm-mcp` |
| Real TRMM Knox token in `bridge.env` | Bridge restarted clean, no auth errors |
| `nanoclaw.service` (system-level systemd unit, custom — bypasses nanoclaw.sh's `nohup` fallback) | Active, `Slack auth completed { botUserId: U0B1GGV409E }`, webhook on :3000 |
| `nginx /nanormm/webhook/` location block (using `^~` prefix) | `X-Nanormm-Route: matched`, proxies to `127.0.0.1:3000` |
| Postgres `nanormm` DB + audit schema applied | `nanormm_actions` table created |
| `#rmm-alerts` channel registered as recon's main channel (`platformId=slack:C0AV4N0K60N`) | nanoclaw setup register output success |
| `groups/recon/CLAUDE.local.md` system prompt | Written, covers v2 `nanormm_card` envelope emission |
| Slack app `recon` reinstalled with all `message.*` event subscriptions | Bot in `#rmm-alerts`, URL verified green |

## What does NOT work (the open issue)

**Slack message events are not arriving at nanoclaw's webhook handler.**

Evidence:
- nginx access log shows Slack POSTs to `/nanormm/webhook/slack` returning HTTP 200 (12-byte body): timestamps 22:22:48, 22:22:52, 22:23:02, 22:25:24, 22:25:29, etc.
- nanoclaw journalctl shows ZERO inbound activity since service start. No "received message", no "delivery", nothing.
- `LOG_LEVEL=debug` in nanoclaw `.env` had no observable effect — INFO is still the floor. Different env var name probably needed.
- chat-sdk-bridge's `setup` runs cleanly on startup, registers webhook adapter, completes Slack auth.

**Hypothesis ranking (untested):**

1. **Most likely:** chat-sdk-bridge silently rejects events because of payload validation (signing secret mismatch?) — but the early curl test that hit it WITHOUT a signature got 401, not 200, so signature validation IS active. Need to verify signing secret was copied correctly.
2. The 200 12-byte response is a generic ACK from chat-sdk-bridge, not actual event handling. Maybe there's a layer of "received but dropped" we're not seeing.
3. The reinstalled Slack app's events don't actually translate to delivery (some Slack-side state lag, OR re-verification needed after reinstall).
4. The chat-sdk-bridge expects a different webhook path than `/webhook/slack`. nginx rewrites `/nanormm/webhook/slack` → `/webhook/slack` — verify against `webhook-server.ts` route registration.

## State on the qsrmm VM

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/etc/nanormm/bridge.env` | nanormm:nanormm | 600 | Bridge config (real Knox token, real Postgres password, real bridge API key) |
| `/etc/systemd/system/approval-bridge.service` | root | 644 | Bridge systemd unit (with `ReadWritePaths=/var/log/nanormm /opt/nanormm`) |
| `/etc/systemd/system/nanoclaw.service` | root | 644 | Nanoclaw systemd unit (custom, replaces nanoclaw.sh's nohup wrapper) |
| `/etc/nginx/sites-enabled/rmm.conf` | root | 644 | Has `location ^~ /nanormm/webhook/` block (lines ~78). Backup at `/root/rmm.conf.pre-nanormm.bak` |
| `/opt/nanormm/qsrmm/nanormm/{trmm-mcp,approval-bridge,policy.yaml,deploy}/` | nanormm:nanormm | — | Code rsync'd from worktree (NOT cloned from origin) |
| `/opt/nanormm/approval-bridge` → symlink | nanormm | — | → `qsrmm/nanormm/approval-bridge` |
| `/opt/nanormm/policy.yaml` → symlink | nanormm | — | → `qsrmm/nanormm/policy.yaml` |
| `/opt/nanoclaw/` | nanoclaw:nanoclaw | — | Cloned from `quickstack-cc/nanormm-nanoclaw` (HEAD = commit at end of session) |
| `/opt/nanoclaw/.env` | nanoclaw:nanoclaw | 600 | Real Slack creds + bridge wiring + Vertex config + `LOG_LEVEL=debug` (untested if respected) |
| `/opt/nanoclaw/data/env/env` | nanoclaw:nanoclaw | 600 | Sync of `.env` for spawned containers |
| `/opt/nanoclaw/groups/recon/CLAUDE.local.md` | nanoclaw:nanoclaw | 644 | Recon system prompt |
| `/home/nanormm/.bridge_api_key` | nanormm:nanormm | 600 | The shared bearer secret (also in both env files) |
| `/home/nanormm/.pg_password` | nanormm:nanormm | 600 | Postgres role password (also in bridge.env) |
| Postgres `nanormm` DB, role `nanormm` | postgres | — | `nanormm_actions` table schema applied |
| Redis DB 11 | redis-server | — | Confirmed reachable; bridge uses for pending actions |

## State in the repos

### `quickstack-cc/qsrmm` — branch `develop`

Plan 3 doc (commits on develop):
- `34653f4b` initial Plan 3
- `f4f140ae` v2 pivot note (Tasks 8-14 redirected)
- `bc3100b9` Task 14 prompt aligned with `ask_question` shape
- `542e6d7e` Webhook-mode correction (Slack adapter is webhook-only)

Plan 3 code on branch `feat/nanormm-recon` (worktree at `.worktrees/nanormm-recon`):
- `a6bfa6c3` build_server regression test
- `c5bd93c9` `/mcp` endpoint
- `b64b67ad` Dockerfile delete + policy audit
- `1f67aeb6` README endpoint update
- `e99e03ec` deploy artifacts
- `b5b41647` ReadWritePaths fix
- `b4cda520` v2 Card-shaped envelope (`nanormm_card`)

`feat/nanormm-recon` is **not merged to develop yet** and **not pushed to origin** (user authorized local merge for Plan 2; no explicit push authorization given for Plan 3 yet).

### `quickstack-cc/nanormm-nanoclaw` — branch `main`

Pushed to origin:
- `57c63ba6` v2 /add-slack adapter copy
- `5f88365` nanormm-bridge response handler module
- `19b0fdb` agent-runner trmm MCP HTTP server registration

**Pending — NOT pushed:** Vertex env passthrough patch in `src/container-runner.ts`. Made directly on the VM via awk to unblock smoke. Needs to land in the fork as a clean commit:

```typescript
// after the existing TRMM_MCP_URL passthrough block:
if (process.env.CLAUDE_CODE_USE_VERTEX === '1') {
    args.push('-e', 'CLAUDE_CODE_USE_VERTEX=1');
    if (process.env.ANTHROPIC_VERTEX_PROJECT_ID) args.push('-e', 'ANTHROPIC_VERTEX_PROJECT_ID=' + process.env.ANTHROPIC_VERTEX_PROJECT_ID);
    if (process.env.CLOUD_ML_REGION) args.push('-e', 'CLOUD_ML_REGION=' + process.env.CLOUD_ML_REGION);
    if (process.env.ANTHROPIC_DEFAULT_MODEL) args.push('-e', 'ANTHROPIC_DEFAULT_MODEL=' + process.env.ANTHROPIC_DEFAULT_MODEL);
}
```

The patched `dist/container-runner.js` is on the VM at `/opt/nanoclaw/dist/`. The fork's main branch HEAD doesn't have this. **First action of next session: SCP the patched `src/container-runner.ts` from the VM to the local nanormm-nanoclaw clone, commit, push.**

## Slack app state

- Name: `recon`
- Workspace: T0A73B58CLA (Quick Stack)
- Bot user ID: `U0B1GGV409E`
- Bot ID: `B0B0JS71RFF`
- Channel: `#rmm-alerts` = `C0AV4N0K60N`
- Mode: webhook (Socket Mode OFF)
- Request URL (Events + Interactivity): `https://api.quickstack.cc/nanormm/webhook/slack` — verified green
- Bot events subscribed: `message.channels`, `message.groups`, `message.im`
- App reinstalled to workspace after events were added — bot token unchanged on reinstall (same `xoxb-`)

## GCP state

- Project: `qsrmm-494222`
- Region: `us-east5` (validated for Sonnet/Haiku, prospect-pro pattern)
- VM service account has `roles/aiplatform.user`
- Vertex `claude-haiku-4-5:rawPredict` confirmed working from VM (Task 5)

## Audit log

- Audit table `nanormm_actions` exists, rows = 0 (no actions have round-tripped yet because no agent has run yet)

## Next session — recommended ordered steps

### Stage 0 — fork hygiene (5 min)

1. SCP the VM's `/opt/nanoclaw/src/container-runner.ts` to `~/quickstack-cc/nanormm-nanoclaw/src/container-runner.ts` and diff against origin/main. Confirm only the Vertex env passthrough is added.
2. Commit + push to `quickstack-cc/nanormm-nanoclaw:main`. Commit message: `container-runner: forward Vertex AI env vars to spawned agent containers when CLAUDE_CODE_USE_VERTEX=1`.

### Stage 1 — debug Slack delivery (30-60 min)

The mystery is "Slack POSTs reach nginx with 200 but nanoclaw doesn't process them." Sequenced diagnostic:

1. **Capture an actual response body** to see what 12 bytes nanoclaw is returning:
   ```bash
   sudo tcpdump -A -i lo -s 0 'tcp port 3000 and tcp[((tcp[12:1] & 0xf0) >> 2):4] = 0x48545450' -c 50
   ```
   Then have user post a message. We'll see the actual response payload nanoclaw sends.

2. **Find the right LOG_LEVEL env var name** in nanoclaw v2:
   ```bash
   grep -rn "LOG_LEVEL\|process\.env\.LOG\|logLevel" /opt/nanoclaw/src/ | head
   ```

3. **Read `webhook-server.ts`** to confirm route mounting and any silent rejection paths:
   - File: `/opt/nanoclaw/src/webhook-server.ts`
   - Look for: how `chat.webhooks[adapterName]` is invoked, any try/catch that swallows errors

4. **Check `@chat-adapter/slack` package** for its webhook handler:
   - Path: `/opt/nanoclaw/node_modules/.pnpm/@chat-adapter+slack@4.26.0/node_modules/@chat-adapter/slack/dist/index.js`
   - Search for the path-matching + signature-validation flow. Most adapters return 200 silently for events they don't match — could be route mismatch.

5. **Test with a real Slack event payload + signature**: write a small script that crafts a valid `event_callback` POST with a correctly-signed signature using the live signing secret, send it, see if nanoclaw fires the inbound handler. This rules in/out the signing secret.

### Stage 2 — once recon replies (the smoke proper)

Once Slack events flow:

1. Watch the next agent container spawn. Confirm the v2 changelog promise that `CLAUDE_CODE_USE_VERTEX=1` actually wires Vertex through Claude Agent SDK. If the container still says "Not logged in", we need to also confirm Vertex AI ADC works inside the container — likely a `--volume /home/nanoclaw/.config/gcloud:/root/.config/gcloud:ro` mount or reliance on metadata server.
2. If recon replies cleanly to `hi recon`, proceed to Task 15 synthetic alert.
3. If recon emits a `nanormm_card` and tech clicks Approve, watch:
   - nanoclaw journal for the response handler running
   - bridge journal for `/api/nanoclaw/actions/execute/` POST
   - audit log row appearing with `executed_at`

### Stage 3 — finish

After smoke green:
1. Merge `feat/nanormm-recon` worktree to `develop` locally.
2. Push develop to origin (with explicit user auth).
3. Update task status, mark Plan 3 done, archive.

## Operational notes for the next session

- Use IAP for VM access: `gcloud compute ssh rmm --project=qsrmm-494222 --zone=us-central1-a --tunnel-through-iap`
- The user `jim_quickstack_cc` has passwordless sudo on the VM.
- TRMM lives at `/rmm/` (bare-metal, NOT containerized).
- nanoclaw runs as `nanoclaw` Linux user, member of `docker` group.
- Bridge runs as `nanormm` Linux user.
- Both services read their env from `/etc/nanormm/bridge.env` and `/opt/nanoclaw/.env` respectively. Restart with `sudo systemctl restart approval-bridge` / `sudo systemctl restart nanoclaw`.

## Known minor TODOs (not blocking smoke)

- `dist/container-runner.js` was rebuilt on the VM with the Vertex patch but has the same content as a corresponding source-level patch — should be re-derived from the canonical source after the fork commit lands.
- The `~~/groups/main/CLAUDE.local.md` exists from initial install with default Andy assistant content — fine, recon group has its own.
- `/opt/nanormm/qsrmm/` contents are rsync'd, not git-tracked. Future upgrades should clone from origin once the feat branch is merged + pushed. The `install-bridge.sh` script handles this automatically once the upstream develop has the Plan 3 code.
