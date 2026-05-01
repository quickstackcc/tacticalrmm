# Bridge-side approval card emission

**Plan 4 spec** — supersedes the [Plan 3 smoke handoff](../handoffs/2026-04-29-plan3-smoke-results.md).

## Problem

Plan 3 v2 routes the human-approval Slack card through the LLM agent: the bridge gates a tool call and returns a `nanormm_card` envelope; the agent's prompt tells it to emit that envelope verbatim as its final assistant message; nanoclaw's chat-sdk-bridge then renders the envelope as a Slack card.

**Steps before and after the agent work reliably. The agent step does not.**

Empirical findings from Plan 3 smoke (2026-04-29):

- Sonnet 4.6 wraps the envelope (`{"type":"card","card":{...}}`), losing the top-level `kind:"chat-sdk"` marker — chat-sdk renders it as plain JSON, no card.
- Sonnet 4.6 also calls `mcp__nanoclaw__ask_user_question` to make its own approval card with a questionId that has no `nrmact-` prefix — clicks dead-end.
- Adding `mcp__nanoclaw__ask_user_question` to `SDK_DISALLOWED_TOOLS` removes that second failure mode but the model then reverts to plain-text "waiting for approval" with no card at all.
- Haiku 4.5 fails identically.
- Multiple rounds of system-prompt strengthening (CAPS, side-by-side WRONG/RIGHT examples, explicit failure consequences) did not change the outcome on either model.

The root cause is the Claude Code preset's training: it prefers natural-tone prose over raw JSON dumps, and that preference is robust to in-context overrides. Correctness depends on prompt-following on a non-trivial format constraint, which is a coin flip on every model release.

## Approach: take the agent out of the rendering loop

The bridge already has the action_id, summary, and rendering material. Have the bridge tell nanoclaw "post this approval card into this session." nanoclaw owns the Slack write path (as it does for every other agent message), so the existing chat-sdk → Slack pipeline handles delivery, button rendering, and click claim — unchanged.

The agent's MCP tool result becomes a plain `{status:"pending", action_id, summary}`. The agent emits whatever it would naturally emit (a sentence or two of natural-language wrap-up). The card stands on its own next to that.

```
Before:                                       After:
agent → tool ──→ bridge gates                 agent → tool ──→ bridge gates
agent ←── nanormm_card envelope ── bridge      bridge ──→ POST nanoclaw inject endpoint
agent → emit envelope verbatim                       └→ nanoclaw enqueues outbound
       (FAILS)                                        └→ chat-sdk-bridge → Slack card
chat-sdk → render card → Slack                bridge ←── 202 ── nanoclaw
                                              agent ←── {status:pending,id,summary}
                                              agent → "I'll check on that approval" (plain text)

click → chat-sdk → handler                    click → chat-sdk → handler  (UNCHANGED)
handler → bridge /actions/execute/            handler → bridge /actions/execute/
```

## Architecture decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Card delivery topology | β: bridge → nanoclaw inject endpoint | Single Slack write path; reuses chat-sdk; no new Slack secret in bridge |
| 2 | Session-id plumbing | A: MCP request header `X-Nanoclaw-Session` | Pure plumbing; agent never sees session_id; one env var + one config line |
| 3 | Endpoint shape | Shape-1: `POST /internal/sessions/<sid>/inject-card` | RESTful; session_id in path makes audit-logging obvious |
| 4 | Endpoint auth | Auth-1: localhost-bound, no token | Bridge and nanoclaw co-located; kernel enforces |
| 5 | Endpoint placement | place-2: new `src/modules/nanormm-bridge/internal-server.ts` | Module already owns this subsystem boundary |
| 6 | Agent post-gating UX | agent-3: no prompt-enforcement | We just learned prompt-enforcement on this kind of constraint doesn't work |
| 7 | Reject wiring | reject-1: included in this plan | Pieces already exist; leaving rejected actions stuck in `pending` is a correctness gap |
| 8 | `nanormm_card` field | compat-1: remove cleanly | No other consumer in the qsrmm/nanormm tree |
| 9 | Failure ordering | fail-1: create approval → record audit → inject → raise on failure | Matches existing AUTO-branch failure shape; orphans TTL out |

## Data flow (after)

