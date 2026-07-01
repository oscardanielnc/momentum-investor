"""
investor — ORQUESTADOR (el director). Une allocator (cerebro) + execution_alpaca (manos) +
db (memoria) en los 3 ritmos, con los patrones anti-error probados en kepler.

Ritmos:
  ⚡ Heartbeat (cada HEARTBEAT_S, ~15min): lee equity MTM → registra → drawdown → circuit breaker.
  🌅 Diario: recalcula pesos; actúa SOLO si algo cruza la banda 5% (anti-whipsaw).
  📅 Mensual: rebalanceo estratégico completo (la config validada).

Anti-errores:
  - "equity ilegible → omite ciclo" (nunca opera con un valor falso).
  - cada ciclo en try/except → loguea y sigue (el loop JAMÁS se cae por un fallo puntual).
  - circuit breaker intradía: si el drawdown cruza el umbral, flatten YA (salida ante eventos).
  - lock de instancia única (no dos robots a la vez).
  - DRY_RUN por defecto. Heartbeat watchdog en la tabla heartbeat.

Run:  python -m engine.orchestrator           # un ciclo (para probar)
      python -m engine.orchestrator --loop     # loop continuo
"""
from __future__ import annotations
import os, sys, time, atexit
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import allocator
import ai_explain
import execution_alpaca as ex
from db import DB

HEARTBEAT_S = int(os.environ.get("INVESTOR_HEARTBEAT_S", "900"))   # 15 min
CB_HALT   = float(os.environ.get("INVESTOR_CB_HALT", "0.25"))      # flatten si dd ≤ −25%
CB_RESUME = float(os.environ.get("INVESTOR_CB_RESUME", "0.15"))    # reanuda al recuperar a −15%
_LOCK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "orchestrator.lock")


def _now():
    return datetime.now(timezone.utc)

# ── Lock de instancia única ──────────────────────────────────────────────────
def _pid_alive(pid):
    """¿El proceso `pid` sigue vivo? (robusto a systemd restarts y SIGTERM sin atexit)."""
    try:
        os.kill(pid, 0)            # señal 0 = solo comprobar existencia
    except ProcessLookupError:
        return False               # no existe → lock huérfano
    except PermissionError:
        return True                # existe (otro dueño)
    except Exception:
        return True                # no se puede saber → conservador
    return True


def acquire_lock():
    os.makedirs(os.path.dirname(_LOCK), exist_ok=True)
    if os.path.exists(_LOCK):
        try:
            old = int(open(_LOCK).read().strip())
        except Exception:
            old = None
        if old and old != os.getpid() and _pid_alive(old):
            raise RuntimeError(f"Otra instancia activa (PID {old}). Aborto.")
        # lock huérfano (proceso muerto, p.ej. tras un restart de systemd) → lo reclamo
    with open(_LOCK, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(_LOCK) and os.remove(_LOCK))

def touch_lock():
    try: os.utime(_LOCK, None)
    except OSError: pass

# ── Helpers ──────────────────────────────────────────────────────────────────
def current_weights(equity):
    """Pesos actuales {sym: mv/equity} desde las posiciones reales ({} en DRY_RUN)."""
    pos = ex.get_positions()
    if not pos or not equity:
        return {}
    return {s: p["mv"] / equity for s, p in pos.items()}


def check_circuit_breaker(d: DB, state):
    """Salida intradía ante eventos: flatten si el drawdown cruza el umbral. Persistente."""
    dd = state["drawdown"] or 0.0
    halted = d.get_config("halted", "false") == "true"
    if not halted and dd <= -CB_HALT:
        ex.flatten()
        d.set_config("halted", "true")
        d.review("cb_activado", f"Circuit breaker HALT: drawdown {dd*100:.1f}% → flatten", severity="critical")
        d.log("CRITICAL", "circuit_breaker", f"HALT dd={dd*100:.1f}% → liquidado a caja")
        return True
    if halted and dd >= -CB_RESUME:
        d.set_config("halted", "false")
        d.log("INFO", "circuit_breaker", f"RESUME dd={dd*100:.1f}% → se permite re-entrar")
        return False
    return halted


def place_trailing_stops(d: DB):
    """Coloca un trailing stop nativo (TRAIL_PCT%) por cada posición → 'sale a tiempo' aunque el
    bot esté caído. Las órdenes viejas ya las canceló rebalance(). Blindado: no tumba el ciclo."""
    if ex.DRY_RUN:
        return 0
    n = 0
    for s, p in ex.get_positions().items():
        try:
            if ex.place_trailing_stop(s, p["qty"], allocator.TRAIL_PCT) is not None:
                n += 1
        except Exception as e:
            d.log_error("orchestrator", f"trailing stop {s} falló", e)
    d.log("INFO", "orchestrator", f"trailing stops colocados: {n} (a {allocator.TRAIL_PCT:.0f}%)")
    return n


