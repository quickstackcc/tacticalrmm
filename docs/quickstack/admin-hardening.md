# QSRMM Admin Hardening Guide

A working checklist for keeping the QSRMM administrative posture defensible. Two distinct surfaces:

1. **Admin workstation** — currently Jim's Zorin Linux daily driver. Used for everything (browsing, dev, AI tooling, Claude Code, etc.) *and* QSRMM admin. This is the highest-probability compromise surface.
2. **QSRMM server** — the GCE VM running TacticalRMM. Smaller attack surface but catastrophic blast radius if compromised.

Risk model: see the reasoning behind each item in the brainstorming session in chat history; the short version is that ~5–10% over 5 years is the realistic compromise probability for a diligent operator, dominated by (a) supply chain in dev tooling and (b) admin-credential pivot from daily driver to RMM. This checklist closes ~80% of that gap.

---

## Phase 1 — Quick wins (this week)

The 80% pile. Half a day of work, ~$50 in YubiKeys, no hardware purchases otherwise.

### Hardware MFA

- [x] Buy YubiKeys — decided 2026-06-24 (2× 5C NFC), **arrived 2026-07-15 as 3 keys: 5C Nano, 5C NFC, 5 NFC (USB-A)**. Placement: Nano stays in the Zorin daily driver's USB-C port; 5 NFC (USB-A) on keyring (NFC covers phone taps, USB-A covers any loaner machine); 5C NFC offsite/fire-safe backup. **Buy direct from yubico.com or Amazon "sold by / ships from Yubico"** — a third-party marketplace seller is a supply-chain risk on a root-of-trust device (exactly the threat Phase 1 guards against).
- [x] Enroll the keys on (all done 2026-07-15 except the PayPal follow-up below):
  - [x] Google account (covers Gmail, GCP console, Workspace) — **the load-bearing one**; TRMM phishing resistance is inherited from here via SSO. *Done 2026-07-15: all 3 keys enrolled (`qsrmm-nano`/`qsrmm-keyring`/`qsrmm-backup`), backup codes printed to offline bundle, all 3 keys tap-tested from incognito.*
  - [x] GitHub account + `quickstack-cc` org enforcement — *done 2026-07-15: all 3 keys registered as security keys (TOTP app stays as required primary — see corrected note below), recovery codes printed to bundle, tap-tested in private window, org `two_factor_requirement_enabled=true` (API-verified; sole member, no outside collaborators).*
  - [x] Slack workspace (admin account) — **corrected 2026-07-15: Slack has no native security-key/WebAuthn support (TOTP/SMS only)**. Phishing resistance comes from signing in with Google (inherits the hardware keys, same pattern as TRMM SSO). *Verified 2026-07-15: no standalone Slack password on the account — Google sign-in is the only door.*
  - [x] AmidaWare sponsorship account — **corrected 2026-07-15: sponsorship is paid via PayPal, not GitHub Sponsors** (doc previously assumed Sponsors). No separate AmidaWare portal password. Compromise impact = sponsorship lapse (EE features / signed installers), not RMM access.
  - [ ] PayPal (sponsorship billing) — supports passkeys natively; enroll the keys when convenient. Until then it rides on password + its own 2FA, with account recovery chaining to the key-gated Gmail.
  - N/A **Password manager** — Quick Stack runs no vault (Bitwarden / 1Password not adopted; see memory `qsrmm-has-no-password-manager`). Recovery story instead = the **backup YubiKey** (offsite) + **printed Google backup codes** stored offline with it. Do not stash recovery codes or passphrases in a vault that doesn't exist.
  - [x] TRMM admin user — **superseded by SSO via Google Workspace**, see "TRMM SSO + break-glass model" below. Native TRMM 2FA is bare `pyotp` TOTP (no WebAuthn / FIDO2 / U2F) and only protects the local `qs-admin` break-glass account. Phishing resistance now comes from the Google account's hardware key, enforced upstream of TRMM via Workspace SSO.
