# Plan 4 — debrief & architecture review

**Date:** 2026-05-02 (late)
**Status:** Shipped to production. Approve and reject smoke-tested end-to-end with audit row verification.

**Companion docs:**
- [Plan 4 spec](../specs/2026-04-30-bridge-side-card-emission.md)
- [Plan 4 plan](../plans/2026-05-01-plan-4-bridge-card.md)
- [Plan 3 smoke handoff (predecessor)](./2026-04-29-plan3-smoke-results.md)
- [Plan 4 spike results](./2026-05-01-plan4-spikes.md)

---

## TL;DR

Plan 4 takes the LLM agent out of the approval-card rendering path. The bridge now tells nanoclaw to deliver the card via its existing chat-sdk → Slack pipeline; the agent just receives `{status:"pending"}` and continues its turn naturally. The full round-trip — gate, deliver, click, approve/reject, audit — works without any prompt-following gymnastics.

**Production state:**
- `qsrmm` `develop` pushed to `quickstackcc/tacticalrmm` (16 commits merged from `feat/nanormm-recon`)
- `nanormm-nanoclaw` `main` pushed to `quickstackcc/nanormm-nanoclaw` (5 new commits)
- VM has rsync'd source + restarted services
- Recon prompt simplified from 167 lines to 60 lines

---

## Architectural shift

### Before (Plan 3 v2)

```
agent → tool call → bridge gates ───┐
                                    ▼
              { status: "pending",
                action_id, summary,
                nanormm_card: {       ← agent must emit this verbatim
                  kind: "chat-sdk",
                  content: { … }      ← chat-sdk envelope
                }
              }
                ▼
agent emits envelope as final assistant message ← UNRELIABLE
                ▼
chat-sdk-bridge sees kind:"chat-sdk" → renders Slack card
```

This made correctness depend on the model's prompt-following on a non-trivial JSON format. Sonnet 4.6 wrapped it. Haiku 4.5 dropped it. Strengthening the system prompt didn't fix it.

### After (Plan 4)

```
agent → tool call → bridge gates ───┐
                                    ▼
              create approval (Redis)
              record_pending audit (Postgres)
              inject_card via httpx ────────┐
                                            ▼
                  POST 127.0.0.1:8765/internal/sessions/<sid>/inject-card
                                            ▼
                  nanoclaw writes ask_question row to outbound.db
                                            ▼
                  delivery.ts polls → chat-sdk-bridge → Slack card
                                            │
              { status: "pending",          │
                action_id, summary }   ◄────┘ (returned to agent in parallel)
                ▼
agent emits whatever it would naturally say (or nothing) — card is independent

[click in Slack]
chat-sdk-bridge.onAction → registered handlers (in load order)
  └─ nanormm-bridge handler matches "nrmact-" prefix
       ├─ approve → POST bridge /api/nanoclaw/actions/execute/
       │                ▼
       │            mark_approved + record_approval + dispatcher.resume()
       │                ▼
       │            registered tool fn → TRMM API → audit executed_at
       └─ reject → POST bridge /api/nanoclaw/actions/reject/
                        ▼
                    mark_rejected + record_rejection → audit rejected_at
```

The agent is no longer in the rendering loop. The card is delivered by the same pipeline that delivers every other agent message. Click handling is unchanged.

---

## Components, file by file

### Bridge (qsrmm/nanormm)

| File | Role | New / changed |
|---|---|---|
| `approval-bridge/approval_bridge/inject_client.py` | Async httpx client. Single method: `inject_card(session_id, question_id, title, question, options) → None`. Raises on non-2xx. No retries. | NEW |
| `approval-bridge/approval_bridge/mcp_app.py` | Adds `SESSION_ID_VAR` (ContextVar) + `_SessionHeaderMiddleware` ASGI middleware. Wraps the MCP Starlette app so `X-Nanoclaw-Session` header lands in the contextvar before the MCP handler runs. | MODIFIED |
| `approval-bridge/approval_bridge/settings.py` | Adds `nanoclaw_internal_url` field (default `http://127.0.0.1:8765`, env override `NANOCLAW_INTERNAL_URL`). | MODIFIED |
| `approval-bridge/approval_bridge/deps.py` | After `_build_dispatcher`, sets `dispatcher._inject = InjectClient(settings.nanoclaw_internal_url)`. Underscore attr write because trmm-mcp's stdio constructor doesn't take inject_client. | MODIFIED |
| `trmm-mcp/trmm_mcp/server.py` | `_call_tool` handler reads `SESSION_ID_VAR.get()` (lazy try-import so stdio path stays decoupled), threads it as `session_id=...` into `dispatcher.dispatch(...)`. | MODIFIED |
| `trmm-mcp/trmm_mcp/tools/_base.py` | `Dispatcher.__init__` gains optional `inject_client`; `dispatch` gains optional `session_id` kwarg. HUMAN_APPROVAL branch calls `inject_client.inject_card(...)` if wired. Strict guard: raises `PolicyError` if inject_client is wired but session_id is missing. `_build_approval_card_envelope` and `nanormm_card` field deleted. | MODIFIED |
| `trmm-mcp/tests/test_envelope.py` | Covered the deleted helper. | DELETED |

