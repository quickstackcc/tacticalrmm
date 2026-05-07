# 2026-05-07 — SSO cutover: RESOLVED

**Date:** 2026-05-07
**Status (final, 2026-05-07 evening):** ✅ **Resolved.** SSO live via the patched fork. Break-glass model in place (qs-admin retains Django-superuser as local-only fallback; daily admin is the new SSO-created `jim` user with TRMM `Administrator` role).

**Status (mid-day, halted):** Halted mid-cutover. Diagnosis complete, patch ready, deploy blocked on build-environment regression. SSO disabled in TRMM, all production state restored to pre-session.

---

## RESOLUTION (2026-05-07 evening session)

The Node-version hypothesis was **wrong**. Rebuilding the patched fork under Node 20 in a `node:20-bookworm` docker container produced **byte-identical chunk hashes** to the Node 24 build (entry chunk `ddb075c4.js`, etc.). Content hashes are deterministic — identical hashes mean identical output. So the build environment was never the variable.

**Actual root cause:** the deploy step `sudo rsync -a --delete /tmp/qs-dist-new/ /var/www/rmm/dist/` deleted `/var/www/rmm/dist/env-config.js`, a VM-managed runtime config (contents: `window._env_ = {PROD_URL: "https://api.quickstack.cc"}`) referenced by `index.html` via `<script src=/env-config.js>`. The fork's `public/` directory does not ship one. With `env-config.js` 404, `window._env_` was undefined, `boot/axios.js:getBaseUrl()` threw on `.PROD_URL`, axios converted the synchronous throw into a rejection with no `error.response`, the response interceptor's bottom branch (`error.response.status !== 423`) read `.status` on undefined and threw the visible `TypeError: Cannot read properties of undefined (reading 'status')`. LoginView crashed on mount, no SSO button.

**Fix:** add `--exclude=env-config.js` to the rsync. One-line change. Re-deployed cleanly.

**Break-glass model setup:** SSO initially auto-linked Google → existing `qs-admin` user because the previous day's session was logged in via username/password when allauth completed the OIDC handshake — allauth's default behavior is to attach the SocialAccount to the current logged-in user. Resulting state showed `User.email='jim@quickstack.tech'` (install-time value) for SSO sessions even though Google identity was `jim@quickstack.cc`. Severed by deleting the SocialAccount row, logging out, and signing back in via Google with no active session — allauth fell into the signup path and created user id=4 (`username='jim'`, `email='jim@quickstack.cc'`, `role=Administrator`). qs-admin retained as Django-superuser break-glass with no SocialAccount link.

**Final post-deploy state (verified 2026-05-07 ~19:18 UTC):**

| Resource | State |
|---|---|
| `/var/www/rmm/dist/` | Patched build (entry `ddb075c4.js`); `env-config.js` preserved |
| `core_settings.sso_enabled` | True |
| `core_settings.block_local_user_logon` | False (intentional — preserves break-glass path) |
| User id=1 `qs-admin` | `email=jim@quickstack.tech`, Django `is_superuser=True`, no SocialAccount link, **break-glass** |
| User id=4 `jim` | `email=jim@quickstack.cc`, Django `is_superuser=False`, `role=Administrator` (TRMM-level superuser), SSO daily admin |
| SocialAccount id=2 | user_id=4, provider=google, uid=113485770787885316821 |
| EmailAddress | jim@quickstack.cc primary+verified for user_id=4 |
| VM backups | `dist.pre-sso-patch-20260507-1631` (yesterday's failed attempt), `dist.pre-sso-patch-20260507-1253` (today's pre-success) |
| GCE snapshot `rmm-pre-sso-20260507-0947` | Retained |
| Fork branch `qs/sso-callback-fix` | `35ffd33` — unchanged from yesterday; the patch was correct all along |

**Memory updates committed to `~/.claude/projects/.../memory/`:**
- `project_qsrmm_trmm_sso_broken.md` — rewritten to reflect resolved state
- `feedback_qsrmm_trmm_web_deploy.md` — new; captures the `--exclude=env-config.js` rule

**Open loose ends (not blocking):**
- Pin a `.nvmrc` on the fork (Node 20 or 24; both work)
- Bake `--exclude=env-config.js` into a deploy script in `nanormm/deploy/` (or a make target on the fork)
- File the SSO route patch as an upstream PR to `amidaware/tacticalrmm-web` referencing commit `75a9ef88` (Path C from the original plan)
- Prune the older `dist.pre-sso-patch-20260507-1631` backup once SSO is proven stable for ~1 week

