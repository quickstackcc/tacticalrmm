# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This is a **fork of [amidaware/tacticalrmm](https://github.com/amidaware/tacticalrmm) for Quick Stack**, currently unmodified from upstream. Remotes:

- `origin` → `https://github.com/quickstack-cc/tacticalrmm`
- `upstream` → `https://github.com/amidaware/tacticalrmm`

Only the `develop` branch was forked. Sync upstream with `git fetch upstream develop && git rebase upstream/develop` (or merge). Rebranding (`tactical*` / `trmm*` / `TRMM_*` → Quick Stack equivalents) has **not** happened yet — the prefixes are threaded through container names, env vars, Django settings, domain defaults, and the `tacticalrmm/` Django project/package name. Coordinate any rename across all of those at once.

## Licensing

Quick Stack is deploying this for **internal use to replace Atera** (managing their own / client fleet), not reselling RMM access as a SaaS — so the root TRMM license is fine. Two things to still watch:

- **`api/tacticalrmm/ee/LICENSE.md`** (EE License): everything under `ee/` (currently `ee/sso/` and `ee/reporting/`) requires a valid sponsorship token, and modifying code to bypass the license-key gates is explicitly prohibited. Options: buy a sponsorship, skip the features, or **replace** those modules with a clean-room implementation. Either way, leave the upstream `ee/` tree untouched to keep future merges clean.
- **Root `LICENSE.md`** (TRMM License v1.0): not OSI-open-source. The SaaS clause doesn't apply to internal use, but it does mean Quick Stack can't later pivot to offering hosted TRMM to third parties without AmidaWare's permission.

## Architecture

Five cooperating services, split across three languages. Claude's job usually touches the Django API, occasionally the Go NATS bridge; the frontend and agent live in separate repos.

- **Django 4.2 + DRF API** — `api/tacticalrmm/`. Django project package is `tacticalrmm/`. Celery workers + Celery Beat (scheduled tasks defined in `tacticalrmm/celery.py`), Django Channels over ASGI/daphne for websockets (`ws_urlpatterns` in `tacticalrmm/urls.py`). REST auth via `django-rest-knox` tokens plus a custom `APIAuthentication` class for agent-to-server calls.
- **Go NATS bridge** — `main.go` + `natsapi/`. Single static binary (`nats-api`) that subscribes to NATS subjects and writes directly to Postgres. Agents talk NATS, not HTTP, for most traffic; `apiv3`/`apiv4` Django apps serve the remaining HTTP agent endpoints.
- **Vue frontend** — **lives in a separate repo** (`amidaware/tacticalrmm-web`). Not in this tree. Pinned via `WEB_VERSION` in `tacticalrmm/settings.py`. `APP_VER` in the same file is a browser-cache-bust string that must be bumped whenever the Vue app changes.
- **Windows/Linux/Mac agent** — separate repo `amidaware/rmmagent`, pinned via `LATEST_AGENT_VER`.
- **MeshCentral** (Node.js) — external project, pinned via `MESH_VER`, runs as its own container for remote desktop / file browser.

Data stores: **Postgres** (app DB), **MongoDB** (MeshCentral's DB only), **Redis** (Celery broker, Django cache backend, Channels layer). The cache uses DB index 10 on Redis — see `tacticalrmm.cache.TacticalRedisCache`.

### Version pinning is cross-cutting

`api/tacticalrmm/tacticalrmm/settings.py` holds the pins for every moving piece: `TRMM_VERSION`, `WEB_VERSION`, `APP_VER`, `LATEST_AGENT_VER`, `MESH_VER`, `NATS_SERVER_VER`, plus `SETUPTOOLS_VER`/`WHEEL_VER` that CI reads via regex. Bumping the server version is never a single-file change.

### Ops kill-switches (via env → settings)

`TRMM_DISABLE_WEB_TERMINAL`, `TRMM_DISABLE_SERVER_SCRIPTS`, `TRMM_DISABLE_SSO`, `HOSTED`, `DEMO` — each disables features and/or URL routes (see the conditionals in `tacticalrmm/urls.py` and `settings.py`). `HOSTED=True` implies SaaS and forces the web terminal off. When changing feature surface area, search for these flags.

### Django app layout (in `api/tacticalrmm/`)

End-user apps: `accounts`, `agents`, `alerts`, `automation`, `autotasks`, `checks`, `clients`, `core`, `logs`, `scripts`, `services`, `software`, `winupdate`. Agent-facing HTTP: `apiv3/` (legacy), `apiv4/` (current). EE: `ee/sso/`, `ee/reporting/`. `beta/` is version-gated via `BETA_API_ENABLED`.

Registration pattern: each app's `urls.py` is included under its path in `tacticalrmm/urls.py`. Websocket consumers are registered in `ws_urlpatterns` at the bottom of the same file.

## Commands

All Python commands run from `api/tacticalrmm/` unless noted. Python **3.11.8** is the CI-pinned version — match it locally.

```bash
# Tests (requires Postgres reachable; CI uses GHACTIONS=yes to switch to pipeline DB creds)
pytest                                       # full suite
pytest agents/tests/test_agent.py            # single file
pytest agents/tests/test_agent.py::AgentTests::test_method_name  # single test
pytest -k "substring"                        # by name

# Format / lint (run from api/, not api/tacticalrmm/, for black)
cd api && black --exclude migrations/ --check --diff tacticalrmm
cd api/tacticalrmm && flake8 --config .flake8 .

# Go NATS bridge (from repo root)
CGO_ENABLED=0 go build -ldflags "-s -w" -o nats-api
./nats-api --config /path/to/config --log DEBUG
```

### Running locally

The supported dev environment is the devcontainer at `.devcontainer/` — it brings up Django, Celery, Celery Beat, Channels (daphne), NATS, MeshCentral, nginx, Postgres, Mongo, Redis together. `docker compose -f .devcontainer/docker-compose.yml up` **requires env vars** (`API_HOST`, `APP_HOST`, `MESH_HOST`, `POSTGRES_*`, `MESH_*`, `MONGODB_*`, `TRMM_USER/PASS`, `DOCKER_NETWORK`, `DOCKER_NGINX_IP`, `CERT_*`, etc.) — see `.devcontainer/entrypoint.sh` for the full list. There is no checked-in `.env`; the devcontainer tooling normally supplies one.

Production orchestration lives at `docker/docker-compose.yml` with 12 services, images built by `docker/image-build.sh` and consumed by `docker/tactical-cli`.

### Local Django settings override

`tacticalrmm/settings.py` does `from .local_settings import *` (suppressed if missing). That file is the expected place for DB creds, `SECRET_KEY`, `ALLOWED_HOSTS`, `CORS_ORIGIN_WHITELIST`, and feature toggles when running outside Docker. Do not commit it.

## Conventions worth knowing

- **Black + flake8** are enforced in CI; black's config excludes `migrations/` — don't reformat migration files.
- **Test discovery** picks up both `tests.py` and `test_*.py`. Coverage omits migrations, asgi/wsgi, `manage.py`, `baker_recipes.py`. Model factories use `model_bakery`.
- **`GHACTIONS=yes`** is the env switch CI uses to swap in the pipeline Postgres creds and disable admin/demo — replicate it if you need to run tests against a fresh Postgres matching CI's shape.
- **Knox tokens, not JWT.** Auth flow: `v2/login/` returns a Knox token; agent-to-server auth uses the custom `APIAuthentication` class keyed off an agent's token field.
- **Celery Beat schedule is defined in code**, not in the DB — edits to `tacticalrmm/celery.py`'s `beat_schedule` require restarting the beat container.