### nanoclaw fork

| File | Role | New / changed |
|---|---|---|
| `src/modules/nanormm-bridge/internal-server.ts` | `127.0.0.1:8765` HTTP listener. `POST /internal/sessions/:id/inject-card` with `{questionId, title, question, options}`. Validates session, JOINs `messaging_groups` for `channel_type` + `platform_id`, writes ask_question row to per-session `outbound.db` (table `messages_out`) with even seq. Returns 202. | NEW |
| `src/modules/nanormm-bridge/index.ts` | Adds reject branch (existing approve path was already wired). Both paths POST to bridge with bearer auth + `X-Slack-User-ID`. Starts the internal-server at module load. | MODIFIED |
| `src/modules/index.ts` | nanormm-bridge import order moved BEFORE interactive — load order matters because interactive's response handler is prefix-blind and would claim every nrmact- click first if registered earlier. | MODIFIED |
| `src/container-runner.ts` | `buildContainerArgs` gained `sessionId` parameter, injects `-e NANOCLAW_SESSION_ID=<id>` into `docker run` args. Call site (in `wakeContainer`) updated. | MODIFIED |
| `container/agent-runner/src/index.ts` | When constructing the trmm http MCP server config, reads `process.env.NANOCLAW_SESSION_ID` and adds it as the `X-Nanoclaw-Session` header. | MODIFIED |

### VM-only state (not in source)

- `/opt/nanoclaw/.env` — `NANORMM_BRIDGE_URL=http://127.0.0.1:8000` (was `host.docker.internal:8000`). The host process doesn't resolve `host.docker.internal`; only containers do. The agent-runner inside the container still uses `host.docker.internal` via `TRMM_MCP_URL`. **Worth folding into the install runbook.**
- `/opt/nanoclaw/groups/recon/CLAUDE.local.md` — replaced 167-line OUTPUT CONTRACT with 60-line minimal version. Backup at `.bak.plan4-pre`. **Not under version control anywhere.**
- Stale Redis pending keys + Postgres pending audit rows from earlier smoke attempts. Will TTL out of Redis. Postgres rows can be cleaned with the SQL in the Plan 3 handoff.

---

## Session-id plumbing (the core data path)

Tracking session_id from container spawn through to `Dispatcher.dispatch(session_id=...)`:

```
container-runner.ts (host)
  buildContainerArgs(... session.id ...)
    args.push('-e', `NANOCLAW_SESSION_ID=${sessionId}`)
       ▼
docker run -e NANOCLAW_SESSION_ID=sess-... [container starts]
       ▼
agent-runner index.ts (in container)
  if (process.env.TRMM_MCP_URL) {
    headers['X-Nanoclaw-Session'] = process.env.NANOCLAW_SESSION_ID;
    mcpServers.trmm = { type: 'http', url, headers };
  }
       ▼
[Claude SDK uses this MCP config; every tool-call HTTP request carries the header]
       ▼
bridge mcp_app.py _SessionHeaderMiddleware (ASGI)
  scope.headers[b'x-nanoclaw-session'] → SESSION_ID_VAR.set(value)
  try: await app(scope, receive, send)
  finally: SESSION_ID_VAR.reset(token)
       ▼
trmm-mcp server.py _call_tool handler
  session_id = SESSION_ID_VAR.get()  # lazy try-import
  await dispatcher.dispatch(name, args, session_id=session_id)
       ▼
Dispatcher.dispatch (HUMAN_APPROVAL branch)
  if inject_client is not None:
    if not session_id: raise PolicyError
    await inject_client.inject_card(session_id=session_id, ...)
       ▼
InjectClient
  POST 127.0.0.1:8765/internal/sessions/<session_id>/inject-card
       ▼
nanoclaw internal-server.ts
  session = getSession(sessionId)
  mg = getMessagingGroupById(session.messaging_group_id)
  outDb.prepare(...).run(... mg.platform_id, mg.channel_type, session.thread_id, ...)
       ▼
[delivery.ts poll picks up the messages_out row and delivers via chat-sdk-bridge]
```

