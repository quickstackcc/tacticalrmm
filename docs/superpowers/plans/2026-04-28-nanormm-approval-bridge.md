# nanormm `approval-bridge` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Slack approval round-trip for `trmm-mcp`. Build a small FastAPI service (`approval-bridge`) that receives Confirm/Cancel callbacks from nanoclaw, marks the matching pending action approved or rejected in Redis, and drives execution through `trmm-mcp`'s existing `Dispatcher.resume()`. Also extend `trmm-mcp`'s pending response with a Slack-blocks envelope (`nanoclaw_action`) ready for the agent to emit verbatim.

**Architecture:** Slack uses Socket Mode (Bolt WebSocket) — there is **no** public ingress. nanoclaw posts message blocks emitted by the agent in a `nanoclaw_action` JSON envelope; when a tech clicks the Confirm button, nanoclaw's `nanoclaw_confirm` action handler POSTs to `${PROSPECT_PRO_API_URL}/api/nanoclaw/actions/execute/` with a Bearer-authenticated `{token}` body. `approval-bridge` hosts that endpoint inside the `nanormm_default` Docker network (no published port to the public internet, only reachable from nanoclaw containers). It imports the `trmm_mcp` Python package, instantiates the same `Dispatcher` configured against shared Redis/Postgres/TRMM, marks the action approved, calls `dispatcher.resume()`, and returns a human-readable `{message}` for nanoclaw to surface in Slack via `chat.update`.

**Why bridge as a sibling package, not folded into trmm-mcp:** the spec calls for separate containers. Two reasons survive Socket Mode: (1) trmm-mcp keeps a single transport (stdio MCP); the bridge owns inbound HTTP. (2) Container restart of one doesn't drop the other's state. Both processes share Redis-backed approval state, so they're free to execute against the same dispatcher logic.

**Tech Stack:**
- Python 3.11.8 (matches trmm-mcp pin)
- `fastapi` + `uvicorn[standard]` (HTTP server)
- `pydantic` v2 (request/response models)
- `trmm-mcp` (path dependency on sibling package — pulls in `redis`, `psycopg`, `httpx`, `pyyaml`)
- `pytest` + `pytest-asyncio` + `httpx` (`AsyncClient` for endpoint tests) + `fakeredis` + `pytest-postgresql` + `respx`
- `ruff` (lint/format; matches trmm-mcp)

