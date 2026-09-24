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
import json, os, sys, time, atexit
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import allocator
import ai_explain
import execution_alpaca as ex
from db import DB

HEARTBEAT_S = int(os.environ.get("INVESTOR_HEARTBEAT_S", "900"))   # 15 min
CB_HALT   = float(os.environ.get("INVESTOR_CB_HALT", "0.25"))      # flatten si dd ≤ −25%
CB_RESUME = float(os.environ.get("INVESTOR_CB_RESUME", "0.15"))    # reanuda al recuperar a −15%
REBAL_BAND = float(os.environ.get("INVESTOR_REBAL_BAND", "0.05"))  # drift de peso que dispara re-equiponderar (anti-whipsaw)
EXIT_RANK  = int(os.environ.get("INVESTOR_EXIT_RANK", "12"))        # banda de histéresis del DIARIO (validada v9): mantiene un nombre mientras siga en el top-EXIT_RANK
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


def _cb_resume(d: DB, why):
    """Reanuda tras un HALT: re-ancla el pico al equity actual (si no, el dd seguiría bajo el umbral
    y se re-haltearía al instante) y fuerza el diario del próximo ciclo para re-entrar al top-5."""
    d.set_config("halted", "false")
    d.set_config("cb_ref", "")
    d.reset_peak(note=f"CB resume: {why}")
    d.set_config("last_daily", "")            # el próximo ciclo re-entra (diario)
    d.review("cb_resume", f"Circuit breaker RESUME: {why}. Pico re-anclado; re-entrada en el próximo ciclo",
             severity="warning")
    d.log("INFO", "circuit_breaker", f"RESUME → {why} · pico re-anclado · re-entrada próximo ciclo")


def check_circuit_breaker(d: DB, state):
    """Salida intradía ante eventos: flatten si el drawdown cruza el umbral. Persistente.
    Reanudación AUTOMÁTICA: en el HALT se fotografía la canasta vendida; mientras dura el HALT se
    calcula el equity HIPOTÉTICO (si no hubiéramos vendido) con los últimos precios — al recuperar
    el nivel de RESUME, re-entra solo. (El equity real en caja queda plano: no sirve para medir.)"""
    dd = state["drawdown"] or 0.0
    halted = d.get_config("halted", "false") == "true"
    if not halted and dd <= -CB_HALT:
        pos = ex.get_positions()
        basket = {s: p["mv"] / p["qty"] for s, p in pos.items() if p.get("qty")}
        d.set_config("cb_ref", json.dumps({"basket": basket, "equity": state["equity"],
                                           "peak": state["peak"], "ts": _now().isoformat()}))
        ex.flatten()
        d.set_config("halted", "true")
        d.review("cb_activado", f"Circuit breaker HALT: drawdown {dd*100:.1f}% → flatten", severity="critical")
        d.log("CRITICAL", "circuit_breaker", f"HALT dd={dd*100:.1f}% → liquidado a caja")
        return True
    if halted:
        if dd >= -CB_RESUME:                  # el equity real recuperó (aportes / test / dd leve)
            _cb_resume(d, f"drawdown real {dd*100:.1f}% sobre el umbral")
            return False
        try:
            ref = json.loads(d.get_config("cb_ref") or "null")
        except Exception:
            ref = None
        if ref and ref.get("basket") and ref.get("equity") and ref.get("peak"):
            px = ex.latest_prices(list(ref["basket"]))
            ratios = [px[s] / p0 for s, p0 in ref["basket"].items() if px.get(s) and p0]
            if ratios:
                hypo = ref["equity"] * (sum(ratios) / len(ratios))
                if hypo >= ref["peak"] * (1 - CB_RESUME):
                    _cb_resume(d, f"canasta vendida recuperó a {hypo/ref['peak']*100-100:+.1f}% del pico "
                                  f"(umbral −{CB_RESUME*100:.0f}%)")
                    return False
                d.log("INFO", "circuit_breaker", f"HALT vigente · equity hipotético "
                      f"{hypo/ref['peak']*100-100:+.1f}% del pico (resume en −{CB_RESUME*100:.0f}%)")
    return halted