The contextvar is the load-bearing piece — `BaseHTTPMiddleware` reportedly has subtle issues with contextvar propagation, so we used raw ASGI middleware (verified by Spike 0.1 against the real `StreamableHTTPSessionManager` stack).

---

## Decisions log

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Card delivery topology | β: bridge → nanoclaw inject endpoint | Single Slack write path; reuses existing chat-sdk pipeline; no new Slack secret in bridge |
| 2 | Session-id plumbing | A: MCP request header | Pure plumbing; agent never sees session_id; one env var + one config line |
| 3 | Endpoint shape | `POST /internal/sessions/<sid>/inject-card` | RESTful, session_id in path makes audit-logging obvious |
| 4 | Endpoint auth | Localhost-bound, no token | Bridge and nanoclaw co-located; kernel enforces; defer auth until multi-host |
| 5 | Endpoint placement | New file in `src/modules/nanormm-bridge/` | Module already owns this subsystem boundary |
| 6 | Agent post-gating UX | No prompt-enforcement | Prior Plan 3 attempt at prompt-enforcement on this kind of constraint demonstrably didn't work |
| 7 | Reject path | Wired in this plan | Pieces already existed; leaving rejected actions in `pending` is a correctness gap, not a feature |
| 8 | `nanormm_card` field | Removed cleanly | No other consumer in qsrmm/nanormm tree |
| 9 | Failure ordering | create approval → record audit → inject → raise on failure | Matches existing AUTO-branch failure shape; orphans TTL out gracefully |

Plus three decisions made during execution:

| Decision | Choice |
|---|---|
| Even/odd seq invariant | Bridge writes EVEN seq into `messages_out` (mirrors the host-writes-even pattern that messages_in already uses) |
| Strict vs loose session_id guard | Strict (raise PolicyError) — caught a real bug during integration test, where the bridge's MCP path wasn't using the wired dispatcher |
| Module load order | nanormm-bridge BEFORE interactive in `modules/index.ts` — interactive's handler is prefix-blind and would steal every nrmact- click |

---

## Five non-obvious gotchas worth keeping in head

1. **Two trmm-mcp installs** in the bridge venv: one editable via .pth, one non-editable in site-packages. A naive rsync of source updates the editable view but the Python import resolves to the non-editable copy. Production deploy must run `uv pip install --reinstall -e ../trmm-mcp` after rsync, or the new code never gets picked up despite the source being up-to-date.

2. **Agent-runner runs `.ts` directly via bun** (no transpile step in-image). Deploying nanoclaw's `dist/` does NOT update what the container sees. The bind-mount source `/opt/nanoclaw/container/agent-runner/src/` must be updated separately.

3. **`host.docker.internal` resolves only inside containers.** When the host process (nanoclaw's nanormm-bridge module) needs to call back into the bridge HTTP endpoint, it must use `127.0.0.1`. The current `.env` has it as `127.0.0.1` for this reason.

4. **`BaseHTTPMiddleware` is the wrong middleware shape for contextvars.** anyio's task-group hop in `BaseHTTPMiddleware` can break contextvar propagation. Always use raw ASGI middleware (`async def __call__(self, scope, receive, send)`) for anything carrying contextvars across an awaited inner call.

5. **Container writes ODD seq, host writes EVEN seq.** Disjoint namespaces are load-bearing because seq is the agent-facing message ID returned by `send_message` and resolved by `getMessageIdBySeq` across both DBs. The bridge's inject-card writes EVEN to stay on the host side of the invariant.

---

## Production smoke results (2026-05-02 ~01:00 UTC)

Successful approve cycle:
```
00:59:25  inject-card written       — bridge → nanoclaw POST landed
00:59:26  Pending question created  — chat-sdk-bridge picked up messages_out row
00:59:27  Slack card delivered      — Approve/Reject buttons rendered
[click]
00:59:29  approved_at populated     — bridge /actions/execute/ resumed dispatcher
[502]     TrmmNotFoundError         — TRMM /agents/<id>/processes/kill/ returned 404
```

Successful reject cycle:
```
01:00:59  inject-card written
01:00:59  Pending question created
[click]
01:01:04  rejected_by=U0A7G93HG2V, rejected_at populated, reason="declined via Slack"
```

Plan 4's job is fully working: gate → deliver → claim → approve/reject → audit. The 502 on the approve path is a pre-existing trmm-mcp `kill_process` URL bug (the actual `/agents/<id>/processes/kill/` endpoint doesn't exist in this TRMM version) — not Plan 4's domain.

