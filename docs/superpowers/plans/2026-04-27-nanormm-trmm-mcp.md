# nanormm `trmm-mcp` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python MCP server (`trmm-mcp`) that exposes TacticalRMM read + write operations as MCP tools, with policy-gated write authority, a Redis-backed pending-approval registry, and a Postgres audit trail. Read tools execute end-to-end. Write tools route through the policy engine; `human_approval` writes are persisted as pending stubs in Redis (the Slack approval round-trip is delivered in Plan 2).

**Architecture:** Standalone Python package with four layers: a Knox-authenticated `httpx` REST client wrapping TRMM's Django API; a tool registry where each MCP tool is a typed Python function decorated with a policy authority; a dispatcher that intercepts every tool call to load `policy.yaml`, look up authority, and either execute, stub, or deny; and persistence via Redis (pending approvals, DB index 11) and Postgres (audit log, dedicated `nanormm` DB).

**Tech Stack:**
- Python 3.11.8 (matches TRMM's CI pin)
- `mcp` (Anthropic's MCP Python SDK)
- `httpx` (async HTTP client for TRMM REST)
- `pydantic` v2 (policy + tool argument validation)
- `redis` (sync client; v5+)
- `psycopg[binary]` (raw-SQL Postgres client; no Django involvement)
- `pyyaml` (policy file)
- `pytest` + `pytest-asyncio` + `respx` + `fakeredis` + `pytest-postgresql`
- `ruff` (lint/format; replaces black + flake8 for this new package)

---

## File structure

This plan creates `nanormm/trmm-mcp/` as a self-contained Python package, plus a top-level `nanormm/policy.yaml` shared with later plans.

```
nanormm/
├── policy.yaml                           # Tool authority config (this plan creates it)
└── trmm-mcp/
    ├── pyproject.toml
    ├── README.md
    ├── trmm_mcp/
    │   ├── __init__.py
    │   ├── __main__.py                   # `python -m trmm_mcp` entrypoint
    │   ├── server.py                     # MCP server + tool registration
    │   ├── trmm_client.py                # Knox-auth httpx wrapper for TRMM REST
    │   ├── policy.py                     # policy.yaml loader, validator, dispatcher
    │   ├── approvals.py                  # Redis-backed pending-action registry
    │   ├── audit.py                      # Postgres audit log writer
    │   ├── exceptions.py                 # Custom exception hierarchy
    │   ├── settings.py                   # Pydantic Settings (env config)
    │   └── tools/
    │       ├── __init__.py               # Tool registry + dispatch wrapper
    │       ├── _base.py                  # @register_tool decorator, types
    │       ├── alerts.py                 # list_alerts, get_alert, search_past_alerts, acknowledge_alert
    │       ├── agents.py                 # list_agents, get_agent, agent_recent_*, agent_patch_state, agent_running_processes
    │       ├── clients.py                # query_clients
    │       ├── scripts.py                # script_history, run_script_on_agent, run_inline_command
    │       └── actions.py                # kill_process, restart_service, reboot_agent, collect_artifacts, isolate/unisolate_host, disable_account, pause_scheduled_task
    └── tests/
        ├── __init__.py
        ├── conftest.py                   # fixtures: mock TRMM (respx), fakeredis, fixture policy file, fixture audit DB
        ├── test_trmm_client.py
        ├── test_policy.py
        ├── test_approvals.py
        ├── test_audit.py
        ├── test_server_smoke.py          # MCP server boots + responds to ping
        ├── test_policy_regression.py     # asserts every registered tool has explicit policy entry
        └── tools/
            ├── __init__.py
            ├── test_alerts.py
            ├── test_agents.py
            ├── test_clients.py
            ├── test_scripts.py
            └── test_actions.py
```

**File responsibility summary:**

- `server.py` — MCP server boot, tool import side-effects, transport (stdio).
- `trmm_client.py` — single source of truth for TRMM REST calls; every other module that needs TRMM data goes through it.
- `policy.py` — loads + validates `policy.yaml` once at startup; exposes `policy.authority(tool_name) -> Authority`. Fails closed on missing/corrupt file.
- `approvals.py` — Redis CRUD for pending actions: `create()`, `get()`, `mark_approved()`, `mark_rejected()`, `expire_old()`. TTL enforced.
- `audit.py` — Postgres writer for `nanormm_actions`. Inserts on pending creation, updates on approval/execution.
- `tools/_base.py` — `@register_tool(authority=...)` decorator that wraps a function, adds it to the registry, and applies the policy/audit/approval dispatcher.
- `tools/<group>.py` — concrete tool implementations grouped by TRMM domain.

---

## Task 0: Repo scaffolding

**Files:**
- Create: `nanormm/.gitignore`
- Create: `nanormm/README.md`
- Create: `nanormm/trmm-mcp/pyproject.toml`
- Create: `nanormm/trmm-mcp/README.md`
- Create: `nanormm/trmm-mcp/trmm_mcp/__init__.py`
- Create: `nanormm/trmm-mcp/tests/__init__.py`

- [ ] **Step 1: Create the directory tree**

```bash
cd /home/jim/quickstack-cc/qsrmm
mkdir -p nanormm/trmm-mcp/trmm_mcp/tools
mkdir -p nanormm/trmm-mcp/tests/tools
```

- [ ] **Step 2: Write `nanormm/.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.venv/
*.egg-info/
dist/
build/
.coverage
htmlcov/
.env
.env.local
```

- [ ] **Step 3: Write `nanormm/README.md`**

```markdown
# nanormm

TacticalRMM × nanoclaw integration: AI agents that triage TRMM alerts and assist
Quick Stack technicians via Slack.

See `docs/superpowers/specs/2026-04-27-nanormm-design.md` for the full design.

## Components

- `trmm-mcp/` — Python MCP server exposing TRMM operations to agents.
- `approval-bridge/` — FastAPI service for Slack interactions (Plan 2).
- `nanoclaw-config/` — Bot configurations (Plan 4).
- `policy.yaml` — Tool authority configuration.
- `docker-compose.yml` — Production orchestration (Plan 4).
```

- [ ] **Step 4: Write `nanormm/trmm-mcp/pyproject.toml`**

```toml
[project]
name = "trmm-mcp"
version = "0.1.0"
description = "MCP server exposing TacticalRMM operations to AI agents"
requires-python = "==3.11.8"
dependencies = [
    "mcp>=1.2.0",
    "httpx>=0.27.0",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.2.0",
    "pyyaml>=6.0.1",
    "redis>=5.0.0",
    "psycopg[binary]>=3.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "respx>=0.21.0",
    "fakeredis>=2.21.0",
    "pytest-postgresql>=6.0.0",
    "ruff>=0.5.0",
]

[project.scripts]
trmm-mcp = "trmm_mcp.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "S"]
ignore = ["S101"]  # allow asserts in tests

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 5: Write `nanormm/trmm-mcp/README.md`**

```markdown
# trmm-mcp

Python MCP server that exposes TacticalRMM read and write operations as MCP tools.

## Run locally

```bash
cd nanormm/trmm-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export TRMM_API_BASE=https://api.quickstack.cc
export TRMM_API_TOKEN=<knox-token>
export NANORMM_POLICY_PATH=../policy.yaml
export NANORMM_REDIS_URL=redis://localhost:6379/11
export NANORMM_AUDIT_DSN=postgresql://nanormm:pw@localhost:5432/nanormm
python -m trmm_mcp
```

## Test

```bash
pytest -v
```
```

- [ ] **Step 6: Write empty `__init__.py` files**

```bash
echo '__version__ = "0.1.0"' > nanormm/trmm-mcp/trmm_mcp/__init__.py
touch nanormm/trmm-mcp/trmm_mcp/tools/__init__.py
touch nanormm/trmm-mcp/tests/__init__.py
touch nanormm/trmm-mcp/tests/tools/__init__.py
```

- [ ] **Step 7: Create venv, install, verify ruff and pytest run**

```bash
cd nanormm/trmm-mcp
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest -v
```

Expected: `ruff check` exits 0 (no Python files yet); `pytest` reports "no tests ran" with exit 0 or 5 (no tests collected). Either is fine for now.

- [ ] **Step 8: Commit**

```bash
cd /home/jim/quickstack-cc/qsrmm
git add nanormm/.gitignore nanormm/README.md nanormm/trmm-mcp/pyproject.toml nanormm/trmm-mcp/README.md nanormm/trmm-mcp/trmm_mcp/__init__.py nanormm/trmm-mcp/trmm_mcp/tools/__init__.py nanormm/trmm-mcp/tests/__init__.py nanormm/trmm-mcp/tests/tools/__init__.py
git commit -m "nanormm: scaffold trmm-mcp package skeleton

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 1: Settings (env config)

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/settings.py`
- Create: `nanormm/trmm-mcp/tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

`nanormm/trmm-mcp/tests/test_settings.py`:
```python
import os
from unittest.mock import patch

import pytest


def test_settings_loads_required_fields():
    from trmm_mcp.settings import Settings

    env = {
        "TRMM_API_BASE": "https://api.example.com",
        "TRMM_API_TOKEN": "tok123",
        "NANORMM_POLICY_PATH": "/etc/nanormm/policy.yaml",
        "NANORMM_REDIS_URL": "redis://localhost:6379/11",
        "NANORMM_AUDIT_DSN": "postgresql://u:p@h:5432/d",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings()

    assert s.trmm_api_base == "https://api.example.com"
    assert s.trmm_api_token == "tok123"
    assert str(s.policy_path) == "/etc/nanormm/policy.yaml"
    assert s.redis_url == "redis://localhost:6379/11"
    assert s.audit_dsn == "postgresql://u:p@h:5432/d"
    assert s.pending_action_ttl_seconds == 1800  # 30-min default


def test_settings_strips_trailing_slash_on_api_base():
    from trmm_mcp.settings import Settings

    env = {
        "TRMM_API_BASE": "https://api.example.com/",
        "TRMM_API_TOKEN": "tok",
        "NANORMM_POLICY_PATH": "/x.yaml",
        "NANORMM_REDIS_URL": "redis://localhost:6379/11",
        "NANORMM_AUDIT_DSN": "postgresql://u:p@h/d",
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings()

    assert s.trmm_api_base == "https://api.example.com"


def test_settings_missing_required_raises():
    from trmm_mcp.settings import Settings

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(Exception):  # pydantic ValidationError
            Settings()
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd nanormm/trmm-mcp && pytest tests/test_settings.py -v
```
Expected: FAIL with `ImportError: cannot import name 'Settings' from 'trmm_mcp.settings'` (or `ModuleNotFoundError`).

- [ ] **Step 3: Implement `settings.py`**

`nanormm/trmm-mcp/trmm_mcp/settings.py`:
```python
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    trmm_api_base: str = Field(..., alias="TRMM_API_BASE")
    trmm_api_token: str = Field(..., alias="TRMM_API_TOKEN")
    policy_path: Path = Field(..., alias="NANORMM_POLICY_PATH")
    redis_url: str = Field(..., alias="NANORMM_REDIS_URL")
    audit_dsn: str = Field(..., alias="NANORMM_AUDIT_DSN")
    pending_action_ttl_seconds: int = Field(1800, alias="NANORMM_PENDING_TTL_SECONDS")

    @field_validator("trmm_api_base")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")
```

- [ ] **Step 4: Run test, verify it passes**

```bash
pytest tests/test_settings.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/settings.py nanormm/trmm-mcp/tests/test_settings.py
git commit -m "trmm-mcp: pydantic settings for env config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Custom exception hierarchy

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/exceptions.py`
- Create: `nanormm/trmm-mcp/tests/test_exceptions.py`

- [ ] **Step 1: Write the failing test**

`nanormm/trmm-mcp/tests/test_exceptions.py`:
```python
import pytest


def test_exception_hierarchy():
    from trmm_mcp.exceptions import (
        NanormmError,
        TrmmApiError,
        TrmmAuthError,
        TrmmNotFoundError,
        PolicyError,
        ApprovalError,
    )

    assert issubclass(TrmmApiError, NanormmError)
    assert issubclass(TrmmAuthError, TrmmApiError)
    assert issubclass(TrmmNotFoundError, TrmmApiError)
    assert issubclass(PolicyError, NanormmError)
    assert issubclass(ApprovalError, NanormmError)


def test_trmm_api_error_carries_status_and_url():
    from trmm_mcp.exceptions import TrmmApiError

    err = TrmmApiError(status_code=500, url="https://api.example/agents", message="boom")
    assert err.status_code == 500
    assert err.url == "https://api.example/agents"
    assert "500" in str(err)
    assert "boom" in str(err)
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_exceptions.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `exceptions.py`**

`nanormm/trmm-mcp/trmm_mcp/exceptions.py`:
```python
class NanormmError(Exception):
    """Base for all nanormm errors."""


class TrmmApiError(NanormmError):
    def __init__(self, status_code: int, url: str, message: str = ""):
        self.status_code = status_code
        self.url = url
        super().__init__(f"TRMM API error {status_code} at {url}: {message}")


class TrmmAuthError(TrmmApiError):
    """401/403 from TRMM."""


class TrmmNotFoundError(TrmmApiError):
    """404 from TRMM."""


class PolicyError(NanormmError):
    """Policy file missing, invalid, or unknown tool."""


class ApprovalError(NanormmError):
    """Approval registry error (Redis unreachable, bad state, expired)."""
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/test_exceptions.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/exceptions.py nanormm/trmm-mcp/tests/test_exceptions.py
git commit -m "trmm-mcp: exception hierarchy

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: TRMM REST client — basic GET

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/trmm_client.py`
- Create: `nanormm/trmm-mcp/tests/test_trmm_client.py`
- Create: `nanormm/trmm-mcp/tests/conftest.py`

- [ ] **Step 1: Write `conftest.py` with shared fixtures**

`nanormm/trmm-mcp/tests/conftest.py`:
```python
import os
from unittest.mock import patch

import pytest


@pytest.fixture
def trmm_env():
    """Minimal env for Settings to load successfully."""
    env = {
        "TRMM_API_BASE": "https://api.test",
        "TRMM_API_TOKEN": "test-token",
        "NANORMM_POLICY_PATH": "/tmp/policy.yaml",
        "NANORMM_REDIS_URL": "redis://localhost:6379/15",
        "NANORMM_AUDIT_DSN": "postgresql://u:p@h:5432/d",
    }
    with patch.dict(os.environ, env, clear=True):
        yield env
```

- [ ] **Step 2: Write the failing test for `TrmmClient.get`**

`nanormm/trmm-mcp/tests/test_trmm_client.py`:
```python
import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_get_returns_json_on_200(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(200, json={"items": [1, 2]}))
        client = TrmmClient.from_env()
        result = await client.get("/agents/")
        assert result == {"items": [1, 2]}


@pytest.mark.asyncio
async def test_get_sends_knox_token_header(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/").mock(return_value=httpx.Response(200, json={}))
        client = TrmmClient.from_env()
        await client.get("/agents/")
        sent = route.calls.last.request
        assert sent.headers["authorization"] == "Token test-token"


@pytest.mark.asyncio
async def test_get_raises_auth_error_on_401(trmm_env):
    from trmm_mcp.exceptions import TrmmAuthError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(401, json={"detail": "no"}))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmAuthError) as exc_info:
            await client.get("/agents/")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_raises_not_found_on_404(trmm_env):
    from trmm_mcp.exceptions import TrmmNotFoundError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/missing/").mock(return_value=httpx.Response(404))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmNotFoundError):
            await client.get("/agents/missing/")


@pytest.mark.asyncio
async def test_get_raises_generic_api_error_on_500(trmm_env):
    from trmm_mcp.exceptions import TrmmApiError
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(500, text="kaboom"))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmApiError) as exc_info:
            await client.get("/agents/")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_get_passes_query_params(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/").mock(return_value=httpx.Response(200, json={}))
        client = TrmmClient.from_env()
        await client.get("/agents/", params={"online": "true", "client": "5"})
        sent = route.calls.last.request
        assert sent.url.params["online"] == "true"
        assert sent.url.params["client"] == "5"
```

- [ ] **Step 3: Run, verify failures**

```bash
pytest tests/test_trmm_client.py -v
```
Expected: 6 FAIL with ImportError.

- [ ] **Step 4: Implement `trmm_client.py`**

`nanormm/trmm-mcp/trmm_mcp/trmm_client.py`:
```python
from typing import Any

import httpx

from .exceptions import TrmmApiError, TrmmAuthError, TrmmNotFoundError
from .settings import Settings


class TrmmClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 15.0):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Token {token}"},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> "TrmmClient":
        s = Settings()
        return cls(base_url=s.trmm_api_base, token=s.trmm_api_token)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        self._raise_for_status(resp)
        return resp.json()

    async def post(self, path: str, *, json: Any = None) -> Any:
        resp = await self._client.post(path, json=json)
        self._raise_for_status(resp)
        return resp.json() if resp.content else None

    async def patch(self, path: str, *, json: Any = None) -> Any:
        resp = await self._client.patch(path, json=json)
        self._raise_for_status(resp)
        return resp.json() if resp.content else None

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        url = str(resp.request.url)
        body = resp.text[:500]
        if resp.status_code in (401, 403):
            raise TrmmAuthError(resp.status_code, url, body)
        if resp.status_code == 404:
            raise TrmmNotFoundError(resp.status_code, url, body)
        raise TrmmApiError(resp.status_code, url, body)
