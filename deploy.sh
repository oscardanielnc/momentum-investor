#!/usr/bin/env bash
# deploy.sh - Update investor on the VM and restart both services.
# Usage (on the VM): bash /opt/investor-app/momentum-investor/deploy.sh
set -euo pipefail

APP_DIR="/opt/investor-app"
GIT_DIR="${APP_DIR}/momentum-investor"
PYTHON="${APP_DIR}/venv/bin/python"

cd "$GIT_DIR"
git config --global --add safe.directory "$GIT_DIR" 2>/dev/null || true
echo "======================================================="
echo "  investor DEPLOY  -  $(date '+%Y-%m-%d %H:%M') UTC"
echo "======================================================="

echo ""
echo "[1/5] Pulling from GitHub..."
git pull
echo "  OK code updated"

echo ""
echo "[2/5] Python dependencies..."
$PYTHON -m pip install --quiet -r requirements.txt 2>/dev/null || true
echo "  OK dependencies"

echo ""
echo "[3/5] Quick check (module imports)..."
if $PYTHON -c "import sys; sys.path.insert(0,'engine'); import allocator, db, ai_explain, execution_alpaca, orchestrator" 2>/dev/null; then
    echo "  OK modules import"
else
    echo "  FAIL import error, aborting deploy"; exit 1
fi

echo ""
echo "[4/5] Restarting the robot (investor-robot)..."
sudo systemctl restart investor-robot && sleep 2
[ "$(systemctl is-active investor-robot 2>/dev/null)" = "active" ] \
    && echo "  OK robot active" \
    || echo "  FAIL robot did not start: journalctl -u investor-robot -n 30"

echo ""
echo "[5/5] Restarting the dashboard (investor-dashboard)..."
sudo systemctl restart investor-dashboard && sleep 2
if [ "$(systemctl is-active investor-dashboard 2>/dev/null)" = "active" ]; then
    IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || echo "<vm-ip>")
    echo "  OK dashboard active: http://${IP}:8080"
else
    echo "  FAIL dashboard did not start: journalctl -u investor-dashboard -n 30"
fi

echo ""
echo "======================================================="
echo "  robot:      $(systemctl is-active investor-robot     2>/dev/null || echo '?')"
echo "  dashboard:  $(systemctl is-active investor-dashboard 2>/dev/null || echo '?')"
echo "======================================================="
