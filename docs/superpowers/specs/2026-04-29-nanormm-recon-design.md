# nanormm `recon` end-to-end design

**Date:** 2026-04-29
**Status:** Design approved, pre-implementation
**Owner:** Jim Hill (jim@quickstack.cc)
**Supersedes (in part):** [`2026-04-27-nanormm-design.md`](2026-04-27-nanormm-design.md) — corrects deployment shape, region, and container-vs-native decisions

## Why

Plan 1 shipped `trmm-mcp` (the Python tool surface). Plan 2 shipped `approval-bridge` (the Slack approval round-trip). This plan ships **`recon` end-to-end** — TRMM alerts get triaged in `#rmm-alerts` by an autonomous agent that drafts a recommended remediation and surfaces approval buttons for any write action. Closing this loop is the MSSP wedge: faster triage, draft-quality remediation on every alert, with a graduation path to autonomous tier-1 action.

Operator (`@operator` — the tech-driven copilot) is deliberately deferred to Plan 4. Recon proves the architecture; operator is then largely a config diff.

## Scope

**In scope:**
- Approval-bridge gains an MCP HTTP endpoint at `/mcp`, serving the same tool surface trmm-mcp registered. Bridge becomes the single MCP server for nanormm.
- Approval-bridge runs as a native systemd service on the qsrmm VM (not a container — TRMM is bare-metal, the bridge follows suit).
- Nanoclaw runs natively on the qsrmm VM via the official `bash nanoclaw.sh` installer, under a dedicated `nanoclaw` Linux user. Spawns ephemeral agent containers via the host Docker daemon.
- Recon agent identity (`groups/recon/CLAUDE.md`) wired to `#rmm-alerts` as a main channel via the standard `/add-slack` skill.
- Action-button mechanism patched into our nanoclaw fork by lifting the prospect-pro fork's action-handler block (renamed env var, attribution comment).
- GCP service-account permissions to enable Vertex AI from agent containers via Workload Identity / ADC.
- New Slack app for `@recon`, separate from the existing TRMM-webhook Slack app. **Webhook mode** (signing-secret-validated) per the v2 `@chat-adapter/slack@4.26.0` adapter, which is webhook-only at every published version. Slack reaches nanoclaw's built-in webhook server (`src/webhook-server.ts`, default port 3000) via TRMM's existing nginx with a new `location /nanormm/webhook/` proxy block.
- Synthetic-alert smoke test against a sandbox TRMM client.

**Out of scope (deferred, not forgotten):**
- `@operator` bot — Plan 4. Same MCP surface, different system prompt and model tier (`claude-sonnet-4-6` vs recon's `claude-haiku-4-5`).
- Wiring nanoclaw's `nanoclaw_cancel` button to call `/api/nanoclaw/actions/reject/`. Currently client-side-only — defer to Plan 4 or later.
- Splitting nanoclaw to a separate VM for stronger isolation. Plan 5+ once operational data shows it matters.
- Customer-facing tier-1 bot ("B persona"). Internal-only at launch.
- Multi-tenancy at the channel level. Single `#rmm-alerts` channel for v1.
- Confidence-based gating. Binary `auto` / `human_approval` only at launch.
- Threat-intel feeds, SIEM ingest, EDR integrations, MeshCentral direct.
- Upstreaming the action-handler env-var rename to `qwibitai/nanoclaw`.
- Open-sourcing any of this.

## Corrections to the original design spec

The original `2026-04-27-nanormm-design.md` made a few assumptions that turned out to be wrong on closer inspection. Plan 3 corrects them:

| Original | Corrected | Why |
|---|---|---|
| Vertex region `us-central1` | **`us-east5`** | Sonnet 4.6 / Haiku 4.5 are confirmed available in us-east5 from prospect-pro's working install; us-central1 is unreliable for Anthropic models. |
| TRMM in Docker | **TRMM is bare-metal** | qsrmm VM was installed via TRMM's official `install.sh`, which provisions Postgres/Redis/MongoDB/Django/Celery/nginx as systemd services on the host. The `docker/` tree in the upstream repo is community-supported, not the official path. |
| Approval-bridge as Docker container | **Native systemd service** | Consistent with TRMM's bare-metal shape. Bridge connects to localhost Postgres + localhost Redis. The Plan 2 Dockerfile is dropped from the prod path (and physically deleted in Plan 3). |
| Nanoclaw as Docker container (DooD) | **Native via `nanoclaw.sh`** | The official-supported Linux install is native. DooD adds layers without changing the security profile (both have host docker-socket access). Native is simpler to operate and debug. |
| Original: Slack Socket Mode (no public ingress) | **Webhook mode via nginx** | The v2 `@chat-adapter/slack@4.26.0` adapter is webhook-only at every published version (`socket_mode_enabled: false`). Plan 3 reverts to nginx-proxied webhook ingress, with TRMM's existing nginx adding a `/nanormm/webhook/` location block proxying to `127.0.0.1:3000` (nanoclaw's built-in webhook server). Slack signing-secret validation happens inside `@chat-adapter/slack`. Slack action button clicks flow back via the same webhook path; nanoclaw's `chat-sdk-bridge` dispatches to `ChannelSetup.onAction`, then to our registered ResponseHandler. |
| `trmm-mcp` as a separate container | **MCP folded into approval-bridge** | The bridge already imports the trmm-mcp Python package and owns the dispatcher. Adding an MCP HTTP endpoint to the same process eliminates a third container without compromising the MCP module boundary. |