**Out of scope (deferred to later plans):**
- nanoclaw bot configuration (`recon`, `operator` system prompts, model tiering, container wiring)
- `docker-compose.yml` for the four-container stack
- Synthetic end-to-end smoke against real Slack
- Modifying nanoclaw to call a reject endpoint on Cancel (its current `nanoclaw_cancel` handler is purely client-side; we ship `/actions/reject/` for symmetry but it won't fire from Slack until nanoclaw is patched)
- nginx route — not needed; Bolt Socket Mode means no public ingress

---

## File structure

This plan creates `nanormm/approval-bridge/` as a self-contained Python package alongside `nanormm/trmm-mcp/`, plus a small extension to `trmm-mcp/trmm_mcp/tools/_base.py` to attach a Slack-blocks envelope to pending responses.

```
nanormm/
├── policy.yaml                                # (existing, no changes)
├── trmm-mcp/                                  # (existing — Plan 1)
│   └── trmm_mcp/
│       └── tools/
│           └── _base.py                       # MODIFIED: extend pending response with nanoclaw_action envelope
└── approval-bridge/                           # NEW
    ├── pyproject.toml
    ├── README.md
    ├── Dockerfile
    ├── approval_bridge/
    │   ├── __init__.py
    │   ├── __main__.py                        # `python -m approval_bridge` -> uvicorn entrypoint
    │   ├── app.py                             # FastAPI app factory + route registration
    │   ├── auth.py                            # Bearer token dependency (NANORMM_BRIDGE_API_KEY)
    │   ├── settings.py                        # Pydantic Settings: env config
    │   ├── deps.py                            # Dispatcher factory wired against shared Redis/Postgres/TRMM
    │   ├── models.py                          # Pydantic request/response models
    │   └── routes.py                          # /actions/execute, /actions/reject, /healthz
    └── tests/
        ├── __init__.py
        ├── conftest.py                        # fixtures: fakeredis, pytest-postgresql, respx-mocked TRMM, AsyncClient
        ├── test_auth.py                       # Bearer header validation
        ├── test_settings.py                   # env var parsing
        ├── test_healthz.py
        ├── test_execute.py                    # /actions/execute/ — happy path, idempotency, error mapping
        ├── test_reject.py                     # /actions/reject/
        └── test_envelope.py                   # trmm-mcp dispatcher pending response shape
```

**File responsibility summary:**

- `app.py` — `create_app()` factory: builds FastAPI instance, mounts routes, attaches global Dispatcher dep. No top-level state — safe to call multiple times in tests.
- `auth.py` — single FastAPI dependency `verify_bearer()` that reads `Authorization: Bearer ...` and compares to `BridgeSettings.api_key` in constant time.
- `settings.py` — `BridgeSettings(BaseSettings)`: `api_key`, `listen_host`, `listen_port`, plus the trmm-mcp env vars needed to construct the Dispatcher (Redis URL, Postgres DSN, TRMM URL+token, policy.yaml path, pending TTL).
- `deps.py` — `build_dispatcher(settings) -> Dispatcher`: factory that imports trmm-mcp's Policy, ApprovalRegistry, AuditLog, TrmmClient, and the registered ToolRegistry, then wires them together. Single Dispatcher instance held in app state.
- `models.py` — `ExecuteRequest`, `RejectRequest`, `ActionResponse`, `ErrorResponse`.
- `routes.py` — three handlers: `execute_action`, `reject_action`, `healthz`. Each route translates dispatcher exceptions into appropriate HTTP status + `{error}` body.
- `tools/_base.py` (modified) — `Dispatcher.dispatch()`'s pending branch now constructs and returns a `nanoclaw_action` envelope alongside `action_id` and `summary`, ready for the agent to emit verbatim.

---

## Task 0: Repo scaffolding

**Files:**
- Create: `nanormm/approval-bridge/pyproject.toml`
- Create: `nanormm/approval-bridge/README.md`
- Create: `nanormm/approval-bridge/approval_bridge/__init__.py` (empty)
- Create: `nanormm/approval-bridge/tests/__init__.py` (empty)
- Create: `nanormm/approval-bridge/.gitignore`

- [ ] **Step 1: Create the `approval-bridge/` directory and pyproject.toml**

```bash
mkdir -p nanormm/approval-bridge/approval_bridge nanormm/approval-bridge/tests
```

Create `nanormm/approval-bridge/pyproject.toml`:

```toml
[project]
name = "approval-bridge"
version = "0.1.0"
description = "FastAPI service that closes the Slack approval round-trip for trmm-mcp."
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "trmm-mcp",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "fakeredis>=2.21",
    "pytest-postgresql>=5",
    "respx>=0.20",
    "ruff>=0.4",
]

[tool.uv.sources]
trmm-mcp = { path = "../trmm-mcp" }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["approval_bridge"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "S"]
ignore = ["S101"]  # pytest assert

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S105", "S106"]  # hardcoded passwords/tokens in fixtures
```

- [ ] **Step 2: Stub README.md**

Create `nanormm/approval-bridge/README.md`:

```markdown
# approval-bridge

FastAPI service that closes the Slack approval round-trip for `trmm-mcp`.

When a tech clicks the Confirm button on a `nanoclaw_action` Slack message,
nanoclaw's `nanoclaw_confirm` handler POSTs to this service with a Bearer-
authenticated `{token}` body. The bridge marks the matching pending action
approved in Redis and calls `trmm_mcp.tools._base.Dispatcher.resume()` to
execute the action against TRMM.

## Endpoints

- `POST /api/nanoclaw/actions/execute/` — execute an approved action
- `POST /api/nanoclaw/actions/reject/`  — record an explicit rejection
- `GET  /healthz`                        — Docker healthcheck

## Environment variables

The bridge shares trmm-mcp's env-var naming so a single `.env` drives both
processes. Bridge-specific vars start with `NANORMM_BRIDGE_`.

| Var | Purpose |
|---|---|
| `NANORMM_BRIDGE_API_KEY`   | Shared bearer secret with nanoclaw (`NANOCLAW_API_KEY` on its end) |
| `NANORMM_BRIDGE_HOST`      | Bind host (default `0.0.0.0`) |
| `NANORMM_BRIDGE_PORT`      | Bind port (default `8000`) |
| `TRMM_API_BASE`            | shared with trmm-mcp |
| `TRMM_API_TOKEN`           | shared with trmm-mcp |
| `NANORMM_POLICY_PATH`      | shared with trmm-mcp |
| `NANORMM_REDIS_URL`        | shared with trmm-mcp |
| `NANORMM_AUDIT_DSN`        | shared with trmm-mcp |
| `NANORMM_PENDING_TTL_SECONDS` | shared with trmm-mcp (default 1800) |

## Run locally

    uv pip install -e '.[dev]'
    NANORMM_BRIDGE_API_KEY=devsecret python -m approval_bridge
```

- [ ] **Step 3: Stub package and test `__init__.py`s, plus a `.gitignore`**

Create empty `nanormm/approval-bridge/approval_bridge/__init__.py` and `nanormm/approval-bridge/tests/__init__.py`.

Create `nanormm/approval-bridge/.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.venv/
```

- [ ] **Step 4: Create the venv and install dev deps**

Run from `nanormm/approval-bridge/`:

```bash
cd nanormm/approval-bridge
uv venv --python 3.11.8 .venv
. .venv/bin/activate
uv pip install -e '.[dev]'
```

Expected: trmm-mcp installs from `../trmm-mcp` (path source), all dev deps install cleanly. (If `uv pip install -e` flags `externally-managed-environment`, activate the venv first as above — Plan 1 hit this same issue.)

- [ ] **Step 5: Confirm the package imports**

```bash
python -c "import approval_bridge; print(approval_bridge.__name__)"
python -c "import trmm_mcp; print(trmm_mcp.__name__)"
```

Expected: both print their module name without errors.

- [ ] **Step 6: Confirm pytest discovers zero tests**

```bash
pytest -q
```

Expected: `no tests ran` (zero collected, zero failed). Confirms test infra works.

- [ ] **Step 7: Commit**

```bash
git add nanormm/approval-bridge/
git commit -m "approval-bridge: package scaffolding and pyproject"
```

---

## Task 1: Settings module

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/settings.py`
- Create: `nanormm/approval-bridge/tests/test_settings.py`

The bridge reuses `trmm_mcp.settings.Settings` for the shared infra vars (`TRMM_API_BASE`, `TRMM_API_TOKEN`, `NANORMM_POLICY_PATH`, `NANORMM_REDIS_URL`, `NANORMM_AUDIT_DSN`, `NANORMM_PENDING_TTL_SECONDS`) and adds three bridge-specific fields (`NANORMM_BRIDGE_API_KEY`, `NANORMM_BRIDGE_HOST`, `NANORMM_BRIDGE_PORT`). Composing instead of duplicating keeps the two services from drifting apart on env conventions.

- [ ] **Step 1: Write the failing test**

Create `nanormm/approval-bridge/tests/test_settings.py`:

```python
import pytest

from approval_bridge.settings import BridgeSettings


@pytest.fixture
def base_env(monkeypatch):
    """Set every shared trmm-mcp var so only bridge-specific fields drive
    the test outcomes.
    """
    monkeypatch.setenv("TRMM_API_BASE", "https://api.example.com")
    monkeypatch.setenv("TRMM_API_TOKEN", "trmm-token")
    monkeypatch.setenv("NANORMM_POLICY_PATH", "/etc/nanormm/policy.yaml")
    monkeypatch.setenv("NANORMM_REDIS_URL", "redis://localhost:6379/11")
    monkeypatch.setenv(
        "NANORMM_AUDIT_DSN",
        "postgresql://nanormm:pw@localhost:5432/nanormm",
    )


def test_loads_required_env_vars(monkeypatch, base_env):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "secret-token")

    s = BridgeSettings()

    assert s.api_key == "secret-token"
    assert s.host == "0.0.0.0"  # default
    assert s.port == 8000  # default

    # Trmm-mcp shared settings exposed via .trmm
    assert s.trmm.trmm_api_base == "https://api.example.com"
    assert s.trmm.trmm_api_token == "trmm-token"
    assert str(s.trmm.policy_path).endswith("policy.yaml")
    assert s.trmm.redis_url.startswith("redis://")
    assert s.trmm.audit_dsn.startswith("postgresql://")
    assert s.trmm.pending_action_ttl_seconds == 1800


def test_missing_api_key_raises(monkeypatch, base_env):
    monkeypatch.delenv("NANORMM_BRIDGE_API_KEY", raising=False)

    with pytest.raises(Exception) as exc:
        BridgeSettings()

    # pydantic ValidationError or similar
    msg = str(exc.value).lower()
    assert "api_key" in msg or "field required" in msg or "nanormm_bridge_api_key" in msg