def persist_traceability(d: DB, rb_id):
    """Trazabilidad completa: vuelca las órdenes 'inv-*' de Alpaca a order_log (idempotente por
    client_order_id) + snapshotea las posiciones vivas. Blindado: nunca tumba el ciclo."""
    if ex.DRY_RUN:
        return
    try:
        mode = ex.mode_str()
        for o in ex.list_orders(limit=50):
            coid = o.get("client_order_id", "")
            if not coid.startswith("inv-"):      # solo lo que colocó este robot
                continue
            st = (o.get("status") or "new").upper()
            qty = float(o.get("qty") or o.get("filled_qty") or 0)
            d.record_order(coid, o["symbol"], o["side"], o["type"], qty, mode,
                           rebalance_id=rb_id, price=o.get("limit_price"),
                           status=st, exchange_order_id=o.get("id"), raw=o)   # INSERT OR IGNORE
            d.update_order(coid, st,
                           filled_qty=(float(o["filled_qty"]) if o.get("filled_qty") else None),
                           avg_fill_price=(float(o["filled_avg_price"]) if o.get("filled_avg_price") else None))
        d.snapshot_positions({s: {"qty": p["qty"], "avg": p["avg"]}
                              for s, p in ex.get_positions().items()})
    except Exception as e:
        d.log_error("orchestrator", "persistencia de órdenes/posiciones falló", e)


def do_rebalance(d: DB, reason, equity, force=False):
    """Robot = ALPACA al 100% · estrategia agresiva multi-sector top-5. Rebalancea + coloca trailing
    stops. Diario: solo si CAMBIA la membresía del top-5 (nuevo líder / uno cae). Global66 NO interviene
    (colchón personal fijo de Oscar, fuera del robot)."""
    if not ex.DRY_RUN and not ex.market_open():
        d.log("INFO", "orchestrator", f"{reason}: mercado cerrado, pospongo")
        return "closed"
    prices = allocator.load_prices()
    if prices.empty or prices.shape[1] < 5:
        d.log_error("orchestrator", "panel de precios insuficiente")
        return "no_data"
    cur = current_weights(equity)
    target, meta = allocator.compute_target(prices, current=cur or None)
    new_set, cur_set = set(target), {s for s, w in cur.items() if w > 0.01}
    if not force and new_set == cur_set:
        d.log("INFO", "orchestrator", f"{reason}: top-5 sin cambios ({sorted(new_set)}), no opero")
        return "skip"
    rb_id = f"rb-{_now().strftime('%Y%m%d-%H%M')}"
    d.record_target(rb_id, target, {s: "líder momentum" for s in meta["leaders"]})
    placed = ex.rebalance(target, equity)
    if not ex.DRY_RUN:
        time.sleep(2)                 # dejar que llenen las market orders antes del trailing stop
        place_trailing_stops(d)
    persist_traceability(d, rb_id)    # vuelca órdenes reales a order_log + snapshot posiciones
    md, struct = allocator.rationale(target, meta, prev=cur)
    prose = ai_explain.explain_prose(struct, meta)          # prosa DeepSeek (None si falla)
    summary = (f"_{prose}_\n\n{md}" if prose else md)        # prosa + tabla; o solo tabla
    d.record_ai_explanation(summary, rebalance_id=rb_id, model=("deepseek" if prose else "deterministic"),
                            inputs=struct)
    d.log("INFO", "orchestrator", f"rebalanceo {reason}: {placed} órden(es) · "
          f"{meta['n_sectors']} sectores · líderes {meta['leaders']}", {"rb": rb_id})
    return placed


# ── Un ciclo completo (testeable) ────────────────────────────────────────────
def run_cycle(d: DB, now=None):
    now = now or _now()
    t0 = time.time()
    try:
        acc = ex.get_account()
        equity = float(acc["equity"]) if acc and acc.get("equity") is not None else None
        if equity is None:
            d.heartbeat("heartbeat", status="skipped", skip_reason="equity_ilegible")
            d.log("WARN", "orchestrator", "equity ilegible → omito ciclo (no opero con valor falso)")
            return "skipped"
        cash = float(acc.get("cash", 0) or 0)
        exposure = (float(acc.get("long_market_value") or 0) / equity) if equity else 0.0
        state = d.record_equity(equity, cash=cash, exposure=exposure)   # −30% sobre el capital de ALPACA (el 100% del robot)
        halted = check_circuit_breaker(d, state)
        d.heartbeat("heartbeat", status="ok", duration_ms=int((time.time()-t0)*1000), equity=equity)
        touch_lock()
        if halted:
            d.log("WARN", "orchestrator", "en HALT (circuit breaker) → no abro posiciones")
            return "halted"
        # programación: mensual (cambio de mes) o diario
        ym, today = now.strftime("%Y-%m"), now.date().isoformat()
        if d.get_config("last_monthly") != ym:
            r = do_rebalance(d, "mensual", equity, force=True)
            if r not in ("closed", "no_data"):
                d.set_config("last_monthly", ym)
            return f"mensual:{r}"
        if d.get_config("last_daily") != today:
            r = do_rebalance(d, "diario", equity, force=False)
            if r not in ("closed", "no_data"):
                d.set_config("last_daily", today)
            return f"diario:{r}"
        return "heartbeat_only"
    except Exception as e:
        d.log_error("orchestrator", "fallo en run_cycle", e)
        return "error"


def main():
    d = DB()
    d.set_config("mode", ex.mode_str())
    d.log("INFO", "orchestrator", f"arranque · modo {ex.mode_str()} · heartbeat {HEARTBEAT_S}s")
    loop = "--loop" in sys.argv
    if not loop:
        print("Un ciclo:", run_cycle(d)); d.close(); return
    acquire_lock()
    try:
        while True:
            print(_now().isoformat(), "→", run_cycle(d))
            time.sleep(HEARTBEAT_S)
    except KeyboardInterrupt:
        d.log("INFO", "orchestrator", "shutdown ordenado (KeyboardInterrupt)")
    finally:
        d.close()


if __name__ == "__main__":
    main()
