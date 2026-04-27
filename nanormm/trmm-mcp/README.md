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