def place_trailing_stops(d: DB):
    """Coloca un trailing stop nativo (TRAIL_PCT%) por cada posición → 'sale a tiempo' aunque el
    bot esté caído. Las órdenes viejas ya las canceló rebalance(). Blindado: no tumba el ciclo."""
    if ex.DRY_RUN:
        return 0
    n = 0
    for s, p in ex.get_positions().items():
        try:
            q = int(abs(p["qty"]))
            if q < 1:
                continue
            if ex.place_trailing_stop(s, q, allocator.TRAIL_PCT) is not None:
                n += 1
            else:   # rechazo HTTP (p.ej. la compra aún no liquida) → dejar rastro; el heartbeat lo repara
                d.log_error("orchestrator", f"trailing stop {s} rechazado por Alpaca ({q} acc) — "
                            "la reconciliación del heartbeat lo repondrá")
        except Exception as e:
            d.log_error("orchestrator", f"trailing stop {s} falló", e)
    d.log("INFO", "orchestrator", f"trailing stops colocados: {n} (a {allocator.TRAIL_PCT:.0f}%)")
    return n


def reconcile_stops(d: DB):
    """Red de seguridad CADA heartbeat: toda posición debe tener trailing stop vivo por sus acciones
    enteras. Si falta cobertura (stop rechazado en el rebalanceo, cancelado a mano, parcial), se
    repone la diferencia y se deja rastro. Blindado: no tumba el ciclo."""
    if ex.DRY_RUN:
        return 0
    try:
        pos = ex.get_positions()
        if not pos:
            return 0
        stops = ex.open_trailing_stops()
        fixed = 0
        for s, p in pos.items():
            missing = int(abs(p["qty"])) - int(stops.get(s, 0))
            if missing < 1:
                continue
            if ex.place_trailing_stop(s, missing, allocator.TRAIL_PCT) is not None:
                fixed += 1
                d.log("WARN", "orchestrator", f"stop faltante repuesto: {s} {missing} acc a {allocator.TRAIL_PCT:.0f}%")
                d.review("stop_reparado", f"{s}: trailing stop repuesto ({missing} acciones sin cobertura)",
                         symbol=s, severity="warning")
            else:
                d.log_error("orchestrator", f"{s} sin trailing stop ({missing} acc) y la reposición falló")
        return fixed
    except Exception as e:
        d.log_error("orchestrator", "reconciliación de stops falló", e)
        return 0


def _iso19(ts):
    """Normaliza timestamps ISO a 'YYYY-MM-DDTHH:MM:SS' (UTC) para comparar/guardar de forma estable."""
    return (ts or "")[:19]


def persist_traceability(d: DB, rb_id, since_iso=None):
    """Trazabilidad completa: vuelca las órdenes 'inv-*' de Alpaca a order_log (idempotente por
    client_order_id) + snapshotea las posiciones vivas. Cada orden se guarda con SU created_at real
    y el rebalance_id se atribuye SOLO a las creadas desde `since_iso` (inicio de este rebalanceo) —
    antes se estampaba todo con la hora/ciclo del volcado y contaminaba la auditoría.
    Blindado: nunca tumba el ciclo."""
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
            created = _iso19(o.get("created_at"))
            rb = rb_id if (since_iso and created and created >= _iso19(since_iso)) else None
            d.record_order(coid, o["symbol"], o["side"], o["type"], qty, mode,
                           rebalance_id=rb, price=o.get("limit_price"),
                           status=st, exchange_order_id=o.get("id"), raw=o,
                           ts=created or None)                                # INSERT OR IGNORE
            d.update_order(coid, st,
                           filled_qty=(float(o["filled_qty"]) if o.get("filled_qty") else None),
                           avg_fill_price=(float(o["filled_avg_price"]) if o.get("filled_avg_price") else None),
                           ts=_iso19(o.get("updated_at")) or None)
        d.snapshot_positions({s: {"qty": p["qty"], "avg": p["avg"]}
                              for s, p in ex.get_positions().items()})
    except Exception as e:
        d.log_error("orchestrator", "persistencia de órdenes/posiciones falló", e)