def test_overrides_host_and_port(monkeypatch, base_env):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "x")
    monkeypatch.setenv("NANORMM_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("NANORMM_BRIDGE_PORT", "9001")

    s = BridgeSettings()
    assert s.host == "127.0.0.1"
    assert s.port == 9001
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_settings.py -v
```

Expected: ImportError / ModuleNotFoundError on `approval_bridge.settings`.

- [ ] **Step 3: Implement `settings.py`**

Create `nanormm/approval-bridge/approval_bridge/settings.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from trmm_mcp.settings import Settings as TrmmMcpSettings


class BridgeSettings(BaseSettings):
    """Environment-driven configuration for approval-bridge.

    Composes `trmm_mcp.settings.Settings` (shared Redis/Postgres/TRMM/policy
    config) under `.trmm` and adds three bridge-specific fields: the bearer
    secret nanoclaw must present, plus listen host/port.

    Composition (not subclassing) keeps the bridge's settings surface tight:
    only ``api_key``, ``host``, ``port`` are bridge-owned, with everything
    else delegated unchanged to trmm-mcp's existing pydantic model.
    """

    model_config = SettingsConfigDict(extra="ignore")

    api_key: str = Field(alias="NANORMM_BRIDGE_API_KEY")
    host: str = Field(default="0.0.0.0", alias="NANORMM_BRIDGE_HOST")
    port: int = Field(default=8000, alias="NANORMM_BRIDGE_PORT")

    @property
    def trmm(self) -> TrmmMcpSettings:
        """Lazily-constructed trmm-mcp settings. Reading this property is
        what triggers env var validation for the shared fields, so missing
        TRMM_API_BASE etc. surfaces as a normal pydantic error at startup.
        """
        if not hasattr(self, "_trmm"):
            object.__setattr__(self, "_trmm", TrmmMcpSettings())
        return self._trmm
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_settings.py -v
```

Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add approval_bridge/settings.py tests/test_settings.py
git commit -m "approval-bridge: settings composing trmm-mcp Settings"
```

---

## Task 2: Bearer auth dependency

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/auth.py`
- Create: `nanormm/approval-bridge/tests/test_auth.py`

A FastAPI `Depends()` that reads the `Authorization` header, asserts `Bearer <token>` shape, and compares the token to `settings.api_key` in constant time. Returns nothing on success; raises `HTTPException(401)` on any failure.

- [ ] **Step 1: Write the failing test**

Create `nanormm/approval-bridge/tests/test_auth.py`:

```python
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from approval_bridge.auth import verify_bearer
from approval_bridge.settings import BridgeSettings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "the-secret")
    monkeypatch.setenv("NANORMM_REDIS_URL", "redis://x/0")
    monkeypatch.setenv("NANORMM_AUDIT_DSN", "postgresql://x/x")
    monkeypatch.setenv("TRMM_API_BASE", "https://x")
    monkeypatch.setenv("TRMM_API_TOKEN", "x")
    monkeypatch.setenv("NANORMM_POLICY_PATH", "/etc/policy.yaml")
    return BridgeSettings()


@pytest.fixture
def client(settings):
    app = FastAPI()

    @app.get("/protected")
    def protected(_: None = verify_bearer(settings)):
        return {"ok": True}

    return TestClient(app)


def test_missing_authorization_header_returns_401(client):
    r = client.get("/protected")
    assert r.status_code == 401


def test_wrong_scheme_returns_401(client):
    r = client.get("/protected", headers={"Authorization": "Token the-secret"})
    assert r.status_code == 401


def test_wrong_secret_returns_401(client):
    r = client.get("/protected", headers={"Authorization": "Bearer not-the-secret"})
    assert r.status_code == 401