```

- [ ] **Step 5: Run, verify pass**

```bash
pytest tests/test_trmm_client.py -v
```
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/trmm_client.py nanormm/trmm-mcp/tests/conftest.py nanormm/trmm-mcp/tests/test_trmm_client.py
git commit -m "trmm-mcp: TRMM REST client with Knox-token auth and error mapping

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: TRMM client — POST and PATCH

**Files:**
- Modify: `nanormm/trmm-mcp/tests/test_trmm_client.py`

(POST and PATCH are already implemented in Task 3's client; this task adds tests for them.)

- [ ] **Step 1: Append POST + PATCH tests**

Append to `nanormm/trmm-mcp/tests/test_trmm_client.py`:
```python
@pytest.mark.asyncio
async def test_post_sends_json_body(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/scripts/run/").mock(return_value=httpx.Response(200, json={"id": 1}))
        client = TrmmClient.from_env()
        result = await client.post("/scripts/run/", json={"agent": "a", "script_id": 7})
        assert result == {"id": 1}
        assert route.calls.last.request.content == b'{"agent": "a", "script_id": 7}'


@pytest.mark.asyncio
async def test_post_returns_none_on_empty_body(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/x/reboot/").mock(return_value=httpx.Response(204))
        client = TrmmClient.from_env()
        result = await client.post("/agents/x/reboot/")
        assert result is None


@pytest.mark.asyncio
async def test_patch_sends_partial_update(trmm_env):
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/42/").mock(return_value=httpx.Response(200, json={"acked": True}))
        client = TrmmClient.from_env()
        result = await client.patch("/alerts/42/", json={"acked": True})
        assert result == {"acked": True}
        assert route.calls.last.request.method == "PATCH"
```

- [ ] **Step 2: Run, verify pass**

```bash
pytest tests/test_trmm_client.py -v
```
Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add nanormm/trmm-mcp/tests/test_trmm_client.py
git commit -m "trmm-mcp: tests for POST and PATCH client methods

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Policy file — schema and loader

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/policy.py`
- Create: `nanormm/trmm-mcp/tests/test_policy.py`
- Create: `nanormm/policy.yaml`

- [ ] **Step 1: Write `nanormm/policy.yaml`** (the actual production policy file, day-1 launch posture)

```yaml
version: 1
default: human_approval
tools:
  # Read tools — always auto
  list_alerts:               auto
  get_alert:                 auto
  list_agents:               auto
  get_agent:                 auto
  agent_recent_checks:       auto
  agent_recent_tasks:        auto
  agent_patch_state:         auto
  agent_running_processes:   auto
  query_clients:             auto
  script_history:            auto
  search_past_alerts:        auto

  # Write tools — gated at launch
  acknowledge_alert:         human_approval
  collect_artifacts:         human_approval
  kill_process:              human_approval
  restart_service:           human_approval
  reboot_agent:              human_approval
  isolate_host:              human_approval
  unisolate_host:            human_approval
  disable_account:           human_approval
  pause_scheduled_task:      human_approval
  run_script_on_agent:       human_approval

  # Permanently gated
  run_inline_command:        human_approval

  # Never agent-driven
  uninstall_agent:           forbidden
```

- [ ] **Step 2: Write the failing test**

`nanormm/trmm-mcp/tests/test_policy.py`:
```python
from pathlib import Path

import pytest


@pytest.fixture
def good_policy(tmp_path: Path) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
  kill_process: human_approval
  uninstall_agent: forbidden
"""
    )
    return p


def test_policy_loads_from_file(good_policy: Path):
    from trmm_mcp.policy import Policy

    p = Policy.load(good_policy)
    assert p.version == 1
    assert p.default == "human_approval"


def test_policy_authority_returns_explicit(good_policy: Path):
    from trmm_mcp.policy import Authority, Policy

    p = Policy.load(good_policy)
    assert p.authority("list_alerts") == Authority.AUTO
    assert p.authority("kill_process") == Authority.HUMAN_APPROVAL
    assert p.authority("uninstall_agent") == Authority.FORBIDDEN


def test_policy_authority_falls_back_to_default(good_policy: Path):
    from trmm_mcp.policy import Authority, Policy

    p = Policy.load(good_policy)
    assert p.authority("brand_new_tool") == Authority.HUMAN_APPROVAL


def test_policy_missing_file_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    with pytest.raises(PolicyError):
        Policy.load(tmp_path / "missing.yaml")


def test_policy_invalid_yaml_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    p = tmp_path / "bad.yaml"
    p.write_text("::: not yaml :::")
    with pytest.raises(PolicyError):
        Policy.load(p)


def test_policy_unknown_authority_value_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    p = tmp_path / "bad.yaml"
    p.write_text(
        """
version: 1
default: human_approval
tools:
  some_tool: maybe_dunno
"""
    )
    with pytest.raises(PolicyError):
        Policy.load(p)


def test_policy_wrong_version_raises(tmp_path: Path):
    from trmm_mcp.exceptions import PolicyError
    from trmm_mcp.policy import Policy

    p = tmp_path / "v9.yaml"
    p.write_text(
        """
version: 9
default: human_approval
tools: {}
"""
    )
    with pytest.raises(PolicyError):
        Policy.load(p)
```

- [ ] **Step 3: Run, verify failures**

```bash
pytest tests/test_policy.py -v
```
Expected: 7 FAIL with ImportError.

- [ ] **Step 4: Implement `policy.py`**

`nanormm/trmm-mcp/trmm_mcp/policy.py`:
```python
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator

from .exceptions import PolicyError


class Authority(str, Enum):
    AUTO = "auto"
    HUMAN_APPROVAL = "human_approval"
    FORBIDDEN = "forbidden"


class _PolicyModel(BaseModel):
    version: int
    default: Authority
    tools: dict[str, Authority]

    @field_validator("version")
    @classmethod
    def _v1_only(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported policy version {v}, expected 1")
        return v


class Policy:
    def __init__(self, model: _PolicyModel):
        self._model = model

    @property
    def version(self) -> int:
        return self._model.version

    @property
    def default(self) -> Authority:
        return self._model.default

    def authority(self, tool_name: str) -> Authority:
        return self._model.tools.get(tool_name, self._model.default)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        try:
            text = Path(path).read_text()
        except FileNotFoundError as e:
            raise PolicyError(f"policy file not found: {path}") from e
        except OSError as e:
            raise PolicyError(f"cannot read policy file: {path}") from e

        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise PolicyError(f"invalid YAML in {path}: {e}") from e

        try:
            model = _PolicyModel.model_validate(raw)
        except ValidationError as e:
            raise PolicyError(f"invalid policy schema in {path}: {e}") from e

        return cls(model)
```

- [ ] **Step 5: Run, verify pass**

```bash
pytest tests/test_policy.py -v
```
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add nanormm/policy.yaml nanormm/trmm-mcp/trmm_mcp/policy.py nanormm/trmm-mcp/tests/test_policy.py
git commit -m "trmm-mcp: policy.yaml schema, loader, and authority lookup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Approval registry — Redis-backed pending actions

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/approvals.py`
- Create: `nanormm/trmm-mcp/tests/test_approvals.py`

- [ ] **Step 1: Add fakeredis fixture to `conftest.py`**

Append to `nanormm/trmm-mcp/tests/conftest.py`:
```python
import fakeredis


@pytest.fixture
def fake_redis():
    """In-memory redis stand-in."""
    return fakeredis.FakeStrictRedis(decode_responses=True)
```

- [ ] **Step 2: Write the failing test**

`nanormm/trmm-mcp/tests/test_approvals.py`:
```python
import pytest


def test_create_returns_action_id_and_persists(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    action_id = reg.create(
        tool_name="kill_process",
        args={"agent_id": "a1", "pid": 9999},
        summary="Kill PID 9999 on a1",
    )
    assert action_id.startswith("act_")
    pending = reg.get(action_id)
    assert pending is not None
    assert pending["tool_name"] == "kill_process"
    assert pending["args"] == {"agent_id": "a1", "pid": 9999}
    assert pending["status"] == "pending"
    assert pending["summary"] == "Kill PID 9999 on a1"


def test_get_returns_none_for_unknown_id(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    assert reg.get("act_nope") is None


def test_mark_approved_changes_status_and_records_approver(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_approved(aid, approved_by="U_SLACK_123")
    p = reg.get(aid)
    assert p["status"] == "approved"
    assert p["approved_by"] == "U_SLACK_123"


def test_mark_rejected_changes_status(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_rejected(aid, rejected_by="U_SLACK_123", reason="not now")
    p = reg.get(aid)
    assert p["status"] == "rejected"
    assert p["rejected_by"] == "U_SLACK_123"
    assert p["reject_reason"] == "not now"


def test_mark_executed_records_result(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_approved(aid, approved_by="U_X")
    reg.mark_executed(aid, result={"killed": True})
    p = reg.get(aid)
    assert p["status"] == "executed"
    assert p["result"] == {"killed": True}


def test_ttl_set_on_create(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=60)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    ttl = fake_redis.ttl(f"nanormm:pending:{aid}")
    assert 0 < ttl <= 60


def test_mark_approved_unknown_action_raises(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry
    from trmm_mcp.exceptions import ApprovalError

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    with pytest.raises(ApprovalError):
        reg.mark_approved("act_nope", approved_by="x")


def test_mark_approved_already_executed_is_idempotent(fake_redis):
    """Re-approving an executed action should not change state — guards against replay."""
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    aid = reg.create(tool_name="kill_process", args={}, summary="x")
    reg.mark_approved(aid, approved_by="U_A")
    reg.mark_executed(aid, result={"ok": True})
    # Second approval attempt should be a no-op
    reg.mark_approved(aid, approved_by="U_B")
    p = reg.get(aid)
    assert p["status"] == "executed"  # unchanged
    assert p["approved_by"] == "U_A"  # unchanged


def test_action_id_is_unique(fake_redis):
    from trmm_mcp.approvals import ApprovalRegistry

    reg = ApprovalRegistry(fake_redis, ttl_seconds=1800)
    ids = {reg.create(tool_name="x", args={}, summary="y") for _ in range(50)}
    assert len(ids) == 50
```

- [ ] **Step 3: Run, verify failures**

```bash
pytest tests/test_approvals.py -v
```
Expected: 9 FAIL with ImportError.

- [ ] **Step 4: Implement `approvals.py`**

`nanormm/trmm-mcp/trmm_mcp/approvals.py`:
```python
import json
import secrets
from datetime import datetime, timezone
from typing import Any

import redis

from .exceptions import ApprovalError

_KEY_PREFIX = "nanormm:pending:"

# Status state machine: pending -> approved | rejected | expired
#                      approved -> executed
# Once executed, the row becomes terminal (idempotent).
_TERMINAL = {"executed", "rejected", "expired"}


class ApprovalRegistry:
    def __init__(self, client: redis.Redis, *, ttl_seconds: int):
        self._r = client
        self._ttl = ttl_seconds

    def create(self, *, tool_name: str, args: dict[str, Any], summary: str) -> str:
        action_id = f"act_{secrets.token_urlsafe(12)}"
        payload = {
            "action_id": action_id,
            "tool_name": tool_name,
            "args": args,
            "summary": summary,
            "status": "pending",
            "created_at": _now_iso(),
        }
        self._r.set(_key(action_id), json.dumps(payload), ex=self._ttl)
        return action_id

    def get(self, action_id: str) -> dict[str, Any] | None:
        raw = self._r.get(_key(action_id))
        if raw is None:
            return None
        return json.loads(raw)

    def mark_approved(self, action_id: str, *, approved_by: str) -> None:
        self._mutate(
            action_id,
            require_status={"pending"},
            update={"status": "approved", "approved_by": approved_by, "approved_at": _now_iso()},
        )

    def mark_rejected(self, action_id: str, *, rejected_by: str, reason: str = "") -> None:
        self._mutate(
            action_id,
            require_status={"pending"},
            update={
                "status": "rejected",
                "rejected_by": rejected_by,
                "reject_reason": reason,
                "rejected_at": _now_iso(),
            },
        )

    def mark_executed(self, action_id: str, *, result: Any) -> None:
        self._mutate(
            action_id,
            require_status={"approved"},
            update={"status": "executed", "result": result, "executed_at": _now_iso()},
        )

    def _mutate(
        self,
        action_id: str,
        *,
        require_status: set[str],
        update: dict[str, Any],
    ) -> None:
        raw = self._r.get(_key(action_id))
        if raw is None:
            raise ApprovalError(f"unknown or expired action_id: {action_id}")
        current = json.loads(raw)
        if current["status"] in _TERMINAL:
            return  # idempotent: terminal state, ignore
        if current["status"] not in require_status:
            raise ApprovalError(
                f"cannot transition from {current['status']!r} via {set(update.keys())}"
            )
        current.update(update)
        # Preserve remaining TTL
        ttl = self._r.ttl(_key(action_id))
        ex = max(ttl, 1) if ttl > 0 else self._ttl
        self._r.set(_key(action_id), json.dumps(current), ex=ex)


def _key(action_id: str) -> str:
    return _KEY_PREFIX + action_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 5: Run, verify pass**

```bash
pytest tests/test_approvals.py -v
```
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/approvals.py nanormm/trmm-mcp/tests/conftest.py nanormm/trmm-mcp/tests/test_approvals.py
git commit -m "trmm-mcp: Redis-backed pending-action registry with state machine

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Audit log — Postgres schema and writer

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/audit.py`
- Create: `nanormm/trmm-mcp/trmm_mcp/migrations/001_init.sql`
- Create: `nanormm/trmm-mcp/tests/test_audit.py`

- [ ] **Step 1: Write the migration SQL**

`nanormm/trmm-mcp/trmm_mcp/migrations/001_init.sql`:
```sql
CREATE TABLE IF NOT EXISTS nanormm_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_id       TEXT NOT NULL UNIQUE,
    tool_name       TEXT NOT NULL,
    args            JSONB NOT NULL,
    policy_decision TEXT NOT NULL,
    pending_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    rejected_by     TEXT,
    rejected_at     TIMESTAMPTZ,
    reject_reason   TEXT,
    executed_at     TIMESTAMPTZ,
    result          JSONB,
    slack_message_ts TEXT
);

CREATE INDEX IF NOT EXISTS idx_nanormm_actions_tool ON nanormm_actions (tool_name);
CREATE INDEX IF NOT EXISTS idx_nanormm_actions_pending_at ON nanormm_actions (pending_at DESC);
```

- [ ] **Step 2: Add `pytest-postgresql` fixture to `conftest.py`**

Append to `nanormm/trmm-mcp/tests/conftest.py`:
```python
from pathlib import Path

from pytest_postgresql import factories


def _load_migrations(**kwargs):
    """Initializer for pytest-postgresql: applies our schema."""
    import psycopg

    migrations_dir = Path(__file__).resolve().parents[1] / "trmm_mcp" / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    with psycopg.connect(**kwargs) as conn:
        for f in sql_files:
            with conn.cursor() as cur:
                cur.execute(f.read_text())
        conn.commit()


# Note: pytest-postgresql provides `postgresql` fixture.  We adapt it.
@pytest.fixture
def audit_dsn(postgresql) -> str:
    """Postgres DSN with nanormm schema applied."""
    info = postgresql.info
    dsn = (
        f"postgresql://{info.user}:@{info.host}:{info.port}/{info.dbname}"
    )
    _load_migrations(
        host=info.host,
        port=info.port,
        user=info.user,
        dbname=info.dbname,
    )
    return dsn
```

- [ ] **Step 3: Write the failing test**

`nanormm/trmm-mcp/tests/test_audit.py`:
```python
import json

import psycopg
import pytest


def _row(dsn: str, action_id: str) -> dict:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT action_id, tool_name, args, policy_decision, approved_by, "
            "approved_at, rejected_by, executed_at, result FROM nanormm_actions "
            "WHERE action_id = %s",
            (action_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = ["action_id", "tool_name", "args", "policy_decision", "approved_by",
            "approved_at", "rejected_by", "executed_at", "result"]
    return dict(zip(cols, row))


def test_record_pending_inserts_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(
        action_id="act_x",
        tool_name="kill_process",
        args={"agent_id": "a1", "pid": 9999},
        policy_decision="human_approval",
    )
    row = _row(audit_dsn, "act_x")
    assert row is not None
    assert row["tool_name"] == "kill_process"
    assert row["args"] == {"agent_id": "a1", "pid": 9999}
    assert row["policy_decision"] == "human_approval"


def test_record_pending_is_idempotent(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(action_id="act_y", tool_name="x", args={}, policy_decision="auto")
    # Second call must not raise (used during MCP server replay on restart)
    log.record_pending(action_id="act_y", tool_name="x", args={}, policy_decision="auto")
    row = _row(audit_dsn, "act_y")
    assert row is not None


def test_record_approval_updates_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(action_id="act_z", tool_name="x", args={}, policy_decision="human_approval")
    log.record_approval(action_id="act_z", approved_by="U_SLACK_42")
    row = _row(audit_dsn, "act_z")
    assert row["approved_by"] == "U_SLACK_42"
    assert row["approved_at"] is not None


def test_record_execution_updates_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(action_id="act_e", tool_name="x", args={}, policy_decision="auto")
    log.record_execution(action_id="act_e", result={"ok": True})
    row = _row(audit_dsn, "act_e")
    assert row["executed_at"] is not None
    assert row["result"] == {"ok": True}


def test_record_rejection_updates_row(audit_dsn: str):
    from trmm_mcp.audit import AuditLog

    log = AuditLog(audit_dsn)
    log.record_pending(action_id="act_r", tool_name="x", args={}, policy_decision="human_approval")
    log.record_rejection(action_id="act_r", rejected_by="U_X", reason="nope")
    row = _row(audit_dsn, "act_r")
    assert row["rejected_by"] == "U_X"
```

- [ ] **Step 4: Run, verify failures**

```bash
pytest tests/test_audit.py -v
```
Expected: 5 FAIL with ImportError. (Skip with `pytest --no-header` if pytest-postgresql can't find a Postgres binary — note this in test plan.)

- [ ] **Step 5: Implement `audit.py`**

`nanormm/trmm-mcp/trmm_mcp/audit.py`:
```python
import json
from typing import Any

import psycopg


class AuditLog:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def record_pending(
        self,
        *,
        action_id: str,
        tool_name: str,
        args: dict[str, Any],
        policy_decision: str,
    ) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nanormm_actions (action_id, tool_name, args, policy_decision)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (action_id) DO NOTHING
                """,
                (action_id, tool_name, json.dumps(args), policy_decision),
            )
            conn.commit()

    def record_approval(self, *, action_id: str, approved_by: str) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nanormm_actions
                   SET approved_by = %s, approved_at = now()
                 WHERE action_id = %s
                """,
                (approved_by, action_id),
            )
            conn.commit()

    def record_rejection(self, *, action_id: str, rejected_by: str, reason: str = "") -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nanormm_actions
                   SET rejected_by = %s, rejected_at = now(), reject_reason = %s
                 WHERE action_id = %s
                """,
                (rejected_by, reason, action_id),
            )
            conn.commit()

    def record_execution(self, *, action_id: str, result: Any) -> None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nanormm_actions
                   SET executed_at = now(), result = %s::jsonb
                 WHERE action_id = %s
                """,
                (json.dumps(result), action_id),
            )
            conn.commit()
```

- [ ] **Step 6: Run, verify pass**

```bash
pytest tests/test_audit.py -v
```
Expected: 5 passed. (Requires Postgres — install via `apt install postgresql` or use a Docker container; pytest-postgresql will start a temp instance.)

- [ ] **Step 7: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/audit.py nanormm/trmm-mcp/trmm_mcp/migrations/001_init.sql nanormm/trmm-mcp/tests/conftest.py nanormm/trmm-mcp/tests/test_audit.py
git commit -m "trmm-mcp: Postgres audit log writer + initial migration

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Tool registry — `@register_tool` decorator and dispatcher

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`
- Create: `nanormm/trmm-mcp/tests/tools/test__base.py`

This is the heart of the policy gate. Every tool registers itself with an `Authority`. When the MCP server invokes a tool, the dispatcher in `_base.py` consults `Policy`, then either executes (auto), creates a pending row + audit (human_approval), or denies (forbidden).

- [ ] **Step 1: Write the failing test**

`nanormm/trmm-mcp/tests/tools/test__base.py`:
```python
import pytest


@pytest.fixture
def registry_env(fake_redis, audit_dsn, tmp_path):
    """Wire policy + approvals + audit into a Dispatcher."""
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
  always_auto: auto
  always_gated: human_approval
  always_forbidden: forbidden
"""
    )
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    dispatcher = Dispatcher(registry=registry, policy=pol, approvals=approvals, audit=audit)
    return registry, dispatcher


def test_auto_tool_executes_immediately(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_auto")
    async def my_tool(x: int) -> int:
        return x + 1

    result = pytest.run(dispatcher.dispatch("always_auto", {"x": 5}))  # placeholder
```

Wait — let me rewrite this test using `pytest-asyncio` properly:

```python
import pytest


@pytest.fixture
def registry_env(fake_redis, audit_dsn, tmp_path):
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
  always_auto: auto
  always_gated: human_approval
  always_forbidden: forbidden
"""
    )
    pol = Policy.load(pol_path)
    approvals = ApprovalRegistry(fake_redis, ttl_seconds=60)
    audit = AuditLog(audit_dsn)
    registry = ToolRegistry()
    dispatcher = Dispatcher(registry=registry, policy=pol, approvals=approvals, audit=audit)
    return registry, dispatcher


@pytest.mark.asyncio
async def test_auto_tool_executes_immediately(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_auto")
    async def my_tool(x: int) -> int:
        return x + 1

    result = await dispatcher.dispatch("always_auto", {"x": 5})
    assert result == {"status": "executed", "result": 6}


@pytest.mark.asyncio
async def test_gated_tool_returns_pending_and_writes_audit(registry_env, audit_dsn):
    import psycopg
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x * 10

    result = await dispatcher.dispatch(
        "always_gated", {"x": 5}, summary="Multiply 5 by 10"
    )
    assert result["status"] == "pending"
    assert result["action_id"].startswith("act_")
    assert result["summary"] == "Multiply 5 by 10"

    with psycopg.connect(audit_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tool_name, policy_decision FROM nanormm_actions")
        row = cur.fetchone()
    assert row == ("always_gated", "human_approval")


@pytest.mark.asyncio
async def test_forbidden_tool_returns_denied(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_forbidden")
    async def my_forbidden() -> None:
        raise AssertionError("must not execute")

    result = await dispatcher.dispatch("always_forbidden", {})
    assert result["status"] == "denied"
    assert "forbidden" in result["reason"].lower()


@pytest.mark.asyncio
async def test_unknown_tool_raises(registry_env):
    from trmm_mcp.exceptions import PolicyError
    registry, dispatcher = registry_env

    with pytest.raises(PolicyError):
        await dispatcher.dispatch("nonexistent", {})


@pytest.mark.asyncio
async def test_resume_executes_approved_action(registry_env, fake_redis):
    registry, dispatcher = registry_env

    captured = {}

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> dict:
        captured["x"] = x
        return {"x_was": x}

    result = await dispatcher.dispatch("always_gated", {"x": 7}, summary="seven")
    aid = result["action_id"]

    # External approval (would normally come from approval-bridge)
    dispatcher._approvals.mark_approved(aid, approved_by="U_TEST")

    # Resume execution
    final = await dispatcher.resume(aid)
    assert final == {"status": "executed", "result": {"x_was": 7}}
    assert captured == {"x": 7}


@pytest.mark.asyncio
async def test_resume_rejects_unapproved_action(registry_env):
    from trmm_mcp.exceptions import ApprovalError
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated() -> int:
        return 1

    result = await dispatcher.dispatch("always_gated", {}, summary="x")
    with pytest.raises(ApprovalError):
        await dispatcher.resume(result["action_id"])  # still pending, not approved


@pytest.mark.asyncio
async def test_register_lists_all_tool_names(registry_env):
    registry, _ = registry_env

    @registry.register(name="always_auto")
    async def a():
        return None

    @registry.register(name="always_gated")
    async def b():
        return None

    assert set(registry.tool_names()) == {"always_auto", "always_gated"}
```

- [ ] **Step 2: Run, verify failures**

```bash
pytest tests/tools/test__base.py -v
```
Expected: 7 FAIL with ImportError.

- [ ] **Step 3: Implement `tools/_base.py`**

`nanormm/trmm-mcp/trmm_mcp/tools/_base.py`:
```python
from collections.abc import Awaitable, Callable
from typing import Any

from ..approvals import ApprovalRegistry
from ..audit import AuditLog
from ..exceptions import ApprovalError, PolicyError
from ..policy import Authority, Policy

ToolFn = Callable[..., Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}

    def register(self, *, name: str) -> Callable[[ToolFn], ToolFn]:
        def deco(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ValueError(f"duplicate tool: {name}")
            self._tools[name] = fn
            return fn

        return deco

    def get(self, name: str) -> ToolFn | None:
        return self._tools.get(name)

    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


class Dispatcher:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: Policy,
        approvals: ApprovalRegistry,
        audit: AuditLog,
    ):
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        summary: str = "",
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
            policy_decision=authority.value,
        )
        return {"status": "pending", "action_id": action_id, "summary": summary}

    async def resume(self, action_id: str) -> dict[str, Any]:
        """Called by approval-bridge once a human approves an action."""
        pending = self._approvals.get(action_id)
        if pending is None:
            raise ApprovalError(f"unknown or expired action: {action_id}")
        if pending["status"] != "approved":
            raise ApprovalError(
                f"action {action_id} is {pending['status']}, not approved"
            )

        fn = self._registry.get(pending["tool_name"])
        if fn is None:
            raise PolicyError(f"tool no longer registered: {pending['tool_name']}")

        result = await fn(**pending["args"])
        self._approvals.mark_executed(action_id, result=result)
        self._audit.record_execution(action_id=action_id, result=result)
        return {"status": "executed", "result": result}
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/tools/test__base.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/_base.py nanormm/trmm-mcp/tests/tools/test__base.py
git commit -m "trmm-mcp: tool registry + policy/approval/audit dispatcher

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Read tools — alerts (`list_alerts`, `get_alert`, `search_past_alerts`)

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/alerts.py`
- Create: `nanormm/trmm-mcp/tests/tools/test_alerts.py`

> Note for the implementer: TRMM REST endpoint paths used here come from `api/tacticalrmm/alerts/urls.py` and `api/tacticalrmm/agents/urls.py` — verify exact paths against the running TRMM before final integration testing. Mocked tests in this plan use placeholder paths and assert the *shape* of the call, not the exact URL — when integration testing reveals the real path, update both the implementation and the test in lockstep. **Do not invent endpoint paths.** If you can't confirm a path, mark the tool's task `blocked` and ask before guessing.

- [ ] **Step 1: Write the failing test**

`nanormm/trmm-mcp/tests/tools/test_alerts.py`:
```python
import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_list_alerts_returns_normalized_records(trmm_env):
    from trmm_mcp.tools.alerts import list_alerts
    from trmm_mcp.trmm_client import TrmmClient

    fake_payload = [
        {
            "id": 1,
            "alert_time": "2026-04-27T10:00:00Z",
            "severity": "warning",
            "message": "CPU high",
            "agent": "agent-uuid-1",
            "snoozed": False,
            "resolved": False,
        },
        {
            "id": 2,
            "alert_time": "2026-04-27T11:00:00Z",
            "severity": "error",
            "message": "Disk full",
            "agent": "agent-uuid-2",
            "snoozed": False,
            "resolved": False,
        },
    ]

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/alerts/").mock(return_value=httpx.Response(200, json=fake_payload))
        client = TrmmClient.from_env()
        result = await list_alerts(client=client)

    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_list_alerts_filters_by_status(trmm_env):
    from trmm_mcp.tools.alerts import list_alerts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/alerts/").mock(return_value=httpx.Response(200, json=[]))
        client = TrmmClient.from_env()
        await list_alerts(client=client, status="unresolved")

    sent = route.calls.last.request
    assert sent.url.params["resolved"] == "false"


@pytest.mark.asyncio
async def test_get_alert_returns_full_record(trmm_env):
    from trmm_mcp.tools.alerts import get_alert
    from trmm_mcp.trmm_client import TrmmClient

    payload = {"id": 42, "severity": "error", "message": "x", "agent": "uuid"}

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/alerts/42/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await get_alert(client=client, alert_id=42)

    assert result["id"] == 42
    assert result["severity"] == "error"


@pytest.mark.asyncio
async def test_get_alert_404_propagates(trmm_env):
    from trmm_mcp.exceptions import TrmmNotFoundError
    from trmm_mcp.tools.alerts import get_alert
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/alerts/9999/").mock(return_value=httpx.Response(404))
        client = TrmmClient.from_env()
        with pytest.raises(TrmmNotFoundError):
            await get_alert(client=client, alert_id=9999)


@pytest.mark.asyncio
async def test_search_past_alerts_filters_by_agent_and_since(trmm_env):
    from trmm_mcp.tools.alerts import search_past_alerts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/alerts/").mock(return_value=httpx.Response(200, json=[]))
        client = TrmmClient.from_env()
        await search_past_alerts(client=client, agent_id="abc", since="2026-04-01T00:00:00Z")

    sent = route.calls.last.request
    assert sent.url.params["agent"] == "abc"
    assert sent.url.params["since"] == "2026-04-01T00:00:00Z"
```

- [ ] **Step 2: Run, verify failures**

```bash
pytest tests/tools/test_alerts.py -v
```
Expected: 5 FAIL with ImportError.

- [ ] **Step 3: Implement `tools/alerts.py`**

`nanormm/trmm-mcp/trmm_mcp/tools/alerts.py`:
```python
from typing import Any

from ..trmm_client import TrmmClient


async def list_alerts(
    *,
    client: TrmmClient,
    status: str | None = None,
    since: str | None = None,
    client_id: int | None = None,
) -> list[dict[str, Any]]:
    """List recent TRMM alerts. status: 'all' | 'unresolved' | 'snoozed'."""
    params: dict[str, Any] = {}
    if status == "unresolved":
        params["resolved"] = "false"
    elif status == "snoozed":
        params["snoozed"] = "true"
    if since is not None:
        params["since"] = since
    if client_id is not None:
        params["client"] = str(client_id)
    return await client.get("/alerts/", params=params or None)


async def get_alert(*, client: TrmmClient, alert_id: int) -> dict[str, Any]:
    return await client.get(f"/alerts/{alert_id}/")


async def search_past_alerts(
    *,
    client: TrmmClient,
    agent_id: str,
    since: str,
) -> list[dict[str, Any]]:
    return await client.get("/alerts/", params={"agent": agent_id, "since": since})
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/tools/test_alerts.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/alerts.py nanormm/trmm-mcp/tests/tools/test_alerts.py
git commit -m "trmm-mcp: alert read tools (list_alerts, get_alert, search_past_alerts)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Read tools — agents (basic listing + lookup)

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/agents.py`
- Create: `nanormm/trmm-mcp/tests/tools/test_agents.py`

- [ ] **Step 1: Write the failing test**

`nanormm/trmm-mcp/tests/tools/test_agents.py`:
```python
import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_list_agents_returns_inventory(trmm_env):
    from trmm_mcp.tools.agents import list_agents
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"agent_id": "uuid-1", "hostname": "DC01", "online": True, "client": "Acme"},
        {"agent_id": "uuid-2", "hostname": "WS-42", "online": False, "client": "Acme"},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await list_agents(client=client)

    assert len(result) == 2
    assert result[0]["hostname"] == "DC01"


@pytest.mark.asyncio
async def test_list_agents_filters(trmm_env):
    from trmm_mcp.tools.agents import list_agents
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/").mock(return_value=httpx.Response(200, json=[]))
        client = TrmmClient.from_env()
        await list_agents(client=client, online=True, client_id=5, site_id=12)

    sent = route.calls.last.request
    assert sent.url.params["online"] == "true"
    assert sent.url.params["client"] == "5"
    assert sent.url.params["site"] == "12"


@pytest.mark.asyncio
async def test_get_agent_returns_full_record(trmm_env):
    from trmm_mcp.tools.agents import get_agent
    from trmm_mcp.trmm_client import TrmmClient

    payload = {
        "agent_id": "uuid-1",
        "hostname": "DC01",
        "operating_system": "Windows Server 2022",
        "online": True,
        "last_seen": "2026-04-27T11:50:00Z",
    }
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await get_agent(client=client, agent_id="uuid-1")

    assert result["hostname"] == "DC01"
    assert result["operating_system"] == "Windows Server 2022"
```

- [ ] **Step 2: Run, verify failures**

```bash
pytest tests/tools/test_agents.py -v
```
Expected: 3 FAIL with ImportError.

- [ ] **Step 3: Implement `tools/agents.py` (list + get only for now)**

`nanormm/trmm-mcp/trmm_mcp/tools/agents.py`:
```python
from typing import Any

from ..trmm_client import TrmmClient


async def list_agents(
    *,
    client: TrmmClient,
    online: bool | None = None,
    client_id: int | None = None,
    site_id: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if online is not None:
        params["online"] = "true" if online else "false"
    if client_id is not None:
        params["client"] = str(client_id)
    if site_id is not None:
        params["site"] = str(site_id)
    return await client.get("/agents/", params=params or None)


async def get_agent(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.get(f"/agents/{agent_id}/")
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/tools/test_agents.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/agents.py nanormm/trmm-mcp/tests/tools/test_agents.py
git commit -m "trmm-mcp: agent inventory read tools (list_agents, get_agent)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Read tools — agent recent activity (checks, tasks, patches, processes)

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/agents.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test_agents.py`

- [ ] **Step 1: Append failing tests**

Append to `nanormm/trmm-mcp/tests/tools/test_agents.py`:
```python
@pytest.mark.asyncio
async def test_agent_recent_checks_returns_list(trmm_env):
    from trmm_mcp.tools.agents import agent_recent_checks
    from trmm_mcp.trmm_client import TrmmClient

    payload = [{"id": 1, "name": "CPU", "status": "passing"}]
    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/uuid-1/checks/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await agent_recent_checks(client=client, agent_id="uuid-1", n=20)

    assert len(result) == 1
    assert route.calls.last.request.url.params["limit"] == "20"


@pytest.mark.asyncio
async def test_agent_recent_tasks_returns_list(trmm_env):
    from trmm_mcp.tools.agents import agent_recent_tasks
    from trmm_mcp.trmm_client import TrmmClient

    payload = [{"id": 1, "name": "Backup", "last_run_status": "success"}]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/tasks/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await agent_recent_tasks(client=client, agent_id="uuid-1", n=20)

    assert result[0]["name"] == "Backup"


@pytest.mark.asyncio
async def test_agent_patch_state_returns_categorized_kbs(trmm_env):
    from trmm_mcp.tools.agents import agent_patch_state
    from trmm_mcp.trmm_client import TrmmClient

    payload = {
        "installed": ["KB1"],
        "missing": ["KB2", "KB3"],
        "failed": [],
        "pending_reboot": ["KB4"],
    }
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/winupdates/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await agent_patch_state(client=client, agent_id="uuid-1")

    assert result["missing"] == ["KB2", "KB3"]


@pytest.mark.asyncio
async def test_agent_running_processes_returns_list(trmm_env):
    from trmm_mcp.tools.agents import agent_running_processes
    from trmm_mcp.trmm_client import TrmmClient

    payload = [{"pid": 1234, "name": "explorer.exe", "cpu": 0.1, "mem_mb": 50.2}]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/agents/uuid-1/processes/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await agent_running_processes(client=client, agent_id="uuid-1")

    assert result[0]["pid"] == 1234
```

- [ ] **Step 2: Append implementation to `tools/agents.py`**

Append to `nanormm/trmm-mcp/trmm_mcp/tools/agents.py`:
```python
async def agent_recent_checks(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    return await client.get(f"/agents/{agent_id}/checks/", params={"limit": str(n)})


async def agent_recent_tasks(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    return await client.get(f"/agents/{agent_id}/tasks/", params={"limit": str(n)})


async def agent_patch_state(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.get(f"/agents/{agent_id}/winupdates/")


async def agent_running_processes(
    *, client: TrmmClient, agent_id: str
) -> list[dict[str, Any]]:
    return await client.get(f"/agents/{agent_id}/processes/")
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/tools/test_agents.py -v
```
Expected: 7 passed (3 from Task 10 + 4 new).

- [ ] **Step 4: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/agents.py nanormm/trmm-mcp/tests/tools/test_agents.py
git commit -m "trmm-mcp: agent recent-activity read tools (checks, tasks, patches, processes)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Read tools — clients and scripts

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/clients.py`
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/scripts.py`
- Create: `nanormm/trmm-mcp/tests/tools/test_clients.py`
- Create: `nanormm/trmm-mcp/tests/tools/test_scripts.py`

- [ ] **Step 1: Write `test_clients.py`**

`nanormm/trmm-mcp/tests/tools/test_clients.py`:
```python
import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_query_clients_returns_tree(trmm_env):
    from trmm_mcp.tools.clients import query_clients
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"id": 1, "name": "Acme", "sites": [{"id": 10, "name": "HQ"}]},
        {"id": 2, "name": "Widgets Co", "sites": []},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        mock.get("/clients/").mock(return_value=httpx.Response(200, json=payload))
        client = TrmmClient.from_env()
        result = await query_clients(client=client)

    assert result[0]["name"] == "Acme"
    assert result[0]["sites"][0]["name"] == "HQ"
```

- [ ] **Step 2: Implement `tools/clients.py`**

`nanormm/trmm-mcp/trmm_mcp/tools/clients.py`:
```python
from typing import Any

from ..trmm_client import TrmmClient


async def query_clients(*, client: TrmmClient) -> list[dict[str, Any]]:
    """Return the full client → site tree."""
    return await client.get("/clients/")
```

- [ ] **Step 3: Write `test_scripts.py` (read-only piece for now)**

`nanormm/trmm-mcp/tests/tools/test_scripts.py`:
```python
import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_script_history_returns_recent_runs(trmm_env):
    from trmm_mcp.tools.scripts import script_history
    from trmm_mcp.trmm_client import TrmmClient

    payload = [
        {"id": 1, "script_name": "Disk-Check", "stdout": "OK", "stderr": "", "retcode": 0},
        {"id": 2, "script_name": "Restart-IIS", "stdout": "", "stderr": "denied", "retcode": 1},
    ]
    with respx.mock(base_url="https://api.test") as mock:
        route = mock.get("/agents/uuid-1/scripthistory/").mock(
            return_value=httpx.Response(200, json=payload)
        )
        client = TrmmClient.from_env()
        result = await script_history(client=client, agent_id="uuid-1", n=20)

    assert len(result) == 2
    assert route.calls.last.request.url.params["limit"] == "20"
```

- [ ] **Step 4: Implement `tools/scripts.py` (read piece only; write tools come later)**

`nanormm/trmm-mcp/trmm_mcp/tools/scripts.py`:
```python
from typing import Any

from ..trmm_client import TrmmClient


async def script_history(
    *, client: TrmmClient, agent_id: str, n: int = 20
) -> list[dict[str, Any]]:
    return await client.get(
        f"/agents/{agent_id}/scripthistory/", params={"limit": str(n)}
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/tools/test_clients.py tests/tools/test_scripts.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/clients.py nanormm/trmm-mcp/trmm_mcp/tools/scripts.py nanormm/trmm-mcp/tests/tools/test_clients.py nanormm/trmm-mcp/tests/tools/test_scripts.py
git commit -m "trmm-mcp: query_clients and script_history read tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Write tools — `acknowledge_alert`

`acknowledge_alert` is the simplest write tool: just a PATCH on an alert. Implementing it first establishes the write-tool pattern.

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/alerts.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test_alerts.py`

- [ ] **Step 1: Append failing test**

Append to `nanormm/trmm-mcp/tests/tools/test_alerts.py`:
```python
@pytest.mark.asyncio
async def test_acknowledge_alert_patches_with_note(trmm_env):
    from trmm_mcp.tools.alerts import acknowledge_alert
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.patch("/alerts/42/").mock(
            return_value=httpx.Response(200, json={"id": 42, "resolved": True})
        )
        client = TrmmClient.from_env()
        result = await acknowledge_alert(
            client=client, alert_id=42, note="handled via nanormm"
        )

    assert result["resolved"] is True
    body = route.calls.last.request.content
    assert b'"resolved": true' in body
    assert b"handled via nanormm" in body
```

- [ ] **Step 2: Append implementation**

Append to `nanormm/trmm-mcp/trmm_mcp/tools/alerts.py`:
```python
async def acknowledge_alert(
    *, client: TrmmClient, alert_id: int, note: str = ""
) -> dict[str, Any]:
    return await client.patch(
        f"/alerts/{alert_id}/", json={"resolved": True, "resolution_notes": note}
    )
```

- [ ] **Step 3: Run, verify pass**

```bash
pytest tests/tools/test_alerts.py -v
```
Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/alerts.py nanormm/trmm-mcp/tests/tools/test_alerts.py
git commit -m "trmm-mcp: acknowledge_alert write tool

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Write tools — agent actions (`kill_process`, `restart_service`, `reboot_agent`)

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/actions.py`
- Create: `nanormm/trmm-mcp/tests/tools/test_actions.py`

- [ ] **Step 1: Write the failing test**

`nanormm/trmm-mcp/tests/tools/test_actions.py`:
```python
import httpx
import pytest
import respx


@pytest.mark.asyncio
async def test_kill_process_by_pid(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/processes/kill/").mock(
            return_value=httpx.Response(200, json={"killed": True})
        )
        client = TrmmClient.from_env()
        result = await kill_process(client=client, agent_id="uuid-1", pid=9999)

    assert result == {"killed": True}
    body = route.calls.last.request.content
    assert b'"pid": 9999' in body


@pytest.mark.asyncio
async def test_kill_process_by_name(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/processes/kill/").mock(
            return_value=httpx.Response(200, json={"killed": True})
        )
        client = TrmmClient.from_env()
        await kill_process(client=client, agent_id="uuid-1", name="bad.exe")

    body = route.calls.last.request.content
    assert b'"name": "bad.exe"' in body


@pytest.mark.asyncio
async def test_kill_process_requires_pid_or_name(trmm_env):
    from trmm_mcp.tools.actions import kill_process
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(ValueError):
        await kill_process(client=client, agent_id="uuid-1")


@pytest.mark.asyncio
async def test_restart_service(trmm_env):
    from trmm_mcp.tools.actions import restart_service
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/services/restart/").mock(
            return_value=httpx.Response(200, json={"status": "restarted"})
        )
        client = TrmmClient.from_env()
        result = await restart_service(
            client=client, agent_id="uuid-1", service_name="W3SVC"
        )
    assert result["status"] == "restarted"
    assert b'"service_name": "W3SVC"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_reboot_agent(trmm_env):
    from trmm_mcp.tools.actions import reboot_agent
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/reboot/").mock(
            return_value=httpx.Response(200, json={"queued": True})
        )
        client = TrmmClient.from_env()
        result = await reboot_agent(client=client, agent_id="uuid-1")
    assert result == {"queued": True}
```

- [ ] **Step 2: Run, verify failures**

```bash
pytest tests/tools/test_actions.py -v
```
Expected: 5 FAIL with ImportError.

- [ ] **Step 3: Implement `tools/actions.py`**

`nanormm/trmm-mcp/trmm_mcp/tools/actions.py`:
```python
from typing import Any

from ..trmm_client import TrmmClient


async def kill_process(
    *,
    client: TrmmClient,
    agent_id: str,
    pid: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    if pid is None and not name:
        raise ValueError("kill_process requires either pid or name")
    body: dict[str, Any] = {}
    if pid is not None:
        body["pid"] = pid
    if name is not None:
        body["name"] = name
    return await client.post(f"/agents/{agent_id}/processes/kill/", json=body)


async def restart_service(
    *, client: TrmmClient, agent_id: str, service_name: str
) -> dict[str, Any]:
    return await client.post(
        f"/agents/{agent_id}/services/restart/",
        json={"service_name": service_name},
    )


async def reboot_agent(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/reboot/")
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/tools/test_actions.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/actions.py nanormm/trmm-mcp/tests/tools/test_actions.py
git commit -m "trmm-mcp: kill_process, restart_service, reboot_agent write tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Write tools — `collect_artifacts`, `isolate_host`, `unisolate_host`

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/actions.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test_actions.py`

- [ ] **Step 1: Append failing tests**

Append to `nanormm/trmm-mcp/tests/tools/test_actions.py`:
```python
@pytest.mark.asyncio
async def test_collect_artifacts_known_set(trmm_env):
    from trmm_mcp.tools.actions import collect_artifacts
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/artifacts/collect/").mock(
            return_value=httpx.Response(202, json={"job_id": "j1"})
        )
        client = TrmmClient.from_env()
        result = await collect_artifacts(
            client=client, agent_id="uuid-1", artifact_set="event_logs"
        )
    assert result["job_id"] == "j1"
    assert b'"artifact_set": "event_logs"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_collect_artifacts_rejects_unknown_set(trmm_env):
    from trmm_mcp.tools.actions import collect_artifacts
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(ValueError):
        await collect_artifacts(
            client=client, agent_id="uuid-1", artifact_set="my_made_up_set"
        )


@pytest.mark.asyncio
async def test_isolate_host(trmm_env):
    from trmm_mcp.tools.actions import isolate_host
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/isolate/").mock(
            return_value=httpx.Response(200, json={"isolated": True})
        )
        client = TrmmClient.from_env()
        result = await isolate_host(client=client, agent_id="uuid-1")
    assert result == {"isolated": True}


@pytest.mark.asyncio
async def test_unisolate_host(trmm_env):
    from trmm_mcp.tools.actions import unisolate_host
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/unisolate/").mock(
            return_value=httpx.Response(200, json={"isolated": False})
        )
        client = TrmmClient.from_env()
        result = await unisolate_host(client=client, agent_id="uuid-1")
    assert result == {"isolated": False}
```

- [ ] **Step 2: Append implementation**

Append to `nanormm/trmm-mcp/trmm_mcp/tools/actions.py`:
```python
ALLOWED_ARTIFACT_SETS = {
    "event_logs",
    "process_list",
    "network_connections",
    "scheduled_tasks",
    "installed_software",
}


async def collect_artifacts(
    *, client: TrmmClient, agent_id: str, artifact_set: str
) -> dict[str, Any]:
    if artifact_set not in ALLOWED_ARTIFACT_SETS:
        raise ValueError(
            f"unknown artifact_set {artifact_set!r}; must be one of {sorted(ALLOWED_ARTIFACT_SETS)}"
        )
    return await client.post(
        f"/agents/{agent_id}/artifacts/collect/",
        json={"artifact_set": artifact_set},
    )


async def isolate_host(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/isolate/")


async def unisolate_host(*, client: TrmmClient, agent_id: str) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/unisolate/")
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/tools/test_actions.py -v
```
Expected: 9 passed.

- [ ] **Step 4: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/actions.py nanormm/trmm-mcp/tests/tools/test_actions.py
git commit -m "trmm-mcp: collect_artifacts, isolate_host, unisolate_host write tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Write tools — `disable_account`, `pause_scheduled_task`

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/actions.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test_actions.py`

- [ ] **Step 1: Append failing tests**

Append to `nanormm/trmm-mcp/tests/tools/test_actions.py`:
```python
@pytest.mark.asyncio
async def test_disable_account(trmm_env):
    from trmm_mcp.tools.actions import disable_account
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/accounts/disable/").mock(
            return_value=httpx.Response(200, json={"disabled": True})
        )
        client = TrmmClient.from_env()
        result = await disable_account(
            client=client, agent_id="uuid-1", username="bad-actor"
        )
    assert result == {"disabled": True}
    assert b'"username": "bad-actor"' in route.calls.last.request.content


@pytest.mark.asyncio
async def test_pause_scheduled_task(trmm_env):
    from trmm_mcp.tools.actions import pause_scheduled_task
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        mock.post("/agents/uuid-1/tasks/77/pause/").mock(
            return_value=httpx.Response(200, json={"paused": True})
        )
        client = TrmmClient.from_env()
        result = await pause_scheduled_task(
            client=client, agent_id="uuid-1", task_id=77
        )
    assert result == {"paused": True}
```

- [ ] **Step 2: Append implementation**

Append to `nanormm/trmm-mcp/trmm_mcp/tools/actions.py`:
```python
async def disable_account(
    *, client: TrmmClient, agent_id: str, username: str
) -> dict[str, Any]:
    return await client.post(
        f"/agents/{agent_id}/accounts/disable/",
        json={"username": username},
    )


async def pause_scheduled_task(
    *, client: TrmmClient, agent_id: str, task_id: int
) -> dict[str, Any]:
    return await client.post(f"/agents/{agent_id}/tasks/{task_id}/pause/")
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/tools/test_actions.py -v
```
Expected: 11 passed.

- [ ] **Step 4: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/actions.py nanormm/trmm-mcp/tests/tools/test_actions.py
git commit -m "trmm-mcp: disable_account, pause_scheduled_task write tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Write tools — `run_script_on_agent`, `run_inline_command`

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/scripts.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test_scripts.py`

`run_script_on_agent` accepts a `script_id` (never a script body — see spec). `run_inline_command` exists for tech-driven ad-hoc execution and is permanently `human_approval`.

- [ ] **Step 1: Append failing tests**

Append to `nanormm/trmm-mcp/tests/tools/test_scripts.py`:
```python
@pytest.mark.asyncio
async def test_run_script_on_agent_by_id(trmm_env):
    from trmm_mcp.tools.scripts import run_script_on_agent
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/scripts/run/").mock(
            return_value=httpx.Response(200, json={"run_id": 555})
        )
        client = TrmmClient.from_env()
        result = await run_script_on_agent(
            client=client, agent_id="uuid-1", script_id=42, args=["--verbose"]
        )
    assert result == {"run_id": 555}
    body = route.calls.last.request.content
    assert b'"script_id": 42' in body
    assert b'"--verbose"' in body


@pytest.mark.asyncio
async def test_run_script_rejects_script_body_kwarg(trmm_env):
    """Defensive: agent must never be able to ship script source."""
    from trmm_mcp.tools.scripts import run_script_on_agent
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(TypeError):
        # script_body is not a parameter; this should fail at call time
        await run_script_on_agent(
            client=client, agent_id="uuid-1", script_id=42, script_body="rm -rf /"
        )


@pytest.mark.asyncio
async def test_run_inline_command(trmm_env):
    from trmm_mcp.tools.scripts import run_inline_command
    from trmm_mcp.trmm_client import TrmmClient

    with respx.mock(base_url="https://api.test") as mock:
        route = mock.post("/agents/uuid-1/cmd/").mock(
            return_value=httpx.Response(200, json={"stdout": "hi", "retcode": 0})
        )
        client = TrmmClient.from_env()
        result = await run_inline_command(
            client=client, agent_id="uuid-1", shell="cmd", command="echo hi"
        )
    assert result["stdout"] == "hi"
    body = route.calls.last.request.content
    assert b'"shell": "cmd"' in body
    assert b'"command": "echo hi"' in body


@pytest.mark.asyncio
async def test_run_inline_command_validates_shell(trmm_env):
    from trmm_mcp.tools.scripts import run_inline_command
    from trmm_mcp.trmm_client import TrmmClient

    client = TrmmClient.from_env()
    with pytest.raises(ValueError):
        await run_inline_command(
            client=client, agent_id="uuid-1", shell="brainfuck", command="x"
        )
```

- [ ] **Step 2: Append implementation**

Append to `nanormm/trmm-mcp/trmm_mcp/tools/scripts.py`:
```python
ALLOWED_SHELLS = {"cmd", "powershell", "bash"}


async def run_script_on_agent(
    *,
    client: TrmmClient,
    agent_id: str,
    script_id: int,
    args: list[str] | None = None,
) -> dict[str, Any]:
    """
    Invoke a TRMM-library script by ID.

    The agent **cannot** ship a script body — it can only invoke scripts that
    already exist in TRMM's library.  Adding new scripts is a tech-led workflow
    in the TRMM UI.
    """
    body: dict[str, Any] = {"script_id": script_id}
    if args:
        body["args"] = list(args)
    return await client.post(f"/agents/{agent_id}/scripts/run/", json=body)


async def run_inline_command(
    *,
    client: TrmmClient,
    agent_id: str,
    shell: str,
    command: str,
) -> dict[str, Any]:
    """
    Run an arbitrary shell command on an agent.

    Permanently `human_approval` in policy.yaml — never graduates to auto.
    """
    if shell not in ALLOWED_SHELLS:
        raise ValueError(
            f"shell must be one of {sorted(ALLOWED_SHELLS)}, got {shell!r}"
        )
    return await client.post(
        f"/agents/{agent_id}/cmd/",
        json={"shell": shell, "command": command},
    )
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/tools/test_scripts.py -v
```
Expected: 5 passed (1 from Task 12 + 4 new).

- [ ] **Step 4: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/scripts.py nanormm/trmm-mcp/tests/tools/test_scripts.py
git commit -m "trmm-mcp: run_script_on_agent (id-only) and run_inline_command write tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: MCP server boot + tool registration

Wires every tool function into the MCP `Server` with proper schemas, registers them through the dispatcher, and exposes a stdio transport.

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/server.py`
- Create: `nanormm/trmm-mcp/trmm_mcp/__main__.py`
- Create: `nanormm/trmm-mcp/tests/test_server_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

`nanormm/trmm-mcp/tests/test_server_smoke.py`:
```python
import pytest


def test_server_module_loads(trmm_env, tmp_path, monkeypatch):
    """Importing the server module must register every documented tool."""
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        """
version: 1
default: human_approval
tools:
  list_alerts: auto
"""
    )
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol))

    from trmm_mcp.server import build_server

    srv = build_server()
    names = srv.tool_registry.tool_names()

    expected = {
        "list_alerts", "get_alert", "search_past_alerts", "acknowledge_alert",
        "list_agents", "get_agent",
        "agent_recent_checks", "agent_recent_tasks",
        "agent_patch_state", "agent_running_processes",
        "query_clients",
        "script_history", "run_script_on_agent", "run_inline_command",
        "kill_process", "restart_service", "reboot_agent",
        "collect_artifacts", "isolate_host", "unisolate_host",
        "disable_account", "pause_scheduled_task",
    }
    missing = expected - set(names)
    assert not missing, f"server is missing tools: {missing}"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_server_smoke.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `server.py`**

`nanormm/trmm-mcp/trmm_mcp/server.py`:
```python
"""MCP server entrypoint for trmm-mcp."""

from dataclasses import dataclass
from typing import Any

import redis
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .approvals import ApprovalRegistry
from .audit import AuditLog
from .policy import Policy
from .settings import Settings
from .tools._base import Dispatcher, ToolRegistry
from .tools import alerts, agents, clients, scripts, actions
from .trmm_client import TrmmClient


@dataclass
class TrmmMcpServer:
    mcp: Server
    tool_registry: ToolRegistry
    dispatcher: Dispatcher
    trmm_client: TrmmClient


def build_server() -> TrmmMcpServer:
    settings = Settings()
    policy = Policy.load(settings.policy_path)
    approvals = ApprovalRegistry(
        redis.Redis.from_url(settings.redis_url, decode_responses=True),
        ttl_seconds=settings.pending_action_ttl_seconds,
    )
    audit = AuditLog(settings.audit_dsn)
    trmm = TrmmClient(base_url=settings.trmm_api_base, token=settings.trmm_api_token)

    registry = ToolRegistry()
    _register_all(registry, trmm)

    dispatcher = Dispatcher(
        registry=registry, policy=policy, approvals=approvals, audit=audit
    )

    mcp = Server("trmm-mcp")

    @mcp.list_tools()
    async def _list_tools() -> list[Tool]:
        return [_tool_descriptor(name) for name in registry.tool_names()]

    @mcp.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        result = await dispatcher.dispatch(name, arguments)
        return [TextContent(type="text", text=_serialize(result))]

    return TrmmMcpServer(
        mcp=mcp, tool_registry=registry, dispatcher=dispatcher, trmm_client=trmm
    )


def _register_all(registry: ToolRegistry, trmm: TrmmClient) -> None:
    """Bind every tool function to the registry, partial-applying the TRMM client."""

    def bind(name: str, fn):
        async def wrapped(**kwargs):
            return await fn(client=trmm, **kwargs)

        registry.register(name=name)(wrapped)

    bind("list_alerts", alerts.list_alerts)
    bind("get_alert", alerts.get_alert)
    bind("search_past_alerts", alerts.search_past_alerts)
    bind("acknowledge_alert", alerts.acknowledge_alert)

    bind("list_agents", agents.list_agents)
    bind("get_agent", agents.get_agent)
    bind("agent_recent_checks", agents.agent_recent_checks)
    bind("agent_recent_tasks", agents.agent_recent_tasks)
    bind("agent_patch_state", agents.agent_patch_state)
    bind("agent_running_processes", agents.agent_running_processes)

    bind("query_clients", clients.query_clients)

    bind("script_history", scripts.script_history)
    bind("run_script_on_agent", scripts.run_script_on_agent)
    bind("run_inline_command", scripts.run_inline_command)

    bind("kill_process", actions.kill_process)
    bind("restart_service", actions.restart_service)
    bind("reboot_agent", actions.reboot_agent)
    bind("collect_artifacts", actions.collect_artifacts)
    bind("isolate_host", actions.isolate_host)
    bind("unisolate_host", actions.unisolate_host)
    bind("disable_account", actions.disable_account)
    bind("pause_scheduled_task", actions.pause_scheduled_task)


def _tool_descriptor(name: str) -> Tool:
    """Minimal Tool descriptor — proper input schemas added in Task 19."""
    return Tool(name=name, description=f"trmm-mcp tool: {name}", inputSchema={"type": "object"})


def _serialize(payload: Any) -> str:
    import json

    return json.dumps(payload, default=str, separators=(",", ":"))


async def run() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.mcp.run(read, write, server.mcp.create_initialization_options())
```

- [ ] **Step 4: Implement `__main__.py`**

`nanormm/trmm-mcp/trmm_mcp/__main__.py`:
```python
import asyncio

from .server import run


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run, verify pass**

```bash
pytest tests/test_server_smoke.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/server.py nanormm/trmm-mcp/trmm_mcp/__main__.py nanormm/trmm-mcp/tests/test_server_smoke.py
git commit -m "trmm-mcp: MCP server entrypoint with full tool registration

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 19: Tool input schemas (proper JSON Schema for each tool)

The placeholder schema `{"type": "object"}` works for plumbing but isn't useful to agents. Replace with real schemas so Claude can call tools correctly.

**Files:**
- Create: `nanormm/trmm-mcp/trmm_mcp/tools/_schemas.py`
- Modify: `nanormm/trmm-mcp/trmm_mcp/server.py`
- Modify: `nanormm/trmm-mcp/tests/test_server_smoke.py`

- [ ] **Step 1: Append failing test**

Append to `nanormm/trmm-mcp/tests/test_server_smoke.py`:
```python
def test_tool_descriptors_have_real_schemas(trmm_env, tmp_path, monkeypatch):
    pol = tmp_path / "policy.yaml"
    pol.write_text("version: 1\ndefault: human_approval\ntools: {}\n")
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol))

    from trmm_mcp.server import build_server, _tool_descriptor

    server = build_server()
    for name in server.tool_registry.tool_names():
        desc = _tool_descriptor(name)
        # Real schemas have at least one property or no required-fields constraint
        assert isinstance(desc.inputSchema, dict)
        assert desc.inputSchema.get("type") == "object"
        # Description should be non-trivial
        assert len(desc.description) > 20, f"{name} has weak description"


def test_kill_process_schema_requires_agent_id(trmm_env, tmp_path, monkeypatch):
    pol = tmp_path / "policy.yaml"
    pol.write_text("version: 1\ndefault: human_approval\ntools: {}\n")
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol))

    from trmm_mcp.server import _tool_descriptor

    desc = _tool_descriptor("kill_process")
    assert "agent_id" in desc.inputSchema["properties"]
    assert "agent_id" in desc.inputSchema["required"]
```

- [ ] **Step 2: Implement `tools/_schemas.py`**

`nanormm/trmm-mcp/trmm_mcp/tools/_schemas.py`:
```python
"""JSON Schemas + descriptions for each tool, surfaced to MCP clients."""

from typing import Any

_AGENT_ID = {"type": "string", "description": "TRMM agent UUID"}
_INT_LIMIT = {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}

SCHEMAS: dict[str, dict[str, Any]] = {
    "list_alerts": {
        "description": (
            "List TRMM alerts. Optionally filter by status (all|unresolved|snoozed), "
            "since timestamp (ISO 8601), or client ID."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["all", "unresolved", "snoozed"]},
                "since": {"type": "string", "description": "ISO 8601 timestamp"},
                "client_id": {"type": "integer"},
            },
        },
    },
    "get_alert": {
        "description": "Fetch full detail for one alert by integer ID.",
        "schema": {
            "type": "object",
            "properties": {"alert_id": {"type": "integer"}},
            "required": ["alert_id"],
        },
    },
    "search_past_alerts": {
        "description": "Find historical alerts on a given agent since an ISO timestamp.",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "since": {"type": "string", "description": "ISO 8601 timestamp"},
            },
            "required": ["agent_id", "since"],
        },
    },
    "acknowledge_alert": {
        "description": "Mark an alert as resolved with an optional note (write).",
        "schema": {
            "type": "object",
            "properties": {
                "alert_id": {"type": "integer"},
                "note": {"type": "string"},
            },
            "required": ["alert_id"],
        },
    },
    "list_agents": {
        "description": "List TRMM agents. Filter by online status, client ID, or site ID.",
        "schema": {
            "type": "object",
            "properties": {
                "online": {"type": "boolean"},
                "client_id": {"type": "integer"},
                "site_id": {"type": "integer"},
            },
        },
    },
    "get_agent": {
        "description": "Fetch the full record for one agent.",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "agent_recent_checks": {
        "description": "Recent check results for an agent (pass/fail + output).",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID, "n": _INT_LIMIT},
            "required": ["agent_id"],
        },
    },
    "agent_recent_tasks": {
        "description": "Recent scheduled-task runs for an agent.",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID, "n": _INT_LIMIT},
            "required": ["agent_id"],
        },
    },
    "agent_patch_state": {
        "description": "Windows Update state: installed, missing, failed, pending-reboot KBs.",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "agent_running_processes": {
        "description": "Live process list from an agent (PID, name, CPU, memory).",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "query_clients": {
        "description": "Return the full client → site tree.",
        "schema": {"type": "object", "properties": {}},
    },
    "script_history": {
        "description": "Recent script runs on an agent (stdout, stderr, retcode).",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID, "n": _INT_LIMIT},
            "required": ["agent_id"],
        },
    },
    "run_script_on_agent": {
        "description": (
            "Invoke a TRMM-library script by integer ID on an agent. "
            "Cannot ship a script body — only IDs of scripts already in TRMM. (write)"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "script_id": {"type": "integer"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["agent_id", "script_id"],
        },
    },
    "run_inline_command": {
        "description": (
            "Run an arbitrary shell command on an agent. PERMANENTLY GATED — every "
            "invocation requires human approval. (write)"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "shell": {"type": "string", "enum": ["cmd", "powershell", "bash"]},
                "command": {"type": "string"},
            },
            "required": ["agent_id", "shell", "command"],
        },
    },
    "kill_process": {
        "description": "Kill a process on an agent by PID or name. (write)",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "pid": {"type": "integer"},
                "name": {"type": "string"},
            },
            "required": ["agent_id"],
        },
    },
    "restart_service": {
        "description": "Restart a Windows/Linux service on an agent. (write)",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "service_name": {"type": "string"},
            },
            "required": ["agent_id", "service_name"],
        },
    },
    "reboot_agent": {
        "description": "Reboot an agent host. (write)",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "collect_artifacts": {
        "description": (
            "Trigger artifact collection on an agent. artifact_set must be one of: "
            "event_logs, process_list, network_connections, scheduled_tasks, "
            "installed_software. (write)"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "artifact_set": {
                    "type": "string",
                    "enum": [
                        "event_logs",
                        "process_list",
                        "network_connections",
                        "scheduled_tasks",
                        "installed_software",
                    ],
                },
            },
            "required": ["agent_id", "artifact_set"],
        },
    },
    "isolate_host": {
        "description": "Isolate an agent from the network via firewall script. (write)",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "unisolate_host": {
        "description": "Reverse host isolation. (write)",
        "schema": {
            "type": "object",
            "properties": {"agent_id": _AGENT_ID},
            "required": ["agent_id"],
        },
    },
    "disable_account": {
        "description": "Disable a local user account on an agent. (write)",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "username": {"type": "string"},
            },
            "required": ["agent_id", "username"],
        },
    },
    "pause_scheduled_task": {
        "description": "Pause a scheduled task on an agent by integer task ID. (write)",
        "schema": {
            "type": "object",
            "properties": {
                "agent_id": _AGENT_ID,
                "task_id": {"type": "integer"},
            },
            "required": ["agent_id", "task_id"],
        },
    },
}
```

- [ ] **Step 3: Update `_tool_descriptor` in `server.py`**

Replace the existing `_tool_descriptor` function in `nanormm/trmm-mcp/trmm_mcp/server.py` with:

```python
def _tool_descriptor(name: str) -> Tool:
    from .tools._schemas import SCHEMAS

    entry = SCHEMAS.get(name)
    if entry is None:
        raise PolicyError(f"no schema for tool {name}; add to tools/_schemas.py")
    return Tool(
        name=name,
        description=entry["description"],
        inputSchema=entry["schema"],
    )
```

Add `from .exceptions import PolicyError` to `server.py`'s imports.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_server_smoke.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/tools/_schemas.py nanormm/trmm-mcp/trmm_mcp/server.py nanormm/trmm-mcp/tests/test_server_smoke.py
git commit -m "trmm-mcp: real JSON Schemas for every tool descriptor

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 20: Policy regression test — every registered tool must have an explicit policy entry

The spec requires this guardrail: catch the case where a developer adds a new tool but forgets to add it to `policy.yaml`, which would otherwise silently inherit `default: human_approval` and create a confusing experience.

**Files:**
- Create: `nanormm/trmm-mcp/tests/test_policy_regression.py`

- [ ] **Step 1: Write the test**

`nanormm/trmm-mcp/tests/test_policy_regression.py`:
```python
from pathlib import Path

import pytest


def test_production_policy_yaml_covers_every_registered_tool(trmm_env, monkeypatch):
    """The shipped nanormm/policy.yaml must list every tool the server registers."""
    repo_root = Path(__file__).resolve().parents[3]
    pol_path = repo_root / "nanormm" / "policy.yaml"
    monkeypatch.setenv("NANORMM_POLICY_PATH", str(pol_path))

    from trmm_mcp.policy import Policy
    from trmm_mcp.server import build_server

    server = build_server()
    registered = set(server.tool_registry.tool_names())

    pol = Policy.load(pol_path)
    listed = set(pol._model.tools.keys())  # internal access ok in test

    missing = registered - listed
    assert not missing, (
        f"policy.yaml is missing entries for: {sorted(missing)}. "
        f"Add explicit `auto`/`human_approval`/`forbidden` lines to nanormm/policy.yaml."
    )

    extra = listed - registered
    assert not extra, (
        f"policy.yaml lists tools that are not registered with the server: {sorted(extra)}. "
        f"Either register the tool or remove the policy entry."
    )
```

- [ ] **Step 2: Run, verify pass**

```bash
pytest tests/test_policy_regression.py -v
```
Expected: 1 passed (the production `nanormm/policy.yaml` from Task 5 already lists every tool we've registered).

If it FAILS: it means a tool was added without updating `policy.yaml`. Update `nanormm/policy.yaml` first, then re-run.

- [ ] **Step 3: Commit**

```bash
git add nanormm/trmm-mcp/tests/test_policy_regression.py
git commit -m "trmm-mcp: regression test asserting every registered tool has a policy entry

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 21: Replay safety — reload pending-approved actions on server restart

**Files:**
- Modify: `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`
- Modify: `nanormm/trmm-mcp/tests/tools/test__base.py`

If the MCP server crashes between an action being approved and being executed, on restart it should scan Redis for `approved` rows and replay them. This is the spec's "Idempotency: write tools include an idempotency key derived from action_id, so replay is safe" requirement.

- [ ] **Step 1: Append failing test**

Append to `nanormm/trmm-mcp/tests/tools/test__base.py`:
```python
@pytest.mark.asyncio
async def test_recover_executes_approved_actions_on_startup(registry_env, fake_redis):
    registry, dispatcher = registry_env

    captured = []

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> dict:
        captured.append(x)
        return {"got": x}

    # Simulate: action created, approved, but server crashed before executing
    a1 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 1}, summary="")
    a2 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 2}, summary="")
    dispatcher._approvals.mark_approved(a1, approved_by="U_X")
    dispatcher._approvals.mark_approved(a2, approved_by="U_X")

    recovered = await dispatcher.recover()
    assert set(recovered) == {a1, a2}
    assert sorted(captured) == [1, 2]


