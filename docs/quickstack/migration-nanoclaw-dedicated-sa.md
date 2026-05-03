# Migrate nanoclaw to a dedicated GCP service account

## Why

During Plan 3 smoke, the nanoclaw orchestrator's spawned agent containers needed `aiplatform.endpoints.predict` to call Vertex AI. The fastest unblock was granting `roles/aiplatform.user` to the VM's **default Compute Engine service account** (`536483581971-compute@developer.gserviceaccount.com`).

That SA is shared by every process running on the VM. For a single-purpose RMM box this is acceptable, but the cleaner posture is a dedicated SA with the role bound only there, attached to the VM so nanoclaw and only nanoclaw can call Vertex.

## What you'll do

1. Create `nanoclaw-runner@qsrmm-494222.iam.gserviceaccount.com`
2. Grant it `roles/aiplatform.user` on the project
3. Swap the VM's attached SA from default → `nanoclaw-runner`
4. Restart the VM (required — attached SA changes don't take effect on running instances)
5. Verify nanoclaw still works
6. Remove `roles/aiplatform.user` from the default Compute SA

All commands run from a shell with `gcloud auth login` as `jim@quickstack.cc` (project owner).

## Runbook

```bash
PROJECT=qsrmm-494222
ZONE=us-central1-a
INSTANCE=rmm
NEW_SA=nanoclaw-runner

# 1. Create the dedicated SA
gcloud iam service-accounts create "$NEW_SA" \
  --project="$PROJECT" \
  --display-name="NanoClaw orchestrator runner" \
  --description="Dedicated SA for nanoclaw agent containers; needs aiplatform.user only"

NEW_SA_EMAIL="${NEW_SA}@${PROJECT}.iam.gserviceaccount.com"

# 2. Grant Vertex AI access
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${NEW_SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --condition=None

# 3. Stop the VM (required to change attached SA)
gcloud compute instances stop "$INSTANCE" \
  --project="$PROJECT" --zone="$ZONE"

# 4. Swap the attached SA. `cloud-platform` scope is needed so the metadata
# server hands the agent containers a token with full aiplatform reach.
gcloud compute instances set-service-account "$INSTANCE" \
  --project="$PROJECT" --zone="$ZONE" \
  --service-account="$NEW_SA_EMAIL" \
  --scopes="https://www.googleapis.com/auth/cloud-platform"

# 5. Start the VM back up
gcloud compute instances start "$INSTANCE" \
  --project="$PROJECT" --zone="$ZONE"

# Wait ~60s for SSH + nanoclaw to come back, then verify the metadata server
# now hands out tokens for the new SA:
gcloud compute ssh "$INSTANCE" --project="$PROJECT" --zone="$ZONE" --tunnel-through-iap --command='
curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email
echo
SA_TOK=$(curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
  | python3 -c "import sys,json; print(json.load(sys.stdin)[\"access_token\"])")
curl -sS -o /dev/null -w "vertex sonnet-4-6 -> %{http_code}\n" \
  -H "Authorization: Bearer $SA_TOK" -H "Content-Type: application/json" \
  -d "{\"anthropic_version\":\"vertex-2023-10-16\",\"max_tokens\":5,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}" \
  https://us-east5-aiplatform.googleapis.com/v1/projects/qsrmm-494222/locations/us-east5/publishers/anthropic/models/claude-sonnet-4-6:rawPredict
'
# Expected output: nanoclaw-runner@qsrmm-494222.iam.gserviceaccount.com / vertex sonnet-4-6 -> 200

# 6. Smoke test — post '@recon hi' in #rmm-alerts in Slack and confirm reply
# (give the new agent container 30-60s to spawn after first message).

# 7. Once #6 is green, remove the broad grant from the default Compute SA.
gcloud projects remove-iam-policy-binding "$PROJECT" \
  --member='serviceAccount:536483581971-compute@developer.gserviceaccount.com' \
  --role='roles/aiplatform.user' \
  --condition=None
```

## Rollback

If step 6 fails (agent container can't reach Vertex with the new SA), you have two options. Either is reversible.

**Option A — restore default SA on the VM:**
```bash
gcloud compute instances stop rmm --project=qsrmm-494222 --zone=us-central1-a
gcloud compute instances set-service-account rmm \
  --project=qsrmm-494222 --zone=us-central1-a \
  --service-account=536483581971-compute@developer.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/cloud-platform
gcloud compute instances start rmm --project=qsrmm-494222 --zone=us-central1-a
```
The default SA still has `roles/aiplatform.user` (you haven't run step 7 yet) so things resume working. Then debug what's wrong with `nanoclaw-runner` separately.

**Option B — keep the new SA but skip the cleanup step (#7).** Both SAs hold the role; nothing breaks; no isolation gain either. Use this only if you've started step 7 and need to back out.

## Pitfalls

- **VM scopes.** GCE has a legacy concept of "access scopes" that gate which APIs the metadata-server token can call, *independent* of the SA's IAM roles. The default Compute SA on this VM was created with `cloud-platform` scope (otherwise the original Vertex calls wouldn't have worked once we granted the role). The `--scopes=https://www.googleapis.com/auth/cloud-platform` flag in step 4 carries this forward. **If you omit `--scopes`, GCE applies the legacy default scope set, which does NOT include aiplatform** — the SA will have the IAM role but the metadata-server token won't carry the right scope and Vertex calls will 403 with a different error message ("Request had insufficient authentication scopes").
- **VM restart breaks active recon sessions.** Any in-flight agent container that nanoclaw spawned will be SIGKILLed when the VM stops. The bridge will retry the originating message, but if a tech is mid-conversation expect a brief "agent restarted" hiccup. Pick a low-traffic time.
- **TRMM keeps running.** TRMM lives at `/rmm/` (bare-metal, not a container) and survives the VM restart fine — it's a normal systemd-managed Django/Daphne stack that comes back on boot.

## Verification after #7

After removing the broad grant, repeat the metadata-server probe one more time:
```bash
gcloud compute ssh rmm --project=qsrmm-494222 --zone=us-central1-a --tunnel-through-iap --command='
SA_TOK=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | python3 -c "import sys,json; print(json.load(sys.stdin)[\"access_token\"])")
curl -sS -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $SA_TOK" -H "Content-Type: application/json" \
  -d "{\"anthropic_version\":\"vertex-2023-10-16\",\"max_tokens\":5,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}" \
  https://us-east5-aiplatform.googleapis.com/v1/projects/qsrmm-494222/locations/us-east5/publishers/anthropic/models/claude-sonnet-4-6:rawPredict
'
# Expected: 200 (the dedicated SA still works; default SA grant is gone but VM no longer uses it)
```

## Source-of-truth dates

- Original broad grant applied: 2026-04-29 during Plan 3 smoke debugging
- This runbook captures state at that point. If significant time has passed since the grant, double-check current IAM bindings before running step 7 to avoid removing a binding someone else added intentionally.