def test_correct_secret_passes(client):
    r = client.get("/protected", headers={"Authorization": "Bearer the-secret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_auth.py -v
```

Expected: ImportError on `approval_bridge.auth`.

- [ ] **Step 3: Implement `auth.py`**

Create `nanormm/approval-bridge/approval_bridge/auth.py`:

```python
import hmac

from fastapi import Depends, Header, HTTPException, status

from .settings import BridgeSettings


def verify_bearer(settings: BridgeSettings):
    """Build a FastAPI dependency that validates the Bearer header against
    `settings.api_key`. Returns the dependency callable for use in route
    signatures: ``def route(_=verify_bearer(settings))``.

    Comparison is constant-time to defeat timing attacks; mismatches and
    malformed headers all return a generic 401 with no detail (don't leak
    whether the auth header was missing vs. wrong).
    """
    expected = settings.api_key

    async def _dep(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return Depends(_dep)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_auth.py -v
```

Expected: all four tests pass.

- [ ] **Step 5: Commit**

```bash
git add approval_bridge/auth.py tests/test_auth.py
git commit -m "approval-bridge: Bearer auth dependency with constant-time compare"
```

---

## Task 3: Refactor trmm-mcp to expose `build_dispatcher`, then write the bridge factory + shared fixtures

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/server.py`
- Create: `nanormm/approval-bridge/approval_bridge/deps.py`
- Create: `nanormm/approval-bridge/tests/conftest.py`
- Create: `nanormm/approval-bridge/tests/test_deps.py`

trmm-mcp's `server.build_server()` already wires the full dispatcher graph, but it also creates an `mcp.server.Server` instance the bridge doesn't need. Extract the dispatcher-construction lines into a public `build_dispatcher(settings)` helper that both `build_server()` and the bridge can call. This is a small, focused change to trmm-mcp; the bridge then has a clean public dependency.

### 3a. trmm-mcp refactor

- [ ] **Step 1: Read current `server.py` build_server**

The current `build_server()` (lines roughly 29–55 of `trmm-mcp/trmm_mcp/server.py`) constructs Settings → Policy → ApprovalRegistry → AuditLog → TrmmClient → ToolRegistry → Dispatcher, then wraps in MCP. We'll keep that whole flow but split it into two functions.

- [ ] **Step 2: Modify `server.py` to extract `build_dispatcher`**

Replace `build_server()` with the following (in `nanormm/trmm-mcp/trmm_mcp/server.py`):

```python
def build_dispatcher(settings: Settings | None = None) -> tuple[Dispatcher, ToolRegistry, TrmmClient]:
    """Construct the Dispatcher graph (policy + approvals + audit + tool
    registry + trmm client). Used by both the MCP server and approval-bridge
    so the two processes share construction logic.
    """
    settings = settings or Settings()
    policy = Policy.load(settings.policy_path)
    approvals = ApprovalRegistry(
        redis.Redis.from_url(settings.redis_url, decode_responses=True),
        ttl_seconds=settings.pending_action_ttl_seconds,
    )
    audit = AuditLog(settings.audit_dsn)
    trmm = TrmmClient(base_url=settings.trmm_api_base, token=settings.trmm_api_token)

    registry = ToolRegistry()
    _register_all(registry, trmm)

    dispatcher = Dispatcher(registry=registry, policy=policy, approvals=approvals, audit=audit)
    return dispatcher, registry, trmm


def build_server() -> TrmmMcpServer:
    dispatcher, registry, trmm = build_dispatcher()

    mcp = Server("trmm-mcp")

    @mcp.list_tools()
    async def _list_tools() -> list[Tool]:
        return [_tool_descriptor(name) for name in registry.tool_names()]

    @mcp.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        result = await dispatcher.dispatch(name, arguments)
        return [TextContent(type="text", text=_serialize(result))]

    return TrmmMcpServer(mcp=mcp, tool_registry=registry, dispatcher=dispatcher, trmm_client=trmm)
```

- [ ] **Step 3: Run trmm-mcp's existing test suite to confirm the refactor is behavior-preserving**

```bash
cd ../trmm-mcp
pytest -q
```

Expected: same pass count as before the refactor (no behavior change, just an extraction).

- [ ] **Step 4: Commit (in trmm-mcp's git tree, same repo)**

```bash
git add trmm_mcp/server.py
git commit -m "trmm-mcp: extract build_dispatcher helper for reuse by bridge"
```

### 3b. Bridge fixtures + factory

- [ ] **Step 5: Write the failing test for the bridge factory**

Create `nanormm/approval-bridge/tests/test_deps.py`:

```python
from approval_bridge.deps import build_dispatcher_for_bridge


def test_build_dispatcher_returns_configured_instance(bridge_settings):
    """Smoke test: factory returns a Dispatcher with the expected methods."""
    dispatcher = build_dispatcher_for_bridge(bridge_settings)

    assert hasattr(dispatcher, "dispatch")
    assert hasattr(dispatcher, "resume")
    assert hasattr(dispatcher, "recover")
    # The shared Redis is fakeredis from the bridge_settings fixture, so a
    # round-trip through the approval registry should work end-to-end.
    assert dispatcher._approvals.get("act_nope") is None
```

- [ ] **Step 6: Write `tests/conftest.py` with shared fixtures**

Create `nanormm/approval-bridge/tests/conftest.py`:

```python
import textwrap
from pathlib import Path

import fakeredis
import pytest
import respx
from pytest_postgresql import factories

from approval_bridge.settings import BridgeSettings

# pytest-postgresql spins up an ephemeral Postgres. The `postgresql` fixture
# is a live psycopg connection; we use it to set up the schema then expose
# its DSN as `audit_db_dsn` so the bridge's deps.py can reach it.
postgresql_proc = factories.postgresql_proc(port=None)
postgresql = factories.postgresql("postgresql_proc")


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    """Minimal policy: one auto read-tool, one human_approval write-tool."""
    f = tmp_path / "policy.yaml"
    f.write_text(textwrap.dedent("""
        version: 1
        default: human_approval
        tools:
          list_alerts: auto
          kill_process: human_approval
    """).strip())
    return f


@pytest.fixture
def fake_redis_client():
    """Shared in-memory Redis. trmm-mcp uses decode_responses=True, so match
    that here for parity with production behavior.
    """
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def audit_db_dsn(postgresql) -> str:
    """Install the nanormm_actions schema on the ephemeral DB and return DSN.
    Schema mirrors what trmm-mcp's audit migration installs in production.
    """
    cur = postgresql.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nanormm_actions (
            action_id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            args JSONB NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            policy_decision TEXT NOT NULL,
            pending_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            approved_by TEXT,
            approved_at TIMESTAMPTZ,
            rejected_by TEXT,
            rejected_at TIMESTAMPTZ,
            reject_reason TEXT,
            executed_at TIMESTAMPTZ,
            result JSONB
        )
    """)
    postgresql.commit()
    info = postgresql.info
    return f"postgresql://{info.user}:@{info.host}:{info.port}/{info.dbname}"


@pytest.fixture
def mock_trmm():
    """respx mock for the TRMM REST API. Tests add specific routes."""
    with respx.mock(base_url="https://trmm.test", assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def bridge_settings(
    monkeypatch, policy_file, fake_redis_client, audit_db_dsn
) -> BridgeSettings:
    monkeypatch.setenv("NANORMM_BRIDGE_API_KEY", "test-secret")
    monkeypatch.setenv("TRMM_API_BASE", "https://trmm.test")
    monkeypatch.setenv("TRMM_API_TOKEN", "trmm-test-token")
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(policy_file))
    monkeypatch.setenv("NANORMM_REDIS_URL", "redis://fake")
    monkeypatch.setenv("NANORMM_AUDIT_DSN", audit_db_dsn)
    monkeypatch.setenv("NANORMM_PENDING_TTL_SECONDS", "60")

    # Replace `redis.Redis.from_url` so build_dispatcher gets fakeredis.
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: fake_redis_client)

    return BridgeSettings()
```

- [ ] **Step 7: Run the test to verify it fails for the right reason**

From `nanormm/approval-bridge/`:

```bash
pytest tests/test_deps.py -v
```

Expected: ImportError on `approval_bridge.deps` (not a fixture/conftest error).

- [ ] **Step 8: Implement `deps.py`**

Create `nanormm/approval-bridge/approval_bridge/deps.py`:

```python
from trmm_mcp.server import build_dispatcher as _build_dispatcher
from trmm_mcp.tools._base import Dispatcher

from .settings import BridgeSettings


def build_dispatcher_for_bridge(settings: BridgeSettings) -> Dispatcher:
    """Construct the dispatcher the bridge will share with trmm-mcp.

    Delegates to trmm-mcp's `build_dispatcher`, passing the trmm-mcp settings
    we composed in BridgeSettings. The bridge ignores the registry/client
    return values — it only needs the dispatcher to call `dispatch.resume()`
    on approval.
    """
    dispatcher, _registry, _trmm = _build_dispatcher(settings.trmm)
    return dispatcher
```

- [ ] **Step 9: Run the test to verify it passes**

```bash
pytest tests/test_deps.py -v
```

Expected: passes.

- [ ] **Step 10: Commit**

```bash
git add approval_bridge/deps.py tests/conftest.py tests/test_deps.py
git commit -m "approval-bridge: dispatcher factory + shared test fixtures"
```

---

## Task 4: Pydantic models

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/models.py`

Just request/response shapes. Trivial; no test file needed beyond the route tests in Task 6.

- [ ] **Step 1: Implement `models.py`**

Create `nanormm/approval-bridge/approval_bridge/models.py`:

```python
from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    token: str = Field(..., min_length=1, description="action_id from the pending Redis row")


class RejectRequest(BaseModel):
    token: str = Field(..., min_length=1)
    reason: str = Field(default="", max_length=500)


class ActionResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    error: str
```

- [ ] **Step 2: Commit**

```bash
git add approval_bridge/models.py
git commit -m "approval-bridge: request/response models"
```

---

## Task 5: Healthz endpoint + app factory

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/app.py`
- Create: `nanormm/approval-bridge/approval_bridge/routes.py`
- Create: `nanormm/approval-bridge/tests/test_healthz.py`

Build the FastAPI factory that mounts routes. Start with `/healthz` since it's auth-free and lets us verify the app comes up before adding the harder endpoints.

- [ ] **Step 1: Write the failing test**

Create `nanormm/approval-bridge/tests/test_healthz.py`:

```python
from fastapi.testclient import TestClient

from approval_bridge.app import create_app


def test_healthz_returns_200(bridge_settings):
    app = create_app(bridge_settings)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_does_not_require_auth(bridge_settings):
    app = create_app(bridge_settings)
    client = TestClient(app)
    # No Authorization header
    r = client.get("/healthz")
    assert r.status_code == 200
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_healthz.py -v
```

Expected: ImportError on `approval_bridge.app`.

- [ ] **Step 3: Implement `routes.py` (healthz only for now)**

Create `nanormm/approval-bridge/approval_bridge/routes.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Implement `app.py`**

Create `nanormm/approval-bridge/approval_bridge/app.py`:

```python
from fastapi import FastAPI

from .deps import build_dispatcher_for_bridge
from .routes import router
from .settings import BridgeSettings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    """Application factory. Builds the Dispatcher once and stashes it on
    `app.state.dispatcher` so route handlers can reach it via Request.

    Pass an explicit `settings` for tests; production calls with no args and
    BridgeSettings() reads from the environment.
    """
    if settings is None:
        settings = BridgeSettings()

    app = FastAPI(title="nanormm approval-bridge", version="0.1.0")
    app.state.settings = settings
    app.state.dispatcher = build_dispatcher_for_bridge(settings)

    app.include_router(router)

    return app
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
pytest tests/test_healthz.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add approval_bridge/app.py approval_bridge/routes.py tests/test_healthz.py
git commit -m "approval-bridge: app factory + /healthz endpoint"
```

---

## Task 6: POST /api/nanoclaw/actions/execute/

**Files:**
- Modify: `nanormm/approval-bridge/approval_bridge/routes.py`
- Create: `nanormm/approval-bridge/tests/test_execute.py`

Implement the core endpoint nanoclaw calls. Behavior:

- Validates Bearer auth.
- Parses `{token}` body.
- Looks up the action in Redis via `dispatcher._approvals.get(token)` (or, equivalently, calls `mark_approved` and lets it raise on missing/terminal).
- Marks approved: `mark_approved(token, approved_by=<X-Slack-User-Name or X-Slack-User-ID>)`. If the action is already in a terminal state, the registry's idempotency makes this a no-op — we then look up the existing result and return it (Slack retry safety).
- Audits the approval: `audit.record_approval(action_id=token, approved_by=...)`.
- Calls `dispatcher.resume(token)` to execute.
- Returns `{message}` derived from the pending row's `summary` (e.g., `"Killed PID 4123 on agent DC01"`).

Error mapping:

| Condition                                  | HTTP | Body                                     |
|--------------------------------------------|------|------------------------------------------|
| Unknown / TTL-expired token                | 404  | `{"error": "Action expired or unknown"}` |
| Action already rejected                    | 409  | `{"error": "Action was rejected"}`       |
| Action already executed (Slack retry)      | 200  | `{"message": "<cached summary>"}`        |
| Tool execution exception (TrmmApiError, …) | 502  | `{"error": "<exception class>: <msg>"}`  |
| Concurrent modification race               | 409  | `{"error": "Concurrent modification, retry"}` |

- [ ] **Step 1: Write the failing tests**

Create `nanormm/approval-bridge/tests/test_execute.py`:

```python
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from approval_bridge.app import create_app


@pytest.fixture
def client(bridge_settings):
    app = create_app(bridge_settings)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-secret",
        "X-Slack-User-ID": "U123",
        "X-Slack-User-Name": "alice",
    }


def _seed_pending(client, tool_name="kill_process", args=None) -> str:
    """Helper: create a pending action via the dispatcher (bypasses Slack)."""
    import asyncio

    args = args or {"agent_id": "agent-1", "pid": 4123}
    dispatcher = client.app.state.dispatcher
    summary = f"kill PID {args['pid']} on {args['agent_id']}"

    result = asyncio.run(dispatcher.dispatch(tool_name, args, summary=summary))
    assert result["status"] == "pending"
    return result["action_id"]


def test_execute_unknown_token_returns_404(client, auth_headers):
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": "act_doesnotexist"},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json() == {"error": "Action expired or unknown"}


def test_execute_missing_token_returns_422(client, auth_headers):
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 422  # pydantic validation


def test_execute_no_auth_returns_401(client):
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": "act_x"},
    )
    assert r.status_code == 401


def test_execute_happy_path(client, auth_headers, mock_trmm):
    # Mock the TRMM call that kill_process makes.
    mock_trmm.post("/api/v3/agents/agent-1/processes/").mock(
        return_value=Response(200, json={"ok": True})
    )

    token = _seed_pending(client)
    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    assert r.status_code == 200
    body = r.json()
    assert "message" in body
    assert "kill" in body["message"].lower()


def test_execute_rejected_action_returns_409(client, auth_headers):
    token = _seed_pending(client)
    # Pre-reject the action via the registry directly.
    client.app.state.dispatcher._approvals.mark_rejected(
        token, rejected_by="bob", reason="unsafe"
    )

    r = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    assert r.status_code == 409
    assert r.json() == {"error": "Action was rejected"}


def test_execute_idempotent_on_slack_retry(client, auth_headers, mock_trmm):
    """If Slack retries the button click, the second POST returns the cached
    result — does not re-execute the TRMM call.
    """
    mock_trmm.post("/api/v3/agents/agent-1/processes/").mock(
        return_value=Response(200, json={"ok": True})
    )

    token = _seed_pending(client)

    r1 = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json() == r1.json()

    # Verify TRMM only called once
    assert mock_trmm.routes[0].call_count == 1


def test_execute_records_approver_in_audit(client, auth_headers, mock_trmm, postgresql):
    mock_trmm.post("/api/v3/agents/agent-1/processes/").mock(
        return_value=Response(200, json={"ok": True})
    )
    token = _seed_pending(client)
    client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )

    cur = postgresql.cursor()
    cur.execute(
        "SELECT approved_by, executed_at FROM nanormm_actions WHERE action_id = %s",
        (token,),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "alice"  # X-Slack-User-Name
    assert row[1] is not None  # executed_at populated
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_execute.py -v
```

Expected: all fail (route doesn't exist yet — 404 on POST, or 405 method not allowed).

- [ ] **Step 3: Replace `routes.py` with the two-router design plus the execute handler**

Replace `nanormm/approval-bridge/approval_bridge/routes.py` with:

```python
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from trmm_mcp.exceptions import ApprovalError, TrmmApiError

from .models import ActionResponse, ErrorResponse, ExecuteRequest, RejectRequest

# Two routers so create_app can apply Bearer auth selectively:
# `public_router` carries unauthenticated endpoints (healthz);
# `authed_router` carries the Slack-callable endpoints (/api/nanoclaw/*).
public_router = APIRouter()
authed_router = APIRouter()


@public_router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _approver_label(slack_user_id: str | None, slack_user_name: str | None) -> str:
    """Prefer the human-readable name; fall back to Slack ID; finally 'unknown'."""
    if slack_user_name:
        return slack_user_name
    if slack_user_id:
        return slack_user_id
    return "unknown"


@authed_router.post(
    "/api/nanoclaw/actions/execute/",
    response_model=ActionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def execute_action(
    request: Request,
    body: ExecuteRequest,
    x_slack_user_id: str | None = Header(default=None, alias="X-Slack-User-ID"),
    x_slack_user_name: str | None = Header(default=None, alias="X-Slack-User-Name"),
) -> ActionResponse:
    dispatcher = request.app.state.dispatcher
    approver = _approver_label(x_slack_user_id, x_slack_user_name)

    pending = dispatcher._approvals.get(body.token)
    if pending is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Action expired or unknown"},
        )

    current_status = pending["status"]

    if current_status == "rejected":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "Action was rejected"},
        )

    if current_status == "executed":
        # Slack retry — return cached summary, do not re-execute.
        return ActionResponse(message=f"Executed: {pending['summary']}")

    if current_status == "expired":
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Action expired or unknown"},
        )

    # status is 'pending' or 'approved' (the latter only if a previous
    # bridge invocation marked approved and crashed before resume completed).
    try:
        if current_status == "pending":
            dispatcher._approvals.mark_approved(body.token, approved_by=approver)
            dispatcher._audit.record_approval(action_id=body.token, approved_by=approver)
        await dispatcher.resume(body.token)
    except ApprovalError as e:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": str(e)},
        )
    except TrmmApiError as e:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"{type(e).__name__}: {e}"},
        )

    return ActionResponse(message=f"Executed: {pending['summary']}")
