# investor — Despliegue en VM (24/7, dashboard en :8080)

El sistema corre dos servicios systemd con reinicio automático:
- **investor-robot** — el orquestador en loop (heartbeat 15min + rebalanceo diario + circuit breaker).
- **investor-dashboard** — el dashboard "Mi Patrimonio" (FastAPI) en el puerto **8080**.

Default seguro: **DRY_RUN** (no envía órdenes). Para la demo se usa **PAPER**.

---

## 1. Probar local (sin riesgo)
```bash
pip install -r requirements.txt
cp .env.example .env            # rellena ALPACA_API_KEY / ALPACA_SECRET_KEY (paper)
python engine/orchestrator.py            # un ciclo (DRY_RUN por defecto)
python dashboard/server.py               # dashboard → http://127.0.0.1:8080
```

## 2. Desplegar en la VM (primera vez)
```bash
# en la VM (Linux), como sudo:
sudo mkdir -p /opt/investor-app && cd /opt/investor-app
sudo git clone https://github.com/oscardanielnc/momentum-investor.git
cd momentum-investor
sudo bash setup_vm.sh
```
`setup_vm.sh` crea el venv, instala dependencias, crea `/etc/investor.env`, instala y habilita
los dos servicios systemd, abre el puerto 8080 y arranca todo.

**Después:** edita las claves y reinicia:
```bash
sudo nano /etc/investor.env     # ALPACA_API_KEY/SECRET (paper) + DEEPSEEK_API_KEY
#   Modo: INVESTOR_DRY_RUN=false + INVESTOR_ALPACA_LIVE=false  → PAPER (demo)
sudo systemctl restart investor-robot investor-dashboard
```
Abre también el **8080** en la consola del proveedor (Oracle VCN / Security List), no solo el firewall del SO.

## 3. Operar y monitorear
```bash
systemctl status investor-robot investor-dashboard
journalctl -u investor-robot -f          # logs del robot en vivo
journalctl -u investor-dashboard -f      # logs del dashboard
```
Dashboard: `http://<IP-de-la-VM>:8080`

## 4. Actualizar (deploys futuros)
```bash
bash /opt/investor-app/momentum-investor/deploy.sh
```
Hace `git pull`, instala deps, verifica imports y reinicia los servicios.

## 5. Pasar a REAL (solo tras 1 semana de demo en paper sin errores)
En `/etc/investor.env`: claves reales + `INVESTOR_ALPACA_LIVE=true`. Empezar con **$500** y subir
gradualmente. `INVESTOR_DRY_RUN=false`.

---

## Modos (en `/etc/investor.env`)
| INVESTOR_DRY_RUN | INVESTOR_ALPACA_LIVE | Resultado |
|---|---|---|
| true | — | Solo loguea, no envía órdenes (seguro) |
| false | false | **PAPER** (cuenta de práctica) — la demo |
| false | true | **REAL** (dinero real) |

## Notas
- Las claves viven en `/etc/investor.env` (chmod 600), **nunca en el repo**.
- La VM debe estar siempre encendida para el 24/7; systemd reinicia los servicios si se caen o tras reboot.
- El robot opera órdenes solo en horario de mercado US; el heartbeat y el circuit breaker corren 24/7.
