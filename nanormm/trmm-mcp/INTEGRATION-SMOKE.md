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
