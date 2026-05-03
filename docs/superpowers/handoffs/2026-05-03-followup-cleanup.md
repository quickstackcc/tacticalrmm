# 2026-05-03 — followup cleanup + nanoclaw upstream catchup

**Date:** 2026-05-03
**Status:** 5 of 6 Plan 4 followups closed; nanoclaw fork brought to upstream parity; production VM redeployed.

**Companion docs:**
- [Plan 4 debrief](./2026-05-02-plan4-debrief.md) — predecessor; the followup list this session worked through

---

## TL;DR

Plan 4 left six followups. This session closed five of them and folded in two opportunistic wins (nanoclaw upstream catchup, stale-untracked-files commit). Production VM is current on both bridge and nanoclaw. One followup deferred (low priority).

---

## What landed (commit-level)

### qsrmm (`quickstackcc/tacticalrmm` `develop`)

| SHA | Headline | Followup |
|---|---|---|
| `9d7ce1d0` | trmm-mcp: align action tools with real TRMM API | #1 kill_process URL |
| `21000df0` | recon: commit agent prompt as canonical source | #4 prompt in git |
| `e1f622de` | deploy: make trmm-mcp editable on the VM and document recon prompt rollout | #5 deploy runbook |
| `ea650b0a` | deploy: correct QSRMM_REPO_URL to the actual public HTTPS URL | discovered during rollout |
| `e7688c2b` | docs: catch up four untracked files | repo hygiene |

### nanoclaw fork (`quickstack-cc/nanormm-nanoclaw` `main`)

| SHA | Headline | Followup |
|---|---|---|
| `74bd8ae` | container-runner: skip OneCLI gateway when ONECLI_API_KEY is unset | #2 OneCLI 401 |
| `3c0dbb3` | Merge branch 'main' of qwibitai/nanoclaw — catch up 161 commits | upstream catchup |

### Production state (operational, no commits)

