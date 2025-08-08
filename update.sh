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

echo "[CarPi] Update done, restarting service"
systemctl restart carpi.service || true



