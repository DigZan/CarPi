#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/carpi"

echo "[CarPi] Updating application in ${INSTALL_DIR}"
cd "${INSTALL_DIR}"

if [ ! -d .git ]; then
  echo "[CarPi] ERROR: ${INSTALL_DIR} is not a git repo"
  exit 1
fi

sudo -u carpi git fetch --all --prune
sudo -u carpi git pull --ff-only

sudo -u carpi "${INSTALL_DIR}/venv/bin/pip" install -r requirements.txt --upgrade --no-cache-dir

echo "[CarPi] Syncing systemd unit files"
cp "${INSTALL_DIR}/systemd/carpi.service" /etc/systemd/system/
cp "${INSTALL_DIR}/systemd/carpi-update.service" /etc/systemd/system/
cp "${INSTALL_DIR}/systemd/carpi-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable carpi.service || true
systemctl enable --now carpi-update.timer || true

echo "[CarPi] Update complete. Changes will take effect on next service restart or reboot."



