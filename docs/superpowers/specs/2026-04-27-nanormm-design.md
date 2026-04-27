# nanormm — TacticalRMM × nanoclaw integration design

**Date:** 2026-04-27
**Status:** Design approved, pre-implementation
**Owner:** Jim Hill (jim@quickstack.cc)

## Why

Quick Stack runs an internal TacticalRMM fork ([qsrmm](../../../README.md)) to manage its own and customer fleets. The MSSP wedge — what differentiates Quick Stack from a generic MSP — is the *quality and speed of incident response*. Today, alerts land in `#rmm-alerts` and a human triages them; tier-1 remediation requires a human in the loop end-to-end.

`nanormm` integrates [nanoclaw](https://github.com/qwibitai/nanoclaw) (container-isolated Claude agent runner) with TRMM via a Python MCP server. Two Slack-attached agents — `recon` and `operator` — give Quick Stack technicians both an autonomous alert-enrichment surface and an interactive Slack copilot for working with TRMM. The competitive story is: faster triage, draft-quality remediation recommendations on every alert, and a graduation path to autonomous tier-1 remediation.

## Scope

**In scope (v1):**
- `recon`: Slack-attached agent that watches `#rmm-alerts`, threads enrichment + recommended remediation on TRMM alert posts.
- `operator`: Slack-attached agent that responds to `@operator` mentions and DMs across the workspace; same TRMM tool surface, broader assistant role.
- `trmm-mcp`: Python MCP server exposing TRMM read + write operations as MCP tools.
- `approval-bridge`: small FastAPI service receiving Slack interaction events and releasing pending write actions.
- `policy.yaml`: per-tool authority configuration (`auto` / `human_approval` / `forbidden`) — every write action defaults to `human_approval` at launch.

**Out of scope (deferred, not forgotten):**
- Customer-facing tier-1 bot (the "B persona" in brainstorming) — power-user admins might want this eventually but launch posture stays internal-only.
- Multi-tenancy at the channel level — single `#rmm-alerts` channel, no per-client scoping in v1.
- Confidence-based gating — binary `auto` / `human_approval` only at launch; revisit after operational data shows whether confidence dimension is needed.
- Threat-intel feeds, SIEM ingest, EDR integrations.
- MeshCentral direct integration (remote-desktop session triggering, etc.).
- Open-sourcing `trmm-mcp` as a standalone repo — keep in `qsrmm/` until shape stabilizes.

## Product shape

Two bots, one MCP backbone, gated write authority with explicit graduation path.

### Bots

- **`@recon`** — alert watcher. Member of `#rmm-alerts`. Triggers when TRMM's existing alert webhook posts a message. Reads alert details via `trmm-mcp`, enriches (recent agent activity, similar past incidents, patch state, related checks), threads a reply with a recommended remediation and approval buttons for any write action.
- **`@operator`** — tech-facing. Triggers on `@operator` mentions and DMs anywhere in the workspace. Same MCP tool surface as `recon`, broader system prompt (general TRMM assistant). Tech says "what's going on with DC01" or "reboot WORKSTATION-42" — operator reads, drafts, asks for approval on writes.

Both bots share the same MCP tool surface and the same approval workflow. The difference is the trigger source (alert vs. tech command) and the system prompt scope.

### Authority model

Write authority is gated through `policy.yaml`. Day 1 launch: every write action is `human_approval`. Graduation = edit the YAML, redeploy. The architecture is designed so that "starts gated, graduates over time" is a config change, not a refactor — that path is non-negotiable because the MSSP competitive advantage depends on the responder eventually taking real actions.

`policy.yaml` shape:

```yaml
version: 1
default: human_approval        # safe-by-default for unlisted tools
tools:
  list_alerts:           auto  # reads always auto
  get_agent:             auto
  agent_recent_checks:   auto
  agent_patch_state:     auto
  collect_artifacts:     human_approval
  kill_process:          human_approval
  isolate_host:          human_approval
  run_script_on_agent:   human_approval
  reboot_agent:          human_approval
  uninstall_agent:       forbidden  # never autonomous
```

A `forbidden` tier exists for actions that should never be agent-driven (e.g. `delete_client`, `uninstall_agent`).

## Architecture

```
                  Slack Workspace
                  ├── #rmm-alerts        ← TRMM webhook posts; recon watches
                  └── any channel         ← techs @-mention operator
                              ↕
   ┌────────────── GCE VM (qsrmm-494222) ──────────────┐
   │ ┌──────────── TRMM stack (existing) ────────────┐ │
   │ │ nginx · daphne · celery · postgres · redis ·  │ │
   │ │ mongo · mesh · nats · ...                     │ │
   │ └────────────────────────────────────────────────┘ │
   │                                                    │
   │ ┌─────────── Docker network: nanormm ───────────┐  │
   │ │  ┌────────────┐  ┌────────────┐  ┌──────────┐ │  │
   │ │  │ nanoclaw   │  │ nanoclaw   │  │ trmm-mcp │ │  │
   │ │  │ recon      │─▶│ operator   │─▶│ (Python) │ │  │
   │ │  └────────────┘  └────────────┘  └────┬─────┘ │  │
   │ │                                       │       │  │
   │ │              ┌────────────┐           │       │  │
   │ │              │ approval-  │◀──────────┘       │  │
   │ │              │ bridge     │                   │  │
   │ │              └────────────┘                   │  │
   │ └────────────────────────────────────────────────┘ │
   └─────────────────────────┬──────────────────────────┘
                             │ Knox service token
                             ▼
              TRMM Django REST API (internal)
```

Four containers in a `nanormm` Docker network on the existing GCE VM, sharing infra (Postgres, Redis, nginx) with TRMM.

### Why co-located, MCP-mediated (Approach 3)

Considered three architectures: (1) co-located REST-direct, (2) separate VM with MCP server, (3) co-located with MCP server. Chose (3) because:

- The MCP boundary is the load-bearing architectural decision. It's the seam that lets a future customer-facing bot, an internal CLI, or a shared/open-sourced TRMM tool layer reuse the same TRMM access logic without a rewrite.
- Co-locating defers an infra decision (second VM) that's cheap to reverse — splitting onto a separate VM later is a config change once the MCP seam exists.
- Cost: no second VM at launch.
- Risk: TRMM stack already uses ~5 GB of the VM's 8 GB. Adding nanormm's four containers leaves modest headroom. If sustained free memory drops below ~500 MB, resize to `e2-standard-4` (gcloud one-liner, ~5 min downtime).

## Components

### `trmm-mcp` (Python MCP server)

Single source of truth for TRMM access. Exposes a tool surface to agents over MCP (stdio or HTTP+SSE per nanoclaw conventions). Authenticates to TRMM Django REST API via a dedicated Knox service-account token.

**Tool dispatch path:**

```
agent calls tool → trmm-mcp dispatcher
                        │
                        ├── read tool? ─────────────► execute, return result
                        │
                        └── write tool?
                                ├─ load policy.yaml
                                ├─ lookup action authority
                                │
                                ├── auto?            ──► execute, return result
                                │
                                ├── human_approval?  ──► create pending row in Redis
                                │                          (action_id, args, expires_at)
                                │                       return {status: "pending",
                                │                               action_id, summary}
                                │
                                └── forbidden?       ──► return {status: "denied",
                                                                reason}
```

**Read tools (all `auto`):**

| Tool | Returns |
|---|---|
| `list_alerts(status?, since?, client?)` | Recent alerts with severity, agent, age |
| `get_alert(alert_id)` | Full alert detail + linked agent/check |
| `list_agents(client?, site?, online?)` | Agent inventory, lightweight |
| `get_agent(agent_id)` | Full agent record: OS, hardware, last seen, mesh/nats status |
| `agent_recent_checks(agent_id, n=20)` | Last N check results — pass/fail, output |
| `agent_recent_tasks(agent_id, n=20)` | Last N scheduled task runs |
| `agent_patch_state(agent_id)` | Installed/missing/failed/pending KBs |
| `agent_running_processes(agent_id)` | Live process list |
| `query_clients()` | Client/site tree |
| `script_history(agent_id, n=20)` | Recent script executions + output |
| `search_past_alerts(agent_id, since)` | Historical alerts on same agent |

**Write tools (all `human_approval` at launch):**

| Tool | Authority graduation candidate |
|---|---|
| `run_script_on_agent(agent_id, script_id, args)` | Yes for *specific* allow-listed scripts only — never arbitrary |
| `run_inline_command(agent_id, shell, command)` | **No** — keep gated permanently. Too broad. |
| `kill_process(agent_id, pid_or_name)` | Yes |
| `restart_service(agent_id, service_name)` | Yes |
| `reboot_agent(agent_id)` | Yes (workstations and servers separate policy) |
| `collect_artifacts(agent_id, artifact_set)` | Yes — pure data collection, low blast radius |
| `isolate_host(agent_id)` | Yes — runs the firewall isolation script |
| `unisolate_host(agent_id)` | Yes — pairs with above |
| `disable_account(agent_id, username)` | Yes |
| `pause_scheduled_task(agent_id, task_id)` | Yes |
| `acknowledge_alert(alert_id, note)` | Yes — close-the-loop in TRMM after handling |

**Important constraint:** `run_script_on_agent` accepts a `script_id`, never a script body. The agent invokes scripts that already exist in TRMM's library — it cannot synthesize and ship new ones. This keeps the supply-chain story sane and means script review is governed by TRMM's existing workflow, not the agent's judgment. `run_inline_command` exists for ad-hoc tech needs and stays permanently `human_approval` (with a possible future tightening to two-approver requirement).

### `nanoclaw-recon`

Slack-attached Claude agent in a nanoclaw-managed container. Slack identity `@recon`. Member of `#rmm-alerts`. System prompt scoped to triage and remediation drafting. Tool list: full `trmm-mcp` surface.

**Trigger:** detects new alert posts (from TRMM webhook bot) → fetches alert details via `trmm-mcp` → enriches with agent context, recent activity, similar past incidents → posts threaded reply with proposed remediation + approval buttons.

### `nanoclaw-operator`

Second nanoclaw container, separate Slack bot identity `@operator`. System prompt: broader TRMM assistant. Same MCP tool surface as `recon`.

**Trigger:** `@operator` mentions and DMs across the workspace. Tech-driven, not alert-driven.

### `approval-bridge` (FastAPI)

Receives Slack interaction events (button clicks). Validates Slack signing-secret signature. Looks up pending action in Redis, marks approved/rejected/edited. Notifies `trmm-mcp` (via Redis pubsub) so the original tool call can complete.

Why a separate container instead of folding into `trmm-mcp`: keeps the MCP server pure (no inbound HTTP), scopes the public-facing route (under nginx `/nanormm/slack/interactions`) narrowly, and makes the auth surface (Slack signature validation) easy to reason about in isolation.

## Data flow

### Recon path (alert-triggered)

1. TRMM detects condition → fires webhook → posts alert to `#rmm-alerts` (existing pipeline; no change).
2. Recon (Slack member) sees the post → triggers nanoclaw agent.
3. Agent reads alert details via `trmm-mcp.get_alert` → calls `get_agent`, `agent_recent_checks`, `search_past_alerts` to enrich.
4. Agent synthesizes enrichment, considers a write action, calls e.g. `kill_process(...)`.
5. `trmm-mcp` checks `policy.yaml` → `human_approval` → creates pending row in Redis with `action_id` and 30-min TTL → returns `{status: "pending", action_id, summary}`.
6. Agent posts threaded reply: enrichment summary + recommended action + `✅ Approve / ❌ Reject / ✏️ Modify` buttons (each carrying `action_id` in `value`).
7. Tech clicks `✅` → Slack POSTs interaction event to `https://api.quickstack.cc/nanormm/slack/interactions` → nginx routes to `approval-bridge`.
8. `approval-bridge` verifies Slack signature, looks up `action_id` in Redis, marks approved → publishes Redis event.
9. `trmm-mcp` (subscribed) sees approval, executes the actual TRMM REST call, returns result to agent.
10. Agent posts final result in same thread.

### Operator path (tech-triggered)

1. Tech: `@operator what's going on with CLIENT-XYZ-DC01?`
2. Operator agent reads via `trmm-mcp` → drafts answer.
3. Tech: `@operator reboot it`.
4. Agent calls `reboot_agent` → policy says `human_approval` → posts confirm prompt with approval buttons.
5. Tech clicks `✅` → executes → result posts in thread.

Same approval flow regardless of trigger source — same MCP, same policy, same buttons.

## Audit trail

- Pending + completed write actions appended to `nanormm_actions` table in a dedicated `nanormm` Postgres DB on the existing TRMM Postgres instance (separate creds, separate DB).
- Schema: `id`, `tool_name`, `args` (jsonb), `policy_decision`, `pending_at`, `approved_by`, `approved_at`, `executed_at`, `result` (jsonb), `slack_message_ts`.
- Slack thread itself is a human-readable record by construction.
- `policy.yaml` is versioned in git — historical record of what authority was granted when.

## Failure modes

- **`policy.yaml` missing/corrupt:** `trmm-mcp` refuses to start. Fail-closed.
- **TRMM token rejected:** MCP returns clean error; agent reports inability to act.
- **Slack API down when posting approval prompt:** MCP returns error; agent reports inability to reach approval channel; Redis pending row is cleaned up.
- **`approval-bridge` down when tech clicks `✅`:** Slack retries (built-in). If still down, action expires on TTL; tech re-triggers.
- **`trmm-mcp` crash between approval and execution:** on restart, MCP scans Redis for `approved_pending_execution` rows and replays. Idempotency: write tools include an idempotency key derived from `action_id`.
- **Approval timeout:** 30-minute TTL default. Expired pending actions auto-cancel.

## Repo layout

Add a top-level `nanormm/` directory to `qsrmm`, sibling to `api/`, `docker/`, `.devcontainer/`:

```
nanormm/
├── trmm-mcp/                # Python MCP server
│   ├── pyproject.toml
│   ├── trmm_mcp/
│   │   ├── server.py
│   │   ├── tools/           # one module per tool group (alerts, agents, ...)
│   │   ├── policy.py        # policy.yaml loader + enforcement
│   │   ├── approvals.py     # Redis-backed pending-approval registry
│   │   └── trmm_client.py   # Knox-token Django REST wrapper
│   └── tests/
├── approval-bridge/         # FastAPI app receiving Slack interactions
├── nanoclaw-config/         # agent prompts + tool wiring
│   ├── recon.yaml
│   └── operator.yaml
├── policy.yaml              # the actual policy file (versioned)
├── docker-compose.yml       # the four containers
└── README.md
```

## Infra & ops

- **Containers:** `trmm-mcp`, `nanoclaw-recon`, `nanoclaw-operator`, `approval-bridge`. All on `nanormm_default` Docker network. `trmm-mcp` has no published ports. `approval-bridge` exposes one port; nginx adds a `location /nanormm/slack/interactions` block.
- **Shared with TRMM stack:** Postgres (new `nanormm` DB), Redis (DB index 11; TRMM owns 10), nginx.
- **Secrets:** TRMM Knox service-account token, Slack signing secret, Slack bot tokens (×2), Anthropic API key — all from GCP Secret Manager via init-script-injected Docker secrets. No secrets in env files in the repo.
- **Sizing:** stay on `e2-standard-2` at launch. Resize to `e2-standard-4` if free memory drops below ~500 MB sustained.
- **Backups:** `nanormm` Postgres DB joins existing daily backup cron to `gs://qsrmm-backups`. `policy.yaml` covered by git. Redis pending-approvals are ephemeral by design.

## Testing

- **`trmm-mcp`:** pytest, mock Django REST client for unit tests; integration tests against the `.devcontainer` TRMM stack.
- **`approval-bridge`:** pytest with mocked Slack interaction payloads; verifies signature validation and Redis state transitions.
- **End-to-end smoke:** synthetic alert injected into `#nanormm-smoke` Slack channel on every deploy; verifies `recon` threads a reply within 30 seconds (P95) — fail the deploy if it doesn't.
- **Policy regression:** test that loads `policy.yaml` and asserts every tool in `trmm-mcp`'s registry has an explicit policy entry — no silent `default` fallthroughs in production.

## Open questions deferred to implementation

- Exact Slack interaction wiring (Bolt SDK vs. raw events) — both work; pick during nanoclaw config.
- Whether to model `nanormm` as a separate Django app inside `api/tacticalrmm/` for the audit table, or use a standalone DB schema with `psycopg` directly. Leaning standalone to keep TRMM core untouched.
- Synthetic alert smoke-test wiring — needs a sandbox client in TRMM that doesn't pollute real metrics.

## Decisions log

| Decision | Choice | Why |
|---|---|---|
| Wedge | Tech copilot + autonomous SOC responder | MSSP differentiation; customer-facing deferred until shape stabilizes |
| Autonomy at launch | Read + recommend only (Level A) | Build trust on read traffic before granting write authority |
| Architecture | Approach 3 — co-located, MCP server | MCP boundary gives reuse leverage; co-location defers a cheap-to-reverse infra decision |
| MCP language | Python | Matches TRMM stack, fastest to write against Django models/API |
| Bot topology | Two bots, one channel for alerts | Tighter system prompts per bot; lower tool-error rate vs. one mode-switching bot |
| Approval mechanism | Policy YAML + Slack buttons (Approach C) | Per-action graduation without code changes; clean audit; panic-button revert |
| Confidence gating | Deferred | Self-reported confidence is fuzzy; revisit with real ops data |
| `run_script_on_agent` | `script_id` only, never script body | Supply-chain hygiene; script review stays in TRMM's existing workflow |
| `run_inline_command` | Permanently `human_approval` | Too broad to ever auto-grant |
| Names | `recon` (alerts) + `operator` (tech) | Avoid Microsoft "copilot" overuse; pair semantically with the roles |