```
recon agent (in container)
  │
  ├─ tool call: kill_process(...)
  │   └─ MCP request to bridge, headers: X-Nanoclaw-Session: <sid>
  │       └─ bridge Dispatcher
  │            ├─ Authority.HUMAN_APPROVAL
  │            ├─ approvals.create() → action_id           (Redis)
  │            ├─ audit.record_pending(...)                (Postgres)
  │            ├─ inject_client.inject_card(sid, ...)
  │            │    └─ POST 127.0.0.1:<port>/internal/sessions/<sid>/inject-card
  │            │         └─ nanoclaw writes row to outbound.db
  │            │              └─ delivery.ts polls, hands to chat-sdk-bridge
  │            │                   └─ Slack chat.postMessage (in-thread, has buttons)
  │            └─ return {status:"pending", action_id, summary}
  └─ tool result lands back in agent; model emits whatever it naturally would

[user clicks Approve in Slack thread]
  │
  Slack interaction → nanoclaw webhook → chat-sdk-bridge.onAction
    └─ matches `ncq:nrmact-<id>:<idx>` → resolves option → fires registered handler
         └─ nanormm-bridge response handler matches `nrmact-` prefix
              └─ POST bridge /api/nanoclaw/actions/execute/    (UNCHANGED)

[user clicks Reject]
  │
  same path → nanormm-bridge handler now ALSO calls
              POST bridge /api/nanoclaw/actions/reject/        (NEW in this plan)
```

## Component-level designs

### Bridge (`qsrmm/nanormm/`)

**NEW — `approval-bridge/approval_bridge/inject_client.py`** (~40 LOC)

Single responsibility: HTTP POST to nanoclaw, raise on non-2xx, no retries (fail-fast).

```python
import httpx

class InjectClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def inject_card(
        self, *, session_id: str, question_id: str,
        title: str, question: str, options: list[dict],
    ) -> None:
        url = f"{self._base}/internal/sessions/{session_id}/inject-card"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(url, json={
                "questionId": question_id, "title": title,
                "question": question, "options": options,
            })
            r.raise_for_status()
```

**MODIFY — `trmm-mcp/trmm_mcp/tools/_base.py`**

- Delete `_build_approval_card_envelope`.
- Dispatcher constructor gains optional `inject_client`. (Optional so the stdio path of trmm-mcp keeps working without bridge wiring.)
- `dispatch` signature gains `session_id: str | None = None` kwarg.
- HUMAN_APPROVAL branch: after `record_pending`, if `inject_client is not None`: require session_id, call `inject_client.inject_card(...)`, return success-shape if it succeeds, raise `PolicyError` otherwise.
- Response no longer includes `nanormm_card`.

**MODIFY — `approval-bridge/approval_bridge/mcp_app.py`**

Add Starlette ASGI middleware that reads `X-Nanoclaw-Session` from request scope and stores it in a `contextvars.ContextVar` for the duration of the request:

```python
from contextvars import ContextVar
SESSION_ID_VAR: ContextVar[str | None] = ContextVar("nanoclaw_session_id", default=None)

class _SessionHeaderMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            sid = headers.get(b"x-nanoclaw-session")
            token = SESSION_ID_VAR.set(sid.decode() if sid else None)
            try:
                await self.app(scope, receive, send)
            finally:
                SESSION_ID_VAR.reset(token)
        else:
            await self.app(scope, receive, send)
```

Wrap the MCP Starlette app with this middleware in `build_mcp_starlette_app`.

**MODIFY — `trmm-mcp/trmm_mcp/server.py`**

Where the MCP tool handler dispatches each call, read `SESSION_ID_VAR.get()` from `mcp_app` and pass it as `session_id=` to `Dispatcher.dispatch`.

**MODIFY — `approval-bridge/approval_bridge/settings.py`**

Add `nanoclaw_internal_url: str = Field(default="http://127.0.0.1:8765", alias="NANOCLAW_INTERNAL_URL")`.

**MODIFY — `approval-bridge/approval_bridge/deps.py`**

Construct `InjectClient(settings.nanoclaw_internal_url)` and pass it into `build_dispatcher_for_bridge`.

### nanoclaw fork (`nanormm-nanoclaw`)

