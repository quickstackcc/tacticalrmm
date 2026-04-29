# approval-bridge

FastAPI service that closes the Slack approval round-trip for `trmm-mcp`.

When a tech clicks the Confirm button on a `nanoclaw_action` Slack message,
nanoclaw's `nanoclaw_confirm` handler POSTs to this service with a Bearer-
authenticated `{token}` body. The bridge marks the matching pending action
approved in Redis and calls `trmm_mcp.tools._base.Dispatcher.resume()` to
execute the action against TRMM.

## Endpoints

- `POST /api/nanoclaw/actions/execute/` — execute an approved action (Bearer-auth)
- `POST /api/nanoclaw/actions/reject/`  — record an explicit rejection (Bearer-auth)
- `POST /mcp/`                           — MCP HTTP transport for trmm-mcp tools (no auth, internal-only binding)
- `GET  /healthz`                        — systemd / load-balancer healthcheck

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

Development:

    uv pip install -e '.[dev]'
    NANORMM_BRIDGE_API_KEY=devsecret python -m approval_bridge

For production deployment on the qsrmm VM, see [`../deploy/README.md`](../deploy/README.md).
