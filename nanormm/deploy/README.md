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

## Upgrade (after merging new bridge or trmm-mcp code to develop)

```bash
sudo /opt/nanormm/qsrmm/nanormm/deploy/install-bridge.sh
```

The bridge's `pyproject.toml` declares trmm-mcp with `editable = true` in
`[tool.uv.sources]`, so `uv pip install -e .` installs both packages
editable. The install script also passes `--reinstall-package` for both
wheels, which forces uv to rebuild from the freshly-pulled source on
upgrade — without it, uv's resolver short-circuits when the version
string hasn't changed, and edits to trmm-mcp source silently don't take
effect on restart.

## Recon prompt deployment

The recon agent prompt lives at `nanormm/recon/CLAUDE.md` in this repo and
must be copied onto the VM at `/opt/nanoclaw/groups/recon/CLAUDE.local.md`
when changed. Nanoclaw runs as its own systemd service (`nanoclaw.service`),
separate from the bridge.

```bash
# From the VM, after install-bridge.sh has refreshed the qsrmm checkout:
sudo cp /opt/nanormm/qsrmm/nanormm/recon/CLAUDE.md \
        /opt/nanoclaw/groups/recon/CLAUDE.local.md
sudo chown nanoclaw:nanoclaw /opt/nanoclaw/groups/recon/CLAUDE.local.md
sudo systemctl restart nanoclaw
```

The destination filename is `CLAUDE.local.md` (with `.local`) by nanoclaw
convention — it is the per-group user-content file. The sibling `CLAUDE.md`
and `.claude-fragments/` on the VM are auto-composed at spawn and should
not be touched.

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