---

## Original mid-day handoff (kept for diagnostic record)

**Companion docs:**
- [Admin hardening checklist](../../quickstack/admin-hardening.md) — Phase 1 prerequisites for TMGW agent rollout (this session was the SSO subtask of Phase 1)
- Memory note: `project_qsrmm_trmm_sso_broken.md` (in `~/.claude/projects/-home-jim-quickstack-cc-qsrmm/memory/`)

---

## TL;DR

Started Phase 1 of the TMGW agent-rollout prerequisite list: phishing-resistant TRMM admin via Workspace SSO. Discovered upstream `tacticalrmm-web` v0.101.59 ships a SSO that is **half-deleted** — commit `75a9ef88` (2024-09-18) ripped out the Vue route + auth-store glue without re-implementing it. Forked the repo, applied a 25-line patch that closes both gaps, built the dist, deployed.

The deployed dist breaks the login page in a different way: LoginView's `onMounted` triggers an axios call that fails before the network, hidden by a latent bug in `boot/axios.js`'s response interceptor. Strong suspicion: building with Node 24 produces output that diverges from whatever Node version was used for the Jan 14 production build. Untested.

Rolled back, disabled SSO toggle, pushed the patch branch to the fork so it persists. Pick up next session by rebuilding under Node 20 (or pivoting to GCP IAP if the build issue stays elusive).

---

## Production state (after rollback)

| Asset | State |
|---|---|
| `/var/www/rmm/dist/` on rmm VM | Restored from backup; matches Jan 14 deploy. Entry chunk: `7da5fcca.js` |
| `/var/www/rmm/dist.pre-sso-patch-20260507-1631/` | Backup taken before our deploy. Keep until SSO cutover lands. |
| GCE disk snapshot `rmm-pre-sso-20260507-0947` | Pre-cutover whole-disk snapshot. Keep. |
| `core_settings.sso_enabled` | **False** (rolled back) |
| `core_settings.block_local_user_logon` | False (never flipped) |
| `SocialApp` row id=1 | Present (provider=`openid_connect`, provider_id=`google`) — leave; harmless when sso_enabled=False |
| `qs-admin.email` | `jim@quickstack.tech` (restored to install-time value) |
| Google OIDC client (project `qsrmm-494222`) | Live: client_id `536483581971-t3td9782o5l2eodc1kkrg6a3196hb87v.apps.googleusercontent.com`. Keep. |
| TRMM_DISABLE_SSO env | Untouched (default False) |
| YubiKey enrollment | Not done (admin-hardening.md Phase 1 still pending) |
| Password manager | Not adopted (admin-hardening.md Phase 1 still pending) |

The user-visible TRMM login page is the same as it was at session start: username + password + TOTP. No SSO button rendered (sso_enabled=False).

---

## Diagnostic chain (in order of discovery)

### 1. Initial setup looked correct

- GCP OAuth client created in `qsrmm-494222` with redirect URI `https://api.quickstack.cc/accounts/oidc/google/login/callback/` — verified at the URL pattern level via `ee/sso/urls.py:14-26`.
- TRMM provider added through the web UI: name=`google`, server_url=`https://accounts.google.com/.well-known/openid-configuration`, default Role = "Administrators" (Role had `is_superuser=True` toggled, so SSO-created users get full TRMM admin).
- `sso_enabled` toggled True; that save validates `token_is_valid()` automatically and succeeded — confirms sponsorship token is current.

### 2. First SSO click → /account/provider/callback 404

Vue's `1a52f335.js` SSO initiator chunk POSTs to `/_allauth/browser/v1/auth/provider/redirect/` with `callback_url=${origin}/account/provider/callback`. Google completes, allauth processes the OIDC handshake server-side, redirects browser to that callback URL. Vue's router has no route for it → renders the catch-all NotFound component (HTTP 200 from nginx, but the SPA renders 404).

### 3. Initial misdiagnosis: SocialApp.DoesNotExist (resolved by service restart)