## Architecture

```
GCE VM (qsrmm-494222, GCP region us-east5)
├── TRMM bare-metal stack (existing, unchanged)
│   systemd: postgres, redis, mongodb, daphne, celery, celery-beat, nginx, mesh, nats
│
├── approval-bridge (NEW, native systemd)
│   /opt/nanormm/approval-bridge/                    # checked-out python package
│     ├── .venv/                                      # uv-managed
│     └── /etc/systemd/system/approval-bridge.service
│   bind: 127.0.0.1:8000 + docker bridge IP          # not externally reachable
│   reaches: localhost postgres (nanormm DB), localhost redis (DB 11)
│   exposes: /api/nanoclaw/actions/*, /mcp, /healthz
│
├── nanoclaw orchestrator (NEW, native via nanoclaw.sh)
│   /opt/nanoclaw/                                    # cloned from quickstack-cc fork
│   .env: SLACK_*, NANOCLAW_API_KEY, CLAUDE_CODE_USE_VERTEX=1, ...
│   /add-slack skill registers @recon as main channel for #rmm-alerts
│   groups/recon/CLAUDE.md = system prompt
│   spawns: per-message agent containers via host Docker daemon
│
└── Docker daemon (existing)
    └── ephemeral nanoclaw-agent containers
        reach bridge at http://host.docker.internal:8000/mcp
        ADC mounted from /home/nanoclaw/.config/gcloud (read-only)
```

Two new systemd services, one fork of nanoclaw, one set of ephemeral Docker containers.

## Components

### 5.1 Bridge MCP HTTP endpoint (extends Plan 2's bridge)

- New route on the bridge: `/mcp`, served by the Python MCP SDK's HTTP transport (SSE or streamable-HTTP — Plan 3 picks based on what the Claude Agent SDK consumes).
- Backed by the same `Dispatcher` instance the bridge already uses for `/api/nanoclaw/actions/*`. Tool list = trmm-mcp's existing registered tools (alerts, agents, scripts, actions).
- Auth: none. Bound only to localhost and the docker bridge interface (`172.17.0.1`). No nginx route to `/mcp`. Network reachability is the auth.
- The standalone `python -m trmm_mcp` stdio entrypoint stays in the codebase as a CLI debugging tool but isn't part of prod deployment.

### 5.2 Bridge systemd unit

- `/etc/systemd/system/approval-bridge.service`
- `ExecStart=/opt/nanormm/approval-bridge/.venv/bin/python -m approval_bridge`
- Runs as a dedicated `nanormm` Linux user
- `EnvironmentFile=/etc/nanormm/bridge.env` (mode 600, owned by `nanormm`)
- `Restart=on-failure`
- Idempotent install/upgrade script at `/opt/nanormm/deploy/install-bridge.sh`: git pull, uv venv, `uv pip install -e .`, `systemctl daemon-reload && systemctl restart approval-bridge`.
- Plan 3 also **deletes** `nanormm/approval-bridge/Dockerfile` and `nanormm/approval-bridge/.dockerignore` — the prod path is systemd, the Dockerfile would rot otherwise.