**NEW — `src/modules/nanormm-bridge/internal-server.ts`** (~80 LOC)

Bind `127.0.0.1:<port>` (default 8765, env-overridable). Single route: `POST /internal/sessions/:id/inject-card`. Validates session exists, opens session's outbound.db, writes a `kind:'normal'` row with content `{type:'ask_question', questionId, title, question, options}` and routing fields copied from session metadata. Returns 202 with the message id, or 404 for unknown session, or 400 for malformed body.

**MODIFY — `src/modules/nanormm-bridge/index.ts`**

- Import and start the internal server at module load.
- Add the reject branch:

```typescript
else /* value !== 'approve' */ {
  if (!API_KEY) { log.error('NANORMM_BRIDGE_API_KEY missing; cannot reject', { actionId }); return true; }
  try {
    const res = await fetch(`${BRIDGE_URL}/api/nanoclaw/actions/reject/`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${API_KEY}`,
        'Content-Type': 'application/json',
        'X-Slack-User-ID': userId,
      },
      body: JSON.stringify({ token: actionId, reason: 'declined via Slack' }),
    });
    const body = (await res.json()) as Record<string, unknown>;
    if (res.ok) log.info('nanormm action rejected', { actionId, userId, message: body.message });
    else log.warn('nanormm action rejection failed', { actionId, status: res.status, error: body.error });
  } catch (err) { log.error('nanormm bridge reject errored', { actionId, err }); }
  return true;
}
```

**MODIFY — `src/container-runner.ts`**

Inject `NANOCLAW_SESSION_ID=<session.id>` into the spawned container's env. Wire that env var into the MCP server config the agent uses to talk to the bridge so it surfaces as the `X-Nanoclaw-Session` header on every MCP request.

### Recon system prompt (`/opt/nanoclaw/groups/recon/CLAUDE.local.md`)

Strip the entire OUTPUT CONTRACT section about emitting `nanormm_card` verbatim. Replace with a single short paragraph:

> When a tool call returns `status:'pending'`, an approval card has been posted to the user automatically. Continue your turn naturally — you don't need to do anything to make the card appear.

## Files to touch

| Repo | File | Change |
|---|---|---|
| qsrmm/nanormm | `trmm-mcp/trmm_mcp/tools/_base.py` | MOD: drop envelope helper, add inject step, signature change |
| qsrmm/nanormm | `trmm-mcp/trmm_mcp/server.py` | MOD: read session_id contextvar, pass to dispatch |
| qsrmm/nanormm | `approval-bridge/approval_bridge/inject_client.py` | NEW: httpx client |
| qsrmm/nanormm | `approval-bridge/approval_bridge/settings.py` | MOD: add `NANOCLAW_INTERNAL_URL` |
| qsrmm/nanormm | `approval-bridge/approval_bridge/deps.py` | MOD: wire inject client |
| qsrmm/nanormm | `approval-bridge/approval_bridge/mcp_app.py` | MOD: session-header middleware + contextvar |
| nanormm-nanoclaw | `src/modules/nanormm-bridge/internal-server.ts` | NEW: localhost HTTP server |
| nanormm-nanoclaw | `src/modules/nanormm-bridge/index.ts` | MOD: start internal server, wire reject |
| nanormm-nanoclaw | `src/container-runner.ts` | MOD: inject NANOCLAW_SESSION_ID + MCP header config |
| ops (VM only) | `/opt/nanoclaw/groups/recon/CLAUDE.local.md` | REPLACE OUTPUT CONTRACT section |

## Failure modes

| Scenario | Behavior |
|---|---|
| Bridge can't reach nanoclaw inject endpoint | httpx raises → Dispatcher raises PolicyError → MCP tool returns error to agent → agent reports failure to user; orphan Redis key TTLs in 30 min; orphan audit row stays pending |
| Inject endpoint returns 404 (unknown session) | Same as above |
| Inject succeeds but Slack delivery fails | Existing delivery.ts retry/failure path applies (3 attempts → mark_failed); user sees no card; action TTL expires |
| Click reaches bridge with stale token | Bridge returns 404 (existing); "Action expired or unknown" |
| User clicks Reject, bridge `/reject/` 404s | Existing 404 handling; chat-sdk-bridge has already updated the card visually |
| Two clicks race on same action | First wins (mark_approved atomic in Redis); second sees `executed` and gets cached "Executed: ..." |
| nanoclaw restarts mid-flight | Card row already written to outbound.db; pending_questions row inserted at delivery time; restart resumes delivery; click flow still works |
| Bridge restarts mid-flight | Bridge stateless on restart; Redis + Postgres survive; click POSTs to bridge land on the restarted process |

## Audit & observability

No `nanormm_actions` schema change. Existing pending/approved/rejected/executed columns continue to populate.

Add one structured log line per inject failure: `event=inject_failed action_id=... session_id=... err=...`. Currently no specific category exists for "card never appeared"; this makes failures grep-able in journalctl.

## Security

- Localhost-only bind on the inject endpoint — kernel-enforced isolation.
- No new outbound network surface from the bridge.
- No new secrets. `SLACK_BOT_TOKEN` stays in nanoclaw's env only; bridge never sees it.
- Reject path uses the existing `NANORMM_BRIDGE_API_KEY` bearer.

## Out of scope

- Multi-host (bridge on a different VM than nanoclaw) — defer; would add bearer auth + cert validation.
- General-purpose nanoclaw "inject any message" API — the endpoint is single-purpose.
- Click attribution beyond `X-Slack-User-ID`.

## Implementation risks (validate FIRST)

Two ~30-minute spikes to run before writing production code:

1. **MCP header pass-through.** Bring up the bridge locally, hit `/mcp/` with `X-Nanoclaw-Session: spike-1`, log `SESSION_ID_VAR.get()` from inside the tool handler. Confirm the value lands. If not, fall back to a tool-args-based approach (option B from question 2 of brainstorm).
2. **Session schema.** `sqlite3 /opt/nanoclaw/data/v2.db '.schema sessions'` and `.schema messaging_groups` to read the actual column names so internal-server.ts can build the right outbound row.

If both spikes pass, the rest is straightforward plumbing.

## Test plan

### Unit

| Component | File | Cases |
|---|---|---|
| `inject_client.py` | `approval-bridge/tests/test_inject_client.py` | Success path posts correct body+url; non-2xx raises; httpx network error propagates |
| Dispatcher | `trmm-mcp/tests/tools/test_dispatcher_inject.py` | HUMAN_APPROVAL with inject wired calls inject before returning; missing session_id raises PolicyError; inject failure leaves audit row pending and surfaces error; `nanormm_card` field absent |
| Dispatcher (no-inject) | `trmm-mcp/tests/tools/test_dispatcher_no_inject.py` | stdio path (inject_client=None) on AUTO/FORBIDDEN unchanged |
| Session middleware | `approval-bridge/tests/test_session_header.py` | Middleware sets contextvar from `X-Nanoclaw-Session`; absent → None; resets after request |
| Reject path | `nanormm-nanoclaw/src/modules/nanormm-bridge/index.test.ts` | Reject value POSTs to `/actions/reject/`; missing API_KEY logs and short-circuits; non-2xx logged |

### Integration

| Layer | File | Cases |
|---|---|---|
| Bridge full path | `approval-bridge/tests/test_inject_integration.py` | Spin up FastAPI test client, mock httpx for inject endpoint, fire MCP `kill_process` call → assert: inject called with correct payload, response shape `{status:pending, action_id, summary}`, audit row exists, approval row exists |
| nanoclaw internal-server | `nanormm-nanoclaw/src/modules/nanormm-bridge/internal-server.test.ts` | Real http.createServer on ephemeral port: POST inject-card → outbound.db has correct row; unknown session → 404; bad JSON → 400 |

### End-to-end smoke (replaces Plan 3 Task 15)

Manual on the VM:

1. Restart bridge + nanoclaw with new code deployed.
2. `@recon kill PID 1234 on host foo because synthetic test`.
3. Within ~5s, Slack card appears in same thread, title "Pending action", Approve/Reject buttons.
4. Click Approve → audit row gets `executed_at`; card edits to "✅ Approved".
5. Repeat with fresh alert; click Reject → audit row gets `rejected_at`; card edits to "❌ Rejected".
6. Tail journalctl for both services — no errors, no "unclaimed action".

If all six pass, Plan 3 Task 15 is closed and Plan 4 is done.