- [x] Disable SMS as a fallback factor everywhere it's offered. SMS gets SIM-swapped. *Google: verified 2026-07-15 — no recovery phone was ever set. Remaining factors: 3 YubiKeys + Pixel device-bound passkey (fingerprint-gated, same phishing-resistant class) + printed backup codes.*
- [x] Disable TOTP-only fallback on accounts that support hardware-only mode. *Google: done 2026-07-15 via Workspace security-key-only enforcement. GitHub: not possible — see correction below.* For the Google identity, do this via **Workspace admin enforcement** (security-key-only for the OU), *not* consumer Advanced Protection — cleaner for a Workspace identity and can't be silently downgraded. See the runbook below. **Corrected 2026-07-15:** GitHub does *not* support hardware-only 2FA — TOTP or SMS must remain as the primary method; security keys/passkeys are supplementary (per GitHub docs). Residual: a password+TOTP phishable path stays open by design; keys + passkey make the default sign-in phishing-resistant, and the TOTP secret sits in Google Authenticator behind the now key-gated Google account.
- [x] Verify: try logging into each account from a private window — must require physical key tap. *Done 2026-07-15: Google (all 3 keys, under enforcement, audit-log-confirmed), GitHub (tap-tested), TRMM (SSO → tap → dashboard). Slack inherits the Google check.*

#### Enrollment runbook — lockout-safe sequence (decided 2026-06-24)

**Decision record:** hardware = 2× YubiKey 5C NFC; enforcement = **Workspace-enforced security-key-only** (not Google Advanced Protection). **Why the order matters:** SSO is already live (since 2026-05-07), but until a hardware key sits on `jim@quickstack.cc` the "phishing-resistant TRMM admin" claim is only plumbing — the SSO chain is only as strong as the Google account's weakest factor (currently password + TOTP).

Cardinal rule: **enroll both keys + print recovery codes BEFORE enforcing anything**, and keep the TRMM break-glass (`qs-admin` local + TOTP, `block_local_user_logon=False`) open the whole time.

**Status 2026-07-15: steps A–C complete.** All 3 keys enrolled and tap-tested under Workspace security-key-only enforcement (audit log shows `Challenge type: Security key, Is second factor: True`); backup codes + recovery toggle printed to offline bundle; SSO → tap → TRMM dashboard verified; `block_local_user_logon` flipped to `True`. **Step D also complete same day:** GitHub keys + org 2FA enforced (API-verified), Slack confirmed Google-sign-in-only, sponsorship confirmed PayPal (keys-on-PayPal left as the one follow-up). Runbook done.

**Precondition verified 2026-07-15:** `qs-admin` break-glass login tested end-to-end (local password + TOTP, private window) and the password is written into the offline bundle. VM state confirmed same day: `sso_enabled=True`, `block_local_user_logon=False`. Note the VM has no `tactical` service user — TRMM runs as `jim_quickstack_cc`, so `manage.py` commands run plainly from that account with `/rmm/api/env/bin/python /rmm/api/tacticalrmm/manage.py …` (no `sudo -u tactical`).

**A — Enroll (do NOT enforce yet)**
1. Google `jim@quickstack.cc` → Security → 2-Step Verification → add both keys, named `qsrmm-primary` / `qsrmm-backup`.
2. Generate Google backup codes → print, store offline with the backup key.
3. Smoke test: private window → Google sign-in → must tap.