### 5.3 Nanoclaw native install

- Fork upstream `qwibitai/nanoclaw` to `quickstack-cc/nanormm-nanoclaw` (separate from any prior prospect-pro fork; explicitly not based on prospect-pro's customizations).
- Apply the standard upstream `/add-slack` skill via the documented merge from `qwibitai/nanoclaw-slack`.
- Apply a single nanormm-specific patch: lift the action handler block from prospect-pro's fork's `slack.ts` into ours (`setupActionHandlers`, `parseActionResponse`, the `nanoclaw_confirm` Bolt handler that POSTs to a configurable URL). Rename `PROSPECT_PRO_API_URL` → `NANOCLAW_ACTION_API_URL` in the lifted code. Leave a code comment crediting prospect-pro as origin.
- Clone to `/opt/nanoclaw/` on the VM under a `nanoclaw` Linux user (member of `docker` group).
- Run `bash nanoclaw.sh` once, interactively, on the VM. The official installer handles `npm install`, `container/build.sh`, and creates the systemd unit.
- `.env` at `/opt/nanoclaw/.env` mode 600:
  ```
  SLACK_BOT_TOKEN=xoxb-...
  SLACK_APP_TOKEN=xapp-...
  NANOCLAW_API_KEY=<shared-secret-with-bridge>
  NANOCLAW_ACTION_API_URL=http://host.docker.internal:8000
  CLAUDE_CODE_USE_VERTEX=1
  ANTHROPIC_VERTEX_PROJECT_ID=qsrmm-494222
  CLOUD_ML_REGION=us-east5
  ANTHROPIC_DEFAULT_MODEL=claude-haiku-4-5
  ```
- OneCLI is **not in the credential path** for Anthropic calls — `CLAUDE_CODE_USE_VERTEX=1` makes nanoclaw's `container-runner.ts` (line 37–42) skip OneCLI and rely on host gcloud ADC instead. `nanoclaw.sh` may install OneCLI as part of its standard setup; that's fine, it just becomes an unused binary.

### 5.4 Recon agent identity (`/opt/nanoclaw/groups/recon/CLAUDE.md`)

System-prompt content:

> You are recon. You watch `#rmm-alerts` for TacticalRMM webhook posts. When a new alert arrives, enrich it with agent context using the `trmm-mcp` tools, then post a threaded reply with: (1) one-paragraph triage summary, (2) recommended remediation citing specific tools, (3) if a write action is warranted, emit the `nanoclaw_action` envelope verbatim from the tool result. Never make up TRMM data — always tool-call. Stay concise.

Slack channel registration:
```
npx tsx setup/index.ts --step register -- \
  --jid "slack:<#rmm-alerts channel ID>" \
  --name "rmm-alerts" \
  --folder "slack_main" \
  --is-main \
  --no-trigger-required \
  --channel slack
```
(`--is-main --no-trigger-required` so recon responds to every post in the channel, including TRMM webhook bot posts.)

MCP server pointer (per-group MCP config; format depends on nanoclaw's mechanism — Plan 3 research task pins it): point at `http://host.docker.internal:8000/mcp`.

### 5.5 GCP / Vertex AI setup

- Add `roles/aiplatform.user` to the qsrmm VM's existing service account (the same one that runs `gs://qsrmm-backups`). Additive — no new SA.
- Workload Identity / ADC works automatically from inside agent containers because `nanoclaw` mounts the host's `~/.config/gcloud` into spawned agent containers. Same SA token, same project, no Anthropic API key anywhere.
- Verify `gcloud auth application-default print-access-token` works on the VM as the `nanoclaw` user before running smoke.

### 5.6 Slack app for `@recon`

- New app at `api.slack.com/apps`, separate from the existing TRMM-webhook app.
- Socket Mode enabled. Generate a bot token (`xoxb-`) and an app-level token (`xapp-`).
- Scopes: `chat:write`, `channels:history`, `groups:history`, `im:history`, `channels:read`, `groups:read`, `users:read`.
- Subscribe to bot events: `message.channels`, `message.groups`, `message.im`.
- Install to workspace, invite the bot to `#rmm-alerts`, capture the channel ID.
- Setup is a manual checkpoint task in Plan 3 — Jim does the api.slack.com clicking, pastes tokens, plan continues.

### 5.7 Policy.yaml

Plan 2 left a minimal `nanormm/policy.yaml`. Plan 3 audits it: confirm every read tool is `auto`, every write tool is `human_approval`, and any catastrophic tool (`uninstall_agent`, `delete_client` if it exists) is `forbidden`. No new tools — recon uses the surface Plan 1 built.

## Data flow — recon happy path

```
1. TRMM detects condition (existing pipeline)
   → fires existing webhook → posts alert to #rmm-alerts
   (no change to TRMM, no change to existing TRMM-webhook Slack app)

2. Recon (different Slack app, same channel) sees the new message
   → nanoclaw routes to recon's agent container

3. Agent starts, reads the alert text
   → calls bridge MCP tools: get_alert(id), get_agent(id),
     agent_recent_checks, search_past_alerts (all `auto` policy)

4. Agent synthesizes triage + recommendation
   case 4a: read-only triage → posts threaded text reply, done
   case 4b: write action needed (e.g. kill_process)
       → calls kill_process(...)
       → bridge dispatcher: policy=human_approval
         → creates Redis pending row, audit row
         → returns {status: pending, action_id, summary, nanoclaw_action: {...}}
       → agent emits the nanoclaw_action envelope verbatim as final response

5. Nanoclaw's slack.ts (our patched version) detects the envelope
   → posts a threaded reply with summary + Confirm/Cancel buttons
   → buttons carry action_id as their value

6. Tech in #rmm-alerts clicks Confirm
   → Slack delivers via Socket Mode WebSocket to nanoclaw orchestrator
   → orchestrator's Bolt nanoclaw_confirm handler fires
   → POSTs {token: action_id} to NANOCLAW_ACTION_API_URL/api/nanoclaw/actions/execute/
     with Bearer NANOCLAW_API_KEY and X-Slack-User-{ID,Name} headers

7. Bridge receives the POST
   → marks Redis approved, audits, calls Dispatcher.resume()
   → resume executes kill_process via TrmmClient
   → returns {message: "Executed: ..."} to nanoclaw

8. Nanoclaw orchestrator updates the original Slack message with the result text

9. Audit DB has a complete row: action, args, who approved, who executed, result
```

### Failure modes

| Failure | Behavior |
|---|---|
| Recon agent crashes mid-tool-chain | No envelope posted, tech never sees buttons. Mitigation: nanoclaw container restart policy + recon system prompt instructs "if any tool fails, post a brief error in the thread." |
| Bridge unreachable when tech clicks Confirm | Slack retries with backoff (Bolt built-in). If still down, action expires on Redis TTL (30 min default). |
| Tech approves twice (Slack retry) | Bridge idempotency (Plan 2) returns cached message; TRMM call is not re-executed. |
| Vertex AI rate limit / outage | Recon can't read the alert. nanoclaw's container exits with error; visible in `journalctl -u nanoclaw`. |
| Unexpected cost spike | Daily budget cap (per prospect-pro's pattern, e.g. $5/day) cuts off recon if a runaway loop bursts cost. Re-enable at midnight CT. |

## Security

Mitigations baked into Plan 3:

- **Dedicated `nanoclaw` Linux user**, member of `docker` group only. Not the qsrmm user, not root. Limits filesystem reach if the orchestrator is compromised.
- **Dedicated `nanormm` Linux user** for the bridge systemd unit. Owns `/opt/nanormm/` and `/etc/nanormm/bridge.env` (mode 600). Separate from `nanoclaw` user.
- **VM service account scoped to `roles/aiplatform.user` only** (additive to whatever the backup job needs). Compromised nanoclaw can call Vertex AI but can't enumerate or pivot through GCP.
- **Bridge MCP endpoint unauthenticated, but bound to localhost + the docker bridge interface only.** No nginx route to `/mcp`. Network reachability is the auth.
- **`NANOCLAW_API_KEY` is NOT passed into spawned agent containers** — only the orchestrator process holds it. The agent itself (the most prompt-injectable surface) cannot self-approve. Plan 3 verifies this against nanoclaw's `container-runner.ts` env-passing logic.
- **`policy.yaml` defaults to `human_approval` for unknown tools** (already from Plan 2).
- **Daily Vertex AI budget cap**, per prospect-pro's pattern. Cuts off recon if a runaway loop bursts cost.

**Trust boundary statement:** v1's trust boundary is the GCE VM. Co-location with TRMM means a nanoclaw compromise = TRMM compromise — period. Splitting nanoclaw to a separate VM is a Plan 5+ option once operational shape is known. The MCP module boundary is designed to make that future split cheap (config change, no rewrite).

## Smoke test

End-to-end smoke that exercises alert → enrichment → approve → execute → result, run as Plan 3's final task:

**One-time setup:**
- Create a sandbox client + agent in TRMM specifically for smoke testing. Name like `nanormm-smoke`.
- Stays as a permanent fixture for re-running smoke after future changes.

**Trigger:** Use TRMM's REST API to create a synthetic alert against the sandbox client. The existing TRMM webhook (already wired to `#rmm-alerts` via the existing Slack app) fires automatically.

**Assertions:**
1. Within ~30 seconds (P95), recon threads a reply on the alert post.
2. The reply includes triage text referencing the agent name (proves recon called `get_agent` and used the result).
3. If the synthetic alert warrants a write action, the reply includes Confirm/Cancel buttons (proves `nanoclaw_action` envelope round-trip works).
4. **Manual:** human clicks Confirm; bridge logs `executed`; `nanormm_actions` row shows `executed_at IS NOT NULL` for that action_id.

**Cleanup:** sandbox client kept; document optional cleanup of test rows from `nanormm_actions` if the table gets noisy.

**Ship signal:** smoke green = Plan 3 done.

## Decisions log

| Decision | Choice | Why |
|---|---|---|
| Plan 3 scope | Recon end-to-end only; operator deferred | Same logic that scoped Plan 2 down — validate the seam, defer the second bot to a config diff |
| Nanoclaw deploy | Native via `nanoclaw.sh`, not containerized | Official supported path on Linux; same security profile as DooD; simpler to debug |
| Bridge deploy | Native systemd, not Docker | TRMM is bare-metal — bridge follows suit. Plan 2's Dockerfile dropped from prod path and deleted |
| MCP transport | Folded into bridge at `/mcp` | Eliminates a third container without compromising the MCP module boundary |
| Vertex region | `us-east5` (corrected from `us-central1`) | Validated working from prospect-pro for Sonnet/Haiku |
| Action-button source | Lifted from prospect-pro fork into our nanormm fork | Avoids dependency on a fork being deprecated; bounded ~80-line patch |
| nanoclaw fork base | Fresh fork off `qwibitai/nanoclaw` | Stay clear of prospect-pro fork operationally |
| OneCLI | Not in credential path | `CLAUDE_CODE_USE_VERTEX=1` bypasses; ADC via mounted gcloud config |
| Approval UX | Action buttons (Confirm/Cancel), not text commands | The whole UX value of recon is one-click; text commands were the cheaper fallback we rejected |
| Cancel→reject wiring | Out of Plan 3 scope | Lifted handler only POSTs `/execute/`; Cancel stays client-side. Polish item for Plan 4+ |

## Open questions deferred to implementation

- Exact MCP HTTP transport (SSE vs streamable-HTTP) the Claude Agent SDK consumes — Plan 3 research task pins this against the SDK's actual support.
- Per-group MCP config mechanism in nanoclaw — `.mcp.json` per group, env vars, or skill-driven. Plan 3 reads the relevant nanoclaw source.
- Daily budget cap mechanism — prospect-pro has a pattern; whether to copy it or simplify is a Plan 3 implementation choice.