`django_debug.log` showed an earlier `allauth.socialaccount.models.SocialApp.DoesNotExist` from `adapter.py:304`. We added debug prints to `list_apps`, restarted `rmm.service`, and the lookup started returning the row correctly on every call. Most likely uwsgi workers had stale Python state from before the SocialApp record was inserted via the UI. **The DoesNotExist error is not the SSO blocker. Restarting `rmm.service` after any DB-side SSO config change is necessary discipline.** Debug prints were reverted (md5 verified equal to `/tmp/adapter.py.bak`).

### 4. Real diagnosis: post-callback Vue route + auth-store both missing

After the restart, Django returned 302 from the callback URL, redirecting to `/account/provider/callback`. Vue still 404'd. Checked:

- Upstream `amidaware/tacticalrmm-web@v0.101.59`: `src/router/routes.js` registers no `/account/provider/callback` route. Same for `develop`.
- `src/ee/sso/views/ProviderCallback.vue` exists, but `gh search code "ProviderCallback" --repo amidaware/tacticalrmm-web` returns zero references. Orphan.
- `src/stores/auth.ts`: only `login()` and `checkCredentials()` set `auth.token`. **No code anywhere in the deployed bundle invokes `getSSOProviderToken()`** — the function is defined in `src/ee/sso/api/sso.ts:83-92`, but nobody calls it. So even if you manually navigate to `/` after a successful Google round-trip, the auth store has no token, `loggedIn` is false, the route guard kicks you to `/login`. Confirmed by Jim navigating to `/` after the failed callback — landed on `/login`.

### 5. Found the upstream regression

`gh api repos/amidaware/tacticalrmm-web/commits?path=src/router/routes.js` history bisect:

```
commit 541134a8 (2024-09-16) "sso init"          — route present
commit 75a9ef88 (2024-09-18) "implement session  — route DELETED
   auth login logic and cleanup views"
```

The diff at `75a9ef88`:

```diff
src/router/routes.js: +0 -5    (deletes the /account/provider/callback block)
src/stores/auth.ts:   +0 -6    (deletes checkSessionAuth() and getCurrentSession import)
src/ee/sso/views/ProviderCallback.vue: +5 -12 (logic simplified, never re-wired)
```

Three coordinated deletions, no replacement. Looks like a "rip out half-finished SSO" commit that intended to land replacement code which never came. The bug has been live for ~14 months.

### 6. Forked + patched

- `gh repo fork amidaware/tacticalrmm-web --org quickstack-cc --clone=false` → `quickstack-cc/tacticalrmm-web`
- Cloned to `/home/jim/quickstack-cc/tacticalrmm-web/`
- Branched off the v0.101.59 tag: `qs/sso-callback-fix`
- Patch (committed as `35ffd33`):
  - `src/router/routes.js` — re-added the 5-line `/account/provider/callback` block before the catch-all
  - `src/ee/sso/views/ProviderCallback.vue` — replaced the inline auth.loggedIn check with an `onMounted` async that calls `getSSOProviderToken()`, stores `token`/`username`/`name`/`ssoLoginProvider` in the auth store, then routes to Dashboard (or `auth.next` if set)
- Pushed branch to `quickstack-cc/tacticalrmm-web`

### 7. Build + deploy worked... mostly

- `npm ci && npx quasar build` — succeeded with 2 cosmetic CSS warnings, no JS errors
- Build verified: `path:"/account/provider/callback"` in the route table; `ssoproviders/token` URL string in the bundle; `ProviderCallback` chunk present (`ddb075c4.js` bundle main entry includes lazy import to it)
- Tar'd the dist, scp'd via IAP to VM, rsync'd to `/var/www/rmm/dist/`, set ownership www-data:www-data
- Curl tests post-deploy: index.html serves new entry `ddb075c4.js`; `/_allauth/browser/v1/config/` returns the google provider with proper CORS headers

### 8. Build regression: SSO button no longer renders at all

Jim hard-reloaded the login page in a private window. SSO button absent. Console showed:
```
e803a2bb.js:1 TypeError: Cannot read properties of undefined (reading 'status')
    at 1b1156b7.js:1:1440
    at async Vs.request (ddb075c4.js:26:1975)
    at async O (decdcb08.js:1:1426)
    at async e803a2bb.js:1:1076
```

Chunk identification:
- `e803a2bb.js` = LoginView (only file with `checkCredentials` string)
- `decdcb08.js` = `src/ee/sso/api/sso.ts` (only file with `_allauth/browser/v1` and the SSO API URLs)
- `1b1156b7.js` = `src/boot/axios.js` (verified by `interceptors.request.use`/`interceptors.response.use`)
- `ddb075c4.js` = entry chunk (Vue + axios `Vs.request`)