```

Note: this replaces the single `router` from Task 5. The healthz endpoint moves from `router` to `public_router`; both Task 5 healthz tests still pass after Step 4 wires `public_router` into the app.

- [ ] **Step 4: Wire both routers in `app.py` with auth applied to authed_router**

Modify `nanormm/approval-bridge/approval_bridge/app.py`:

```python
from fastapi import Depends, FastAPI

from .auth import verify_bearer
from .deps import build_dispatcher_for_bridge
from .routes import authed_router, public_router
from .settings import BridgeSettings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    if settings is None:
        settings = BridgeSettings()

    app = FastAPI(title="nanormm approval-bridge", version="0.1.0")
    app.state.settings = settings
    app.state.dispatcher = build_dispatcher_for_bridge(settings)

    app.include_router(public_router)
    app.include_router(authed_router, dependencies=[verify_bearer(settings)])

    return app
```

> **Note for executor:** `verify_bearer(settings)` already returns a `Depends(...)` object, so it slots straight into `dependencies=[...]`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/test_execute.py -v
```

Expected: all seven tests pass. If `_seed_pending`'s asyncio loop call fails inside TestClient (which already has its own loop), switch to `asyncio.run()` or pytest-asyncio's helper.

- [ ] **Step 6: Commit**

```bash
git add approval_bridge/routes.py approval_bridge/app.py tests/test_execute.py
git commit -m "approval-bridge: POST /actions/execute/ with Slack retry idempotency"
```