@pytest.mark.asyncio
async def test_recover_skips_pending_and_rejected(registry_env):
    registry, dispatcher = registry_env

    @registry.register(name="always_gated")
    async def my_gated(x: int) -> int:
        return x

    a1 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 1}, summary="")
    a2 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 2}, summary="")
    a3 = dispatcher._approvals.create(tool_name="always_gated", args={"x": 3}, summary="")
    dispatcher._approvals.mark_approved(a1, approved_by="U_X")
    # a2 stays pending
    dispatcher._approvals.mark_rejected(a3, rejected_by="U_X", reason="no")

    recovered = await dispatcher.recover()
    assert recovered == [a1]
```

- [ ] **Step 2: Add `recover()` to `Dispatcher` and `iter_pending()` to `ApprovalRegistry`**

In `nanormm/trmm-mcp/trmm_mcp/approvals.py`, append to the `ApprovalRegistry` class:

```python
    def iter_all(self) -> list[dict[str, Any]]:
        """Return every action in the registry — used by Dispatcher.recover()."""
        out: list[dict[str, Any]] = []
        for k in self._r.scan_iter(match=f"{_KEY_PREFIX}*", count=200):
            raw = self._r.get(k)
            if raw is not None:
                out.append(json.loads(raw))
        return out
```

In `nanormm/trmm-mcp/trmm_mcp/tools/_base.py`, append to the `Dispatcher` class:

```python
    async def recover(self) -> list[str]:
        """Replay any actions that were approved but not yet executed (post-crash recovery)."""
        replayed: list[str] = []
        for row in self._approvals.iter_all():
            if row["status"] == "approved":
                try:
                    await self.resume(row["action_id"])
                    replayed.append(row["action_id"])
                except (ApprovalError, PolicyError):
                    # Tool no longer registered or row mutated — skip and log via audit
                    continue
        return replayed
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/tools/test__base.py -v
```
Expected: 9 passed (7 from Task 8 + 2 new).

- [ ] **Step 4: Wire `recover()` into server boot**

In `nanormm/trmm-mcp/trmm_mcp/server.py`, modify `run()`:

```python
async def run() -> None:
    server = build_server()
    # Replay any approved-but-unexecuted actions from before a crash
    await server.dispatcher.recover()
    async with stdio_server() as (read, write):
        await server.mcp.run(read, write, server.mcp.create_initialization_options())
