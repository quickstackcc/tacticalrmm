#!/usr/bin/env bash
# install-bridge.sh — idempotent install/upgrade of the approval-bridge.
# Run as root (sudo) on the qsrmm VM. Safe to re-run.
#
# This script:
#   1. Ensures the `nanormm` Linux user and required directories exist.
#   2. Pulls the latest qsrmm code into /opt/nanormm/approval-bridge.
#   3. Builds/refreshes the venv via uv.
#   4. Installs the systemd unit and reloads.
#   5. Restarts the service.
#
# Pre-requisites (one-time, NOT this script's job):
#   - /etc/nanormm/bridge.env (mode 600, owned by nanormm) — see bridge.env.example.
#   - Postgres `nanormm` DB and Redis DB 11 reachable on localhost.
#   - uv installed and on PATH for root (or full path used here).

set -euo pipefail

NANORMM_USER="nanormm"
INSTALL_ROOT="/opt/nanormm"
BRIDGE_DIR="${INSTALL_ROOT}/approval-bridge"
QSRMM_REPO_URL="https://github.com/quickstackcc/tacticalrmm.git"
ENV_FILE="/etc/nanormm/bridge.env"
SERVICE_NAME="approval-bridge"
LOG_DIR="/var/log/nanormm"

echo "==> Ensuring nanormm user exists"
if ! id -u "${NANORMM_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/home/${NANORMM_USER}" \
        --shell /bin/bash "${NANORMM_USER}"
fi

echo "==> Creating directories"
mkdir -p "${INSTALL_ROOT}" "${LOG_DIR}"
chown -R "${NANORMM_USER}:${NANORMM_USER}" "${INSTALL_ROOT}" "${LOG_DIR}"

echo "==> Verifying env file"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: ${ENV_FILE} is missing." >&2
    echo "Copy nanormm/deploy/bridge.env.example to ${ENV_FILE}, fill in values," >&2
    echo "then chmod 600 and chown nanormm:nanormm." >&2
    exit 1
fi
chmod 600 "${ENV_FILE}"
chown "${NANORMM_USER}:${NANORMM_USER}" "${ENV_FILE}"

echo "==> Cloning or updating qsrmm checkout (sparse: only nanormm/approval-bridge + nanormm/trmm-mcp + nanormm/recon)"
QSRMM_CHECKOUT="${INSTALL_ROOT}/qsrmm"
if [[ ! -d "${QSRMM_CHECKOUT}/.git" ]]; then
    sudo -u "${NANORMM_USER}" git clone --depth 1 --filter=blob:none --sparse \
        "${QSRMM_REPO_URL}" "${QSRMM_CHECKOUT}"
    sudo -u "${NANORMM_USER}" git -C "${QSRMM_CHECKOUT}" sparse-checkout set \
        nanormm/approval-bridge nanormm/trmm-mcp nanormm/policy.yaml nanormm/recon
else
    sudo -u "${NANORMM_USER}" git -C "${QSRMM_CHECKOUT}" fetch --depth 1 origin develop
    sudo -u "${NANORMM_USER}" git -C "${QSRMM_CHECKOUT}" reset --hard origin/develop
fi

# Symlink for the systemd unit's WorkingDirectory expectation
ln -sfn "${QSRMM_CHECKOUT}/nanormm/approval-bridge" "${BRIDGE_DIR}"
ln -sfn "${QSRMM_CHECKOUT}/nanormm/policy.yaml" "${INSTALL_ROOT}/policy.yaml"

echo "==> Building venv with uv"
# --reinstall-package on both wheels forces uv to rebuild from the
# freshly-pulled source on upgrade, even when the version string didn't
# change. Without this, `uv pip install -e .` skips trmm-mcp because the
# version is already satisfied, and edits to trmm-mcp source silently
# don't take effect on restart. trmm-mcp is editable via the bridge's
# pyproject [tool.uv.sources] entry, so the single `-e .` pulls both.
sudo -u "${NANORMM_USER}" bash <<EOF
cd ${BRIDGE_DIR}
if [[ ! -d .venv ]]; then
    uv venv --python 3.11.8 .venv
fi
. .venv/bin/activate
uv pip install --reinstall-package approval-bridge --reinstall-package trmm-mcp -e .
EOF

echo "==> Installing systemd unit"
install -m 0644 \
    "${QSRMM_CHECKOUT}/nanormm/deploy/approval-bridge.service" \
    "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

echo "==> Enabling and (re)starting service"
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

echo "==> Verifying service is active"
sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    echo "OK: ${SERVICE_NAME} is running"
else
    echo "FAIL: ${SERVICE_NAME} not running. Recent journal:"
    journalctl -u "${SERVICE_NAME}.service" -n 50 --no-pager
    exit 1
fi