---

## Known issues / followups

1. **`kill_process` tool URL is wrong.** TRMM API returns 404 on `/agents/<id>/processes/kill/`. Pre-existing in trmm-mcp's URL builder; was masked previously because the approve flow never reached the resume step. Worth its own ticket.

2. **OneCLI 401 on every container spawn.** Existing infrastructure issue from Plan 3 era; agents run without their per-agent vault credentials. Not blocking the Plan 4 path, but every TRMM call has worse credential coverage than designed.

3. **Stale audit rows.** Production has accumulated several `pending` rows from failed smoke attempts (across Plan 3 and Plan 4). Run the cleanup SQL from the Plan 3 handoff if you want them gone, or let them sit (they don't hurt anything).

4. **Recon prompt is not in version control.** The simplified version on the VM has no source of truth in git. Either commit a copy somewhere in qsrmm or accept that any future redeploy of nanoclaw needs the prompt-rewrite step done by hand again.

5. **Two-handler dispatch pattern in nanoclaw.** Both `nanormm-bridge` and `interactive` register response handlers and rely on load order. Works today; would benefit from explicit prefix-claim registration if the registry grows further.

6. **Reject value paths** — chat-sdk-bridge resolves the option index back to the value; the bridge index handler keys on `payload.value === 'approve'` for the dispatch. If the option set ever changes (e.g., a third "Approve with note" button), the handler would route everything-not-approve to reject. Tighten the check if the card grows.

7. **No multi-host story.** Bridge and nanoclaw must share localhost. If we ever need to separate them, the localhost-only inject endpoint becomes inadequate; bearer auth + cert validation needed.

8. **Bridge venv install path is fragile.** The pip-via-uv reinstall step on deploy is a manual operation. The deploy runbook should be updated to capture: rsync source → `uv pip install --reinstall -e ./trmm-mcp` → `uv pip install --reinstall-package approval-bridge -e ./approval-bridge` → systemctl restart.

---

## Architectural review prompts (things worth pushing on)

If reviewing this for soundness, here are the questions I'd interrogate:

- **Coupling between bridge and nanoclaw schema.** internal-server.ts directly opens nanoclaw's per-session sqlite and writes to `messages_out`. If nanoclaw refactors session storage, the bridge breaks. Worth wrapping behind a public API in nanoclaw (e.g., `enqueueOutbound(sessionId, content)`) so the schema is encapsulated. Cost: a small new helper in nanoclaw + the bridge dropping the `Database`+`better-sqlite3` dependency for this path.

- **The strict-guard / no-inject-client coupling in deps.py.** `dispatcher._inject = InjectClient(...)` is a private-attr write because `_build_dispatcher` from trmm-mcp doesn't take inject_client. Cleaner: extend `_build_dispatcher` to accept it, or factor the inject-aware Dispatcher into a separate constructor entirely. Today it works; a future refactor to make the constructor a single source of truth would be hygiene.

- **Why is the audit row written before the inject succeeds?** The current order (audit → inject) means a failed inject leaves a `pending` row that never resolves. We chose this so the audit captures the attempt; the alternative (inject first) means we'd Slack a card that has no Postgres backing. Either is defensible; document the choice.

- **Session-id flow vs. session token.** session_id is opaque from the agent's perspective but in practice flows through the network unauthenticated (header on localhost). If the bridge ever moved off localhost, session_id alone wouldn't authorize the request. Worth noting that this is a localhost-trust model.

- **Recon agent's natural-tone freedom.** We chose agent-3 (no prompt-enforcement on the post-gating message). Cost: the agent might emit a redundant "I've requested approval" alongside the card. Benefit: we don't fight the model. If the duplication is annoying, prompt-tuning to elide the post-gating message is the next lever — but only after we have evidence it's actually annoying.

- **Single-handler-claims vs. multi-handler-routing in chat-sdk's onAction.** Today the registry is "first true wins." Plan 4 exposed that this is order-sensitive. A more robust model: each handler declares the prefix it claims, registry routes by prefix. Only worth doing if more handlers get added.