```

- [ ] **Step 5: Commit**

```bash
git add nanormm/trmm-mcp/trmm_mcp/approvals.py nanormm/trmm-mcp/trmm_mcp/tools/_base.py nanormm/trmm-mcp/trmm_mcp/server.py nanormm/trmm-mcp/tests/tools/test__base.py
git commit -m "trmm-mcp: replay approved-but-unexecuted actions on server startup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 22: Manual integration smoke against the dev TRMM stack

This is the only task that runs against a real TRMM. It cannot be fully automated in this plan, but every step must be reproducible by an engineer with `.devcontainer` running. Output is captured in a checklist file the engineer fills in.

**Files:**
- Create: `nanormm/trmm-mcp/INTEGRATION-SMOKE.md`

- [ ] **Step 1: Write the smoke checklist**

`nanormm/trmm-mcp/INTEGRATION-SMOKE.md`:
```markdown
# trmm-mcp integration smoke

Run this once before considering the package ready for Plan 2 (approval-bridge).
This verifies the server actually talks to a live TRMM and that endpoint paths
match TRMM's current Django URL conf.

## Prereqs

- `.devcontainer/docker-compose.yml` is up
- A test client + at least one online agent in TRMM
- A Knox token issued for a `nanormm-svc` user with read+write API permissions
- Local Redis running (`docker run -p 6379:6379 redis`)
- Local Postgres with an empty `nanormm` database (`createdb nanormm`)

## Setup

```bash
cd nanormm/trmm-mcp
source .venv/bin/activate