def hysteresis_target(held, ranking):
    """Banda de histéresis del DIARIO (validada v9, exit_rank=12): mantiene los nombres tenidos que
    sigan dentro del top-EXIT_RANK; rellena hasta TOPN con los mejores por ranking, respetando el tope
    por sector. Devuelve la lista de símbolos objetivo. Evita el churn del borde #5/#6 (57% de días)."""
    rank = {s: i + 1 for i, s in enumerate(ranking)}
    top = [s for s in held if rank.get(s, 10**9) <= EXIT_RANK]      # se quedan los que siguen en la banda
    sec_count = {}
    for s in top:
        sec_count[allocator.SECTOR.get(s, "?")] = sec_count.get(allocator.SECTOR.get(s, "?"), 0) + 1
    for s in ranking:                                              # rellena huecos por mejor momentum
        if len(top) >= allocator.TOPN:
            break
        sec = allocator.SECTOR.get(s, "?")
        if s in top or sec_count.get(sec, 0) >= allocator.MAX_PER_SECTOR:
            continue
        top.append(s); sec_count[sec] = sec_count.get(sec, 0) + 1
    return top[:allocator.TOPN]


def do_rebalance(d: DB, reason, equity, force=False):
    """Robot = ALPACA al 100% · estrategia agresiva multi-sector top-5. Rebalancea + coloca trailing
    stops. MENSUAL (force): reset completo al top-5 (con tope sector), salvo drift < banda. DIARIO:
    histéresis de rango — solo rota si un nombre cae fuera del top-EXIT_RANK (anti-churn, validado v9).
    Global66 NO interviene (colchón personal fijo de Oscar, fuera del robot)."""
    if not ex.DRY_RUN and not ex.market_open():
        d.log("INFO", "orchestrator", f"{reason}: mercado cerrado, pospongo")
        return "closed"
    prices = allocator.load_prices()
    if prices.empty or prices.shape[1] < 5:
        d.log_error("orchestrator", "panel de precios insuficiente")
        return "no_data"
    cur = current_weights(equity)
    target, meta = allocator.compute_target(prices)
    held = {s for s, w in cur.items() if w > 0.01}
    if force:
        # MENSUAL: reset al top-5. Salta si nada cambió y el drift es chico (anti-whipsaw).
        drift = max((abs(w - cur.get(s, 0.0)) for s, w in target.items()), default=1.0)
        if set(target) == held and drift < REBAL_BAND:
            d.log("INFO", "orchestrator", f"{reason}: top-5 sin cambios ({sorted(held)}), "
                  f"drift {drift*100:.1f}% < banda {REBAL_BAND*100:.0f}% → no opero")
            return "skip"
        final_syms = list(target)
    else:
        # DIARIO: histéresis de rango. Solo rota si un nombre tenido sale del top-EXIT_RANK.
        final_syms = hysteresis_target(held, meta["ranking"])
        if held and set(final_syms) == held:
            d.log("INFO", "orchestrator", f"{reason}: cartera dentro de la banda top-{EXIT_RANK} "
                  f"({sorted(held)}) → no roto", {"exit_rank": EXIT_RANK})
            return "skip"
    final = {s: round(1.0 / len(final_syms), 4) for s in final_syms} if final_syms else {}
    reasons = {s: ("líder top-5" if s in meta["leaders"] else f"dentro de banda top-{EXIT_RANK}")
               for s in final}
    t_start = _now()
    rb_id = f"rb-{t_start.strftime('%Y%m%d-%H%M')}"
    d.record_target(rb_id, final, reasons)
    placed = ex.rebalance(final, equity)
    if not ex.DRY_RUN:
        time.sleep(2)                 # dejar que llenen las market orders antes del trailing stop
        place_trailing_stops(d)
    # vuelca órdenes reales a order_log (ts reales; rb_id solo para las de ESTE rebalanceo) + snapshot
    persist_traceability(d, rb_id, since_iso=t_start.isoformat())
    md, struct = allocator.rationale(final, meta, prev=cur)
    prose = ai_explain.explain_prose(struct, meta)          # prosa DeepSeek (None si falla)
    summary = (f"_{prose}_\n\n{md}" if prose else md)        # prosa + tabla; o solo tabla
    d.record_ai_explanation(summary, rebalance_id=rb_id, model=("deepseek" if prose else "deterministic"),
                            inputs=struct)
    d.log("INFO", "orchestrator", f"rebalanceo {reason}: {placed} órden(es) · "
          f"cartera {sorted(final)} · top-5 {meta['leaders']}", {"rb": rb_id})
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
        reconcile_stops(d)            # red de seguridad: toda posición con su trailing stop vivo
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
