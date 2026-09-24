#!/usr/bin/env bash
# setup_vm.sh - One-time install of investor on a Linux VM.
# Before:  sudo mkdir -p /opt/investor-app && cd /opt/investor-app
#          git clone https://github.com/oscardanielnc/momentum-investor.git
# Usage:   cd /opt/investor-app/momentum-investor && sudo bash setup_vm.sh
set -euo pipefail

APP_DIR="/opt/investor-app"
GIT_DIR="${APP_DIR}/momentum-investor"
VENV="${APP_DIR}/venv"
ENV_FILE="/etc/investor.env"

echo "======================================================="
echo "  investor - VM SETUP (first time)"
echo "======================================================="

echo ""
echo "[1/5] Virtual environment + dependencies..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$GIT_DIR/requirements.txt"
echo "  OK venv and dependencies installed"

echo ""
echo "[2/5] Environment file ($ENV_FILE)..."
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# investor - VM environment (keys and mode). chmod 600. Never commit this file.
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
AI_ENGINE=deepseek
DEEPSEEK_API_KEY=
# Mode: DRY_RUN=true (no orders) · PAPER = false + false · LIVE = false + LIVE=true
INVESTOR_DRY_RUN=false
INVESTOR_ALPACA_LIVE=false
INVESTOR_HEARTBEAT_S=900
INVESTOR_DASHBOARD_PORT=8080
EOF
    chmod 600 "$ENV_FILE"
    echo "  OK created. EDIT $ENV_FILE with your Alpaca paper keys (+ DeepSeek, optional)."
else
    echo "  OK already exists (not overwritten)"
fi

echo ""
echo "[3/5] Installing systemd services (robot + dashboard)..."
cp "$GIT_DIR/investor-robot.service"     /etc/systemd/system/investor-robot.service
cp "$GIT_DIR/investor-dashboard.service" /etc/systemd/system/investor-dashboard.service
systemctl daemon-reload
systemctl enable investor-robot investor-dashboard
echo "  OK services enabled (start on boot)"

echo ""
echo "[4/5] Opening port 8080 (dashboard)..."
if command -v firewall-cmd &>/dev/null; then
    firewall-cmd --permanent --add-port=8080/tcp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
elif command -v ufw &>/dev/null; then
    ufw allow 8080/tcp 2>/dev/null || true
fi
echo "  NOTE also open 8080 in the cloud provider's network rules (e.g. Oracle VCN security list)"
echo "  NOTE the dashboard has no authentication: restrict the source IPs"

echo ""
echo "[4b/5] Ownership (the user who invoked sudo)..."
OWNER="${SUDO_USER:-opc}"
chown -R "$OWNER":"$OWNER" "$APP_DIR" 2>/dev/null || true
git config --global --add safe.directory "$GIT_DIR" 2>/dev/null || true

echo ""
echo "[5/5] Starting..."
systemctl restart investor-robot investor-dashboard
sleep 3
IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "<vm-ip>")

echo ""
echo "======================================================="
echo "  SETUP COMPLETE"
echo "  robot:      $(systemctl is-active investor-robot 2>/dev/null || echo '?')"
echo "  dashboard:  $(systemctl is-active investor-dashboard 2>/dev/null || echo '?')"
echo "  Dashboard:  http://${IP}:8080"
echo ""
echo "  1) Edit $ENV_FILE with your keys, then: sudo systemctl restart investor-robot investor-dashboard"
echo "  2) Logs:    journalctl -u investor-robot -f   ·   journalctl -u investor-dashboard -f"
echo "  3) Future deploys: bash $GIT_DIR/deploy.sh"
echo "======================================================="