# Apply migrations
psql -d nanormm -f trmm_mcp/migrations/001_init.sql

export TRMM_API_BASE=http://localhost:8000  # devcontainer Django port
export TRMM_API_TOKEN=<paste-knox-token>
export NANORMM_POLICY_PATH=$(pwd)/../policy.yaml
export NANORMM_REDIS_URL=redis://localhost:6379/11
export NANORMM_AUDIT_DSN=postgresql://localhost:5432/nanormm
```

## Smoke via mcp inspector

```bash
npx @modelcontextprotocol/inspector python -m trmm_mcp
```

Then in the inspector UI:

- [ ] `list_alerts` with no args returns at least an empty array
- [ ] `list_agents` returns the test agent(s)
- [ ] `get_agent` with a real `agent_id` returns hostname + OS
- [ ] `agent_patch_state` returns categorized KBs
- [ ] `kill_process` with `agent_id` and a fake `pid=1` returns `{"status": "pending", ...}`
- [ ] Inspect Redis: `redis-cli -n 11 keys "nanormm:pending:*"` lists the pending action
- [ ] Inspect Postgres: `psql nanormm -c "SELECT action_id, tool_name, policy_decision FROM nanormm_actions"` shows the pending row
- [ ] Edit `nanormm/policy.yaml` to set `kill_process: auto`, restart server, retry — should now return `{"status": "executed", ...}` and TRMM logs should show the kill attempt
- [ ] Reset `policy.yaml` to `human_approval`

## What to do if endpoint paths are wrong

If any tool returns 404, the path in the implementation doesn't match
TRMM's Django URL conf.  Find the real path in
`api/tacticalrmm/<app>/urls.py`, update the implementation, update the
mocked test to match the new URL pattern, re-run that tool's tests, and
re-attempt the smoke.  **Do not skip this step** — fixing it now is far
cheaper than fixing it during Plan 2.
```

