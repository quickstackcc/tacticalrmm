# GCP deployment runbook — Quick Stack

Target: single `e2-standard-2` GCE VM running Debian 12, installed via upstream `install.sh`. Audience: internal fleet replacing Atera (Windows updates + remote support).

## Fixed values for this deployment

| | Value |
|---|---|
| GCP project | `qsrmm` |
| GCP region / zone | `us-central1` / `us-central1-a` (change if you prefer closer to ops location) |
| Root domain | `quickstack.cc` |
| Frontend (UI) | `rmm.quickstack.cc` |
| Backend (API) | `api.quickstack.cc` |
| MeshCentral | `mesh.quickstack.cc` |
| Let's Encrypt / admin email | `jim@quickstack.cc` |
| Django superuser | `qs-admin` (you'll set the password interactively) |

All three subdomains resolve to the **same** VM IP. The wildcard TLS cert (`*.quickstack.cc`) covers them.

## DNS ordering

`quickstack.cc` DNS lives at **Namecheap** (Domain List → Manage → Advanced DNS). Don't create the A records yet — you need the static IP first (next step). Plan the ACME wildcard challenge too: during install, certbot will tell you to add a `_acme-challenge.quickstack.cc` TXT record, and you'll add it in the same Advanced DNS UI.

**Lower the TTL before install.** Namecheap defaults to 1800s (30 min); the ACME manual DNS challenge polls and will time out waiting for a stale cache. In Namecheap Advanced DNS, set TTLs to `1 min` for the records you're about to add. You can raise them back after install succeeds.

Using the flat shape (`*.quickstack.cc` wildcard, subdomains as siblings) — decided because `quickstack.cc` currently has no non-RMM records to worry about.

## Step 1 — Static IP + VM

```bash
# Set context
gcloud config set project qsrmm
gcloud config set compute/region us-central1
gcloud config set compute/zone us-central1-a

# Static external IP
gcloud compute addresses create rmm-ip --region us-central1
RMM_IP=$(gcloud compute addresses describe rmm-ip --region us-central1 --format='value(address)')
echo "$RMM_IP"   # point all three A records at this

# Firewall: 80/443 ingress from anywhere (Let's Encrypt + UI + agent HTTP)
# 4222 (NATS) ingress from agent networks — wide-open is fine if agents roam
gcloud compute firewall-rules create rmm-web \
  --direction=INGRESS --action=ALLOW --rules=tcp:80,tcp:443 \
  --source-ranges=0.0.0.0/0 --target-tags=rmm

gcloud compute firewall-rules create rmm-nats \
  --direction=INGRESS --action=ALLOW --rules=tcp:4222 \
  --source-ranges=0.0.0.0/0 --target-tags=rmm

# VM: Debian 12, 2 vCPU / 8 GB, 50 GB pd-balanced
gcloud compute instances create rmm \
  --machine-type=e2-standard-2 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB --boot-disk-type=pd-balanced \
  --address=rmm-ip \
  --tags=rmm \
  --metadata=enable-oslogin=TRUE
```

## Step 2 — DNS records (Namecheap)

Namecheap → Domain List → `quickstack.cc` → Manage → Advanced DNS → **Add New Record** (A Record, TTL 1 min):

| Host | Value |
|---|---|
| `api` | `$RMM_IP` |
| `rmm` | `$RMM_IP` |
| `mesh` | `$RMM_IP` |

Wait for propagation (`dig +short rmm.quickstack.cc` from your laptop should return the IP — usually 1–3 min with TTL=1, sometimes longer on first publication) before starting install. Install will fail at the cert step otherwise.

## Step 3 — SSH in and run upstream installer

```bash
gcloud compute ssh rmm

# As a non-root user with sudo (don't use root for install)
# If oslogin gave you a weird username, that's fine — just not root.
wget https://raw.githubusercontent.com/amidaware/tacticalrmm/master/install.sh
chmod +x install.sh
./install.sh
```

The installer is **interactive**. It will prompt for:

1. Backend subdomain → `api.quickstack.cc`
2. Frontend subdomain → `rmm.quickstack.cc`
3. MeshCentral subdomain → `mesh.quickstack.cc`
4. Root domain → `quickstack.cc`
5. Admin email → `jim@quickstack.cc`
6. Then certbot runs `certbot certonly --manual --preferred-challenges dns -d '*.quickstack.cc'` — it will pause and print a TXT record value to add. In Namecheap Advanced DNS, add a **TXT Record** with Host = `_acme-challenge` and Value = (what certbot printed), TTL 1 min. Wait ~60s (`dig +short TXT _acme-challenge.quickstack.cc` should show it), then press enter in the installer.
7. Later in the install it prompts for a Django superuser name → `qs-admin`, and you'll set a password.

Expect ~20–40 min for the full run. The script leaves a summary with the generated MeshCentral and TRMM user credentials — capture those.

### Which installer to run — upstream vs. fork

Run upstream's `install.sh` (hardcoded at `amidaware/tacticalrmm/master`). Don't run this fork's `install.sh` until you've either (a) rebranded and pushed, or (b) have a concrete reason to diverge. The installer also clones `amidaware/tacticalrmm.git` into `/rmm/` — it doesn't use this repo checkout at all. This repo only matters when you start developing modifications.

## Step 4 — Post-install verification

From your laptop:

```bash
# Should load the Vue frontend
curl -I https://rmm.quickstack.cc

# Should return a JSON API response or a redirect, not a TLS error
curl -I https://api.quickstack.cc

# Should load MeshCentral's login page
curl -I https://mesh.quickstack.cc
```

Then browse to `https://rmm.quickstack.cc` and log in as `qs-admin`.

## Step 5 — Backups

Two layers:

### Layer 1: GCE disk snapshots (fast restore)

```bash
gcloud compute resource-policies create snapshot-schedule rmm-daily \
  --region=us-central1 \
  --max-retention-days=14 \
  --start-time=07:00 \
  --daily-schedule \
  --on-source-disk-delete=apply-retention-policy

gcloud compute disks add-resource-policies rmm \
  --resource-policies=rmm-daily \
  --zone=us-central1-a
```

### Layer 2: App-level backup to GCS (portable across VM rebuilds)

TRMM ships `backup.sh` at the repo root which tars Postgres, MongoDB, MeshCentral data, certs, and `local_settings.py`. Cron it nightly and ship the tarball to a GCS bucket:

```bash
# From your laptop (not the VM) — one-time bucket creation
gcloud storage buckets create gs://qsrmm-backups \
  --location=us-central1 \
  --uniform-bucket-level-access

# Grant the VM's default compute service account write access to the bucket
VM_SA=$(gcloud compute instances describe rmm --format='value(serviceAccounts[0].email)')
gcloud storage buckets add-iam-policy-binding gs://qsrmm-backups \
  --member="serviceAccount:$VM_SA" \
  --role=roles/storage.objectCreator

# Then on the VM:
cat <<'EOF' | sudo tee /etc/cron.daily/rmm-backup
#!/bin/bash
set -e
OUT=/tmp/rmm-$(date +%Y%m%d).tar
/rmm/backup.sh --backup-path "$OUT"     # confirm path after install (install.sh clones repo to /rmm/)
gcloud storage cp "$OUT" gs://qsrmm-backups/
rm "$OUT"
EOF
sudo chmod +x /etc/cron.daily/rmm-backup
```

(Verify `backup.sh`'s actual flags after install — `/rmm/backup.sh --help` is the ground truth.)

## Step 6 — Ongoing

- **Upstream updates**: upstream ships an `update.sh` at the repo root. Run it on the VM periodically. It handles the Django migrations, asset rebuild, and service restarts.
- **Agent rollout**: admin UI generates per-client/per-site agent installers. Windows install is a `.exe`, Linux/Mac are shell installers. Each installer embeds the server URL and a one-time token — no cert pinning or hardcoding needed on endpoints.
- **Monitoring the RMM itself**: `journalctl -u tacticalrmm*` shows the Django/Celery/Daphne/NATS services. Optional: install Ops Agent on the VM and send logs/metrics to Cloud Logging.

## Known rough edges

- `install.sh` errors if `/rmm/api/tacticalrmm` already exists. If the install fails partway, clean the VM by deleting and recreating (cheap with terraform/gcloud) rather than trying to salvage.
- MeshCentral's websockets don't survive being fronted by an L7 LB that doesn't understand them. **Do not** put a GCP HTTPS load balancer in front of this VM. The VM's own nginx terminates TLS.
- The installer assumes the `_acme-challenge.quickstack.cc` TXT record you just added is visible via public DNS before you press enter. Namecheap can lag; if certbot fails, the installer has retry loops — wait longer, verify `dig +short TXT _acme-challenge.quickstack.cc` returns the value, then retry.
- Upstream only supports Debian 11/12 and Ubuntu 22.04. Don't try 24.04 or anything else — the script hard-errors on other releases.

## If things go wrong

Repo ships `troubleshoot_server.sh` at the root — run it on the VM and it produces a diagnostic bundle that upstream's Discord will actually look at. Before posting, sanitize it for any internal IPs/domains you don't want to share publicly.