---

## Task 7: POST /api/nanoclaw/actions/reject/

**Files:**
- Modify: `nanormm/approval-bridge/approval_bridge/routes.py`
- Create: `nanormm/approval-bridge/tests/test_reject.py`

Symmetric to execute: validates Bearer, parses `{token, reason}`, calls `mark_rejected`, audits. Returns `{message: "Rejected: <summary>"}`.

> **Caveat:** nanoclaw's current `nanoclaw_cancel` Bolt handler does not POST anywhere — it just calls `chat.update` to set the message text to "Cancelled." So this endpoint is reachable today only via direct curl from operators or future nanoclaw patches. Ship it anyway: the audit trail benefits, and nanoclaw can be updated later without bridge changes.

- [ ] **Step 1: Write the failing tests**

Create `nanormm/approval-bridge/tests/test_reject.py`:

```python
import pytest
from fastapi.testclient import TestClient

from approval_bridge.app import create_app


@pytest.fixture
def client(bridge_settings):
    app = create_app(bridge_settings)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-secret",
        "X-Slack-User-ID": "U999",
        "X-Slack-User-Name": "carol",
    }


def _seed_pending(client) -> str:
    import asyncio

    dispatcher = client.app.state.dispatcher
    result = asyncio.run(
        dispatcher.dispatch(
            "kill_process",
            {"agent_id": "agent-1", "pid": 4123},
            summary="kill PID 4123 on agent-1",
        )
    )
    return result["action_id"]


def test_reject_unknown_token_returns_404(client, auth_headers):
    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": "act_doesnotexist"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_reject_no_auth_returns_401(client):
    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": "x"},
    )
    assert r.status_code == 401


def test_reject_happy_path(client, auth_headers, postgresql):
    token = _seed_pending(client)

    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": token, "reason": "unsafe in prod"},
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert "message" in r.json()

    cur = postgresql.cursor()
    cur.execute(
        "SELECT rejected_by, reject_reason FROM nanormm_actions WHERE action_id = %s",
        (token,),
    )
    row = cur.fetchone()
    assert row[0] == "carol"
    assert row[1] == "unsafe in prod"


def test_reject_already_executed_returns_409(client, auth_headers, mock_trmm):
    from httpx import Response

    mock_trmm.post("/api/v3/agents/agent-1/processes/").mock(
        return_value=Response(200, json={"ok": True})
    )
    token = _seed_pending(client)
    # Execute first
    client.post(
        "/api/nanoclaw/actions/execute/",
        json={"token": token},
        headers=auth_headers,
    )
    # Now try to reject — should 409
    r = client.post(
        "/api/nanoclaw/actions/reject/",
        json={"token": token},
        headers=auth_headers,
    )
    assert r.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_reject.py -v
```

Expected: all four fail (route missing).

- [ ] **Step 3: Add the reject route to `routes.py`**

Append to `nanormm/approval-bridge/approval_bridge/routes.py`:

```python
@authed_router.post(
    "/api/nanoclaw/actions/reject/",
    response_model=ActionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def reject_action(
    request: Request,
    body: RejectRequest,
    x_slack_user_id: str | None = Header(default=None, alias="X-Slack-User-ID"),
    x_slack_user_name: str | None = Header(default=None, alias="X-Slack-User-Name"),
) -> ActionResponse:
    dispatcher = request.app.state.dispatcher
    rejecter = _approver_label(x_slack_user_id, x_slack_user_name)

    pending = dispatcher._approvals.get(body.token)
    if pending is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Action expired or unknown"},
        )

    if pending["status"] in {"executed", "expired"}:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": f"Action is already {pending['status']}"},
        )

    if pending["status"] == "rejected":
        # Idempotent: same caller re-rejecting is fine; surface the existing record.
        return ActionResponse(message=f"Rejected: {pending['summary']}")

    try:
        dispatcher._approvals.mark_rejected(
            body.token, rejected_by=rejecter, reason=body.reason
        )
        dispatcher._audit.record_rejection(
            action_id=body.token, rejected_by=rejecter, reason=body.reason
        )
    except ApprovalError as e:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": str(e)},
        )

    return ActionResponse(message=f"Rejected: {pending['summary']}")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_reject.py -v
```

