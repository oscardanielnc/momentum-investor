# Deploying on a Linux VM

Two systemd services, both restarted automatically:

- **investor-robot**: the orchestrator loop (15-minute heartbeat, daily and monthly
  rebalance checks, circuit breaker).
- **investor-dashboard**: the FastAPI dashboard on port **8080**.

The safe default is **DRY_RUN** (no orders). `setup_vm.sh` configures **PAPER** mode, which
trades Alpaca's paper account with simulated money.

## 1. Try it locally first

```bash
pip install -r requirements.txt
cp .env.example .env                 # Alpaca PAPER keys
python engine/orchestrator.py        # one cycle (DRY_RUN by default)
python dashboard/server.py           # http://127.0.0.1:8080
```

## 2. First deployment

```bash
# on the VM (Linux), with sudo:
sudo mkdir -p /opt/investor-app && cd /opt/investor-app
sudo git clone https://github.com/oscardanielnc/momentum-investor.git
cd momentum-investor
sudo bash setup_vm.sh
```

`setup_vm.sh` creates a virtualenv, installs dependencies, writes `/etc/investor.env`
(chmod 600, empty keys), installs and enables both services, opens port 8080 in the OS
firewall, and starts everything.

Then add the keys and restart:

```bash
sudo nano /etc/investor.env     # ALPACA_API_KEY / ALPACA_SECRET_KEY (paper), DEEPSEEK_API_KEY (optional)
sudo systemctl restart investor-robot investor-dashboard
```

Also open port 8080 in the cloud provider's network rules (for example an Oracle VCN security
list). The dashboard has no authentication: allow only the IP addresses that need it.

## 3. Operate and monitor

```bash
systemctl status investor-robot investor-dashboard
journalctl -u investor-robot -f          # robot logs
journalctl -u investor-dashboard -f      # dashboard logs
```

Dashboard: `http://<vm-ip>:8080`

## 4. Update

```bash
bash /opt/investor-app/momentum-investor/deploy.sh
```

It runs `git pull`, installs dependencies, checks that the engine modules import, and restarts
both services.

## Modes (in `/etc/investor.env`)

| INVESTOR_DRY_RUN | INVESTOR_ALPACA_LIVE | Result |
|---|---|---|
| true | any | Logs only, sends no orders |
| false | false | PAPER account (simulated money) |
| false | true | LIVE account (real money) |

Live mode exists in the code but was never used. The research in this repository concluded
that the robot's strategy does not beat buying QQQ (see the README), so running it with real
money is not recommended.

## Notes

- Keys live in `/etc/investor.env` (chmod 600) and never in the repository.
- The robot rebalances only during US market hours; the heartbeat, stop reconciliation and circuit breaker run
  around the clock.
- systemd restarts the services if they crash and after a reboot.
