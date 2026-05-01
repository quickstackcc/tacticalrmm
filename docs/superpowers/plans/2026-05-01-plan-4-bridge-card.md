# Plan 4 — Bridge-side approval card emission

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move human-approval card emission out of the LLM agent. The bridge tells nanoclaw to inject the card into the session's outbound queue; nanoclaw's existing chat-sdk → Slack pipeline delivers it; the click flow is unchanged.

**Architecture:** Bridge gains an `InjectClient` that POSTs to a new localhost-only HTTP endpoint inside nanoclaw's `nanormm-bridge` module. The endpoint validates the session, then writes a `kind:'normal'` row with `{type:'ask_question', ...}` content to the session's outbound.db. session_id flows from container env → MCP request header (`X-Nanoclaw-Session`) → Starlette middleware contextvar → Dispatcher kwarg.

**Tech Stack:**
- Python 3.11 / FastAPI / pytest / fakeredis / pytest-postgresql / respx (bridge + trmm-mcp)
- Node.js / TypeScript / vitest / better-sqlite3 (nanoclaw fork)

**Spec:** [`docs/superpowers/specs/2026-04-30-bridge-side-card-emission.md`](../specs/2026-04-30-bridge-side-card-emission.md)

**Working directories:**
- qsrmm/nanormm changes: `/home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon` (branch `feat/nanormm-recon`)
- nanoclaw fork changes: `/home/jim/quickstack-cc/nanormm-nanoclaw` (branch `main`)
- Recon prompt change: `/opt/nanoclaw/groups/recon/CLAUDE.local.md` on the VM (ops-only; not in git)

---

## Phase 0 — Validate risks before writing code

### Task 0.1: Spike — MCP header pass-through

**Goal:** Confirm Starlette middleware can set a `ContextVar` from a request header that the MCP tool handler then reads. If this works, the production design proceeds. If not, we fall back to tool-args plumbing (option B from spec brainstorm question 2).

**Files:**
- Create: `/tmp/spike_session_header.py` (one-shot script, not committed)

- [ ] **Step 1: Create the spike script**

```python
# /tmp/spike_session_header.py
"""Spike: prove ContextVar set by Starlette middleware reaches MCP tool handler."""
import asyncio
from contextvars import ContextVar

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import Server
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent
from starlette.applications import Starlette
from starlette.routing import Mount, Route

SID: ContextVar[str | None] = ContextVar("sid", default=None)


class HeaderMW:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            sid = headers.get(b"x-nanoclaw-session")
            tok = SID.set(sid.decode() if sid else None)
            try:
                await self.app(scope, receive, send)
            finally:
                SID.reset(tok)
        else:
            await self.app(scope, receive, send)


server = Server("spike")


@server.call_tool()
async def call_tool(name, arguments):
    seen = SID.get()
    return [TextContent(type="text", text=f"sid={seen!r}")]


sm = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)
asgi = StreamableHTTPASGIApp(sm)

import contextlib
@contextlib.asynccontextmanager
async def lifespan(_):
    async with sm.run():
        yield

mcp_app = Starlette(routes=[Route("/", endpoint=asgi)], lifespan=lifespan)
mcp_app = HeaderMW(mcp_app)

app = FastAPI()
app.routes.append(Mount("/mcp", app=mcp_app))

if __name__ == "__main__":
    with TestClient(app) as c:
        r = c.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "spike", "version": "0"}}},
            headers={"accept": "application/json, text/event-stream",
                     "content-type": "application/json",
                     "x-nanoclaw-session": "spike-1"},
        )
        sid = r.headers.get("mcp-session-id")
        h2 = {"accept": "application/json, text/event-stream",
              "content-type": "application/json",
              "x-nanoclaw-session": "spike-1"}
        if sid:
            h2["mcp-session-id"] = sid
        r2 = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "spike", "arguments": {}}},
                    headers=h2)
        print("STATUS", r2.status_code)
        print("BODY", r2.text)
```

- [ ] **Step 2: Run the spike from the worktree's bridge venv**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/approval-bridge
.venv/bin/python /tmp/spike_session_header.py
```

If the venv doesn't exist:

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/approval-bridge
python3.11 -m venv .venv
.venv/bin/pip install -e . pytest pytest-asyncio fakeredis pytest-postgresql respx httpx
```

Expected: STATUS 200 (or 202). BODY contains `"sid='spike-1'"`. If BODY shows `sid=None`, the contextvar didn't survive — go to fallback.

- [ ] **Step 3: Record finding**

Append a single line to `/home/jim/quickstack-cc/qsrmm/docs/superpowers/handoffs/2026-05-01-plan4-spikes.md` (create the file if absent):

```markdown
## Spike 0.1: MCP header pass-through
- Result: PASS / FAIL
- BODY observed: `<paste relevant excerpt>`
- Decision: proceed with middleware+contextvar / fall back to tool-args plumbing
```

If FAIL, **stop the plan** and convert all `session_id` plumbing tasks to the tool-args fallback before proceeding.

---

### Task 0.2: Spike — session schema lookup

**Goal:** Read the actual column names of the `sessions` and `messaging_groups` tables in nanoclaw's sqlite so internal-server.ts knows the correct field names to copy into outbound.db.

**Files:** No code; read-only schema inspection.

- [ ] **Step 1: SSH to the VM**

```bash
gcloud compute ssh rmm --project=qsrmm-494222 --zone=us-central1-a --tunnel-through-iap
```

- [ ] **Step 2: Dump schema**

```bash
sudo -u nanoclaw sqlite3 /opt/nanoclaw/data/v2.db <<'SQL'
.schema sessions
.schema messaging_groups
.schema messaging_group_agents
SQL
```

- [ ] **Step 3: Record observed columns**

Append to the same handoff file:

```markdown
## Spike 0.2: Session schema
- sessions columns: <paste>
- messaging_groups columns: <paste>
- The fields needed by internal-server.ts to populate an outbound row:
  - platform_id source: <e.g. messaging_groups.platform_id via session.messaging_group_id>
  - channel_type source: <...>
  - thread_id source: <...>
```

The spec calls these `origin_platform_id` / `origin_channel_type` / `origin_thread_id` — the actual names will replace those placeholders in Task 5.1 below.

- [ ] **Step 4: Commit the spike notes**

```bash
cd /home/jim/quickstack-cc/qsrmm
git add docs/superpowers/handoffs/2026-05-01-plan4-spikes.md
git commit -m "$(cat <<'EOF'
handoff: Plan 4 spike results — header pass-through and session schema

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 1 — Bridge: session-id plumbing through MCP

All Phase 1 tasks happen in `/home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon`. Always run pytest from `nanormm/approval-bridge/` or `nanormm/trmm-mcp/` as appropriate.

### Task 1.1: SESSION_ID_VAR contextvar and Starlette middleware

**Files:**
- Modify: `nanormm/approval-bridge/approval_bridge/mcp_app.py`
- Test: `nanormm/approval-bridge/tests/test_session_header.py` (create)

- [ ] **Step 1: Write the failing test**

Create `nanormm/approval-bridge/tests/test_session_header.py`:

```python
"""Tests for the X-Nanoclaw-Session middleware and contextvar."""
import pytest
from fastapi.testclient import TestClient

from approval_bridge.mcp_app import SESSION_ID_VAR, _SessionHeaderMiddleware


