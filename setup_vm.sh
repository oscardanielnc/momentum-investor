#!/usr/bin/env bash
# setup_vm.sh — Instala investor en la VM (ejecutar UNA sola vez).
# Antes:  sudo mkdir -p /opt/investor-app && cd /opt/investor-app
#         git clone https://github.com/oscardanielnc/momentum-investor.git
# Uso:    cd /opt/investor-app/momentum-investor && sudo bash setup_vm.sh
set -euo pipefail

APP_DIR="/opt/investor-app"
GIT_DIR="${APP_DIR}/momentum-investor"
VENV="${APP_DIR}/venv"
ENV_FILE="/etc/investor.env"

echo "═══════════════════════════════════════════════════════"
echo "  investor — SETUP VM (primera vez)"
echo "═══════════════════════════════════════════════════════"

echo ""
echo "[1/5] Entorno virtual + dependencias..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$GIT_DIR/requirements.txt"
echo "  ✓ venv y dependencias instaladas"

echo ""
echo "[2/5] Archivo de entorno ($ENV_FILE)..."
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# investor — entorno de la VM (claves y modo). chmod 600. NO se versiona.
ALPACA_API_KEY=REEMPLAZAR
ALPACA_SECRET_KEY=REEMPLAZAR
AI_ENGINE=deepseek
DEEPSEEK_API_KEY=
# Modo: DRY_RUN=true (no opera) · PAPER = false+false · REAL = false + LIVE=true
INVESTOR_DRY_RUN=false
INVESTOR_ALPACA_LIVE=false
INVESTOR_HEARTBEAT_S=900
INVESTOR_DASHBOARD_PORT=8080
EOF
    chmod 600 "$ENV_FILE"
    echo "  ✓ Creado. EDITA $ENV_FILE con tus claves Alpaca (paper) + DeepSeek."
else
    echo "  ✓ Ya existe (no se sobrescribe)"
fi

echo ""
echo "[3/5] Instalando servicios systemd (robot + dashboard)..."
cp "$GIT_DIR/investor-robot.service"     /etc/systemd/system/investor-robot.service
cp "$GIT_DIR/investor-dashboard.service" /etc/systemd/system/investor-dashboard.service
systemctl daemon-reload
systemctl enable investor-robot investor-dashboard
echo "  ✓ Servicios habilitados (arrancan solos al reiniciar la VM)"

echo ""
echo "[4/5] Abriendo puerto 8080 (dashboard)..."
if command -v firewall-cmd &>/dev/null; then
    firewall-cmd --permanent --add-port=8080/tcp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
elif command -v ufw &>/dev/null; then
    ufw allow 8080/tcp 2>/dev/null || true
fi
echo "  ⚠ Abre también el 8080 en la consola del proveedor (Oracle VCN / Security List)"

echo ""
echo "[4b/5] Permisos (dueño = usuario que invocó sudo)..."
OWNER="${SUDO_USER:-opc}"
chown -R "$OWNER":"$OWNER" "$APP_DIR" 2>/dev/null || true
git config --global --add safe.directory "$GIT_DIR" 2>/dev/null || true

echo ""
echo "[5/5] Arrancando..."
systemctl restart investor-robot investor-dashboard
sleep 3
IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "<ip-vm>")

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  SETUP COMPLETO"
echo "  robot:      $(systemctl is-active investor-robot 2>/dev/null || echo '?')"
echo "  dashboard:  $(systemctl is-active investor-dashboard 2>/dev/null || echo '?')"
echo "  Dashboard:  http://${IP}:8080"
echo ""
echo "  1) Edita $ENV_FILE con tus claves, luego: sudo systemctl restart investor-robot investor-dashboard"
echo "  2) Logs:    journalctl -u investor-robot -f   ·   journalctl -u investor-dashboard -f"
echo "  3) Deploys futuros: bash $GIT_DIR/deploy.sh"
echo "═══════════════════════════════════════════════════════"
