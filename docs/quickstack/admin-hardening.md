# QSRMM Admin Hardening Guide

A working checklist for keeping the QSRMM administrative posture defensible. Two distinct surfaces:

1. **Admin workstation** — currently Jim's Zorin Linux daily driver. Used for everything (browsing, dev, AI tooling, Claude Code, etc.) *and* QSRMM admin. This is the highest-probability compromise surface.
2. **QSRMM server** — the GCE VM running TacticalRMM. Smaller attack surface but catastrophic blast radius if compromised.

Risk model: see the reasoning behind each item in the brainstorming session in chat history; the short version is that ~5–10% over 5 years is the realistic compromise probability for a diligent operator, dominated by (a) supply chain in dev tooling and (b) admin-credential pivot from daily driver to RMM. This checklist closes ~80% of that gap.

---

## Phase 1 — Quick wins (this week)

The 80% pile. Half a day of work, ~$50 in YubiKeys, no hardware purchases otherwise.

### Hardware MFA

- [ ] Buy two YubiKeys (primary + backup, store backup offsite or in a fire safe). YubiKey 5 NFC or 5C NFC depending on your ports.
- [ ] Enroll both keys on:
  - [ ] Google account (covers Gmail, GCP console, Workspace)
  - [ ] GitHub account + `quickstack-cc` org enforcement
  - [ ] Slack workspace (admin account)
  - [ ] AmidaWare sponsorship account
  - [ ] Password manager (Bitwarden / 1Password) as second factor on the vault itself
  - [ ] TRMM admin user (TRMM 2FA is bare `pyotp` TOTP — no WebAuthn / FIDO2 / U2F. Provision the TOTP seed into the YubiKey via the Yubico Authenticator app, *not* a phone-based authenticator. This gives seed protection but **not** phishing resistance — see the QSRMM Admin browser profile below for the phishing-side mitigation.)
- [ ] Disable SMS as a fallback factor everywhere it's offered. SMS gets SIM-swapped.
- [ ] Disable TOTP-only fallback on accounts that support hardware-only mode (GitHub does; Google does via Advanced Protection).
- [ ] Verify: try logging into each account from a private window — must require physical key tap.

### Browser separation

- [ ] Create a dedicated browser profile (Firefox container / Chrome profile) named **QSRMM Admin**.
- [ ] In that profile, install only:
  - Password manager extension
  - Nothing else. No ad-blocker, no productivity tools, no AI assistants.
- [ ] Use this profile *only* for: TRMM web UI, GCP console (when admin'ing the QSRMM project), AmidaWare sponsor portal, and Mesh admin.
- [ ] Never log into Google personal, Slack personal, social media, or random sites in this profile.
- [ ] Verify: extensions list in the QSRMM Admin profile shows ≤ 1 extension.

### SSH key separation

- [ ] Generate a dedicated key for QSRMM server access:
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/qsrmm_ed25519 -C "qsrmm-admin-$(hostname)"
  ```
  Set a strong passphrase (≥ 6 random words, store in password manager).
- [ ] Configure ssh-agent to forget this key after 4 hours: `ssh-add -t 14400 ~/.ssh/qsrmm_ed25519`.
- [ ] Add only this key to the QSRMM VM's `~/.ssh/authorized_keys`. Remove any other keys that crept in during install.
- [ ] Add a `~/.ssh/config` block scoping the key to the QSRMM VM only — other hosts must not be able to use it.
- [ ] Verify: `ssh -i ~/.ssh/qsrmm_ed25519 qsrmm-vm` works; daily-driver default key (`id_ed25519`) does *not*.

### Audit installed dev/admin tooling

The single biggest residual risk vector. Inventory and prune.

- [ ] List Claude Code plugins / skills / MCP servers currently configured. Remove any unused.
- [ ] List VS Code (or Cursor / your editor) extensions. Remove any not actively used. Pay special attention to extensions with permission to read all files or run commands.
- [ ] List globally-installed npm packages (`npm ls -g --depth=0`). Remove unused.
- [ ] List globally-installed pip packages (`pip list --user`). Remove unused.
- [ ] List Snap and Flatpak apps (`snap list`, `flatpak list`). Remove unused.
- [ ] Going forward: every new MCP server / Claude plugin / editor extension is a trust event. Read the source or skip it.

### Server-side: lock down SSH on QSRMM VM

- [ ] In `/etc/ssh/sshd_config`:
  - `PasswordAuthentication no`
  - `PermitRootLogin no`
  - `ChallengeResponseAuthentication no`
  - `KbdInteractiveAuthentication no`
- [ ] Reload sshd; verify with `ssh -o PreferredAuthentications=password user@vm` — must reject.
- [ ] Confirm GCP firewall rule for SSH (port 22) restricts source IPs to your admin workstation IP, or move SSH access behind GCP IAP (recommended — `gcloud compute ssh` over IAP works without exposing 22).

### Server-side: TRMM admin hardening

- [ ] Audit TRMM admin user count: only Jim should have superuser. Demote or delete any others.
- [ ] Confirm hardware-backed TOTP enforced on all TRMM admin accounts.
- [ ] Audit existing Knox tokens (`SELECT * FROM knox_authtoken;` via the Django admin or psql). Revoke any orphaned ones.
- [ ] MeshCentral admin: hardware-MFA-backed, separate from TRMM admin password.

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