Expected: all four pass.

- [ ] **Step 5: Commit**

```bash
git add approval_bridge/routes.py tests/test_reject.py
git commit -m "approval-bridge: POST /actions/reject/ with audit attribution"
```

---

## Task 8: nanoclaw_action envelope in trmm-mcp pending response

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`
- Create: `nanormm/trmm-mcp/tests/test_envelope.py`

The agent (a Claude container managed by nanoclaw) emits final responses to Slack via nanoclaw's channel. To trigger the Confirm/Cancel buttons, the agent must produce a JSON envelope of the shape nanoclaw's `parseActionResponse` looks for:

```json
{
  "nanoclaw_action": {
    "preview": "kill PID 4123 on agent DC01?",
    "slack_blocks": [ ... section + actions blocks ... ]
  }
}
```

We make this trivial for the agent: when `Dispatcher.dispatch()` returns a pending response, include the full `nanoclaw_action` envelope inline. The agent's system prompt then instructs: "if a tool result contains a `nanoclaw_action` field, emit the entire `nanoclaw_action` JSON object as your final response." (System-prompt wiring lives in the bot-config plan; here we just deliver the envelope.)

- [ ] **Step 1: Write the failing test**

Create `nanormm/trmm-mcp/tests/test_envelope.py` (uses the existing `fake_redis` and `audit_dsn` fixtures from `tests/conftest.py`):

```python
import pytest

from trmm_mcp.approvals import ApprovalRegistry
from trmm_mcp.audit import AuditLog
from trmm_mcp.policy import Policy
from trmm_mcp.tools._base import Dispatcher, ToolRegistry


@pytest.fixture
def envelope_env(fake_redis, audit_dsn, tmp_path):
    """Dispatcher wired with one human_approval and one auto tool."""
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
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    dispatcher = Dispatcher(registry=registry, policy=pol, approvals=approvals, audit=audit)
    return registry, dispatcher


@pytest.mark.asyncio
async def test_pending_response_includes_nanoclaw_action_envelope(envelope_env):
    """Envelope shape matches nanoclaw's parseActionResponse expectations."""
    registry, dispatcher = envelope_env

    @registry.register(name="kill_process")
    async def kill_process(*, agent_id: str, pid: int) -> dict:
        return {"ok": True}

    summary = "Kill PID 4123 on agent DC01"
    result = await dispatcher.dispatch(
        "kill_process",
        {"agent_id": "DC01", "pid": 4123},
        summary=summary,
    )

    assert result["status"] == "pending"
    assert "action_id" in result
    assert "nanoclaw_action" in result

    env = result["nanoclaw_action"]
    assert env["preview"] == summary

    blocks = env["slack_blocks"]
    assert isinstance(blocks, list)
    # Must contain a section block with the summary text
    sections = [b for b in blocks if b.get("type") == "section"]
    assert any(summary in str(b) for b in sections)

    # Must contain an actions block with confirm + cancel buttons
    actions_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions_blocks) == 1
    elements = actions_blocks[0]["elements"]

    confirm = next(e for e in elements if e["action_id"] == "nanoclaw_confirm")
    assert confirm["value"] == result["action_id"]
    assert confirm.get("style") == "primary"

    cancel = next(e for e in elements if e["action_id"] == "nanoclaw_cancel")
    assert cancel.get("style") == "danger"


@pytest.mark.asyncio
async def test_auto_response_does_not_include_envelope(envelope_env):
    registry, dispatcher = envelope_env

    @registry.register(name="list_alerts")
    async def list_alerts() -> list:
        return [{"id": 1}]

    result = await dispatcher.dispatch("list_alerts", {}, summary="")
    assert result["status"] == "executed"
    assert "nanoclaw_action" not in result
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ../trmm-mcp
pytest tests/test_envelope.py -v
```

Expected: fails — no `nanoclaw_action` key in pending response.

- [ ] **Step 3: Add an envelope builder to `_base.py`**

Modify `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`. After the existing imports, add a builder helper:

```python
def _build_nanoclaw_action_envelope(*, action_id: str, summary: str) -> dict[str, Any]:
    """Construct a Slack-blocks envelope in the shape nanoclaw's
    `parseActionResponse` looks for. The agent emits this verbatim as its
    final response; nanoclaw detects the `nanoclaw_action` key and posts
    the message with Confirm/Cancel buttons.

    Block IDs `nanoclaw_confirm` and `nanoclaw_cancel` match nanoclaw's
    Bolt action handlers; `value` on the confirm button must be the action_id
    so the Confirm callback can POST the right token to approval-bridge.
    """
    preview = summary or "Action requires confirmation"
    return {
        "preview": preview,
        "slack_blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Pending action:*\n{preview}"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "nanoclaw_confirm",
                        "text": {"type": "plain_text", "text": "Confirm"},
                        "style": "primary",
                        "value": action_id,
                    },
                    {
                        "type": "button",
                        "action_id": "nanoclaw_cancel",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "style": "danger",
                        "value": action_id,
                    },
                ],
            },
        ],
    }
```

Then modify the `dispatch()` method's pending branch:

```python
        # human_approval
        action_id = self._approvals.create(tool_name=tool_name, args=args, summary=summary)
        self._audit.record_pending(
            action_id=action_id,
            tool_name=tool_name,
            args=args,
            summary=summary,
            policy_decision=authority.value,
        )
        return {
            "status": "pending",
            "action_id": action_id,
            "summary": summary,
            "nanoclaw_action": _build_nanoclaw_action_envelope(
                action_id=action_id, summary=summary
            ),
        }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_envelope.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Run the full trmm-mcp test suite to confirm nothing else broke**

```bash
pytest -q
```

Expected: existing pass count + 2 new (envelope tests). If any existing test asserts the exact shape of the pending response (e.g. `assert result == {"status": "pending", "action_id": ..., "summary": ...}`), update it to assert subset-match or add the new key.