- Bootstrapped `/opt/nanormm/qsrmm/` as a real git checkout (was previously rsync-deployed; install-bridge.sh's clone path had never been exercised — the URL was wrong on three counts).
- Ran the updated `install-bridge.sh` on the VM. Both `approval-bridge` and `trmm-mcp` are now installed editable (`_editable_impl_*.pth` in site-packages) — verified.
- Pulled, rebuilt, restarted nanoclaw on the VM. Stashed VM-side hotfixes (Vertex AI forwarding, session-id header, ask_user_question disallow) before pull because they had been upstreamed; stash later dropped.
- Cleaned up 10 stale `pending` rows in `nanormm_actions`. Redis pending keys had already TTL'd out.

---

## Followup status

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | `kill_process` tool URL is wrong | ✅ closed | Was `POST .../processes/kill/`; real is `DELETE .../processes/<pid>/`. Audited the rest of `tools/actions.py` while there: 5 phantom tools (collect_artifacts, isolate_host, unisolate_host, disable_account, pause_scheduled_task) had no corresponding TRMM endpoints at all. Removed them from `actions.py`, `_schemas.py`, `server.py` registry, `policy.yaml`, and tests. `restart_service` URL/body shape was also wrong — fixed (`POST /services/<id>/<svc>/` with `{sv_action: "restart"}`). |
| 2 | OneCLI 401 on every container spawn | ✅ closed | `ONECLI_API_KEY` is empty on the VM (deployment doesn't use OneCLI's vault — TRMM creds live in the bridge). Wrapped the entire `ensureAgent + applyContainerConfig` block in `if (ONECLI_API_KEY)`; preserves upstream's later hard-fail-on-error tightening when configured, skips silently when not. |
| 3 | Stale audit rows | ✅ closed | 10 rows deleted via the cleanup SQL from the Plan 3 handoff. Includes one approved-but-not-executed row from the Plan 4 smoke (`act_nP1lepAlUyB1Cjz0`) — deliberately deleted rather than letting the dispatcher resume a 6-day-old kill request post-fix. |
| 4 | Recon prompt not in version control | ✅ closed | Lives at `nanormm/recon/CLAUDE.md` with a README explaining the deploy mapping (canonical here → `/opt/nanoclaw/groups/recon/CLAUDE.local.md` on VM). `groups/*` is gitignored in the nanoclaw fork itself, which is why the prompt has to be mirrored in qsrmm rather than living alongside `groups/main` and `groups/global` upstream. |
| 5 | Bridge venv install path is fragile | ✅ closed | Two changes: (a) `[tool.uv.sources]` now has `editable = true` so `uv pip install -e .` makes both packages editable in one shot, and (b) `install-bridge.sh` passes `--reinstall-package` for both wheels to defeat uv's version-skip on upgrade. Runbook explains both. |
| 6 | Two-handler dispatch pattern in nanoclaw | ⏸ deferred | Architectural hygiene only. Today both `nanormm-bridge` and `interactive` register response handlers and rely on load order; works fine. Worth the refactor only if the registry grows further. |

---

## Plan 4 hangovers from Plan 3 era — also addressed this session

- **VM had no real git checkout at /opt/nanormm/qsrmm.** install-bridge.sh's clone path had three errors (SSH URL with no keys, wrong org `quickstack-cc` vs `quickstackcc`, wrong repo `qsrmm` vs `tacticalrmm`). Bootstrapped manually with the correct URL; future runs go through the `git fetch + reset --hard` branch which works fine.
- **Untracked working-tree state.** Four files had been sitting untracked in qsrmm since long before this session: root `CLAUDE.md`, `docs/quickstack/{admin-hardening,gcp-deploy,migration-nanoclaw-dedicated-sa}.md`, `docs/superpowers/handoffs/2026-04-29-plan3-smoke-results.md`. Committed in one batch.
- **Whitespace churn in nanoclaw bridge module.** Three files had uncommitted prettier reflow only — discarded with `git checkout --`.

---

## Nanoclaw upstream catchup

`qwibitai/nanoclaw` had advanced **161 commits** in roughly the two weeks since we forked. Caught up via merge (not rebase — preserves our 12 commits' SHAs that the VM and any pinned references rely on).

**Resolution detail** worth remembering: only one conflict, in `src/container-runner.ts`. Upstream tightened the OneCLI gateway block to throw on failure instead of warning; our HEAD had the original try/warn wrapped in our `if (ONECLI_API_KEY)` skip. Resolution preserves both intents — upstream's hard-fail when configured + our silent-skip when not.

**Test status post-merge:** 253/255. The 2 failures are in `src/host-sweep.test.ts > resetStuckProcessingRows orphan-cleanup` and reproduce on **clean upstream/main** — pre-existing upstream test-side bug (`TypeError: Cannot open database because the directory does not exist` in `openOutboundDbRw`), not caused by our changes. Worth filing upstream.

**Going forward:** memory note `project_nanoclaw_upstream_sync.md` captures a 2–4 week cadence target. Heuristic: `git log origin/main..upstream/main | wc -l` — if >50 commits behind, catchup before new feature work.

**Heads-up about a contract change in this merge:** `wakeContainer` return type changed from `Promise<void>` to `Promise<boolean>` (callers can branch on spawn success vs transient failure). None of our 12 fork-specific commits call it directly, but if future work touches the call sites, the contract has shifted.

---

## VM state (relevant cosmetic noise)

These are visible in `git status` on the VM but harmless. Decided to leave alone:

- `/opt/nanoclaw` working tree shows `D groups/global/CLAUDE.md` — nanoclaw's startup migration logic deleted the unused group dir. Fine, the file was already orphaned.
- Several `.bak` files in `/opt/nanoclaw/container/` from Plan 3/4 era hot-fix backups. Cosmetic, harmless. `dist.bak.plan4/` likewise.

The VM is a consumer of source, not a source of commits. Cleaning these up doesn't matter unless somebody is reading `git status` on the VM to gauge state.

---

## Architecture-review followups (long term)

Carried over from the Plan 4 debrief, still open:

- **Bridge writes directly to nanoclaw's per-session sqlite outbound.db.** If nanoclaw refactors session storage, the bridge breaks. Wrap behind a public API in nanoclaw (e.g. `enqueueOutbound(sessionId, content)`) so the schema is encapsulated.
- **`dispatcher._inject = InjectClient(...)` in bridge `deps.py` is a private-attr write** because `_build_dispatcher` from trmm-mcp doesn't take inject_client. Cleaner: extend the constructor to accept it, or factor the inject-aware Dispatcher into a separate constructor.
- **Localhost-trust for the inject endpoint.** Bridge → nanoclaw HTTP on 127.0.0.1:8765 is unauthenticated. If we ever separate the two hosts, need bearer auth + cert validation.
- **Nanoclaw fork structural fix.** If keeping the fork in lockstep with upstream gets too painful, the long-term answer is to push for an extension-API hook in upstream so our `nanormm-bridge` module can be loaded as a plugin instead of patched in. Today the manual merge cadence is the cheaper option.

---

## Counts

- 7 commits across two repos (5 qsrmm + 2 nanoclaw)
- 161 upstream commits absorbed into the nanoclaw fork
- 3 followups closed via code, 1 closed via data cleanup, 1 closed via doc, 1 deferred
- 2 production rollouts (bridge + nanoclaw, both clean restart, no errors)
- 1 memory note added: nanoclaw upstream sync discipline