Stack reads as: LoginView's `onMounted` → `getSSOConfig()` → axios.get → response interceptor crashes.

**Network tab during failure: NO XHR/fetch entries.** Only static script/stylesheet/font/document/icon loads, all 200. No request was actually sent for the failed axios call.

**Conclusion:** axios crashed *before* dispatching the HTTP request. The `boot/axios.js` response interceptor has a latent bug at the bottom:

```js
if ((text || error.response) && error.response.status !== 423) {
  Notify.create({...});
}
```

If `error.response` is undefined and `text` is truthy, the second condition reads `.status` on undefined and throws. This would normally be silent if the surrounding handler caught it, but in our case the throw bubbles up to the LoginView catch block as the visible TypeError.

**Why the build behaves differently from Jan 14's:** Speculation, untested. The most likely culprit is Node version. Original deploy was 4 months ago; my build was on `node v24.14.1`. Quasar 2 (2.18.5), Vite 2.9, esbuild — toolchain published 2022 era. Node 24 may produce subtly different output (ESM resolution, module init order, etc.) that triggers axios's pre-network failure mode.

Did NOT verify by rebuilding with Node 20. That's the first thing for next session.

### 9. Rollback

Restored `/var/www/rmm/dist/` from the `dist.pre-sso-patch-20260507-1631` backup; SSO button reappears in its original (still-broken-on-click) state. Disabled `core_settings.sso_enabled` to hide the dead-end button entirely. Reverted qs-admin email to install-time `jim@quickstack.tech`.

---

## Where the work lives

### Local
- `/home/jim/quickstack-cc/tacticalrmm-web/` — checkout, on branch `qs/sso-callback-fix`, clean tree (commit `35ffd33`)
- `/home/jim/quickstack-cc/tacticalrmm-web/dist/` — build artifacts from today (Node 24). Not deployed.

### Remote
- `https://github.com/quickstack-cc/tacticalrmm-web` — fork
  - Branch `qs/sso-callback-fix` — pushed (commit `35ffd33`)
  - Branch tracking: `origin/qs/sso-callback-fix`
  - Upstream remote in local clone: `https://github.com/amidaware/tacticalrmm-web.git` (added as `upstream`)

### VM (rmm.us-central1-a)
- `/var/www/rmm/dist/` — original Jan 14 production build (rolled back)
- `/var/www/rmm/dist.pre-sso-patch-20260507-1631/` — copy of original; redundant safety
- GCE snapshot `rmm-pre-sso-20260507-0947` — whole-disk pre-session snapshot

### GCP `qsrmm-494222`
- OAuth 2.0 Client ID `QSRMM TRMM SSO`, client_id starts `536483581971-t3td9782...`, secret in TRMM SocialApp DB row
- Authorized redirect: `https://api.quickstack.cc/accounts/oidc/google/login/callback/`
- Authorized JS origins: `https://rmm.quickstack.cc`
- OAuth consent screen: Internal, restricted to `quickstack.cc` Workspace

---

## Next-session plan

### Path A (recommended) — Resolve the build regression and deploy

Goal: get SSO actually working with the patched fork.

