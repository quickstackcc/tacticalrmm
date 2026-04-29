# nanormm `recon` end-to-end Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Hybrid plan note:** Some tasks are pure code work an agent can execute (marked **AGENTIC**). Others require Jim to do something on the GCE VM, in a browser, or in a third-party console (marked **OPERATOR**). The agent should not attempt OPERATOR tasks — surface them to Jim and wait for confirmation that they're done before proceeding.

**Goal:** Ship `@recon` as a working alert-triage agent in `#rmm-alerts`. TRMM webhook fires → recon enriches via the bridge's MCP tools → posts a triage thread with Confirm/Cancel buttons for any write action → tech clicks Confirm → bridge executes → audit log captures the round-trip.

**Architecture:** Approval-bridge gains an MCP HTTP endpoint at `/mcp` (mounting the existing trmm-mcp `Server` instance via the Python MCP SDK's `streamable_http_app()`). Bridge runs as a native systemd service on the qsrmm VM (TRMM is bare-metal, so the bridge is too). Nanoclaw runs natively via the official `nanoclaw.sh` installer under a dedicated `nanoclaw` Linux user, spawning ephemeral agent containers via the host Docker daemon. Three small patches to a fresh nanormm-specific nanoclaw fork: (1) lift the Slack action-button handler from prospect-pro's fork (renamed env var), (2) extend `agent-runner` to register the trmm MCP HTTP server alongside nanoclaw's built-in IPC one, (3) pass the trmm MCP URL through `container-runner` into spawned agents.

**Tech Stack:**
- Bridge code: Python 3.11.8, FastAPI, MCP Python SDK ≥ 1.12 (`streamable_http_app()` on lowlevel `Server`)
- Bridge deployment: native systemd, uv-managed venv
- Nanoclaw: Node.js 22, TypeScript, Claude Agent SDK, Bolt for Slack
- Vertex AI region: `us-east5` (validated against Sonnet/Haiku availability)
- Model: `claude-haiku-4-5` for recon

**Spec:** `docs/superpowers/specs/2026-04-29-nanormm-recon-design.md` — read it before starting; this plan implements that spec.

---

## File structure

This plan touches three repos and produces deployment artifacts on the GCE VM.

### Repo 1: `quickstack-cc/qsrmm` (this repo)

```
nanormm/
├── approval-bridge/
│   ├── approval_bridge/
│   │   ├── app.py                  # MODIFIED: mount /mcp, lifespan
│   │   ├── mcp_app.py              # NEW: builds the MCP Starlette app from trmm-mcp Server
│   │   └── settings.py             # MODIFIED: add NANORMM_MCP_PATH (default /mcp)
│   ├── tests/
│   │   ├── conftest.py             # MODIFIED: stub MCP setup if needed
│   │   └── test_mcp_endpoint.py    # NEW: integration test against /mcp
│   ├── Dockerfile                  # DELETED
│   └── .dockerignore               # DELETED
├── trmm-mcp/
│   └── trmm_mcp/
│       └── server.py               # MODIFIED: expose `build_mcp_server()` returning just the Server
└── deploy/                          # NEW
    ├── approval-bridge.service     # systemd unit
    ├── install-bridge.sh           # idempotent install/upgrade script
    ├── bridge.env.example          # template for /etc/nanormm/bridge.env
    └── README.md                   # operator runbook
```

### Repo 2: `quickstack-cc/nanormm-nanoclaw` (NEW fork)

Created in Phase 5. Forked from `qwibitai/nanoclaw`. Three small patches on top of the upstream baseline.

```
src/
├── channels/
│   └── slack.ts                    # MODIFIED via /add-slack merge + action-handler patch
└── container-runner.ts             # MODIFIED: pass TRMM_MCP_URL into spawned agent containers
container/
└── agent-runner/
    └── src/
        └── index.ts                # MODIFIED: register trmm MCP HTTP server
.env.example                         # MODIFIED: documents NANOCLAW_ACTION_API_URL, TRMM_MCP_URL
groups/
└── recon/                          # CREATED in Phase 6 (deploy)
    └── CLAUDE.md                   # recon's system prompt
```

### Deployment artifacts (on the qsrmm GCE VM)

```
/opt/nanormm/approval-bridge/        # checkout of qsrmm repo's nanormm/approval-bridge/
├── .venv/                           # uv-managed
└── ... (mirrors the repo)
/opt/nanoclaw/                       # checkout of quickstack-cc/nanormm-nanoclaw
└── .env                             # mode 600, owned by nanoclaw user
/etc/nanormm/bridge.env              # mode 600, owned by nanormm user
/etc/systemd/system/approval-bridge.service
```

---

## Phase 1: Bridge MCP HTTP endpoint (AGENTIC)

Adds `/mcp` to the existing approval-bridge so nanoclaw agent containers can call trmm-mcp tools over HTTP.

### Task 1: Refactor trmm-mcp `server.py` to expose the populated `Server` (AGENTIC)

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/server.py`
- Modify: `nanormm/trmm-mcp/tests/test_server_smoke.py`

The existing `build_server()` returns a `TrmmMcpServer` dataclass containing both the dispatcher graph and an `mcp = Server("trmm-mcp")` with `@mcp.list_tools()` and `@mcp.call_tool()` handlers. The bridge needs that Server (to mount its `streamable_http_app()`), but the existing `run()` function couples Server construction with the stdio loop. We extract the Server construction so the bridge can grab just the Server without running stdio.

- [ ] **Step 1: Read the current `build_server()` and `run()`**

```bash
sed -n '20,120p' nanormm/trmm-mcp/trmm_mcp/server.py
```

Confirm the current shape: `build_server()` returns `TrmmMcpServer` (mcp, registry, dispatcher, trmm_client). `run()` calls `build_server()` then enters `stdio_server()`. We do not need to change either's external contract — `build_server()` already gives us the populated Server. We just need to verify the Server returned actually has tools registered via decorators (it does, but the decorators run during `build_server()`).

- [ ] **Step 2: Write a failing test that the Server returned from `build_server()` is fully populated**

Create or modify `nanormm/trmm-mcp/tests/test_server_smoke.py` to add:

```python
import pytest

from trmm_mcp.server import build_server


@pytest.mark.asyncio
async def test_build_server_returns_populated_mcp_server(trmm_env, fake_redis, audit_dsn, tmp_path, monkeypatch):
    """The Server instance returned by build_server() must have list_tools
    handlers wired (so streamable_http_app() can serve it later)."""
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: fake_redis)

    pol_path = tmp_path / "p.yaml"
    pol_path.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
  kill_process: human_approval
"""
    )
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol_path))
    monkeypatch.setenv("NANORMM_AUDIT_DSN", audit_dsn)

    server = build_server()

    # Server has request handlers registered
    assert server.mcp is not None
    # Tool registry is populated with at least one read tool
    assert "list_alerts" in server.tool_registry.tool_names()
```

- [ ] **Step 3: Run the test and confirm it passes against the existing implementation**

```bash
cd nanormm/trmm-mcp
. .venv/bin/activate
pytest tests/test_server_smoke.py -v
```

Expected: PASS (the existing `build_server()` already does what the test asserts; this is regression coverage for downstream changes in Task 2).

- [ ] **Step 4: Commit**

```bash
git add tests/test_server_smoke.py
git commit -m "trmm-mcp: regression test for build_server populating Server"
```

---

### Task 2: Add `/mcp` route to the bridge (AGENTIC)

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/mcp_app.py`
- Modify: `nanormm/approval-bridge/approval_bridge/app.py`
- Modify: `nanormm/approval-bridge/approval_bridge/settings.py`
- Create: `nanormm/approval-bridge/tests/test_mcp_endpoint.py`

The Python MCP SDK's lowlevel `Server` exposes `streamable_http_app()` which returns a Starlette ASGI app. We mount this on the bridge's FastAPI at `/mcp`. The session manager requires a lifespan to start/stop, integrated with FastAPI's lifespan.

- [ ] **Step 1: Write a failing endpoint test**

Create `nanormm/approval-bridge/tests/test_mcp_endpoint.py`:

```python
"""Integration test for the /mcp endpoint exposed by the bridge.

Uses the MCP Python SDK's HTTP client to perform a real initialize +
list_tools handshake against the bridge's mounted streamable_http app.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from approval_bridge.app import create_app


