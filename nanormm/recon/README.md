# recon — agent group config

Canonical source for the **recon** nanoclaw agent group: the alert-triage
agent that watches `#rmm-alerts` for TacticalRMM webhook posts.

## Deploy mapping

| File in this directory | Deploys to (on the qsrmm VM)                      |
|------------------------|---------------------------------------------------|
| `CLAUDE.md`            | `/opt/nanoclaw/groups/recon/CLAUDE.local.md`      |

The destination filename is `CLAUDE.local.md` (with `.local`) by
nanoclaw convention: it is the per-group user-content file. Nanoclaw
auto-composes the sibling `CLAUDE.md` and `.claude-fragments/` at spawn,
so we only own `CLAUDE.local.md` content.

`groups/*` is gitignored in the nanoclaw fork itself, which is why this
prompt is mirrored into qsrmm rather than living alongside `groups/main`
and `groups/global` upstream.

## When to update

Any time the recon agent's behavior contract changes — for example,
when its tool surface gains/loses tools, or when its approval-flow
contract evolves. Edit `CLAUDE.md` here, commit, then `scp` it onto the
VM as `CLAUDE.local.md` and `systemctl restart nanoclaw`.

## Not committed here

- `container.json` — has an instance-specific `agentGroupId` (the
  `agent_group_members` row this group binds to). Lives only on the VM.
- `.claude-fragments/` and `CLAUDE.md` (the auto-composed wrapper) —
  recreated by nanoclaw at every spawn.
