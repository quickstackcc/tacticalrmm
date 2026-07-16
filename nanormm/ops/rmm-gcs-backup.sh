#!/usr/bin/env bash
# Runs /rmm/backup.sh --auto (handles local rotation) then uploads any
# newly-created .tar under /rmmbackups/ to gs://qsrmm-backups/.
# The VM's service account has objectCreator only -- it can PUT but
# cannot DELETE or OVERWRITE, so an attacker on the VM cannot erase
# backup history.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
BUCKET=gs://qsrmm-backups
LOG=$HOME/rmm-gcs-backup.log
exec >>$LOG 2>&1
echo "=== $(date -u -Iseconds) run start ==="
BEFORE_EPOCH=$(date -u +%s)
/rmm/backup.sh --auto
mapfile -t NEW < <(find /rmmbackups -type f -name '*.tar' -newermt "@${BEFORE_EPOCH}" -print)
if [ ${#NEW[@]} -eq 0 ]; then
  echo "WARN: no new backup file produced"
  exit 1
fi
# Bundle /etc/nanormm (bridge + tripwire env; root-only secrets) into each
# fresh archive so a bare-metal rebuild doesn't stall on re-minting them.
# Gap found during the 2026-07-16 restore drill.
STAGE=$(mktemp -d)
mkdir -p "$STAGE/nanormm"
sudo tar -czf "$STAGE/nanormm/etc-nanormm.tar.gz" -C /etc nanormm
for f in "${NEW[@]}"; do
  sudo tar -rf "$f" -C "$STAGE" ./nanormm/etc-nanormm.tar.gz
done
rm -rf "$STAGE"
for f in "${NEW[@]}"; do
  rel=${f#/rmmbackups/}
  gcloud storage cp "$f" "${BUCKET}/${rel}"
done
echo "=== $(date -u -Iseconds) run done ==="