def test_mcp_endpoint_responds_to_initialize(bridge_settings):
    """Hit /mcp/ with a JSON-RPC initialize and confirm a 200 + protocol response.

    We don't drive the full MCP client here — we just confirm the route is
    mounted and accepts JSON-RPC POSTs (the streamable-http transport's
    primary entry point).
    """
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        # MCP streamable-http expects POST with JSON-RPC body and the
        # mcp-protocol-version + accept headers per the SDK's HTTP transport.
        r = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.0.0"},
                },
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )
        # SDK returns 200 with either a JSON or SSE body depending on
        # negotiation. Either way, NOT 404 (route mounted) and NOT 500.
        assert r.status_code in (200, 202)


def test_mcp_endpoint_lists_trmm_tools(bridge_settings):
    """After initialize, list_tools should return the trmm-mcp tool surface."""
    app = create_app(bridge_settings)
    with TestClient(app) as client:
        # Initialize first to establish a session
        init = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.0.0"},
                },
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )
        assert init.status_code in (200, 202)

        # Capture session ID from response headers (streamable-http convention)
        session_id = init.headers.get("mcp-session-id")

        # If session_id is required, include it; otherwise the second call
        # is stateless. Try without first.
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        if session_id:
            headers["mcp-session-id"] = session_id

        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert r.status_code == 200

        # Body may be JSON or SSE — handle both
        body_text = r.text
        # Look for one of the known tool names in either parsed JSON or SSE body
        assert "list_alerts" in body_text or "kill_process" in body_text
```

- [ ] **Step 2: Run the test and verify it fails for the right reason**

```bash
cd nanormm/approval-bridge
. .venv/bin/activate
pytest tests/test_mcp_endpoint.py -v
```

Expected: FAIL with 404 on `/mcp/` (route not mounted yet).

- [ ] **Step 3: Add an `mcp_path` setting**

Modify `nanormm/approval-bridge/approval_bridge/settings.py`. Add a single field after the existing port field:

```python
    mcp_path: str = Field(default="/mcp", alias="NANORMM_MCP_PATH")
```

- [ ] **Step 4: Create `mcp_app.py` to build the MCP Starlette app**

Create `nanormm/approval-bridge/approval_bridge/mcp_app.py`:

```python
"""MCP HTTP transport for the approval-bridge.

Mounts trmm-mcp's lowlevel Server (with all tools registered) as a Starlette
sub-application using the streamable-http transport from the Python MCP SDK.
The same Server instance backs both stdio (`python -m trmm_mcp`) and the
bridge's HTTP endpoint — single source of truth for the MCP surface.
"""

from starlette.applications import Starlette

from trmm_mcp.server import build_server


def build_mcp_starlette_app() -> Starlette:
    """Construct the Starlette ASGI app that serves trmm-mcp over HTTP.

    The lifespan returned by streamable_http_app() owns the session manager.
    Caller mounts this and includes the lifespan in the host app's lifespan.
    """
    server = build_server()
    return server.mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
    )
```

- [ ] **Step 5: Modify `app.py` to mount `/mcp` and wire the lifespan**

Replace `nanormm/approval-bridge/approval_bridge/app.py` with:

```python
import contextlib

from fastapi import FastAPI
from starlette.routing import Mount