- [ ] **Step 2: Commit**

```bash
git add nanormm/trmm-mcp/INTEGRATION-SMOKE.md
git commit -m "trmm-mcp: integration smoke checklist for dev TRMM stack

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Execute the checklist**

Follow `nanormm/trmm-mcp/INTEGRATION-SMOKE.md` end-to-end. If any tool fails, fix paths in lockstep with the mocked tests as the checklist describes. Once the entire checklist passes, this plan is complete.

---

## Task 23: Final cleanup — full test suite, lint, type-check

**Files:** none new

- [ ] **Step 1: Run the full test suite**

```bash
cd nanormm/trmm-mcp
pytest -v
```
Expected: every test passes. If pytest-postgresql can't find a Postgres binary, document the workaround in `README.md` and add a `pytest -m "not requires_postgres"` skip option (out of scope here — file an issue if it bites).

- [ ] **Step 2: Lint**

```bash
ruff check .
ruff format --check .
```
Expected: clean. If issues, fix them.

- [ ] **Step 3: Verify import order, no dead code**

```bash
ruff check --select I,F .
```
Expected: clean.

- [ ] **Step 4: Tag plan complete**

```bash
git tag nanormm-trmm-mcp-plan-1-complete
```

---

## Self-Review (run before declaring this plan complete to write)

**1. Spec coverage**

| Spec section | Tasks |
|---|---|
| `trmm-mcp` (Python MCP server) | 0–23 |
| Tool dispatch path (read/auto/human_approval/forbidden) | 8 |
| Read tools — every entry in spec table | 9, 10, 11, 12 |
| Write tools — every entry in spec table | 13–17 |
| `run_script_on_agent` accepts `script_id` not body | 17 (test asserts `script_body` kwarg fails) |
| `run_inline_command` permanently `human_approval` | 5 (policy.yaml) + 17 (impl) |
| `policy.yaml` shape | 5 |
| Default = `human_approval` for unlisted tools | 5 (test) |
| Approval registry, Redis DB index 11 | 6 + 22 (env in smoke) |
| 30-min default TTL | 1 (settings default) |
| Audit table `nanormm_actions` schema | 7 |
| Crash recovery — replay approved actions | 21 |
| Idempotency key derived from `action_id` | 21 (mark_executed once-only via state machine) |
| Fail-closed on missing/corrupt policy | 5 (test) |
| Policy regression test for unlisted tools | 20 |
| Slack approval flow end-to-end | **NOT IN THIS PLAN** — Plan 2 |

**2. Placeholder scan** — searched for "TBD", "TODO", "FIXME", "fill in", "implement later", "similar to". One reference to `script_body` in test is intentional (asserting absence of that kwarg). Otherwise clean.

**3. Type/name consistency** — `Authority` enum used consistently; `action_id` formatted as `act_<token>` everywhere; `ApprovalRegistry` methods (`create/get/mark_approved/mark_rejected/mark_executed/iter_all`) match across `approvals.py`, `_base.py`, and tests; `AuditLog` methods (`record_pending/record_approval/record_rejection/record_execution`) match.

**4. Known caveats**
- **TRMM endpoint paths in tool implementations are best-guess** based on TRMM's app names (`alerts`, `agents`, `scripts`). Task 22's integration smoke explicitly verifies them and tells the engineer to fix any 404s before completing the plan. This is the only place real-world adjustments may be needed.
- `pytest-postgresql` requires `pg_ctl`/`initdb` available locally. On Debian: `apt install postgresql`. The CI image will need this too — note for Plan 4 deployment.