- [ ] **Step 6: Commit (in trmm-mcp's git tree, same repo)**

```bash
git add trmm_mcp/tools/_base.py tests/test_envelope.py
git commit -m "trmm-mcp: emit nanoclaw_action envelope in pending response"
```

---

## Task 9: __main__ uvicorn entrypoint

**Files:**
- Create: `nanormm/approval-bridge/approval_bridge/__main__.py`
- Create: `nanormm/approval-bridge/tests/test_main.py`

`python -m approval_bridge` should boot uvicorn against the FastAPI app, binding to `settings.host:settings.port`.

- [ ] **Step 1: Write the failing test**

Create `nanormm/approval-bridge/tests/test_main.py`:

```python
from unittest.mock import patch


def test_main_invokes_uvicorn_with_app_factory(bridge_settings):
    """Smoke test: the entrypoint module wires uvicorn.run with the app factory."""
    with patch("uvicorn.run") as mock_run:
        # Importing __main__ for its side effect won't run main() — call explicitly.
        from approval_bridge.__main__ import main

        main()

        assert mock_run.called
        args, kwargs = mock_run.call_args
        # First positional should be either the app instance or import string
        assert args or "app" in kwargs
        assert kwargs.get("host") == bridge_settings.host
        assert kwargs.get("port") == bridge_settings.port
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_main.py -v
```

Expected: ImportError on `approval_bridge.__main__`.

- [ ] **Step 3: Implement the entrypoint**

Create `nanormm/approval-bridge/approval_bridge/__main__.py`:

```python
import uvicorn

from .app import create_app
from .settings import BridgeSettings


def main() -> None:
    settings = BridgeSettings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_main.py -v
```

Expected: passes.

- [ ] **Step 5: Smoke-run the server (manual, optional)**

In one terminal:

```bash
NANORMM_BRIDGE_API_KEY=devsecret \
NANORMM_REDIS_URL=redis://localhost:6379/11 \
NANORMM_AUDIT_DSN=postgresql://nanormm:devpw@localhost:5432/nanormm \
TRMM_API_BASE=https://api.example.com \
TRMM_API_TOKEN=trmm-token \
NANORMM_POLICY_PATH=$(pwd)/../policy.yaml \
python -m approval_bridge
```

In another:

```bash
curl -i http://localhost:8000/healthz
```

Expected: `HTTP/1.1 200 OK` with body `{"status":"ok"}`. (Skip if Redis/Postgres aren't running locally — this only verifies the binary works.)

- [ ] **Step 6: Commit**

```bash
git add approval_bridge/__main__.py tests/test_main.py
git commit -m "approval-bridge: python -m entrypoint binding uvicorn"
```

---

## Task 10: Dockerfile

**Files:**
- Create: `nanormm/approval-bridge/Dockerfile`
- Create: `nanormm/approval-bridge/.dockerignore`

Single-stage Python 3.11 image. Install both `trmm-mcp` (path dep) and the bridge package, then run `python -m approval_bridge`.

- [ ] **Step 1: Write the Dockerfile**

Create `nanormm/approval-bridge/Dockerfile`:

```dockerfile
FROM python:3.11.8-slim-bookworm

# Install build deps for psycopg (binary wheel obviates this normally, but
# keep gcc available for fallback compilation).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the trmm-mcp source first (path dep), then the bridge.
COPY trmm-mcp/ /app/trmm-mcp/
COPY approval-bridge/ /app/approval-bridge/

WORKDIR /app/approval-bridge

RUN pip install --no-cache-dir -e /app/trmm-mcp \
    && pip install --no-cache-dir -e /app/approval-bridge

EXPOSE 8000

# Healthcheck: hit /healthz (no auth needed)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["python", "-m", "approval_bridge"]
```

> **Note for executor:** Docker COPY paths assume the build context is `nanormm/`, not `nanormm/approval-bridge/`. The `docker-compose.yml` in the (later) orchestration plan will set `context: ./nanormm` and `dockerfile: approval-bridge/Dockerfile`. If building standalone, the user must run `docker build -f approval-bridge/Dockerfile .` from inside `nanormm/`.

- [ ] **Step 2: Write `.dockerignore`**

Create `nanormm/approval-bridge/.dockerignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
tests/
*.md
```

- [ ] **Step 3: Build the image**

From `nanormm/`:

```bash
cd nanormm
docker build -f approval-bridge/Dockerfile -t nanormm/approval-bridge:dev .
```

Expected: image builds. Image size roughly 200-300 MB (slim base + deps).

- [ ] **Step 4: Run the image and hit /healthz**

```bash
docker run --rm -d --name approval-bridge-test -p 8000:8000 \
    -e NANORMM_BRIDGE_API_KEY=devsecret \
    -e NANORMM_REDIS_URL=redis://host.docker.internal:6379/11 \
    -e NANORMM_AUDIT_DSN=postgresql://nanormm:devpw@host.docker.internal:5432/nanormm \
    -e TRMM_API_BASE=https://api.example.com \
    -e TRMM_API_TOKEN=trmm-token \
    -e NANORMM_POLICY_PATH=/app/nanormm/policy.yaml \
    -v $(pwd)/policy.yaml:/app/nanormm/policy.yaml:ro \
    nanormm/approval-bridge:dev

sleep 3
curl -i http://localhost:8000/healthz
docker stop approval-bridge-test
```

Expected: `HTTP/1.1 200 OK`, body `{"status":"ok"}`. (If Redis/Postgres aren't reachable, the container may fail to start when building the Dispatcher — that's a real failure mode to catch in this smoke test, not a test-environment artifact.)

> **Note for executor:** if the dispatcher factory needs Redis/Postgres at app-creation time, that means the container can't even start without them. That's the right design (fail-closed on missing infra), but means this manual smoke test requires them running. The container test in CI would be against the full compose stack; here it's optional.

- [ ] **Step 5: Run the bridge package's test suite end-to-end**

```bash
cd approval-bridge
pytest -q
```

Expected: every test from Tasks 0–9 passes. Aggregate count should be ~25 tests.

- [ ] **Step 6: Lint**

```bash
ruff check .
ruff format --check .
```

Expected: clean. Fix any issues, re-run, then proceed.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "approval-bridge: containerize with healthcheck"
```

---

## Self-review checklist for the executor

After Task 10, verify:

1. **Spec coverage:**
   - ☐ Bridge receives Slack approval callbacks (via nanoclaw's confirm handler) — Tasks 5–7.
   - ☐ Bearer auth — Task 2.
   - ☐ Audit attribution by Slack user — Task 6 + Task 7.
   - ☐ Idempotency on Slack retries — Task 6.
   - ☐ Slack envelope reaches the agent — Task 8.
   - ☐ Healthcheck for Docker — Tasks 5 + 10.
   - ☐ Reject endpoint exists for symmetry — Task 7.
   - ☐ Container builds and runs — Task 10.

2. **Out-of-scope guard:** confirm no nanoclaw bot config, `docker-compose.yml`, or nginx route was added — those belong to the next plan.

3. **No public ingress:** confirm the Dockerfile does NOT add a Slack signature validation handler. Slack reaches nanoclaw via Socket Mode; the bridge is internal-only.

4. **Test count:** roughly 25 new tests across `test_settings.py`, `test_auth.py`, `test_deps.py`, `test_healthz.py`, `test_execute.py`, `test_reject.py`, `test_main.py` (bridge) plus `test_envelope.py` (trmm-mcp). Plus the existing trmm-mcp suite still passes.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-28-nanormm-approval-bridge.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints.