from .auth import verify_bearer
from .deps import build_dispatcher_for_bridge
from .mcp_app import build_mcp_starlette_app
from .routes import authed_router, public_router
from .settings import BridgeSettings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    """Application factory. Builds the Dispatcher once and stashes it on
    `app.state.dispatcher` so route handlers can reach it via Request.

    Pass an explicit `settings` for tests; production calls with no args and
    BridgeSettings() reads from the environment.
    """
    if settings is None:
        settings = BridgeSettings()

    mcp_app = build_mcp_starlette_app()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        # MCP streamable-http needs its session manager running for the
        # lifetime of the parent app. The mounted app's own lifespan does
        # this; we just need to delegate to it.
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(
        title="nanormm approval-bridge",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.dispatcher = build_dispatcher_for_bridge(settings)

    app.include_router(public_router)
    app.include_router(authed_router, dependencies=[verify_bearer(settings)])

    # Mount MCP at the configured path (default `/mcp`).
    app.routes.append(Mount(settings.mcp_path, app=mcp_app))

    return app
```

- [ ] **Step 6: Run the test and verify both pass**

```bash
pytest tests/test_mcp_endpoint.py -v
```

Expected: both tests PASS. If `test_mcp_endpoint_lists_trmm_tools` fails on session-ID handling, inspect the streamable-http transport's actual session protocol via:

```bash
python -c "from approval_bridge.mcp_app import build_mcp_starlette_app; app = build_mcp_starlette_app(); print(type(app))"
```

If the transport is SSE-only by default, set `json_response=True` (already done in Step 4) — this returns the response inline rather than via SSE, simplifying the test assertion. If still flaky, accept that the second test verifies "200 with body containing tool name" rather than full JSON-RPC parse.

- [ ] **Step 7: Run the full bridge suite for regression**

```bash
pytest -q
```

Expected: previous 25 passes + 2 new = 27 passing.

- [ ] **Step 8: Commit**

```bash
git add approval_bridge/mcp_app.py approval_bridge/app.py approval_bridge/settings.py tests/test_mcp_endpoint.py
git commit -m "approval-bridge: mount trmm-mcp at /mcp via streamable-http"
```

---

### Task 3: Cleanup — delete Dockerfile, audit policy.yaml (AGENTIC)

**Files:**
- Delete: `nanormm/approval-bridge/Dockerfile`
- Delete: `nanormm/approval-bridge/.dockerignore`
- Modify: `nanormm/policy.yaml` (audit only, may not need changes)
- Modify: `nanormm/approval-bridge/README.md` (remove Dockerfile reference)

The Dockerfile is dead weight per the design spec — prod path is systemd. Audit `policy.yaml` against the trmm-mcp tool registry to confirm every tool has an explicit entry and the defaults are correct.

- [ ] **Step 1: Delete the Dockerfile and .dockerignore**

```bash
cd /home/jim/quickstack-cc/qsrmm
git rm nanormm/approval-bridge/Dockerfile nanormm/approval-bridge/.dockerignore
```

- [ ] **Step 2: Audit policy.yaml against the tool registry**

```bash
cat nanormm/policy.yaml
. nanormm/trmm-mcp/.venv/bin/activate
python -c "
from trmm_mcp.server import build_server
import os
os.environ.setdefault('TRMM_API_BASE','x'); os.environ.setdefault('TRMM_API_TOKEN','x')
os.environ.setdefault('NANORMM_POLICY_PATH','/tmp/x'); os.environ.setdefault('NANORMM_REDIS_URL','x'); os.environ.setdefault('NANORMM_AUDIT_DSN','x')
# We just need the registry shape; mock the construction
from trmm_mcp.server import _register_all
from trmm_mcp.tools._base import ToolRegistry
from trmm_mcp.trmm_client import TrmmClient
class _Stub: pass
r = ToolRegistry(); _register_all(r, _Stub())
print('TOOLS REGISTERED:', sorted(r.tool_names()))
"
```

(If the stub-client trick fails because tools call methods on it, just hardcode the expected tool list from `nanormm/trmm-mcp/trmm_mcp/server.py:_register_all`.)

Expected tool list per `_register_all` in `server.py`:
- Read tools (must be `auto`): `list_alerts`, `get_alert`, `search_past_alerts`, `list_agents`, `get_agent`, `agent_recent_checks`, `agent_recent_tasks`, `agent_patch_state`, `agent_running_processes`, `query_clients`, `script_history`
- Write tools (must be `human_approval`): `acknowledge_alert`, `run_script_on_agent`, `run_inline_command`, `kill_process`, `restart_service`, `reboot_agent`, `collect_artifacts`, `isolate_host`, `unisolate_host`, `disable_account`, `pause_scheduled_task`

Compare against `nanormm/policy.yaml`. Update the file if any tool is missing or has the wrong tier.

- [ ] **Step 3: Update policy.yaml as needed**

If audit reveals gaps, edit `nanormm/policy.yaml` to:

```yaml
version: 1
default: human_approval

tools:
  # Read tools — auto-execute
  list_alerts: auto
  get_alert: auto
  search_past_alerts: auto
  list_agents: auto
  get_agent: auto
  agent_recent_checks: auto
  agent_recent_tasks: auto
  agent_patch_state: auto
  agent_running_processes: auto
  query_clients: auto
  script_history: auto

  # Write tools — human_approval
  acknowledge_alert: human_approval
  run_script_on_agent: human_approval
  run_inline_command: human_approval
  kill_process: human_approval
  restart_service: human_approval
  reboot_agent: human_approval
  collect_artifacts: human_approval
  isolate_host: human_approval
  unisolate_host: human_approval
  disable_account: human_approval
  pause_scheduled_task: human_approval
```

- [ ] **Step 4: Run trmm-mcp's `test_policy_regression` to catch silent fallthroughs**

```bash
cd nanormm/trmm-mcp
. .venv/bin/activate
pytest tests/test_policy_regression.py -v
```

Expected: PASS (asserts every registered tool has an explicit policy entry).

- [ ] **Step 5: Update bridge README**

Modify `nanormm/approval-bridge/README.md` — remove the "## Run locally" section's Docker reference; add a pointer to `nanormm/deploy/README.md` for production deployment. (The deploy README is created in Phase 2.)

Replace the existing "Run locally" block with:

```markdown
## Run locally

Development:

    uv pip install -e '.[dev]'
    NANORMM_BRIDGE_API_KEY=devsecret python -m approval_bridge

For production deployment on the qsrmm VM, see [`../deploy/README.md`](../deploy/README.md).
```

- [ ] **Step 6: Commit**

```bash
git add -u nanormm/approval-bridge/Dockerfile nanormm/approval-bridge/.dockerignore nanormm/policy.yaml nanormm/approval-bridge/README.md
git commit -m "approval-bridge: drop Dockerfile, audit policy coverage"
```

---

## Phase 2: Bridge deployment artifacts (AGENTIC)

Adds the systemd unit, install script, env template, and operator runbook so the bridge can be deployed to the VM in Phase 6.

### Task 4: systemd unit + install script + env example + runbook (AGENTIC)

**Files:**
- Create: `nanormm/deploy/approval-bridge.service`
- Create: `nanormm/deploy/install-bridge.sh`
- Create: `nanormm/deploy/bridge.env.example`
- Create: `nanormm/deploy/README.md`

- [ ] **Step 1: Create the systemd unit**

Create `nanormm/deploy/approval-bridge.service`:

```ini
[Unit]
Description=nanormm approval-bridge (FastAPI + MCP HTTP)
After=network.target postgresql.service redis.service
Requires=network.target

[Service]
Type=simple
User=nanormm
Group=nanormm
WorkingDirectory=/opt/nanormm/approval-bridge
EnvironmentFile=/etc/nanormm/bridge.env
ExecStart=/opt/nanormm/approval-bridge/.venv/bin/python -m approval_bridge
Restart=on-failure
RestartSec=5
# Bind on the docker bridge IP + localhost only via NANORMM_BRIDGE_HOST.
# Default 0.0.0.0 in the bridge package is overridden via the EnvironmentFile.

# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/var/log/nanormm
ProtectHome=read-only
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the env example**

Create `nanormm/deploy/bridge.env.example`:

```bash
# Bridge-specific
NANORMM_BRIDGE_API_KEY=GENERATE_WITH_OPENSSL_RAND_HEX_32
NANORMM_BRIDGE_HOST=0.0.0.0
NANORMM_BRIDGE_PORT=8000
NANORMM_MCP_PATH=/mcp

# Shared with trmm-mcp (Python MCP server tools)
TRMM_API_BASE=https://api.qsrmm.example.com
TRMM_API_TOKEN=YOUR_TRMM_KNOX_TOKEN
NANORMM_POLICY_PATH=/opt/nanormm/policy.yaml
NANORMM_REDIS_URL=redis://localhost:6379/11
NANORMM_AUDIT_DSN=postgresql://nanormm:CHANGEME@localhost:5432/nanormm
NANORMM_PENDING_TTL_SECONDS=1800
```

- [ ] **Step 3: Create the install script**

Create `nanormm/deploy/install-bridge.sh`:

```bash
#!/usr/bin/env bash
# install-bridge.sh — idempotent install/upgrade of the approval-bridge.
# Run as root (sudo) on the qsrmm VM. Safe to re-run.
#
# This script:
#   1. Ensures the `nanormm` Linux user and required directories exist.
#   2. Pulls the latest qsrmm code into /opt/nanormm/approval-bridge.
#   3. Builds/refreshes the venv via uv.
#   4. Installs the systemd unit and reloads.
#   5. Restarts the service.
#
# Pre-requisites (one-time, NOT this script's job):
#   - /etc/nanormm/bridge.env (mode 600, owned by nanormm) — see bridge.env.example.
#   - Postgres `nanormm` DB and Redis DB 11 reachable on localhost.
#   - uv installed and on PATH for root (or full path used here).

set -euo pipefail

NANORMM_USER="nanormm"
INSTALL_ROOT="/opt/nanormm"
BRIDGE_DIR="${INSTALL_ROOT}/approval-bridge"
QSRMM_REPO_URL="git@github.com:quickstack-cc/qsrmm.git"
ENV_FILE="/etc/nanormm/bridge.env"
SERVICE_NAME="approval-bridge"
LOG_DIR="/var/log/nanormm"

echo "==> Ensuring nanormm user exists"
if ! id -u "${NANORMM_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/home/${NANORMM_USER}" \
        --shell /bin/bash "${NANORMM_USER}"
fi

echo "==> Creating directories"
mkdir -p "${INSTALL_ROOT}" "${LOG_DIR}"
chown -R "${NANORMM_USER}:${NANORMM_USER}" "${INSTALL_ROOT}" "${LOG_DIR}"

echo "==> Verifying env file"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: ${ENV_FILE} is missing." >&2
    echo "Copy nanormm/deploy/bridge.env.example to ${ENV_FILE}, fill in values," >&2
    echo "then chmod 600 and chown nanormm:nanormm." >&2
    exit 1
fi
chmod 600 "${ENV_FILE}"
chown "${NANORMM_USER}:${NANORMM_USER}" "${ENV_FILE}"

echo "==> Cloning or updating qsrmm checkout (sparse: only nanormm/approval-bridge + nanormm/trmm-mcp)"
QSRMM_CHECKOUT="${INSTALL_ROOT}/qsrmm"
if [[ ! -d "${QSRMM_CHECKOUT}/.git" ]]; then
    sudo -u "${NANORMM_USER}" git clone --depth 1 --filter=blob:none --sparse \
        "${QSRMM_REPO_URL}" "${QSRMM_CHECKOUT}"
    sudo -u "${NANORMM_USER}" git -C "${QSRMM_CHECKOUT}" sparse-checkout set \
        nanormm/approval-bridge nanormm/trmm-mcp nanormm/policy.yaml
else
    sudo -u "${NANORMM_USER}" git -C "${QSRMM_CHECKOUT}" fetch --depth 1 origin develop
    sudo -u "${NANORMM_USER}" git -C "${QSRMM_CHECKOUT}" reset --hard origin/develop
fi

# Symlink for the systemd unit's WorkingDirectory expectation
ln -sfn "${QSRMM_CHECKOUT}/nanormm/approval-bridge" "${BRIDGE_DIR}"
ln -sfn "${QSRMM_CHECKOUT}/nanormm/policy.yaml" "${INSTALL_ROOT}/policy.yaml"

echo "==> Building venv with uv"
sudo -u "${NANORMM_USER}" bash <<EOF
cd ${BRIDGE_DIR}
if [[ ! -d .venv ]]; then
    uv venv --python 3.11.8 .venv
fi
. .venv/bin/activate
uv pip install -e .
EOF

echo "==> Installing systemd unit"
install -m 0644 \
    "${QSRMM_CHECKOUT}/nanormm/deploy/approval-bridge.service" \
    "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

echo "==> Enabling and (re)starting service"
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

echo "==> Verifying service is active"
sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    echo "OK: ${SERVICE_NAME} is running"
else
    echo "FAIL: ${SERVICE_NAME} not running. Recent journal:"
    journalctl -u "${SERVICE_NAME}.service" -n 50 --no-pager
    exit 1
fi
```

Make it executable:

```bash
chmod +x nanormm/deploy/install-bridge.sh
```

- [ ] **Step 4: Create the deploy README runbook**

Create `nanormm/deploy/README.md`:

```markdown
# nanormm deployment runbook

Operator instructions for deploying nanormm components to the qsrmm GCE VM.
See `docs/superpowers/specs/2026-04-29-nanormm-recon-design.md` for design.

## Prerequisites (one-time)

- The qsrmm VM has TRMM running (bare-metal, systemd).
- Postgres has a `nanormm` database; Redis has DB 11 reachable.
  - Create the DB:
    ```bash
    sudo -u postgres createuser nanormm --pwprompt
    sudo -u postgres createdb -O nanormm nanormm
    ```
  - Apply the audit schema (one-time, idempotent):
    ```bash
    psql postgresql://nanormm@localhost:5432/nanormm \
        -f /opt/nanormm/qsrmm/nanormm/trmm-mcp/trmm_mcp/migrations/001_init.sql
    ```
- `uv` is installed and on PATH for root.
- The VM service account has `roles/aiplatform.user` (see Phase 3 in the plan).

## First-time bridge install

```bash
# As root on the VM:
sudo install -d -m 0755 /etc/nanormm
sudo cp /path/to/qsrmm/nanormm/deploy/bridge.env.example /etc/nanormm/bridge.env
# Generate a strong API key
echo "NANORMM_BRIDGE_API_KEY=$(openssl rand -hex 32)" | sudo tee -a /etc/nanormm/bridge.env
sudo $EDITOR /etc/nanormm/bridge.env  # fill in TRMM_API_TOKEN, audit DSN password
sudo chmod 600 /etc/nanormm/bridge.env
sudo chown nanormm:nanormm /etc/nanormm/bridge.env

# Run the install script
sudo /path/to/qsrmm/nanormm/deploy/install-bridge.sh
```

## Upgrade (after merging new bridge code to develop)

```bash
sudo /opt/nanormm/qsrmm/nanormm/deploy/install-bridge.sh
```

## Verify

```bash
curl -s http://127.0.0.1:8000/healthz   # → {"status":"ok"}

# MCP endpoint sanity (note: streamable-http needs proper headers)
curl -s -X POST http://127.0.0.1:8000/mcp/ \
    -H 'accept: application/json, text/event-stream' \
    -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
# → 200 OK with initialize result

# Service status + logs
sudo systemctl status approval-bridge
sudo journalctl -u approval-bridge -f
```

## Rollback

```bash
sudo -u nanormm git -C /opt/nanormm/qsrmm checkout <previous-sha>
sudo systemctl restart approval-bridge
```

## Networking

The bridge binds on `0.0.0.0:8000` per the systemd EnvironmentFile, which means
any container on the VM's docker bridge can reach it via `host.docker.internal`.
The VM's external firewall (GCP firewall rule on the VM) must NOT expose
port 8000 to the public internet. Verify:

```bash
gcloud compute firewall-rules list --filter='allowed.ports=8000'
# Should return nothing or only internal rules.
```
```

- [ ] **Step 5: Confirm the artifacts are syntactically reasonable**

```bash
# Shell script
bash -n nanormm/deploy/install-bridge.sh

# systemd unit (analyze, not run)
systemd-analyze verify nanormm/deploy/approval-bridge.service 2>&1 | head -10 || true
```

`systemd-analyze verify` may complain about non-existent paths (it can't see the live VM), but it should not flag syntax errors. Ignore "non-existent" warnings.

- [ ] **Step 6: Commit**

```bash
git add nanormm/deploy/
git commit -m "nanormm/deploy: systemd unit, install script, runbook for bridge"
```

---

## Phase 3: GCP / Vertex AI setup (OPERATOR)

### Task 5: Grant Vertex AI access to the VM service account (OPERATOR)

This is operator work — Jim runs gcloud commands from his laptop or the VM.

- [ ] **Step 1: Identify the VM's attached service account**

```bash
gcloud compute instances describe qsrmm-vm \
    --project=qsrmm-494222 \
    --zone=us-central1-a \
    --format='value(serviceAccounts[0].email)'
```

(Replace zone if the VM is elsewhere; check the qsrmm infrastructure memory for the actual zone.)

Capture the email address output, e.g. `qsrmm-vm-sa@qsrmm-494222.iam.gserviceaccount.com`.

- [ ] **Step 2: Grant `roles/aiplatform.user` to that SA**

```bash
gcloud projects add-iam-policy-binding qsrmm-494222 \
    --member="serviceAccount:<SA_EMAIL_FROM_STEP_1>" \
    --role="roles/aiplatform.user"
```

Confirm the binding:

```bash
gcloud projects get-iam-policy qsrmm-494222 \
    --flatten='bindings[].members' \
    --filter="bindings.members:<SA_EMAIL_FROM_STEP_1>" \
    --format='table(bindings.role)'
```

You should see `roles/aiplatform.user` in the output.

- [ ] **Step 3: Verify ADC works on the VM (as a future-nanoclaw user proxy)**

SSH into the VM, then:

```bash
gcloud auth application-default print-access-token | head -c 40
echo
```

Should print 40 characters of an access token. If it errors with "no credentials", the VM doesn't have the metadata-server flow enabled — escalate to me.

- [ ] **Step 4: Confirm the right region for Anthropic models on Vertex**

This is verification, not provisioning. Just check the model is callable in `us-east5`:

```bash
TOKEN=$(gcloud auth application-default print-access-token)
curl -s -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "https://us-east5-aiplatform.googleapis.com/v1/projects/qsrmm-494222/locations/us-east5/publishers/anthropic/models/claude-haiku-4-5:rawPredict" \
    -d '{
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": 32,
        "messages": [{"role":"user","content":"Say hi in 3 words."}]
    }' | jq -r '.content[0].text // .error.message'
```

Expected: a short greeting like "Hi there friend!" — confirming model availability + permissions.
Failure: "PERMISSION_DENIED" → re-check Step 2; "MODEL_NOT_FOUND" → enable the model in Vertex Model Garden console (you mentioned this is already done).

- [ ] **Step 5: Confirm done**

When all four steps return expected output, mark this task done. No commit — IAM grants don't live in git.

---

## Phase 4: Slack app for `@recon` (OPERATOR)

### Task 6: Create the recon Slack app (OPERATOR)

Manual checkpoint — Jim creates the Slack app, gathers tokens, hands them back.

- [ ] **Step 1: Create the app**

1. Go to https://api.slack.com/apps and click **Create New App** → **From scratch**.
2. Name it `recon` (or `qsrmm-recon` to disambiguate from your other apps).
3. Pick the Quick Stack workspace.

- [ ] **Step 2: Enable Socket Mode**

1. Left nav → **Socket Mode** → toggle ON.
2. When prompted, generate an **App-Level Token** with the `connections:write` scope.
3. Copy the token (starts with `xapp-`). Save securely.

- [ ] **Step 3: Add OAuth scopes**

Left nav → **OAuth & Permissions** → **Bot Token Scopes** → add each:

```
chat:write
channels:history
groups:history
im:history
channels:read
groups:read
users:read
```

- [ ] **Step 4: Subscribe to bot events**

Left nav → **Event Subscriptions** → toggle ON (Socket Mode handles delivery, no Request URL needed) → **Subscribe to bot events** → add:

```
message.channels
message.groups
message.im
```

- [ ] **Step 5: Enable interactivity (for action buttons)**

Left nav → **Interactivity & Shortcuts** → toggle ON. With Socket Mode, no Request URL is needed.

- [ ] **Step 6: Install to workspace**

Left nav → **Install App** → click **Install to Workspace** → approve. Copy the **Bot User OAuth Token** (starts with `xoxb-`). Save securely.

- [ ] **Step 7: Add the bot to `#rmm-alerts`**

In Slack: right-click `#rmm-alerts` → **View channel details** → **Integrations** → **Add apps** → find `recon` → **Add**.

Capture the channel ID: open `#rmm-alerts` in the browser, the URL contains `/C0XXXXXXXXX/` — that's the channel ID. Save it.

- [ ] **Step 8: Hand off the values**

Surface to me (the agent) when done:

- `SLACK_BOT_TOKEN=xoxb-...`
- `SLACK_APP_TOKEN=xapp-...`
- Channel ID for `#rmm-alerts`: `C0XXXXXXXXX`

I'll incorporate these into Phase 6 deployment env files. Don't paste them into git.

---

## Phase 5: nanormm-nanoclaw fork (HYBRID)

### Task 7: Fork upstream nanoclaw and set up the working directory (OPERATOR)

- [ ] **Step 1: Create the fork on GitHub (OPERATOR)**

In a browser:
1. Go to https://github.com/qwibitai/nanoclaw
2. Click **Fork** → set the destination to the **quickstack-cc** org → name it **`nanormm-nanoclaw`**.

This creates `https://github.com/quickstack-cc/nanormm-nanoclaw`.

- [ ] **Step 2: Clone locally for development (OPERATOR)**

On your dev machine (NOT the VM yet):

```bash
cd ~/quickstack-cc
git clone git@github.com:quickstack-cc/nanormm-nanoclaw.git
cd nanormm-nanoclaw
git remote add upstream https://github.com/qwibitai/nanoclaw.git
git remote add slack https://github.com/qwibitai/nanoclaw-slack.git
# (optional) reference the prospect-pro fork for the action handler we'll lift
git remote add reference-pp git@github.com:quickstack-cc/nanoclaw-private.git
git fetch --all
```

Confirm you're on `main` and clean.

- [ ] **Step 3: Hand off**

The fork is ready for the agentic Tasks 8–10. Tell me which directory it's in.

---

### Task 8: Apply the upstream `/add-slack` skill (AGENTIC)

**Files:**
- Modify (via merge): `src/channels/slack.ts`, `src/channels/index.ts`, `src/channels/slack.test.ts`, `package.json`, `.env.example`

This is the standard nanoclaw `/add-slack` skill flow per `nanoclaw/.claude/skills/add-slack/SKILL.md` Phase 2.

- [ ] **Step 1: Verify the slack remote is configured**

```bash
cd ~/quickstack-cc/nanormm-nanoclaw
git remote -v | grep slack
```

Expected: `slack  https://github.com/qwibitai/nanoclaw-slack.git`. If missing, `git remote add slack https://github.com/qwibitai/nanoclaw-slack.git && git fetch slack`.

- [ ] **Step 2: Merge the slack skill branch**

```bash
git fetch slack main
git merge slack/main || {
    # The skill's documented conflict resolution
    git checkout --theirs package-lock.json
    git add package-lock.json
    git merge --continue
}
```

If other conflicts appear, read both sides and resolve preserving the upstream nanoclaw structure plus the slack channel additions.

- [ ] **Step 3: Validate the build**

```bash
npm install
npm run build
npx vitest run src/channels/slack.test.ts
```

Expected: build clean, all 46 slack tests pass.

- [ ] **Step 4: Commit (the merge already created a merge commit; nothing extra needed)**

Confirm `git log --oneline -3` shows a merge commit from `slack/main`.

---

### Task 9: Lift the action-button handler from prospect-pro's fork (AGENTIC)

**Files:**
- Modify: `src/channels/slack.ts`
- Modify: `.env.example`

We extract the action-button machinery from `quickstack-cc/nanoclaw-private` (the prospect-pro fork) and adapt it: rename `PROSPECT_PRO_API_URL` → `NANOCLAW_ACTION_API_URL`, leave a comment crediting origin, and rename the agent-side env var that nanoclaw reads.

- [ ] **Step 1: Read the prospect-pro fork's slack.ts to identify the action-handler block**

```bash
git fetch reference-pp
git show reference-pp/main:src/channels/slack.ts > /tmp/pp-slack.ts
# Find the relevant sections
grep -n "setupActionHandlers\|nanoclaw_confirm\|nanoclaw_cancel\|parseActionResponse\|PROSPECT_PRO_API_URL\|NANOCLAW_API_KEY" /tmp/pp-slack.ts
```

You should see lines around the constructor's env reading, the constructor's call to `setupActionHandlers`, the `parseActionResponse()` method, and the `setupActionHandlers()` method.

- [ ] **Step 2: Apply the patch to `src/channels/slack.ts`**

Modify these specific places in the just-merged `src/channels/slack.ts`:

1. **Imports stay as is.** No new imports needed.

2. **In `SlackChannel` class fields**, add three private fields (after `private userNameCache`):

```typescript
  private opts: SlackChannelOpts;
  private apiKey: string | undefined;
  private apiBaseUrl: string;
```

3. **In the constructor**, modify the `readEnvFile` call to also read the action-callback env vars:

```typescript
    // Read tokens from .env (not process.env — keeps secrets off the environment
    // so they don't leak to child processes, matching NanoClaw's security pattern)
    const env = readEnvFile([
      'SLACK_BOT_TOKEN',
      'SLACK_APP_TOKEN',
      'NANOCLAW_ACTION_API_URL',  // bridge URL for action callbacks (nanormm fork addition)
    ]);
    const botToken = env.SLACK_BOT_TOKEN;
    const appToken = env.SLACK_APP_TOKEN;
    this.apiBaseUrl = env.NANOCLAW_ACTION_API_URL || 'http://web:8000';
```

4. **In `setupEventHandlers()`** (or at the end of the constructor — match wherever prospect-pro's fork puts it), add a call to `setupActionHandlers` only if an API key is present:

At the start of `setupEventHandlers()`, before `this.app.event('message', …)`:

```typescript
  private setupEventHandlers(): void {
    // Read API key for action button handlers (optional — only set when wired
    // to nanormm approval-bridge or an equivalent confirm-callback endpoint).
    // Lifted from quickstack-cc/nanoclaw-private (prospect-pro's fork) and
    // generalized via NANOCLAW_ACTION_API_URL.
    const actionEnv = readEnvFile(['NANOCLAW_API_KEY']);
    this.apiKey = actionEnv.NANOCLAW_API_KEY;

    if (this.apiKey) {
      this.setupActionHandlers();
    }

    // ... existing app.event('message') handler stays unchanged below
```

5. **In `sendMessage()`**, add envelope detection BEFORE the existing `chat.postMessage` block. This intercepts agent output that contains a `nanoclaw_action` JSON envelope and posts message blocks instead of plain text:

```typescript
  async sendMessage(jid: string, text: string): Promise<void> {
    const channelId = jid.replace(/^slack:/, '');

    if (!this.connected) {
      this.outgoingQueue.push({ jid, text });
      logger.info(
        { jid, queueSize: this.outgoingQueue.length },
        'Slack disconnected, message queued',
      );
      return;
    }

    try {
      // Detect action response JSON envelope from agent
      // (lifted from quickstack-cc/nanoclaw-private)
      const actionPayload = this.parseActionResponse(text);
      if (actionPayload) {
        await this.app.client.chat.postMessage({
          channel: channelId,
          text: actionPayload.preview || 'Action requires confirmation',
          blocks: actionPayload.slack_blocks,
        });
        logger.info({ jid }, 'Slack action buttons posted');
        return;
      }

      // ... existing splitting + postMessage logic stays unchanged
```

6. **Add `parseActionResponse()` method** (anywhere in the class, by convention near other private helpers):

```typescript
  /**
   * Parse agent output for nanoclaw_action JSON envelope.
   * Returns the action payload if detected, null otherwise.
   *
   * Lifted from quickstack-cc/nanoclaw-private (prospect-pro fork).
   */
  private parseActionResponse(
    text: string,
  ): { preview: string; slack_blocks: any[] } | null {
    // Strip markdown code fences if present
    let cleaned = text.trim();
    if (cleaned.startsWith('```')) {
      cleaned = cleaned
        .replace(/^```(?:json)?\n?/, '')
        .replace(/\n?```$/, '')
        .trim();
    }
    try {
      const parsed = JSON.parse(cleaned);
      if (parsed.nanoclaw_action && parsed.nanoclaw_action.slack_blocks) {
        return {
          preview:
            parsed.nanoclaw_action.preview || 'Action requires confirmation',
          slack_blocks: parsed.nanoclaw_action.slack_blocks,
        };
      }
    } catch {
      // Not JSON — normal text message
    }
    return null;
  }
```

7. **Add `setupActionHandlers()` method** with `nanoclaw_confirm` and `nanoclaw_cancel` Bolt action handlers:

```typescript
  /**
   * Register Slack interactive action handlers for Confirm/Cancel buttons.
   * These run in the NanoClaw host process (not an agent container).
   *
   * Lifted from quickstack-cc/nanoclaw-private (prospect-pro fork) and
   * generalized: env var renamed PROSPECT_PRO_API_URL -> NANOCLAW_ACTION_API_URL.
   */
  private setupActionHandlers(): void {
    // Confirm button: call action-callback endpoint, edit message with outcome
    this.app.action('nanoclaw_confirm', async ({ ack, body }) => {
      await ack();

      const token = (body as any).actions?.[0]?.value as string | undefined;
      const channelId = body.channel?.id;
      const messageTs = (body as any).message?.ts;
      const userId = body.user.id;
      const userName = (body.user as any).name || '';

      if (!channelId || !messageTs) {
        logger.warn('nanoclaw_confirm: missing channel or message ts');
        return;
      }

      try {
        const res = await fetch(
          `${this.apiBaseUrl}/api/nanoclaw/actions/execute/`,
          {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${this.apiKey}`,
              'Content-Type': 'application/json',
              'X-Slack-User-ID': userId,
              'X-Slack-User-Name': userName,
            },
            body: JSON.stringify({ token }),
          },
        );

        const data = (await res.json()) as Record<string, any>;

        if (res.ok) {
          await this.app.client.chat.update({
            channel: channelId,
            ts: messageTs,
            text: `Confirmed: ${data.message || 'Action executed.'}`,
            blocks: [],
          });
          logger.info({ token, userId }, 'Action confirmed and executed');
        } else {
          await this.app.client.chat.update({
            channel: channelId,
            ts: messageTs,
            text: `Failed: ${data.error || 'Unknown error'}`,
            blocks: [],
          });
          logger.warn(
            { token, userId, status: res.status, error: data.error },
            'Action execution failed',
          );
        }
      } catch (err: any) {
        await this.app.client.chat.update({
          channel: channelId,
          ts: messageTs,
          text: `Action failed: ${err.message || 'Network error'}`,
          blocks: [],
        });
        logger.error({ token, userId, err }, 'Action execution error');
      }
    });

    // Cancel button: edit message to show Cancelled (client-side only — does
    // not currently call /actions/reject/. See spec section 7 — deferred.)
    this.app.action('nanoclaw_cancel', async ({ ack, body }) => {
      await ack();

      const channelId = body.channel?.id;
      const messageTs = (body as any).message?.ts;

      if (!channelId || !messageTs) {
        logger.warn('nanoclaw_cancel: missing channel or message ts');
        return;
      }

      await this.app.client.chat.update({
        channel: channelId,
        ts: messageTs,
        text: 'Cancelled.',
        blocks: [],
      });
      logger.info({ userId: body.user.id }, 'Action cancelled by user');
    });

    logger.info('Slack action button handlers registered');
  }
```

- [ ] **Step 3: Update `.env.example` with the new env vars**

Append to `.env.example`:

```
# Optional: when set, Slack action buttons emitted by the agent will be
# wired to call this endpoint on Confirm with NANOCLAW_API_KEY as Bearer.
NANOCLAW_ACTION_API_URL=
NANOCLAW_API_KEY=
```

- [ ] **Step 4: Build and run tests**

```bash
npm run build
npx vitest run src/channels/slack.test.ts
```

Expected: TypeScript compiles. The pre-existing 46 slack tests still pass (we added new methods — they don't break existing behavior unless one of those tests asserted `setupEventHandlers` behavior tightly. If any fails because it expected a method to NOT exist, update the test to ignore the new methods.).

If the existing tests don't assert anything about the action handlers, they'll all still pass. If a few fail because of the new env reads, mock them in the test setup with `process.env.NANOCLAW_API_KEY = ''` or similar.

- [ ] **Step 5: Commit**

```bash
git add src/channels/slack.ts .env.example
git commit -m "slack: lift action-button handler from prospect-pro fork

Adds nanoclaw_confirm / nanoclaw_cancel Bolt handlers and the
nanoclaw_action JSON envelope parser, generalized from prospect-pro's
fork by renaming PROSPECT_PRO_API_URL -> NANOCLAW_ACTION_API_URL.
NANOCLAW_API_KEY remains as the Bearer secret.

Origin: quickstack-cc/nanoclaw-private (prospect-pro)"
```

---

### Task 10: Add the trmm MCP HTTP server to the agent runner (AGENTIC)

**Files:**
- Modify: `container/agent-runner/src/index.ts`
- Modify: `src/container-runner.ts`
- Modify: `.env.example`

The agent SDK reads `mcpServers` from its query options. We add a second entry pointing at the bridge's `/mcp` endpoint. The URL is passed in via the agent container's env, set by the host `container-runner.ts`.

- [ ] **Step 1: Add `mcp__trmm__*` to the `allowedTools` list and add the trmm MCP server**

Find both blocks. They're in the same `query()` options object, so one read locates both:

```bash
grep -n "mcp__nanoclaw\|mcpServers\|allowedTools" container/agent-runner/src/index.ts
```

You should see (a) an `allowedTools` array containing `'mcp__nanoclaw__*'` and (b) the `mcpServers` block. Both need updating.

**1a. Add `mcp__trmm__*` to the `allowedTools` list.** Find the line containing `'mcp__nanoclaw__*'` and add the trmm pattern right after:

```typescript
        'NotebookEdit',
        'mcp__nanoclaw__*',
        'mcp__trmm__*',
      ],
```

(Match existing indentation; whatever surrounding identifier the array is named under, append `mcp__trmm__*` adjacent to `mcp__nanoclaw__*`.)

**1b. Add the trmm MCP server to the `mcpServers` block.** Find:

```typescript
      mcpServers: {
        nanoclaw: {
          command: 'node',
          args: [mcpServerPath],
          env: {
            NANOCLAW_CHAT_JID: containerInput.chatJid,
            NANOCLAW_GROUP_FOLDER: containerInput.groupFolder,
            NANOCLAW_IS_MAIN: containerInput.isMain ? '1' : '0',
          },
        },
      },
```

With:

```typescript
      mcpServers: {
        nanoclaw: {
          command: 'node',
          args: [mcpServerPath],
          env: {
            NANOCLAW_CHAT_JID: containerInput.chatJid,
            NANOCLAW_GROUP_FOLDER: containerInput.groupFolder,
            NANOCLAW_IS_MAIN: containerInput.isMain ? '1' : '0',
          },
        },
        // trmm-mcp HTTP server — set TRMM_MCP_URL in the orchestrator's
        // env (forwarded into agent containers by container-runner.ts).
        // Empty string disables; non-empty enables HTTP MCP transport.
        ...(process.env.TRMM_MCP_URL
          ? {
              trmm: {
                type: 'http' as const,
                url: process.env.TRMM_MCP_URL,
              },
            }
          : {}),
      },
```

- [ ] **Step 2: Modify `src/container-runner.ts` to forward `TRMM_MCP_URL` into agent containers**

`container-runner.ts` has a section that builds the env for the spawned agent container. Find where it sets up the env (look for `--env` flags or an `env` object in the docker run args). Add a passthrough:

Locate the existing env construction block (likely a function that builds a list of `--env` Docker CLI args, around the section that sets `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_VERTEX_PROJECT_ID`, etc.).

Add `TRMM_MCP_URL` to the list of env vars forwarded:

```typescript
    // Pass through to spawned agent container if set in orchestrator env
    if (process.env.TRMM_MCP_URL) {
      args.push('--env', `TRMM_MCP_URL=${process.env.TRMM_MCP_URL}`);
    }
```

(Adjust to the file's actual idiom — if it uses a different `args` variable name or builds env differently, conform to that.)

> **Note for executor:** If `container-runner.ts` builds env via a typed object rather than CLI args, add `TRMM_MCP_URL: process.env.TRMM_MCP_URL` to that object instead. The intent is "if `TRMM_MCP_URL` is set in the orchestrator's env, forward it into the agent container's env."

- [ ] **Step 3: Document `TRMM_MCP_URL` in `.env.example`**

Append to `.env.example`:

```
# When set, agent containers receive an additional MCP server pointing at
# this URL (HTTP transport). Used to give recon access to trmm-mcp tools
# served by the nanormm approval-bridge.
TRMM_MCP_URL=
```

- [ ] **Step 4: Build**

```bash
npm run build
```

Expected: clean TypeScript compile. If a type error mentions `'http' as const` not being assignable to the SDK's `mcpServers` type, check the SDK version: `node -e "console.log(require('@anthropic-ai/claude-agent-sdk/package.json').version)"`. The HTTP transport type in older SDKs was named slightly differently — `'sse'` is the most common alternative. If `'http'` doesn't typecheck, try `'sse'` or check the SDK's own docs for `Options.mcpServers`.

If you change `'http'` → `'sse'`, also update the bridge's transport in Task 2's `mcp_app.py` accordingly. The Python SDK supports both.

- [ ] **Step 5: Run all tests**

```bash
npx vitest run
```

Expected: all green.

- [ ] **Step 6: Commit and push**

```bash
git add container/agent-runner/src/index.ts src/container-runner.ts .env.example
git commit -m "agent-runner: register trmm-mcp HTTP MCP server when TRMM_MCP_URL is set

container-runner forwards TRMM_MCP_URL from orchestrator env into spawned
agent container env. Empty / unset disables the trmm MCP entry."

git push origin main
```

---

## Phase 6: VM deployment (OPERATOR)

### Task 11: Set up Linux users + filesystem layout on the VM (OPERATOR)

SSH into the qsrmm GCE VM. Run all of these as root (sudo).

- [ ] **Step 1: Create the `nanormm` user (for the bridge)**

```bash
sudo useradd --system --create-home --home-dir /home/nanormm \
    --shell /bin/bash nanormm
```

- [ ] **Step 2: Create the `nanoclaw` user (for the orchestrator)**

```bash
sudo useradd --system --create-home --home-dir /home/nanoclaw \
    --shell /bin/bash nanoclaw
sudo usermod -aG docker nanoclaw
```

Verify:

```bash
id nanormm
id nanoclaw  # should include 'docker' group
```

- [ ] **Step 3: Create install + log directories**

```bash
sudo install -d -m 0755 -o nanormm -g nanormm /opt/nanormm /var/log/nanormm
sudo install -d -m 0755 -o nanoclaw -g nanoclaw /opt/nanoclaw
sudo install -d -m 0755 /etc/nanormm
```

- [ ] **Step 4: Provision Postgres `nanormm` DB and confirm Redis DB 11**

```bash
# Postgres
sudo -u postgres createuser nanormm --pwprompt
# Pick a strong password; you'll use it in /etc/nanormm/bridge.env
sudo -u postgres createdb -O nanormm nanormm

# Redis (no auth on local TRMM Redis by default — confirm DB 11 is unused)
redis-cli -n 11 dbsize
# should print: (integer) 0  — empty DB 11 is fine
```

- [ ] **Step 5: Apply the audit schema**

```bash
# Clone qsrmm temporarily to get the migration; this gets symlinked properly later
sudo -u nanormm git clone --depth 1 git@github.com:quickstack-cc/qsrmm.git \
    /opt/nanormm/qsrmm
sudo -u nanormm psql postgresql://nanormm@localhost:5432/nanormm \
    -f /opt/nanormm/qsrmm/nanormm/trmm-mcp/trmm_mcp/migrations/001_init.sql
```

(You'll be prompted for the nanormm DB password.)

Verify:

```bash
psql postgresql://nanormm@localhost:5432/nanormm -c '\dt'
# Should list nanormm_actions
```

---

### Task 12: Deploy the bridge as a systemd service (OPERATOR)

- [ ] **Step 1: Create `/etc/nanormm/bridge.env` with real values**

```bash
sudo cp /opt/nanormm/qsrmm/nanormm/deploy/bridge.env.example /etc/nanormm/bridge.env
sudo chmod 600 /etc/nanormm/bridge.env
sudo chown nanormm:nanormm /etc/nanormm/bridge.env
sudo $EDITOR /etc/nanormm/bridge.env
```

Replace placeholders:
- `NANORMM_BRIDGE_API_KEY=<openssl rand -hex 32>` (run that command separately and paste the result; **save it for nanoclaw's .env in Task 13**)
- `TRMM_API_BASE` — the actual TRMM URL on this VM (probably `https://api.qsrmm.example.com`)
- `TRMM_API_TOKEN` — Knox token for a TRMM service account (create one in TRMM admin if you don't have one)
- `NANORMM_AUDIT_DSN` — `postgresql://nanormm:<password>@localhost:5432/nanormm`

- [ ] **Step 2: Run the install script**

```bash
sudo /opt/nanormm/qsrmm/nanormm/deploy/install-bridge.sh
```

Expected output: ends with `OK: approval-bridge is running`. If not, follow the journal hint the script prints.

- [ ] **Step 3: Verify endpoints**

```bash
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok"}

# MCP endpoint
curl -s -X POST http://127.0.0.1:8000/mcp/ \
    -H 'accept: application/json, text/event-stream' \
    -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
    | head -c 200
echo
# Should return a JSON-RPC response containing serverInfo
```

- [ ] **Step 4: Confirm the firewall rule**

```bash
gcloud compute firewall-rules list --filter='allowed.ports=8000' --format='value(name)'
```

Expected: empty (or only an internal rule). The bridge port must NOT be exposed externally.

---

### Task 13: Install nanoclaw natively on the VM (OPERATOR)

- [ ] **Step 1: Clone the nanormm-nanoclaw fork**

```bash
sudo -u nanoclaw git clone git@github.com:quickstack-cc/nanormm-nanoclaw.git \
    /opt/nanoclaw
```

- [ ] **Step 2: Run the official installer**

```bash
sudo -u nanoclaw -i bash -c 'cd /opt/nanoclaw && bash nanoclaw.sh'
```

Follow the interactive prompts. The script handles npm install, Docker container build for agents, and creates a user-level systemd unit at `~nanoclaw/.config/systemd/user/nanoclaw.service` (or similar — depends on the script's output).

When prompted about Anthropic auth, **decline** (or skip OneCLI vault setup) — we're going Vertex AI instead, configured in Step 3.

- [ ] **Step 3: Configure `/opt/nanoclaw/.env`**

```bash
sudo -u nanoclaw $EDITOR /opt/nanoclaw/.env
sudo chmod 600 /opt/nanoclaw/.env
```

Set the following (substitute the values gathered earlier):

```bash
SLACK_BOT_TOKEN=xoxb-<from Task 6 step 6>
SLACK_APP_TOKEN=xapp-<from Task 6 step 2>

# Bridge wiring — the API key MUST match NANORMM_BRIDGE_API_KEY in /etc/nanormm/bridge.env
NANOCLAW_ACTION_API_URL=http://host.docker.internal:8000
NANOCLAW_API_KEY=<paste the same hex string used for NANORMM_BRIDGE_API_KEY>

# trmm-mcp HTTP MCP server — agent containers will see this and connect
TRMM_MCP_URL=http://host.docker.internal:8000/mcp

# Vertex AI configuration
CLAUDE_CODE_USE_VERTEX=1
ANTHROPIC_VERTEX_PROJECT_ID=qsrmm-494222
CLOUD_ML_REGION=us-east5
ANTHROPIC_DEFAULT_MODEL=claude-haiku-4-5
```

- [ ] **Step 4: Sync to nanoclaw's container env (per add-slack skill convention)**

```bash
sudo -u nanoclaw bash -c 'cd /opt/nanoclaw && mkdir -p data/env && cp .env data/env/env'
```

- [ ] **Step 5: Build and start nanoclaw service**

```bash
sudo -u nanoclaw -i bash -c 'cd /opt/nanoclaw && npm run build'
# Restart per nanoclaw.sh's systemd unit (path varies — check your install)
sudo -u nanoclaw -i bash -c 'systemctl --user restart nanoclaw' || \
    sudo -u nanoclaw -i bash -c 'launchctl kickstart -k gui/$(id -u)/com.nanoclaw' || \
    echo "Manual restart needed — check what nanoclaw.sh installed"
```

- [ ] **Step 6: Verify nanoclaw is running**

```bash
sudo -u nanoclaw -i tail -n 50 /opt/nanoclaw/logs/nanoclaw.log
# or wherever nanoclaw.sh configures logging
```

Expected: a line like `Connected to Slack` and `Slack action button handlers registered`.

If you see `SLACK_BOT_TOKEN ... must be set`, the env didn't sync to `data/env/env` — repeat Step 4 and restart.

---

### Task 14: Register `#rmm-alerts` as recon's main channel + write `groups/recon/CLAUDE.md` (OPERATOR)

- [ ] **Step 1: Register the channel**

On the VM, as `nanoclaw`:

```bash
sudo -u nanoclaw -i bash -c 'cd /opt/nanoclaw && \
    npx tsx setup/index.ts --step register -- \
    --jid "slack:<CHANNEL_ID_FROM_TASK_6>" \
    --name "rmm-alerts" \
    --folder "recon" \
    --channel slack \
    --is-main \
    --no-trigger-required'
```

Replace `<CHANNEL_ID_FROM_TASK_6>` with the actual `C0...` channel ID.

(Note: we use `--folder recon` so the per-group memory dir is `groups/recon/`.)

Confirm registration:

```bash
sudo -u nanoclaw sqlite3 /opt/nanoclaw/store/messages.db \
    "SELECT jid, folder, is_main FROM registered_groups WHERE jid LIKE 'slack:%'"
```

- [ ] **Step 2: Write the recon system prompt**

```bash
sudo -u nanoclaw mkdir -p /opt/nanoclaw/groups/recon
sudo -u nanoclaw $EDITOR /opt/nanoclaw/groups/recon/CLAUDE.md
```

Contents:

```markdown
# recon — TacticalRMM alert triage agent

You are recon. You watch the `#rmm-alerts` Slack channel for TacticalRMM
webhook posts. When a new alert arrives, your job is to enrich it and
draft a recommended remediation.

## Workflow for every alert

1. Read the alert text in the message you were triggered on.
2. Use the `mcp__trmm__*` tools (these come from the trmm MCP server) to
   gather context:
   - `mcp__trmm__get_alert(id)` — alert details
   - `mcp__trmm__get_agent(id)` — affected agent's state
   - `mcp__trmm__agent_recent_checks(id)` — recent check history
   - `mcp__trmm__search_past_alerts(query)` — similar past alerts
3. Synthesize: in 3-6 sentences, summarize what you found.
4. Recommend a remediation. Cite the specific tool you'd use (e.g.
   "kill_process(agent_id=X, pid=Y) would clear the runaway worker").
5. If a write action is warranted, **call the tool now**. The bridge will
   return a `nanoclaw_action` envelope — emit that envelope verbatim as
   your final response. Do NOT describe it; do NOT add commentary around
   it; do NOT wrap it in markdown. Just the raw JSON.

## Rules

- Never make up TRMM data. Always tool-call to verify before claiming a fact.
- Never call write tools without first showing the tech the read-side
  context that justifies the write.
- Stay concise. Triage threads should be readable in 30 seconds.
- If a tool errors (network, permission, etc.), post a brief error in
  the thread and stop. Do not retry silently.
- Forbidden actions (e.g. `uninstall_agent`) will return a `denied` status
  from the dispatcher — surface that fact to the human.

## On `nanoclaw_action` envelope emission

When a write tool returns:

```
{
  "status": "pending",
  "action_id": "act_abc123",
  "summary": "Kill PID 4123 on agent DC01",
  "nanoclaw_action": {
    "preview": "...",
    "slack_blocks": [...]
  }
}
```

Your final response must be the JSON object `{"nanoclaw_action": {...}}` —
just the envelope, nothing else. NanoClaw's Slack channel detects this and
posts the Confirm/Cancel buttons.
```

- [ ] **Step 3: Restart nanoclaw to pick up the new group dir**

```bash
sudo -u nanoclaw -i bash -c 'systemctl --user restart nanoclaw' \
    || sudo -u nanoclaw -i bash -c 'launchctl kickstart -k gui/$(id -u)/com.nanoclaw'
```

- [ ] **Step 4: Send a test message in `#rmm-alerts`**

In Slack, post a message in `#rmm-alerts`: `@recon are you online?`

Expected: recon replies (in the channel, not threaded for this kind of message). May take 5-15 seconds for the agent container to spin up the first time.

If recon doesn't reply, tail the nanoclaw logs:

```bash
sudo -u nanoclaw -i tail -f /opt/nanoclaw/logs/nanoclaw.log
```

Common issues:
- `mcp__trmm__*` tools not available → `TRMM_MCP_URL` not in agent container env. Confirm `data/env/env` has it; check that `container-runner.ts` is forwarding it.
- Vertex permission denied → re-check Task 5.
- Slack mention not triggering → recon is registered as `--is-main --no-trigger-required`, so it responds to ALL messages in that channel; confirm via the sqlite query in Step 1.

---

## Phase 7: Smoke test (OPERATOR)

### Task 15: End-to-end smoke against a sandbox alert (OPERATOR)

The acceptance test for Plan 3.

- [ ] **Step 1: Create a sandbox client + agent in TRMM**

In TRMM web UI:
1. **Clients** → **Add Client** → name: `nanormm-smoke`
2. Add a single site under it: `nanormm-smoke-site`
3. Either install the TRMM agent on a throwaway VM and have it report in, OR manually create an agent record via TRMM admin CLI (whichever your TRMM instance supports).

Note the agent ID — it'll be in the format `<hex-string>` in the URL when you click into the agent.

- [ ] **Step 2: Trigger a synthetic alert via TRMM API**

```bash
# Get a Knox token for your TRMM admin user (use the existing one used for bridge)
TRMM_TOKEN=<your knox token>
TRMM_BASE=<your TRMM URL>
SMOKE_AGENT_ID=<from step 1>

curl -X POST "${TRMM_BASE}/api/v3/agents/${SMOKE_AGENT_ID}/alerts/" \
    -H "X-API-KEY: ${TRMM_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d '{
        "alert_type": "availability",
        "severity": "warning",
        "message": "[smoke] high CPU detected on smoke agent",
        "agent": "'${SMOKE_AGENT_ID}'"
    }'
```

(Adjust the URL/path/body to match your TRMM's actual alert API — this is illustrative. The point is: trigger the existing TRMM webhook by creating an alert.)

- [ ] **Step 3: Watch `#rmm-alerts` for ≤30 seconds**

Within 30 seconds of the API call:
1. The existing TRMM webhook posts the alert message to `#rmm-alerts`.
2. Recon threads a reply within ~10–20 more seconds.

Expected reply structure:
- 2-4 sentences of triage referencing the smoke agent's name
- A recommended remediation line (e.g. "I'd run `kill_process` on the runaway worker")
- For this smoke (an availability alert with no clear write target), recon may not actually emit a write tool — that's fine; the read-side proves the chain.

If you want to also test the approve flow, configure the synthetic alert message to mention something kill_process-shaped (e.g. include a fake PID and process name in the alert), and prompt recon to act.

- [ ] **Step 4: Verify the write-action approve flow**

Post a follow-up message in the alert thread: `@recon kill PID 1234 on this agent please.`

Expected:
1. Recon calls `kill_process(agent_id=<smoke>, pid=1234)`.
2. Bridge returns the `nanoclaw_action` envelope.
3. Recon emits the envelope verbatim.
4. nanoclaw's slack channel posts a follow-up message in the thread with Confirm/Cancel buttons.

- [ ] **Step 5: Click Confirm**

Click the green Confirm button. Within ~5 seconds, the message updates to `Confirmed: Executed: ...`.

(The actual TRMM kill_process call might fail since the smoke agent isn't a real machine — that's OK for smoke. The test is whether the round-trip works, not whether kill_process succeeds.)

- [ ] **Step 6: Verify the audit log**

```bash
sudo -u nanormm psql postgresql://nanormm@localhost:5432/nanormm \
    -c "SELECT action_id, tool_name, approved_by, executed_at, summary
        FROM nanormm_actions
        ORDER BY pending_at DESC LIMIT 5;"
```

Expected: a row with:
- `tool_name = kill_process`
- `approved_by = <your slack username>`
- `executed_at IS NOT NULL`
- `summary` matching the action description

- [ ] **Step 7: Smoke complete**

If steps 3–6 all worked, **Plan 3 is shipped.** Recon is live in `#rmm-alerts`.

If any step failed, capture the failure (logs, screenshots, audit row state) and surface to me — we'll triage from there.

---

## Self-review checklist

After all tasks, verify:

1. **Spec coverage:**
   - ☐ Bridge has `/mcp` HTTP endpoint serving trmm-mcp tools — Tasks 1–2.
   - ☐ Dockerfile + .dockerignore deleted — Task 3.
   - ☐ Policy.yaml audit pass — Task 3.
   - ☐ Bridge runs as systemd service on VM — Tasks 4 + 12.
   - ☐ Vertex AI service account configured — Task 5.
   - ☐ Slack app for @recon created — Task 6.
   - ☐ Nanormm-nanoclaw fork created with three patches — Tasks 7–10.
   - ☐ Native nanoclaw deploy on VM — Task 13.
   - ☐ Recon registered for #rmm-alerts with system prompt — Task 14.
   - ☐ End-to-end smoke green — Task 15.

2. **No-placeholders confirmation:** every step shows actual code or actual commands; no "TBD" or "implement later".

3. **Cross-repo consistency:**
   - Bridge env `NANORMM_BRIDGE_API_KEY` and nanoclaw env `NANOCLAW_API_KEY` MUST be the same value (Task 13 step 3 vs Task 12 step 1).
   - Bridge URL `http://host.docker.internal:8000` is what nanoclaw's `NANOCLAW_ACTION_API_URL` points to AND `TRMM_MCP_URL` points to (with `/mcp` suffix for the latter).
   - Channel ID captured in Task 6 step 7 is used in Task 14 step 1.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-29-nanormm-recon.md`. Two execution options:

1. **Subagent-Driven (recommended for code-only Phases 1, 2, 5)** — I dispatch a fresh subagent per task, review between tasks. Operator phases (3, 4, 6, 7) you do yourself; surface results back and the subagent path resumes for the next code task.

2. **Inline Execution** — I execute code tasks in this session with checkpoints; you handle operator tasks as we hit them.

Which approach?