def test_session_header_middleware_sets_contextvar(monkeypatch):
    captured: list[str | None] = []

    async def inner(scope, receive, send):
        captured.append(SESSION_ID_VAR.get())
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    app = _SessionHeaderMiddleware(inner)

    from starlette.applications import Starlette
    from starlette.routing import Route

    async def endpoint(request):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("ok")

    starlette_app = Starlette(routes=[Route("/", endpoint=endpoint)])
    wrapped = _SessionHeaderMiddleware(starlette_app)

    captured.clear()

    async def capture(scope, receive, send):
        captured.append(SESSION_ID_VAR.get())
        await starlette_app(scope, receive, send)

    wrapped_with_capture = _SessionHeaderMiddleware(capture)

    with TestClient(wrapped_with_capture) as client:
        r = client.get("/", headers={"X-Nanoclaw-Session": "sess-abc"})
        assert r.status_code == 200
    assert captured == ["sess-abc"]


def test_session_header_middleware_absent_yields_none():
    captured: list[str | None] = []

    async def capture(scope, receive, send):
        captured.append(SESSION_ID_VAR.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = _SessionHeaderMiddleware(capture)

    with TestClient(wrapped) as client:
        r = client.get("/")
        assert r.status_code == 200
    assert captured == [None]


def test_session_header_resets_after_request():
    """ContextVar must reset after the request, not leak across requests."""
    async def capture(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = _SessionHeaderMiddleware(capture)

    with TestClient(wrapped) as client:
        client.get("/", headers={"X-Nanoclaw-Session": "sess-xyz"})
        # After the request, outside the middleware, contextvar should be default
        assert SESSION_ID_VAR.get() is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/approval-bridge
.venv/bin/pytest tests/test_session_header.py -v
```

Expected: FAIL with `ImportError: cannot import name 'SESSION_ID_VAR' from 'approval_bridge.mcp_app'` (or similar).

- [ ] **Step 3: Add the contextvar and middleware to mcp_app.py**

Edit `nanormm/approval-bridge/approval_bridge/mcp_app.py`. Add at the top of the file (after the existing module docstring and imports):

```python
from contextvars import ContextVar

# Set by _SessionHeaderMiddleware on each HTTP request, read by trmm-mcp's
# call_tool handler so the Dispatcher knows which nanoclaw session to inject
# the approval card into.
SESSION_ID_VAR: ContextVar[str | None] = ContextVar(
    "nanoclaw_session_id", default=None
)


class _SessionHeaderMiddleware:
    """ASGI middleware that copies X-Nanoclaw-Session into SESSION_ID_VAR
    for the duration of each HTTP request, resetting on completion.
    """

    def __init__(self, app):
        self.app = app

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

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_session_header.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon
git add nanormm/approval-bridge/approval_bridge/mcp_app.py nanormm/approval-bridge/tests/test_session_header.py
git commit -m "$(cat <<'EOF'
approval-bridge: add SESSION_ID_VAR contextvar and X-Nanoclaw-Session middleware

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.2: Wire `_SessionHeaderMiddleware` into `build_mcp_starlette_app`

**Files:**
- Modify: `nanormm/approval-bridge/approval_bridge/mcp_app.py`
- Modify: `nanormm/approval-bridge/tests/test_mcp_endpoint.py` (add header propagation test)

- [ ] **Step 1: Write the failing test**

Append to `nanormm/approval-bridge/tests/test_mcp_endpoint.py`:

```python
def test_mcp_endpoint_sets_session_contextvar(bridge_settings, monkeypatch):
    """X-Nanoclaw-Session on /mcp/ requests must reach SESSION_ID_VAR."""
    from approval_bridge.app import create_app
    from approval_bridge.mcp_app import SESSION_ID_VAR

    captured: list[str | None] = []

    # Patch the dispatcher's dispatch so we can see what session_id arrives.
    # We don't actually call any tool — we read the contextvar inside dispatch.
    from trmm_mcp.tools._base import Dispatcher
    orig_dispatch = Dispatcher.dispatch

    async def spying_dispatch(self, name, args, *, summary="", session_id=None):
        captured.append(SESSION_ID_VAR.get())
        return {"status": "executed", "result": None}

    monkeypatch.setattr(Dispatcher, "dispatch", spying_dispatch)

    app = create_app(bridge_settings)
    with TestClient(app) as client:
        # Initialize
        init = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "test", "version": "0"}}},
            headers={"accept": "application/json, text/event-stream",
                     "content-type": "application/json",
                     "x-nanoclaw-session": "session-77"},
        )
        sid = init.headers.get("mcp-session-id")
        h = {"accept": "application/json, text/event-stream",
             "content-type": "application/json",
             "x-nanoclaw-session": "session-77"}
        if sid:
            h["mcp-session-id"] = sid

        # Call any registered tool — list_alerts is auto in conftest's policy.
        client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "list_alerts", "arguments": {}}},
            headers=h,
        )
    assert captured == ["session-77"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_mcp_endpoint.py::test_mcp_endpoint_sets_session_contextvar -v
```

Expected: FAIL with `assert captured == ["session-77"]` (captured is `[None]` because middleware isn't wired).

- [ ] **Step 3: Wire the middleware in `build_mcp_starlette_app`**

Edit `nanormm/approval-bridge/approval_bridge/mcp_app.py`. Find the `return Starlette(...)` at the bottom of `build_mcp_starlette_app` and replace it:

```python
    starlette_app = Starlette(
        routes=[Route("/", endpoint=asgi_handler)],
        lifespan=lifespan,
    )
    # Wrap so SESSION_ID_VAR is populated before the MCP handler runs.
    return _SessionHeaderMiddleware(starlette_app)
```

If `build_mcp_starlette_app`'s annotated return type is `Starlette`, change it to `ASGIApp`:

```python
from starlette.types import ASGIApp


def build_mcp_starlette_app() -> ASGIApp:
    ...
```

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_mcp_endpoint.py -v
```

Expected: all tests pass (existing 2 + the new one).

- [ ] **Step 5: Commit**

```bash
git add nanormm/approval-bridge/approval_bridge/mcp_app.py nanormm/approval-bridge/tests/test_mcp_endpoint.py
git commit -m "$(cat <<'EOF'
approval-bridge: wrap MCP app with X-Nanoclaw-Session middleware

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1.3: server.py reads SESSION_ID_VAR and passes session_id to dispatch

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/server.py`

This task is a one-liner change in `trmm-mcp` so the lowlevel Server's `call_tool` handler reads the contextvar populated by the bridge's middleware and threads it into `Dispatcher.dispatch`. Note: trmm-mcp's stdio path won't have this middleware, but `SESSION_ID_VAR.get()` returns `None` by default, which is fine — the dispatcher's `inject_client` is also `None` in stdio mode, so neither is consulted.

- [ ] **Step 1: Read current shape**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/trmm-mcp
sed -n '50,70p' trmm_mcp/server.py
```

Confirm the call_tool handler currently does `result = await dispatcher.dispatch(name, arguments)` with no session_id.

- [ ] **Step 2: Add the session_id pass-through**

Edit `nanormm/trmm-mcp/trmm_mcp/server.py`. Find the `_call_tool` async function and modify it to import the contextvar lazily (so trmm-mcp doesn't gain a hard dep on approval_bridge) and read it:

```python
@mcp.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    # Read X-Nanoclaw-Session populated by the bridge's middleware.
    # Lazy import: trmm-mcp's stdio path doesn't have approval_bridge installed,
    # so try/except keeps the stdio path independent.
    session_id: str | None = None
    try:
        from approval_bridge.mcp_app import SESSION_ID_VAR  # type: ignore
        session_id = SESSION_ID_VAR.get()
    except ImportError:
        pass

    result = await dispatcher.dispatch(name, arguments, session_id=session_id)
    return [TextContent(type="text", text=json.dumps(result))]
```

(If the existing handler returns `result` as JSON differently, preserve the existing serialization — only add the `session_id=session_id` kwarg.)

- [ ] **Step 3: Stage the change but DO NOT run tests or commit yet**

```bash
git add nanormm/trmm-mcp/trmm_mcp/server.py
# Tests will fail until Task 3.1 makes Dispatcher accept session_id.
# This change is committed together with Task 3.1.
```

The reason: `dispatcher.dispatch(name, arguments, session_id=session_id)` will fail with `TypeError: unexpected keyword 'session_id'` until Task 3.1 lands. The full trmm-mcp test run happens at the end of Task 3.1 step 4.

---

## Phase 2 — Bridge: InjectClient + settings

### Task 2.1: InjectClient

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/inject_client.py`
- Test: `nanormm/approval-bridge/tests/test_inject_client.py`

- [ ] **Step 1: Write the failing test**

Create `nanormm/approval-bridge/tests/test_inject_client.py`:

```python
import httpx
import pytest
import respx

from approval_bridge.inject_client import InjectClient


@pytest.mark.asyncio
async def test_inject_card_posts_correct_payload():
    client = InjectClient("http://127.0.0.1:8765")

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("http://127.0.0.1:8765/internal/sessions/sess-1/inject-card").mock(
            return_value=httpx.Response(202, json={"accepted": True, "messageId": "m-1"})
        )
        await client.inject_card(
            session_id="sess-1",
            question_id="nrmact-act_xyz",
            title="Pending action",
            question="Kill PID 1234",
            options=[
                {"label": "Approve", "selectedLabel": "✅ Approved", "value": "approve"},
                {"label": "Reject", "selectedLabel": "❌ Rejected", "value": "reject"},
            ],
        )
        assert route.called
        sent = route.calls.last.request
        import json as _json
        body = _json.loads(sent.content)
        assert body == {
            "questionId": "nrmact-act_xyz",
            "title": "Pending action",
            "question": "Kill PID 1234",
            "options": [
                {"label": "Approve", "selectedLabel": "✅ Approved", "value": "approve"},
                {"label": "Reject", "selectedLabel": "❌ Rejected", "value": "reject"},
            ],
        }


@pytest.mark.asyncio
async def test_inject_card_raises_on_non_2xx():
    client = InjectClient("http://127.0.0.1:8765")
    with respx.mock() as mock:
        mock.post("http://127.0.0.1:8765/internal/sessions/bad/inject-card").mock(
            return_value=httpx.Response(404, json={"error": "unknown session"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.inject_card(
                session_id="bad", question_id="x", title="t", question="q", options=[]
            )


@pytest.mark.asyncio
async def test_inject_card_propagates_network_error():
    client = InjectClient("http://127.0.0.1:8765", timeout=0.1)
    with respx.mock() as mock:
        mock.post("http://127.0.0.1:8765/internal/sessions/sess-1/inject-card").mock(
            side_effect=httpx.ConnectError("nope")
        )
        with pytest.raises(httpx.ConnectError):
            await client.inject_card(
                session_id="sess-1", question_id="x", title="t", question="q", options=[]
            )


def test_base_url_trailing_slash_is_normalized():
    c = InjectClient("http://127.0.0.1:8765/")
    assert c._base == "http://127.0.0.1:8765"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/approval-bridge
.venv/bin/pytest tests/test_inject_client.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'approval_bridge.inject_client'`.

- [ ] **Step 3: Implement InjectClient**

Create `nanormm/approval-bridge/approval_bridge/inject_client.py`:

```python
"""HTTP client that posts approval cards into nanoclaw's internal endpoint.

Used by the Dispatcher's HUMAN_APPROVAL branch: after gating an action and
recording the audit row, the dispatcher tells nanoclaw to post a Slack card
into the originating session's outbound queue. nanoclaw's existing chat-sdk
delivery path then renders the card and handles button clicks.
"""

from typing import Any

import httpx


class InjectClient:
    """Single-purpose HTTP client for /internal/sessions/<sid>/inject-card.

    No retries: failure raises and the dispatcher surfaces it to the agent.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def inject_card(
        self,
        *,
        session_id: str,
        question_id: str,
        title: str,
        question: str,
        options: list[dict[str, Any]],
    ) -> None:
        url = f"{self._base}/internal/sessions/{session_id}/inject-card"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                url,
                json={
                    "questionId": question_id,
                    "title": title,
                    "question": question,
                    "options": options,
                },
            )
            r.raise_for_status()
```

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_inject_client.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/approval-bridge/approval_bridge/inject_client.py nanormm/approval-bridge/tests/test_inject_client.py
git commit -m "$(cat <<'EOF'
approval-bridge: add InjectClient — POSTs approval cards to nanoclaw

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2.2: Settings — NANOCLAW_INTERNAL_URL

**Files:**
- Modify: `nanormm/approval-bridge/approval_bridge/settings.py`
- Modify: `nanormm/approval-bridge/tests/test_settings.py` (or create if absent)

- [ ] **Step 1: Write the failing test**

Append to `nanormm/approval-bridge/tests/test_settings.py` (creating it if it doesn't exist; check first with `ls`):

```python
def test_nanoclaw_internal_url_default(monkeypatch):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "x")
    from approval_bridge.settings import BridgeSettings
    s = BridgeSettings()
    assert s.nanoclaw_internal_url == "http://127.0.0.1:8765"


def test_nanoclaw_internal_url_overridable(monkeypatch):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "x")
    monkeypatch.setenv("NANOCLAW_INTERNAL_URL", "http://example.local:9999")
    from approval_bridge.settings import BridgeSettings
    s = BridgeSettings()
    assert s.nanoclaw_internal_url == "http://example.local:9999"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_settings.py -v
```

Expected: FAIL with `AttributeError: 'BridgeSettings' object has no attribute 'nanoclaw_internal_url'` (or similar).

- [ ] **Step 3: Add the field**

Edit `nanormm/approval-bridge/approval_bridge/settings.py`. Inside `class BridgeSettings`, after the `mcp_path` line, add:

```python
    nanoclaw_internal_url: str = Field(
        default="http://127.0.0.1:8765",
        alias="NANOCLAW_INTERNAL_URL",
    )
```

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_settings.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/approval-bridge/approval_bridge/settings.py nanormm/approval-bridge/tests/test_settings.py
git commit -m "$(cat <<'EOF'
approval-bridge: add NANOCLAW_INTERNAL_URL setting (default http://127.0.0.1:8765)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Bridge: Dispatcher integration

### Task 3.1: Dispatcher gains inject_client and session_id; HUMAN_APPROVAL branch calls inject

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test__base.py`

- [ ] **Step 1: Write the failing tests**

Append to `nanormm/trmm-mcp/tests/tools/test__base.py`:

```python
class _StubInjectClient:
    """Records calls; raises if instructed."""

    def __init__(self, raise_exc: Exception | None = None):
        self.calls: list[dict] = []
        self.raise_exc = raise_exc

    async def inject_card(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc


@pytest.fixture
def gated_env_with_inject(fake_redis, audit_dsn, tmp_path):
    """Same as registry_env but Dispatcher has an InjectClient stub wired."""
    from trmm_mcp.approvals import ApprovalRegistry
    from trmm_mcp.audit import AuditLog
    from trmm_mcp.policy import Policy
    from trmm_mcp.tools._base import Dispatcher, ToolRegistry

    pol_path = tmp_path / "p.yaml"
    pol_path.write_text(
        """
version: 1
default: human_approval
tools:
  always_gated: human_approval
"""
    )
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    inject = _StubInjectClient()
    dispatcher = Dispatcher(
        registry=registry,
        policy=pol,
        approvals=approvals,
        audit=audit,
        inject_client=inject,
    )
    return registry, dispatcher, inject


@pytest.mark.asyncio
async def test_gated_tool_with_inject_calls_inject(gated_env_with_inject):
    registry, dispatcher, inject = gated_env_with_inject

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x * 10

    result = await dispatcher.dispatch(
        "always_gated", {"x": 5}, summary="Multiply 5 by 10", session_id="sess-99"
    )
    assert result["status"] == "pending"
    assert result["action_id"].startswith("act_")
    assert result["summary"] == "Multiply 5 by 10"
    assert "nanormm_card" not in result

    assert len(inject.calls) == 1
    call = inject.calls[0]
    assert call["session_id"] == "sess-99"
    assert call["question_id"] == f"nrmact-{result['action_id']}"
    assert call["title"] == "Pending action"
    assert call["question"] == "Multiply 5 by 10"
    assert call["options"] == [
        {"label": "Approve", "selectedLabel": "✅ Approved", "value": "approve"},
        {"label": "Reject", "selectedLabel": "❌ Rejected", "value": "reject"},
    ]


@pytest.mark.asyncio
async def test_gated_tool_with_inject_missing_session_id_raises(gated_env_with_inject):
    from trmm_mcp.exceptions import PolicyError

    registry, dispatcher, _inject = gated_env_with_inject

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    with pytest.raises(PolicyError, match="session_id"):
        await dispatcher.dispatch("always_gated", {}, summary="x", session_id=None)


@pytest.mark.asyncio
async def test_gated_tool_inject_failure_raises(gated_env_with_inject):
    import httpx

    registry, dispatcher, inject = gated_env_with_inject
    inject.raise_exc = httpx.ConnectError("nanoclaw down")

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    with pytest.raises(httpx.ConnectError):
        await dispatcher.dispatch(
            "always_gated", {}, summary="x", session_id="sess-99"
        )


@pytest.mark.asyncio
async def test_gated_tool_no_inject_client_works(registry_env):
    """When inject_client=None (stdio mode), HUMAN_APPROVAL still works
    and returns plain pending response without nanormm_card."""
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    result = await dispatcher.dispatch("always_gated", {}, summary="x")
    assert result["status"] == "pending"
    assert "nanormm_card" not in result
```

Also UPDATE the existing `test_gated_tool_returns_pending_and_writes_audit` to no longer expect `nanormm_card`. Find that test and confirm/edit:

```python
async def test_gated_tool_returns_pending_and_writes_audit(registry_env, audit_dsn):
    import psycopg

    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x * 10

    result = await dispatcher.dispatch("always_gated", {"x": 5}, summary="Multiply 5 by 10")
    assert result["status"] == "pending"
    assert result["action_id"].startswith("act_")
    assert result["summary"] == "Multiply 5 by 10"
    assert "nanormm_card" not in result  # <-- new assertion
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/trmm-mcp
.venv/bin/pytest tests/tools/test__base.py -v
```

Expected: failures on the new tests (Dispatcher signature unchanged, doesn't accept `inject_client` or `session_id`); also `test_gated_tool_returns_pending_and_writes_audit` now fails because the existing impl returns `nanormm_card`.

- [ ] **Step 3: Modify Dispatcher**

Edit `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`:

1. **Delete** the `_build_approval_card_envelope` function (lines 38–68).
2. **Modify** `Dispatcher.__init__` to accept `inject_client`:

```python
class Dispatcher:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: Policy,
        approvals: ApprovalRegistry,
        audit: AuditLog,
        inject_client: Any | None = None,
    ):
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._inject = inject_client
```

3. **Modify** `Dispatcher.dispatch` to accept `session_id` and call inject:

```python
    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        summary: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        fn = self._registry.get(tool_name)
        if fn is None:
            raise PolicyError(f"unknown tool: {tool_name}")

        authority = self._policy.authority(tool_name)

        if authority is Authority.FORBIDDEN:
            return {"status": "denied", "reason": "tool is forbidden by policy"}

        if authority is Authority.AUTO:
            result = await fn(**args)
            return {"status": "executed", "result": result}

        # human_approval
        action_id = self._approvals.create(
            tool_name=tool_name, args=args, summary=summary
        )
        self._audit.record_pending(
            action_id=action_id,
            tool_name=tool_name,
            args=args,
            summary=summary,
            policy_decision=authority.value,
        )
        if self._inject is not None:
            if not session_id:
                raise PolicyError(
                    "session_id required for human_approval when inject_client wired"
                )
            await self._inject.inject_card(
                session_id=session_id,
                question_id=f"nrmact-{action_id}",
                title="Pending action",
                question=summary or "Action requires confirmation",
                options=[
                    {"label": "Approve", "selectedLabel": "✅ Approved",
                     "value": "approve"},
                    {"label": "Reject", "selectedLabel": "❌ Rejected",
                     "value": "reject"},
                ],
            )
        return {
            "status": "pending",
            "action_id": action_id,
            "summary": summary,
        }
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/pytest tests/tools/test__base.py -v
```

Expected: all tests pass (existing + 4 new).

- [ ] **Step 5: Commit (this commit also picks up Task 1.3's staged change)**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon
git add nanormm/trmm-mcp/trmm_mcp/tools/_base.py nanormm/trmm-mcp/tests/tools/test__base.py nanormm/trmm-mcp/trmm_mcp/server.py
git commit -m "$(cat <<'EOF'
trmm-mcp: Dispatcher accepts inject_client+session_id; calls inject on human_approval

Removes _build_approval_card_envelope and the nanormm_card field. The
HUMAN_APPROVAL branch now hands the card to nanoclaw via the optional
inject_client (None in stdio mode, set in bridge mode). server.py
threads session_id from the X-Nanoclaw-Session contextvar.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.2: deps.py wires InjectClient into the bridge's dispatcher

**Files:**
- Modify: `nanormm/approval-bridge/approval_bridge/deps.py`
- Modify: `nanormm/approval-bridge/tests/test_deps.py`

- [ ] **Step 1: Write the failing test**

Append to `nanormm/approval-bridge/tests/test_deps.py`:

```python
def test_dispatcher_has_inject_client_wired(bridge_settings):
    from approval_bridge.deps import build_dispatcher_for_bridge
    from approval_bridge.inject_client import InjectClient

    dispatcher = build_dispatcher_for_bridge(bridge_settings)
    assert isinstance(dispatcher._inject, InjectClient)
    assert dispatcher._inject._base == "http://127.0.0.1:8765"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/approval-bridge
.venv/bin/pytest tests/test_deps.py -v
```

Expected: FAIL — `dispatcher._inject` is `None`.

- [ ] **Step 3: Modify deps.py**

Edit `nanormm/approval-bridge/approval_bridge/deps.py`:

```python
from trmm_mcp.server import build_dispatcher as _build_dispatcher
from trmm_mcp.tools._base import Dispatcher

from .inject_client import InjectClient
from .settings import BridgeSettings


def build_dispatcher_for_bridge(settings: BridgeSettings) -> Dispatcher:
    """Construct the dispatcher with the inject_client wired in.

    The dispatcher built here is shared by both the bridge's MCP HTTP path
    (which sets X-Nanoclaw-Session header → contextvar → session_id) and
    the bridge's /api/nanoclaw/actions/execute/ path (which calls
    dispatcher.resume()). Only the HUMAN_APPROVAL branch consults
    inject_client; resume() doesn't.
    """
    dispatcher, _registry, _trmm = _build_dispatcher(settings.trmm)
    dispatcher._inject = InjectClient(settings.nanoclaw_internal_url)
    return dispatcher
```

- [ ] **Step 4: Run test to verify pass**

```bash
.venv/bin/pytest tests/test_deps.py -v
```

Expected: pass (plus existing tests still pass).

- [ ] **Step 5: Commit**

```bash
git add nanormm/approval-bridge/approval_bridge/deps.py nanormm/approval-bridge/tests/test_deps.py
git commit -m "$(cat <<'EOF'
approval-bridge: wire InjectClient into Dispatcher via deps

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3.3: Run the full bridge test suite

**Goal:** Catch any regressions from removing `nanormm_card` and changing the Dispatcher signature.

- [ ] **Step 1: Run trmm-mcp suite**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/trmm-mcp
.venv/bin/pytest -v
```

Expected: all green. If `tests/test_envelope.py` fails (it tests the removed function), delete the file:

```bash
git rm tests/test_envelope.py
```

- [ ] **Step 2: Run approval-bridge suite**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon/nanormm/approval-bridge
.venv/bin/pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit any cleanup**

```bash
cd /home/jim/quickstack-cc/qsrmm/.worktrees/nanormm-recon
git status
# If files staged from cleanup:
git commit -m "$(cat <<'EOF'
trmm-mcp: drop test_envelope (covers removed _build_approval_card_envelope)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If nothing to commit, skip.

---

## Phase 4 — Bridge: integration test

### Task 4.1: End-to-end inject integration test

**Files:**
- Create: `nanormm/approval-bridge/tests/test_inject_integration.py`

- [ ] **Step 1: Write the integration test**

Create `nanormm/approval-bridge/tests/test_inject_integration.py`:

```python
"""End-to-end: MCP tool call → dispatch → inject → response shape.

This test wires the real bridge app, mocks the inject HTTP target with respx,
and confirms a HUMAN_APPROVAL tool call POSTs to the inject endpoint with the
correct payload, persists the audit row, and returns the expected response.
"""
import httpx
import pytest
import respx
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_mcp_kill_process_call_injects_card_and_responds(
    bridge_settings, mock_trmm
):
    from approval_bridge.app import create_app

    # Mock both the upstream TRMM API (so the tool itself can dispatch its
    # downstream call if it gets that far — it shouldn't, gating happens first)
    # and nanoclaw's inject endpoint.
    with respx.mock(assert_all_called=False) as mock:
        inject_route = mock.post(
            "http://127.0.0.1:8765/internal/sessions/sess-77/inject-card"
        ).mock(return_value=httpx.Response(202, json={"accepted": True}))

        app = create_app(bridge_settings)
        with TestClient(app) as client:
            init = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"accept": "application/json, text/event-stream",
                         "content-type": "application/json",
                         "x-nanoclaw-session": "sess-77"},
            )
            sid = init.headers.get("mcp-session-id")
            h = {"accept": "application/json, text/event-stream",
                 "content-type": "application/json",
                 "x-nanoclaw-session": "sess-77"}
            if sid:
                h["mcp-session-id"] = sid

            r = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "kill_process",
                                 "arguments": {"agent_id": "a", "pid": 1234}}},
                headers=h,
            )
            assert r.status_code == 200

        assert inject_route.called
        body = inject_route.calls.last.request.read()
        import json as _json
        payload = _json.loads(body)
        assert payload["title"] == "Pending action"
        assert payload["questionId"].startswith("nrmact-act_")
        assert payload["options"][0]["value"] == "approve"
        assert payload["options"][1]["value"] == "reject"
```

- [ ] **Step 2: Run the test**

```bash
.venv/bin/pytest tests/test_inject_integration.py -v
```

Expected: pass. If it fails because `kill_process` requires more args than provided, adjust the `arguments` dict to match the actual tool schema (check `nanormm/trmm-mcp/trmm_mcp/tools/actions.py` or similar for the registered signature).

- [ ] **Step 3: Commit**

```bash
git add nanormm/approval-bridge/tests/test_inject_integration.py
git commit -m "$(cat <<'EOF'
approval-bridge: integration test — MCP call to gated tool injects card

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — nanoclaw: internal-server

All Phase 5+ tasks happen in `/home/jim/quickstack-cc/nanormm-nanoclaw` on the `main` branch.

### Task 5.1: internal-server.ts

**Files:**
- Create: `src/modules/nanormm-bridge/internal-server.ts`
- Test: `src/modules/nanormm-bridge/internal-server.test.ts`

**Note:** The code below uses `origin_platform_id` / `origin_channel_type` / `origin_thread_id` as the names of the routing fields on the `Session` record (matching the spec). Spike 0.2 confirms the actual column names in `/opt/nanoclaw/data/v2.db`. If they differ (e.g. the columns are bare `platform_id` / `channel_type` / `thread_id` without an `origin_` prefix), do a find-and-replace in this task's code blocks before running the test.

- [ ] **Step 1: Read existing helpers in nanoclaw**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
grep -rn "openOutboundDb\|writeOutboundMessage\|insertOutbound" src/db/ | head -20
```

Note the exact import paths and function names. The plan code below uses `openOutboundDb` from the existing delivery path; if the actual function is named differently (e.g., `getOutboundDb`), adjust.

- [ ] **Step 2: Write the failing test**

Create `src/modules/nanormm-bridge/internal-server.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import http from 'node:http';
import Database from 'better-sqlite3';
import { tmpdir } from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

vi.mock('../../log.js', () => ({
  log: {
    debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn(),
  },
}));

// Mock the session lookup: tests substitute a session record per case.
const sessionMock = vi.hoisted(() => ({ value: null as any }));
vi.mock('../../db/sessions.js', () => ({
  getSession: vi.fn((id: string) => sessionMock.value?.id === id ? sessionMock.value : undefined),
}));

import { startInternalServer } from './internal-server.js';

let server: http.Server;
let port: number;
let tmpDir: string;

beforeEach(async () => {
  tmpDir = fs.mkdtempSync(path.join(tmpdir(), 'plan4-test-'));
  process.env.NANOCLAW_INTERNAL_PORT = '0'; // ephemeral
  process.env.NANOCLAW_DATA_DIR = tmpDir;   // adjust if internal-server uses a different env
  server = await startInternalServer();
  const addr = server.address();
  port = typeof addr === 'object' && addr ? addr.port : 0;
});

afterEach(() => {
  server.close();
  fs.rmSync(tmpDir, { recursive: true, force: true });
  sessionMock.value = null;
});

describe('internal-server', () => {
  it('returns 404 for unknown session', async () => {
    sessionMock.value = null;
    const r = await fetch(`http://127.0.0.1:${port}/internal/sessions/no-such/inject-card`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questionId: 'nrmact-x', title: 't', question: 'q', options: [] }),
    });
    expect(r.status).toBe(404);
  });

  it('returns 404 for non-POST', async () => {
    const r = await fetch(`http://127.0.0.1:${port}/internal/sessions/anything/inject-card`);
    expect(r.status).toBe(404);
  });

  it('returns 400 for malformed body', async () => {
    sessionMock.value = { id: 'sess-1', agent_group_id: 'ag-1' };  // present but body bad
    const r = await fetch(`http://127.0.0.1:${port}/internal/sessions/sess-1/inject-card`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{not json',
    });
    expect(r.status).toBe(400);
  });

  it('writes ask_question row to outbound.db on success', async () => {
    // Pre-create the per-session outbound.db skeleton matching nanoclaw's schema.
    const sessionsDir = path.join(tmpDir, 'v2-sessions', 'ag-1', 'sess-1');
    fs.mkdirSync(sessionsDir, { recursive: true });
    const outDb = new Database(path.join(sessionsDir, 'outbound.db'));
    // Use the same schema as nanoclaw/src/db/outbound.ts createOutboundDb
    // (replace with actual columns from spike 0.2 if the schema differs).
    outDb.exec(`
      CREATE TABLE outbound (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        platform_id TEXT,
        channel_type TEXT,
        thread_id TEXT,
        content TEXT NOT NULL,
        due_at TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
    `);
    outDb.close();

    sessionMock.value = {
      id: 'sess-1',
      agent_group_id: 'ag-1',
      // PLACEHOLDERS — replace with actual field names from spike 0.2
      origin_platform_id: 'C0123456',
      origin_channel_type: 'slack',
      origin_thread_id: '1700000000.123456',
    };

    const r = await fetch(`http://127.0.0.1:${port}/internal/sessions/sess-1/inject-card`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        questionId: 'nrmact-act_xyz',
        title: 'Pending action',
        question: 'Kill PID 1234',
        options: [
          { label: 'Approve', value: 'approve' },
          { label: 'Reject', value: 'reject' },
        ],
      }),
    });
    expect(r.status).toBe(202);

    const verify = new Database(path.join(sessionsDir, 'outbound.db'));
    const rows = verify.prepare('SELECT id, kind, platform_id, channel_type, thread_id, content FROM outbound').all() as any[];
    verify.close();

    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe('normal');
    expect(rows[0].platform_id).toBe('C0123456');
    expect(rows[0].channel_type).toBe('slack');
    expect(rows[0].thread_id).toBe('1700000000.123456');
    const content = JSON.parse(rows[0].content);
    expect(content.type).toBe('ask_question');
    expect(content.questionId).toBe('nrmact-act_xyz');
    expect(content.options).toHaveLength(2);
  });
});
```

- [ ] **Step 3: Run the test**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
npx vitest run src/modules/nanormm-bridge/internal-server.test.ts
```

Expected: FAIL with `Cannot find module './internal-server.js'`.

- [ ] **Step 4: Implement internal-server.ts**

Create `src/modules/nanormm-bridge/internal-server.ts`:

```typescript
/**
 * Localhost-bound HTTP server that accepts approval-card injection requests
 * from the bridge. Writes a chat-sdk ask_question row into the session's
 * outbound.db; the existing delivery path then renders & posts the Slack card.
 *
 * Endpoint: POST /internal/sessions/:id/inject-card
 *   body: { questionId, title, question, options }
 *   200/202: row written
 *   404: session not found, or non-matching path/method
 *   400: malformed body
 */
import http from 'node:http';
import path from 'node:path';
import Database from 'better-sqlite3';
import { log } from '../../log.js';
import { getSession } from '../../db/sessions.js';

const DEFAULT_PORT = 8765;
const DATA_DIR = process.env.NANOCLAW_DATA_DIR ?? '/opt/nanoclaw/data';

function readJson(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

function send(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

interface InjectBody {
  questionId: string;
  title: string;
  question: string;
  options: Array<{ label: string; selectedLabel?: string; value: string }>;
}

function isInjectBody(b: unknown): b is InjectBody {
  if (!b || typeof b !== 'object') return false;
  const o = b as Record<string, unknown>;
  return typeof o.questionId === 'string'
    && typeof o.title === 'string'
    && typeof o.question === 'string'
    && Array.isArray(o.options);
}

export async function startInternalServer(): Promise<http.Server> {
  const port = Number(process.env.NANOCLAW_INTERNAL_PORT ?? DEFAULT_PORT);
  const server = http.createServer(async (req, res) => {
    const m = req.url?.match(/^\/internal\/sessions\/([^/]+)\/inject-card$/);
    if (!m || req.method !== 'POST') {
      return send(res, 404, { error: 'not found' });
    }
    const sessionId = m[1];
    const session = getSession(sessionId);
    if (!session) {
      return send(res, 404, { error: 'unknown session' });
    }

    let body: unknown;
    try {
      body = await readJson(req);
    } catch (e) {
      return send(res, 400, { error: 'malformed json' });
    }
    if (!isInjectBody(body)) {
      return send(res, 400, { error: 'missing required fields' });
    }

    const messageId = `inj-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const outboundPath = path.join(
      DATA_DIR, 'v2-sessions', session.agent_group_id, session.id, 'outbound.db',
    );

    const outDb = new Database(outboundPath);
    try {
      // Field names below assume spike 0.2 confirmed origin_platform_id /
      // origin_channel_type / origin_thread_id. Replace if different.
      outDb.prepare(`
        INSERT INTO outbound (id, kind, platform_id, channel_type, thread_id, content, due_at, created_at)
        VALUES (?, 'normal', ?, ?, ?, ?, ?, ?)
      `).run(
        messageId,
        (session as any).origin_platform_id,
        (session as any).origin_channel_type,
        (session as any).origin_thread_id,
        JSON.stringify({
          type: 'ask_question',
          questionId: body.questionId,
          title: body.title,
          question: body.question,
          options: body.options,
        }),
        new Date().toISOString(),
        new Date().toISOString(),
      );
    } finally {
      outDb.close();
    }

    log.info('nanormm-bridge inject-card written', {
      sessionId, messageId, questionId: body.questionId,
    });
    return send(res, 202, { accepted: true, messageId });
  });

  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => {
      const addr = server.address();
      const actualPort = typeof addr === 'object' && addr ? addr.port : port;
      log.info('nanormm-bridge internal server listening', { port: actualPort });
      resolve(server);
    });
  });
}
```

- [ ] **Step 5: Run the test**

```bash
npx vitest run src/modules/nanormm-bridge/internal-server.test.ts
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
git add src/modules/nanormm-bridge/internal-server.ts src/modules/nanormm-bridge/internal-server.test.ts
git commit -m "$(cat <<'EOF'
nanormm-bridge: add internal-server (localhost) with inject-card endpoint

Bound to 127.0.0.1; writes chat-sdk ask_question rows into the per-session
outbound.db so the existing delivery path renders and posts the card.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — nanoclaw: wire internal-server + reject path

### Task 6.1: Start internal-server from module init; add reject branch

**Files:**
- Modify: `src/modules/nanormm-bridge/index.ts`
- Test: `src/modules/nanormm-bridge/index.test.ts` (create — module currently has no tests)

- [ ] **Step 1: Write the failing test for the reject branch**

Create `src/modules/nanormm-bridge/index.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

vi.mock('../../log.js', () => ({
  log: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

vi.mock('../../env.js', () => ({
  readEnvFile: vi.fn(() => ({
    NANORMM_BRIDGE_URL: 'http://bridge.test',
    NANORMM_BRIDGE_API_KEY: 'test-key',
  })),
}));

const handlers: any[] = [];
vi.mock('../../response-registry.js', () => ({
  registerResponseHandler: (h: any) => handlers.push(h),
}));

vi.mock('./internal-server.js', () => ({
  startInternalServer: vi.fn(async () => undefined),
}));

const fetchSpy = vi.fn();
beforeEach(() => {
  handlers.length = 0;
  fetchSpy.mockReset();
  globalThis.fetch = fetchSpy as any;
});

describe('nanormm-bridge response handler', () => {
  it('non-nrmact prefix is not claimed', async () => {
    await import('./index.js');
    const handler = handlers[0];
    const claimed = await handler({ questionId: 'other-x', value: 'approve', userId: 'U1' });
    expect(claimed).toBe(false);
  });

  it('approve POSTs /actions/execute/', async () => {
    fetchSpy.mockResolvedValue({ ok: true, json: async () => ({ message: 'ok' }), status: 200 });
    await import('./index.js');
    const handler = handlers[0];
    const claimed = await handler({ questionId: 'nrmact-act_a', value: 'approve', userId: 'U1' });
    expect(claimed).toBe(true);
    expect(fetchSpy).toHaveBeenCalledWith(
      'http://bridge.test/api/nanoclaw/actions/execute/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-key',
          'X-Slack-User-ID': 'U1',
        }),
      }),
    );
  });

  it('reject POSTs /actions/reject/ with reason', async () => {
    fetchSpy.mockResolvedValue({ ok: true, json: async () => ({ message: 'rejected' }), status: 200 });
    await import('./index.js');
    const handler = handlers[0];
    const claimed = await handler({ questionId: 'nrmact-act_b', value: 'reject', userId: 'U2' });
    expect(claimed).toBe(true);
    expect(fetchSpy).toHaveBeenCalledWith(
      'http://bridge.test/api/nanoclaw/actions/reject/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-key',
          'X-Slack-User-ID': 'U2',
        }),
      }),
    );
    const callArg = fetchSpy.mock.calls[0][1];
    const body = JSON.parse(callArg.body);
    expect(body).toEqual({ token: 'act_b', reason: 'declined via Slack' });
  });
});
```

- [ ] **Step 2: Run the test**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
npx vitest run src/modules/nanormm-bridge/index.test.ts
```

Expected: the reject test fails (current code no-ops on non-approve values, doesn't call fetch).

- [ ] **Step 3: Modify index.ts**

Edit `src/modules/nanormm-bridge/index.ts`:

1. **Add** import + start of internal-server at the top:

```typescript
import { startInternalServer } from './internal-server.js';
```

2. **Replace** the existing handler body. Find the existing `handleNanormmResponse` and rewrite it:

```typescript
async function handleNanormmResponse(payload: ResponsePayload): Promise<boolean> {
  if (!payload.questionId.startsWith(QUESTION_ID_PREFIX)) return false;

  const actionId = payload.questionId.slice(QUESTION_ID_PREFIX.length);
  const userId = payload.userId ?? '';

  if (!API_KEY) {
    log.error('NANORMM_BRIDGE_API_KEY missing; cannot complete action', { actionId });
    return true;
  }

  const isApprove = payload.value === 'approve';
  const path = isApprove ? 'execute' : 'reject';
  const body = isApprove
    ? { token: actionId }
    : { token: actionId, reason: 'declined via Slack' };

  try {
    const res = await fetch(`${BRIDGE_URL}/api/nanoclaw/actions/${path}/`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${API_KEY}`,
        'Content-Type': 'application/json',
        'X-Slack-User-ID': userId,
      },
      body: JSON.stringify(body),
    });
    const respBody = (await res.json()) as Record<string, unknown>;
    if (res.ok) {
      log.info(`nanormm action ${isApprove ? 'executed' : 'rejected'}`,
        { actionId, userId, message: respBody.message });
    } else {
      log.warn(`nanormm action ${path} failed`,
        { actionId, status: res.status, error: respBody.error });
    }
  } catch (err) {
    log.error(`nanormm bridge ${path} call errored`, { actionId, err });
  }
  return true;
}
```

3. **Add** at the very bottom, after the existing `registerResponseHandler(...)` line:

```typescript
// Start the localhost-bound inject endpoint so the bridge can post approval cards.
// Errors are swallowed-and-logged; nanoclaw stays up if the listener can't bind.
startInternalServer().catch((err) => {
  log.error('nanormm-bridge internal server failed to start', { err });
});
```

- [ ] **Step 4: Run the test**

```bash
npx vitest run src/modules/nanormm-bridge/index.test.ts
```

Expected: 3 passed.

- [ ] **Step 5: Run the full nanoclaw suite to catch regressions**

```bash
npx vitest run
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/modules/nanormm-bridge/index.ts src/modules/nanormm-bridge/index.test.ts
git commit -m "$(cat <<'EOF'
nanormm-bridge: wire reject path and start internal-server on module load

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 7 — nanoclaw: container env + MCP header config

### Task 7.1: Inject NANOCLAW_SESSION_ID into container env

**Files:**
- Modify: `src/container-runner.ts` (function `buildContainerArgs` around line 415, plus its call site around line 128)

`buildContainerArgs` doesn't currently take `session.id`. The cleanest fix is to add it as a parameter. The call site already has `session.id` in scope.

- [ ] **Step 1: Modify `buildContainerArgs` signature and body**

Edit `src/container-runner.ts`. Find the `buildContainerArgs` declaration (around line 415–423):

```typescript
async function buildContainerArgs(
  mounts: VolumeMount[],
  containerName: string,
  sessionId: string,                     // NEW
  agentGroup: AgentGroup,
  containerConfig: import('./container-config.js').ContainerConfig,
  provider: string,
  providerContribution: ProviderContainerContribution,
  agentIdentifier?: string,
): Promise<string[]> {
```

Then near the existing `TRMM_MCP_URL` block (around line 432–434), add:

```typescript
  // Per-session id, surfaced into the trmm MCP request header so the bridge
  // can route inject-card calls back to this session's outbound queue.
  args.push('-e', `NANOCLAW_SESSION_ID=${sessionId}`);
```

- [ ] **Step 2: Update the call site**

Find the existing call to `buildContainerArgs` (around line 128). Add `session.id` as the third argument:

```typescript
  const args = await buildContainerArgs(
    mounts,
    containerName,
    session.id,                          // NEW
    agentGroup,
    containerConfig,
    provider,
    providerContribution,
    agentIdentifier,
  );
```

- [ ] **Step 3: Run container-runner tests**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
npx vitest run src/container-runner.test.ts
```

Expected: tests that exercise `buildContainerArgs` will fail with arity mismatch — update them to pass a sessionId arg (use `'test-session'` or similar). If a test asserts the assembled `args` array shape, update it to expect `'-e', 'NANOCLAW_SESSION_ID=test-session'`.

- [ ] **Step 4: Commit**

```bash
git add src/container-runner.ts src/container-runner.test.ts
git commit -m "$(cat <<'EOF'
container-runner: inject NANOCLAW_SESSION_ID env var into per-session containers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7.2: agent-runner — register X-Nanoclaw-Session header on the trmm http MCP server

**Files:**
- Modify: `container/agent-runner/src/index.ts` (around line 94–100, the `TRMM_MCP_URL` block)

The agent-runner already supports `McpHttpServerConfig` with `headers` (`container/agent-runner/src/providers/types.ts:58–62`). The trmm http MCP entry today has only `type` and `url`; we add a `headers` map referencing the env var injected by Task 7.1.

- [ ] **Step 1: Modify the trmm MCP registration**

Edit `container/agent-runner/src/index.ts`. Replace the existing block:

```typescript
  if (process.env.TRMM_MCP_URL) {
    mcpServers.trmm = {
      type: 'http' as const,
      url: process.env.TRMM_MCP_URL,
    };
    log(`trmm MCP server registered at ${process.env.TRMM_MCP_URL}`);
  }
```

with:

```typescript
  if (process.env.TRMM_MCP_URL) {
    const headers: Record<string, string> = {};
    if (process.env.NANOCLAW_SESSION_ID) {
      headers['X-Nanoclaw-Session'] = process.env.NANOCLAW_SESSION_ID;
    }
    mcpServers.trmm = {
      type: 'http' as const,
      url: process.env.TRMM_MCP_URL,
      ...(Object.keys(headers).length > 0 ? { headers } : {}),
    };
    log(`trmm MCP server registered at ${process.env.TRMM_MCP_URL} (sessionHeader=${headers['X-Nanoclaw-Session'] ?? '<absent>'})`);
  }
```

- [ ] **Step 2: Run agent-runner tests if any cover this area**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw/container/agent-runner
ls *.test.ts src/*.test.ts 2>/dev/null
npx vitest run 2>&1 | tail -20
```

Expected: existing tests still pass. There likely isn't a unit test for the MCP server-list assembly in `index.ts` — that's fine, the integration is exercised by Phase 9 smoke.

- [ ] **Step 3: Commit (in nanormm-nanoclaw repo)**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
git add container/agent-runner/src/index.ts
git commit -m "$(cat <<'EOF'
agent-runner: forward NANOCLAW_SESSION_ID as X-Nanoclaw-Session header on trmm MCP

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 8 — recon prompt update (VM-only)

### Task 8.1: Replace OUTPUT CONTRACT section in recon prompt

**Files:**
- Modify (on VM): `/opt/nanoclaw/groups/recon/CLAUDE.local.md`

- [ ] **Step 1: SSH to VM**

```bash
gcloud compute ssh rmm --project=qsrmm-494222 --zone=us-central1-a --tunnel-through-iap
```

- [ ] **Step 2: Back up current prompt**

```bash
sudo cp /opt/nanoclaw/groups/recon/CLAUDE.local.md \
        /opt/nanoclaw/groups/recon/CLAUDE.local.md.bak.plan4
```

- [ ] **Step 3: Strip OUTPUT CONTRACT and replace**

```bash
sudo -u nanoclaw $EDITOR /opt/nanoclaw/groups/recon/CLAUDE.local.md
```

Delete every line from the start of `## OUTPUT CONTRACT` (or whatever exact heading was added during Plan 3 strengthening) to the end of that section. Replace with:

```markdown
## Approval card behavior

When a tool call returns `status: 'pending'`, an approval card has been
posted to the user automatically. Continue your turn naturally — you
don't need to do anything to make the card appear.
```

- [ ] **Step 4: Restart nanoclaw to pick up the prompt**

```bash
sudo systemctl restart nanoclaw
sudo journalctl -u nanoclaw -f -o cat | head -20
```

Confirm "Recon agent group loaded" or equivalent startup line.

---

## Phase 9 — deploy + smoke

### Task 9.1: Deploy bridge

**Files:** ops only.

- [ ] **Step 1: Push the new bridge/dispatcher code from the worktree to deploy location**

Mirroring the deploy pattern documented in the handoff:

```bash
cd /home/jim/quickstack-cc/qsrmm
sudo rsync -av --delete \
  .worktrees/nanormm-recon/nanormm/trmm-mcp/ \
  /opt/nanormm/qsrmm/nanormm/trmm-mcp/
sudo rsync -av --delete \
  .worktrees/nanormm-recon/nanormm/approval-bridge/ \
  /opt/nanormm/qsrmm/nanormm/approval-bridge/
```

- [ ] **Step 2: Reinstall the bridge venv with new deps if any (httpx already present)**

```bash
sudo -u nanormm /opt/nanormm/approval-bridge/.venv/bin/pip install -e /opt/nanormm/qsrmm/nanormm/approval-bridge
```

- [ ] **Step 3: Add NANOCLAW_INTERNAL_URL to bridge env if not default**

```bash
sudo grep -q NANOCLAW_INTERNAL_URL /etc/nanormm/bridge.env || \
  echo "NANOCLAW_INTERNAL_URL=http://127.0.0.1:8765" | sudo tee -a /etc/nanormm/bridge.env
```

- [ ] **Step 4: Restart bridge**

```bash
sudo systemctl restart approval-bridge
sudo journalctl -u approval-bridge -n 50 -o cat
```

Confirm `Uvicorn running` and no startup errors.

---

### Task 9.2: Deploy nanoclaw

**Files:** ops only.

- [ ] **Step 1: Build nanoclaw fork**

```bash
cd /home/jim/quickstack-cc/nanormm-nanoclaw
npm run build
```

Expected: `dist/` directory updated with new TS-compiled JS.

- [ ] **Step 2: Push to VM**

The exact deploy mechanism for nanoclaw isn't in the handoff — confirm with whatever sync command was used previously. As a fallback:

```bash
gcloud compute scp --recurse --tunnel-through-iap \
  --project=qsrmm-494222 --zone=us-central1-a \
  dist/ package.json package-lock.json node_modules/ \
  rmm:/tmp/nanoclaw-deploy/
```

Then on the VM:

```bash
sudo rsync -av --delete /tmp/nanoclaw-deploy/dist/ /opt/nanoclaw/dist/
sudo rsync -av /tmp/nanoclaw-deploy/package.json /opt/nanoclaw/package.json
# package-lock + node_modules only if there are new deps
```

- [ ] **Step 3: Restart nanoclaw**

```bash
sudo systemctl restart nanoclaw
sudo journalctl -u nanoclaw -f -o cat | head -30
```

Confirm `nanormm-bridge internal server listening` log line on port 8765.

---

### Task 9.3: End-to-end smoke

- [ ] **Step 1: Trigger a fake critical alert**

In `#rmm-alerts` Slack channel:

```
@recon synthetic test: kill PID 1234 on host foo because process is stuck
```

- [ ] **Step 2: Within ~5s, verify card**

A Slack card should appear in the same thread:
- Title: "Pending action"
- Body: a summary mentioning PID 1234 and host foo
- Two buttons: Approve, Reject

If no card appears within 30s, tail logs:

```bash
sudo journalctl -u nanoclaw -u approval-bridge -n 100 -o cat
```

Look for `nanormm-bridge inject-card written` (success) or `inject_failed` (error). Check `pending_questions` table:

```bash
sudo -u nanoclaw sqlite3 /opt/nanoclaw/data/v2.db \
  "SELECT question_id, session_id, created_at FROM pending_questions ORDER BY created_at DESC LIMIT 3;"
```

- [ ] **Step 3: Click Approve**

Card should edit to "✅ Approved". Audit row should populate `approved_at` and `executed_at`:

```bash
sudo bash -c 'export PGPASSWORD=$(cat /home/nanormm/.pg_password); sudo -u nanormm -E psql -h localhost -U nanormm -d nanormm -c "SELECT action_id, tool_name, pending_at, approved_at, executed_at, rejected_at FROM nanormm_actions ORDER BY pending_at DESC LIMIT 3;"'
```

- [ ] **Step 4: Repeat with a fresh alert; click Reject**

Card should edit to "❌ Rejected". Audit row should populate `rejected_at`.

- [ ] **Step 5: Confirm logs are clean**

```bash
sudo journalctl -u nanoclaw -u approval-bridge --since "5 minutes ago" -o cat | grep -iE "error|warn|unclaimed"
```

Expected: no unexplained errors, no "unclaimed action" lines.

- [ ] **Step 6: Mark Plan 3 Task 15 complete**

If all six steps passed, edit `docs/superpowers/plans/2026-04-28-plan-3-recon-implementation.md` (or wherever Task 15 lives) to mark the smoke-test step done.

- [ ] **Step 7: Commit any final notes**

```bash
cd /home/jim/quickstack-cc/qsrmm
git status
# update plan file if checkboxes flipped
git add docs/superpowers/plans/
git commit -m "$(cat <<'EOF'
plans: Plan 3 Task 15 closed via Plan 4 smoke test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Post-plan checklist

- [ ] Both repos committed and pushed to their remotes (with explicit user authorization for `develop`/`main` pushes)
- [ ] `feat/nanormm-recon` worktree merged to `develop` (per Plan 3 handoff item #2)
- [ ] Stale Plan 3 redis pending keys + Postgres pending audit rows cleaned (or left to TTL):
  ```sql
  DELETE FROM nanormm_actions WHERE executed_at IS NULL AND rejected_at IS NULL AND pending_at < NOW() - INTERVAL '1 hour';
  ```
- [ ] Optional: dedicated-SA migration runbook (`docs/quickstack/migration-nanoclaw-dedicated-sa.md`) — defer if not blocking