**B — Enforce (Workspace admin console)**
4. Admin → Security → Authentication → 2-Step Verification → Enforcement **On**, allowed methods = **Only security key** for the OU (the whole org — it's just Jim).
5. Disable SMS recovery/2SV; confirm no TOTP fallback remains.
6. Re-test in a fresh private window. NB: this gates *every* Google login — Gmail, GCP console, `gcloud auth login` all demand a tap. Service-account automation is unaffected (SA keys, not your identity).

**C — Realize the TRMM benefit + flip force-SSO**
7. Fresh private window → `rmm.quickstack.cc` → Sign in with Google → tap → Dashboard. **This is the moment phishing resistance becomes real.**
8. Print the recovery toggle (the `block_local_user_logon=False` shell command in "TRMM SSO + break-glass model" below) and store it offline.
9. Flip `core_settings.block_local_user_logon=True`. `qs-admin` local + TOTP remains usable at any time — Django superusers are exempt from the block (see corrected note in "TRMM SSO + break-glass model"); the flag rejects local login server-side for everyone else. The login form stays visible in the UI regardless (upstream never wired form-hiding).

**D — Spread to the other accounts**
10. GitHub: enroll both keys → then enable org-level 2FA-required on `quickstack-cc` **only after** confirming no human member / machine-user gets locked out (GitHub App bots are exempt, so the nanoclaw/Slack app side is fine — do a 1-minute member check first).
11. Slack workspace admin: use **Sign in with Google** (Slack has no native security-key support — corrected 2026-07-15). Confirm no standalone Slack password remains on the account.
12. AmidaWare sponsorship — **corrected 2026-07-15: paid via PayPal, not GitHub Sponsors.** No separate AmidaWare portal login. Follow-up: enroll the keys as PayPal passkeys (supported natively).

### Browser separation

- [x] Create a dedicated browser profile named **QSRMM Admin** — a **full separate profile** (Chrome profile or Firefox `about:profiles`). **Not a Firefox container** (corrected 2026-07-15: extensions run across all containers, so containers don't isolate the extension threat). *Done 2026-07-15: Chrome "Profile 13" (QSRMM), signed into jim@quickstack.cc, launcher `~/.local/share/applications/chrome-qsrmm.desktop` matching the per-client shortcut convention.*
- [x] In that profile, install **zero extensions** (corrected 2026-07-15: the password-manager allowance is moot — no vault exists). No ad-blocker, no productivity tools, no AI assistants. *Verified 2026-07-15: zero user-installed extensions (only Chrome's stock Docs Offline + Web Store Payments components).*
- [ ] Use this profile *only* for: TRMM web UI, GCP console (when admin'ing the QSRMM project), Workspace admin console, PayPal (sponsorship billing), and Mesh admin. (Ongoing habit.)
- [ ] Never log into Google personal, Slack personal, social media, or random sites in this profile. (Ongoing habit.)
- [x] Verify: extensions list in the QSRMM Admin profile shows zero user-installed extensions. *Verified 2026-07-15.*

### SSH key separation

**Rewritten 2026-07-15 to match reality:** VM access is `gcloud compute ssh` over IAP with **OS Login on** — there are no `authorized_keys` files in play (verified zero entries on the VM), keys are authorized via the YubiKey-gated Google identity, and port 22 accepts only Google's IAP range (`35.235.240.0/20`). A dedicated per-host key adds nothing under OS Login (authorization is IAM, not key placement). The laptop-resident credentials that actually matter are the unencrypted `~/.ssh/google_compute_engine` key and the cached gcloud OAuth refresh token in `~/.config/gcloud`.

- [x] Prune stale OS Login keys — *done 2026-07-15: removed leftover Windows-era key (`5601b79e…`); sole remaining key is the current Zorin one (`7e49da79…`).*
- [x] Passphrase the local keys in place (passphrases → offline bundle; no vault exists) — *done 2026-07-15, verified both keys reject empty passphrase.* Caveat: GNOME keyring caches the passphrase per login session — the win is at-rest encryption of the key file.
- [x] **Workspace Google Cloud session control** (admin.google.com → Security → Access and data control) — *done 2026-07-15: Google's 2023 default had already capped sessions at 16h (explains the daily gcloud reauth); reauthentication method set to Security key, so the daily refresh is a tap. Caps the stolen-refresh-token window at ≤16h. Service accounts unaffected.*

### Audit installed dev/admin tooling

The single biggest residual risk vector. Inventory and prune.

- [ ] List Claude Code plugins / skills / MCP servers currently configured. Remove any unused.
- [ ] List VS Code (or Cursor / your editor) extensions. Remove any not actively used. Pay special attention to extensions with permission to read all files or run commands.
- [ ] List globally-installed npm packages (`npm ls -g --depth=0`). Remove unused.
- [ ] List globally-installed pip packages (`pip list --user`). Remove unused.
- [ ] List Snap and Flatpak apps (`snap list`, `flatpak list`). Remove unused.
- [ ] Going forward: every new MCP server / Claude plugin / editor extension is a trust event. Read the source or skip it.

### Server-side: lock down SSH on QSRMM VM

- [x] In `/etc/ssh/sshd_config`: `PasswordAuthentication no`, `PermitRootLogin no`, `KbdInteractiveAuthentication no` — *verified live via `sshd -T` 2026-07-15.*
- [x] Confirm GCP firewall rule for SSH (port 22) restricts sources — *verified 2026-07-15: rule `rmm-ssh-iap` allows 22 only from IAP range `35.235.240.0/20`; no public SSH exposure. OS Login enabled.*

### Server-side: TRMM admin hardening

- [ ] Audit TRMM admin user count: only Jim should have superuser. Demote or delete any others.
- [ ] Confirm hardware-backed TOTP enforced on all TRMM admin accounts.
- [ ] Audit existing Knox tokens (`SELECT * FROM knox_authtoken;` via the Django admin or psql). Revoke any orphaned ones.
- [x] MeshCentral admin: hardware-MFA-backed, separate from TRMM admin password. *Done 2026-07-15: site-admin `zuxpotqj` password reset (→ offline bundle), all 3 YubiKeys enrolled via Mesh's native WebAuthn + backup codes printed, and `force2factor: true` set on the domain (config backup at `meshcentral-data/config.json.bak-20260715`) — no password-only login path remains on Mesh, including TRMM's auto-provisioned users. TRMM Take Control verified working after (login tokens bypass the login page, so 2FA doesn't touch the integration). Existing guards confirmed: geo-block covers the mesh vhost, brute-force cooloff 5/5min→30min, `newAccounts: false`.*

### TRMM SSO + break-glass model (configured 2026-05-07)

Phishing-resistant TRMM admin via Google Workspace SSO. Inherits hardware-key enforcement from the Google account, side-steps TRMM's TOTP-only 2FA limitation.

**Caveat — fork dependency:** Upstream `tacticalrmm-web` v0.101.59 ships SSO that is half-deleted (commit `75a9ef88` removed the post-callback Vue route + auth-store glue without re-implementing it; ~14 months in upstream develop). We run a forked frontend at `quickstack-cc/tacticalrmm-web` branch `qs/sso-callback-fix` (commit `35ffd33`). When upstream restores the missing wiring or merges our patch, drop the fork. See `docs/superpowers/handoffs/2026-05-07-sso-cutover.md` for the full diagnostic record.

**Caveat — deploy procedure:** the VM hosts a runtime-config file at `/var/www/rmm/dist/env-config.js` that the fork's `public/` does not ship. Any `rsync` deploy of `dist/` MUST `--exclude=env-config.js`, or `window._env_` goes undefined and the entire frontend breaks with a confusing `TypeError`. Memory note `feedback_qsrmm_trmm_web_deploy.md` carries the rule.

**Identity model in production (verified 2026-05-07):**

| User | Email | Django superuser | TRMM role | SocialAccount | Use |
|---|---|---|---|---|---|
| `qs-admin` | `jim@quickstack.tech` | True | — | (none) | **Break-glass only**, local username/password + TOTP |
| `jim` | `jim@quickstack.cc` | False | `Administrator` (role-level superuser) | google `113485…` | Daily admin via Google SSO |

- [x] SSO enabled (`core_settings.sso_enabled=True`)
- [x] Break-glass user has no SocialAccount link (Google compromise can't escalate via the local superuser)
- [x] Daily SSO user has no Django `is_superuser` (limits Django-admin blast radius if SSO identity is compromised; full TRMM perms still granted via Role)
- [x] **Decide and act:** flipped to `block_local_user_logon=True` on 2026-07-15, after YubiKey enrollment + Workspace security-key-only enforcement + SSO tap-to-dashboard test all verified. Recovery toggle printed to offline bundle first.
- **Corrected 2026-07-15 (code-verified):** the flag does **not** seal out `qs-admin`. Both login endpoints exempt Django superusers (`accounts/views.py:79` and `:108` — `if not user.is_superuser and core_settings.block_local_user_logon`), so `qs-admin` + password + TOTP keeps working at the API even with the flag `True`. What the flag does: rejects *non-superuser* local logins server-side ("Bad credentials"). **It does not hide the login form** — verified 2026-07-15 against both our fork and upstream develop: `LoginView.vue` never consults the flag, and no pre-auth endpoint exposes it, so the form always renders. Net effect: the visible form is a working door only for `qs-admin` (break-glass via UI needs no shell access). The VM-shell toggle below only matters if `qs-admin` ever loses its superuser bit.
- [ ] Once flipped to `True`, keep the recovery toggle printed offline (full VM form, since there is no `tactical` user): `/rmm/api/env/bin/python /rmm/api/tacticalrmm/manage.py shell -c "from core.utils import get_core_settings; cs=get_core_settings(); cs.block_local_user_logon=False; cs.save()"`. `CoreSettings.save()` busts the settings cache (`core/models.py:127`), so it takes effect immediately, no service restart.

---

## Phase 2 — Ongoing habits (codify, don't just intend)

These are behavioral, not one-time. Worth writing into a personal weekly/monthly checklist.

### Patch discipline (the load-bearing habit)

- [ ] Subscribe to:
  - GitHub watch on `amidaware/tacticalrmm` releases (Releases only, not all activity)
  - GitHub watch on `Ylianst/MeshCentral` releases
  - Django security advisories: <https://docs.djangoproject.com/en/stable/internals/security/>
  - oss-security mailing list (high signal-to-noise for serious CVEs): <https://oss-security.openwall.org/>
- [ ] Commit to **patching critical CVEs within 48h** of disclosure. The Kaseya/ConnectWise mass-exploit window was hours-to-days; 48h is your shield.
- [ ] Pin TRMM to **stable release tags**, not the `develop` branch, before customer #1 (currently on develop per repo state).
- [ ] Snapshot the VM before every patch deploy. GCE disk snapshots are cheap.

### "No `curl | sh`" rule

- [ ] When you need to run an installer script, download it first, eyeball it, then execute:
  ```bash
  curl -O https://example.com/install.sh
  less install.sh    # actually read it
  bash install.sh
  ```
- [ ] If the script is too long to read meaningfully, that's its own red flag.

### Project-scoped dependencies

- [ ] No `npm install -g` for project deps. Use project-local `node_modules`.
- [ ] No `pip install` outside venvs / `uv` projects.
- [ ] Treat `apt`/`brew`/Snap as the trust boundary for system tools; everything else lives in project sandboxes.

### Quarterly access review

- [ ] First Monday of each quarter, audit:
  - TRMM admin users
  - GCP `qsrmm-494222` IAM bindings
  - GitHub `quickstack-cc` org members
  - Slack workspace admins
  - AmidaWare account access (if any team members get added later)
- [ ] Anyone who shouldn't have access anymore — remove, don't just demote.

---

## Phase 3 — Pre-customer #1 (before signing the first paying customer)

Don't take customer money without these in place. Patterns set with customer #1 are hard to renegotiate by customer #4.

> **Prerequisite:** Phase 1 hardware-MFA + browser separation + SSH key separation must be complete before any Phase 3 item is sufficient on its own. The risk model in the brainstorm puts admin-credential pivot from the daily driver as the dominant compromise vector — Phase 3 controls (insurance, MSA, tripwires, log streaming) constrain blast radius *after* a compromise; Phase 1 reduces the probability of one. Both are needed.
>
> Also from Phase 2 and binding here: TRMM must be pinned to a **stable upstream release tag** (not `develop`) before any customer agent install. Snapshot the VM before the pin-and-redeploy.

### Legal & insurance

- [ ] Bind E&O / cyber liability insurance. ~$1–2K/yr for a small MSP. Coverage should include breach response (forensics + lawyer + customer notification).
- [ ] MSA template with:
  - Limit-of-liability clause (capped at fees paid in last 12 months is standard)
  - Mutual indemnification
  - EDR-required clause (customer must run Defender for Business / CrowdStrike / S1 / similar on every endpoint, at their cost)
  - Off-RMM backup clause (customer's backup system must not have credentials accessible from QSRMM)
  - Incident notification SLA (you commit to notifying customer within X hours of suspected compromise)
- [ ] Have a lawyer review the template once. Reuse for all customers.

### Operational readiness

- [ ] Status page provisioned (Atlassian Statuspage / Instatus / self-host).
- [ ] Pre-written customer comms templates for:
  - Incident detected
  - Incident update (interim status)
  - Incident postmortem (post-resolution)
- [ ] Out-of-band comms plan: customer phone numbers stored *outside* QSRMM, accessible if QSRMM/Slack are unavailable. Treat Signal as the OOB channel for incidents — don't use Slack to report a Slack-or-RMM compromise.

### Tripwire alerts

Wire these up before customer #1. Each one is cheap to implement and would catch a compromise within minutes.

- [ ] Alert on bulk script execution (> 3 agents in < 60 seconds)
- [ ] Alert on TRMM script library modification (any add/edit/delete)
- [ ] Alert on new TRMM admin user creation
- [ ] Alert on TRMM admin login from new IP (or any non-US IP, given the geo-block — that should be a *security incident* alert, not just info)
- [ ] Alert on MeshCentral session opened off-hours (define "off-hours" — likely 10pm–6am local)
- [ ] Alert on agent isolation actions (someone using QSRMM to cut endpoints off the network)
- [ ] Send all alerts to a dedicated Slack channel + email + push notification. Three-channel redundancy.

### Logging

- [ ] Stream Django audit logs, nginx access logs, MeshCentral logs, and sshd auth logs to GCS via Cloud Logging.
- [ ] GCS bucket has retention lock / object retention configured (memory says immutable backups already exist — confirm log sink uses same posture).
- [ ] Verify: deleting a log on the VM does not delete it from GCS.

### Recovery drill

- [ ] Perform at least one full restore drill from GCS backups to a fresh VM. Document the time-to-recover.
- [ ] If TTR > 4 hours, fix the bottleneck before customer #1.

---

## Phase 4 — Pre-scale (customer #4 OR ~100 devices, whichever comes first)

When the side hustle stops being a side hustle.

- [ ] **Dedicated admin VM** — a separate Linux VM (local or GCE) that you boot *only* for QSRMM admin work. Snapshot/revert workflow. No browsing, no AI tooling, no dev work. Drops joint-compromise probability ~10×.
- [ ] **Cloud SQL migration playbook written** (not executed yet — written so it can be executed in a weekend if Postgres becomes the bottleneck).
- [ ] **Deputy arrangement** — a contractor or trusted peer who can take incident calls during your PTO / outages. Define escalation path: how do they get TRMM access (break-glass account with sealed credentials), what authority do they have, how is it revoked.
- [ ] **Vacation runbook** — for each automated tier-1 action class, document: can it wait 24h? If not, who relays the approval? How is auth revoked when you return?
- [ ] **Annual security review** — even informal. External eyes once a year. Could be a friend in security, could be a paid pentest.

---

## Reference — Incident response readiness

When you suspect compromise, panic costs time. Have this ready.

- [ ] Printed runbook for "QSRMM compromise suspected" stored offline (not on Zorin, not in Drive). One page, decision tree.
- [ ] Insurance carrier contact + policy number — printed.
- [ ] Cyber-incident lawyer contact (state varies; specialty matters more than firm size) — printed.
- [ ] Customer notification list with phone numbers — exported quarterly to printed copy.
- [ ] First moves on suspected compromise:
  1. Don't power off the VM — preserve memory state for forensics.
  2. Snapshot the disk (forensic copy).
  3. Cut external network access (firewall rule to deny all egress except your admin IP).
  4. Revoke all Knox tokens, force re-auth.
  5. Notify insurance carrier within their SLA window (usually 24–72h).
  6. Notify customers per MSA.
  7. Engage forensics (insurance carrier usually has a panel).

---

## What this checklist does NOT cover (intentionally)

- **Application allow-listing on customer endpoints** — not realistic for SMB; rely on EDR instead.
- **Air-gapped admin workstation** — overkill at the side-hustle scale; revisit at >10 customers.
- **SOC 2 / HIPAA formal compliance** — only relevant once a customer demands it and pays for it.
- **Bug bounty program** — meaningful at >$1M ARR scale, not now.
- **24/7 SOC monitoring** — not viable as a single operator. Tripwire alerts + your phone are the proxy until scale justifies a SOC contract.

---

## Tracking

When an item in Phase 1–3 is completed, check the box in this file and commit. The checked state of this file is the source of truth for QSRMM admin posture. Recheck quarterly that completed items haven't drifted (e.g., new browser extensions snuck in, new TRMM admin user got added).
