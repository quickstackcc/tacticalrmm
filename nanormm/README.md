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