```bash
# 1. Switch to Node 20 locally
nvm install 20
nvm use 20
node -v   # should print v20.x

# 2. Clean rebuild
cd /home/jim/quickstack-cc/tacticalrmm-web
git status   # should be clean on qs/sso-callback-fix
rm -rf node_modules dist
npm ci
npx quasar build
# Build should succeed with same CSS warnings; dist/ populated

# 3. Sanity check the build before deploying
grep -lE "_allauth/browser/v1" dist/*.js                    # expect at least 1
grep -lE "path:\"/account/provider/callback\"" dist/*.js    # expect at least 1
grep -lE "ssoproviders/token" dist/*.js                     # expect at least 1

# 4. Deploy via IAP
cd /home/jim/quickstack-cc/tacticalrmm-web/dist
tar -czf /tmp/qs-dist.tar.gz .
gcloud compute scp --tunnel-through-iap /tmp/qs-dist.tar.gz \
  rmm:/tmp/qs-dist.tar.gz --zone=us-central1-a

# 5. On the VM (via gcloud compute ssh ... --tunnel-through-iap):
sudo cp -a /var/www/rmm/dist /var/www/rmm/dist.pre-sso-patch-$(date +%Y%m%d-%H%M)
sudo mkdir -p /tmp/qs-dist-new
sudo tar -xzf /tmp/qs-dist.tar.gz -C /tmp/qs-dist-new
sudo rsync -a --delete /tmp/qs-dist-new/ /var/www/rmm/dist/
sudo chown -R www-data:www-data /var/www/rmm/dist
sudo rm -rf /tmp/qs-dist-new /tmp/qs-dist.tar.gz

# 6. Re-enable SSO in TRMM via Django shell:
sudo -u jim_quickstack_cc /rmm/api/env/bin/python /rmm/api/tacticalrmm/manage.py shell -c \
  "from core.utils import get_core_settings; cs=get_core_settings(); cs.sso_enabled=True; cs.save(); print('sso_enabled:', cs.sso_enabled)"

# 7. Restart rmm.service to clear any stale uwsgi state
sudo systemctl restart rmm.service daphne.service

# 8. Test in private window: rmm.quickstack.cc → Sign in with Google → Dashboard
```

If Node 20 build also reproduces the LoginView TypeError, fall through to:

- Try Node 18 (was the LTS when 0.101.59 was tagged) via `nvm install 18; nvm use 18`
- Compare `package-lock.json` against the lock that produced the Jan 14 build (no easy way to recover this — the original build likely happened in CI on amidaware's infrastructure)
- Use a docker container with a known node:18 image and rebuild there
- Bisect: rebuild from `v0.101.59` tag with NO patches and confirm whether THAT also breaks the LoginView (would prove the build env is at fault, not our patch)

### Path B (fallback) — Pivot to GCP IAP

If the build path stays elusive, IAP gives the same security outcome (Workspace identity check + hardware key enforced at Google before traffic reaches Django) without touching the TRMM frontend.

```bash
# Rough sketch — needs full design before executing
# 1. Configure OAuth consent + IAP-protected resource
gcloud iap web set-iam-policy ...   # binds quickstack.cc users to TRMM admin paths

# 2. nginx → IAP integration:
# Front rmm.quickstack.cc and api.quickstack.cc/admin paths with IAP
# Exclude agent endpoints (/api/v3/, /api/v4/, NATS port 4222) — these are
# agent-token-authenticated and cannot go through IAP user-identity check
```

This is a half-day of work in its own right; the design needs careful path partitioning so agent traffic isn't gated.

### Path C (long tail) — File upstream issue + PR

Independent of A or B, file an issue at `amidaware/tacticalrmm-web` referencing commit `75a9ef88` with the diff that got deleted. Maybe submit our patch as a PR. If upstream merges it, we drop the fork in a future WEB_VERSION bump. Low priority.

---

## Decisions for next session

1. **Build environment baseline:** which Node version to standardize on for our fork? My intuition is to match upstream's `.nvmrc` if they have one, or default to Node 20 LTS otherwise. Worth pinning so we don't re-discover this on every rebuild.
2. **Deploy automation:** today's deploy was hand-rolled (tar + scp + rsync). For ongoing maintenance, this should be a script in `nanormm/deploy/` or a small make target in the fork. Not blocking.
3. **Whether to file the upstream issue:** Path C. Cheap to do and helps the community.
4. **YubiKey order:** still pending. Phase 1 of admin-hardening.md says hardware MFA is the first thing for TMGW canary. Even with SSO landed, no YubiKey = no real phishing resistance (only Google TOTP, better than pyotp but not phishing-resistant).
5. **Password manager adoption:** also Phase 1. Currently no vault for the Google OIDC client secret (it lives only in TRMM's SocialApp DB and Google's secret-once-shown). Worth fixing before next operator-visible secret.

---

## Counts

- 1 fork created (`quickstack-cc/tacticalrmm-web`)
- 1 commit pushed (`35ffd33` to `qs/sso-callback-fix`)
- 2 files patched, 23 lines added, 6 lines removed
- 1 OAuth client live in GCP (kept)
- 1 SocialApp row in TRMM (kept; sso_enabled=False)
- 1 GCE snapshot retained (`rmm-pre-sso-20260507-0947`)
- 1 dist backup retained on VM
- 0 deploy attempts that worked end-to-end
- ~5 hours elapsed
