"""
Orchestrator: runs the allocator, the Alpaca execution layer and the database on three clocks.

Clocks:
  Heartbeat (every HEARTBEAT_S, 15 min by default): read MTM equity, record it, update the
    drawdown and run the circuit breaker; verify every position still has its trailing stop.
  Daily: re-rank; rotate only when a holding falls outside the top EXIT_RANK (hysteresis).
  Monthly: reset to the exact top 5, unless membership is unchanged and drift is below
    REBAL_BAND.

Safety rules:
  - Unreadable equity means the cycle is skipped; the robot never trades on a made-up value.
  - Every cycle runs inside try/except: a single failure is logged, the loop keeps going.
  - Intraday circuit breaker: if drawdown crosses CB_HALT, flatten immediately.
  - Single-instance lock, so two robots never trade the same account.
  - DRY_RUN by default. Heartbeats are recorded in the heartbeat table as a watchdog.

Run:  python engine/orchestrator.py           # one cycle
      python engine/orchestrator.py --loop    # continuous loop
"""
from __future__ import annotations
import json, os, sys, time, atexit
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import allocator
import ai_explain
import execution_alpaca as ex
from db import DB

HEARTBEAT_S = int(os.environ.get("INVESTOR_HEARTBEAT_S", "900"))
CB_HALT   = float(os.environ.get("INVESTOR_CB_HALT", "0.25"))      # flatten when dd <= -25%
CB_RESUME = float(os.environ.get("INVESTOR_CB_RESUME", "0.15"))    # resume when back to -15%
REBAL_BAND = float(os.environ.get("INVESTOR_REBAL_BAND", "0.05"))  # monthly drift that triggers re-weighting
EXIT_RANK  = int(os.environ.get("INVESTOR_EXIT_RANK", "12"))        # daily hysteresis band (research/backtest_v9)
_LOCK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "orchestrator.lock")


def _now():
    return datetime.now(timezone.utc)

# ── Single-instance lock ─────────────────────────────────────────────────────
def _pid_alive(pid):
    """Whether process `pid` is still running (works across systemd restarts and SIGTERM)."""
    try:
        os.kill(pid, 0)            # signal 0 only checks existence
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                # exists, owned by someone else
    except Exception:
        return True                # unknown: assume alive, the conservative choice
    return True


def acquire_lock():
    os.makedirs(os.path.dirname(_LOCK), exist_ok=True)
    if os.path.exists(_LOCK):
        try:
            old = int(open(_LOCK).read().strip())
        except Exception:
            old = None
        if old and old != os.getpid() and _pid_alive(old):
            raise RuntimeError(f"Another instance is running (PID {old}). Aborting.")
        # stale lock from a dead process (e.g. after a systemd restart): take it over
    with open(_LOCK, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(_LOCK) and os.remove(_LOCK))

def touch_lock():
    try: os.utime(_LOCK, None)
    except OSError: pass

# ── Helpers ──────────────────────────────────────────────────────────────────
def current_weights(equity):
    """Current weights {sym: mv/equity} from live positions ({} in DRY_RUN)."""
    pos = ex.get_positions()
    if not pos or not equity:
        return {}
    return {s: p["mv"] / equity for s, p in pos.items()}


def _cb_resume(d: DB, why):
    """Resume after a halt. Re-anchor the peak to current equity (otherwise the drawdown would
    still be past the threshold and trigger again at once) and force the next daily cycle so
    the robot re-enters the top 5."""
    d.set_config("halted", "false")
    d.set_config("cb_ref", "")
    d.reset_peak(note=f"CB resume: {why}")
    d.set_config("last_daily", "")
    d.review("cb_resume", f"Circuit breaker RESUME: {why}. Peak re-anchored; re-entry next cycle",
             severity="warning")
    d.log("INFO", "circuit_breaker", f"RESUME: {why} · peak re-anchored · re-entry next cycle")


def check_circuit_breaker(d: DB, state):
    """Flatten if the drawdown crosses CB_HALT. The halt persists across restarts.

    Resume is automatic. At halt time the sold basket is recorded; while halted, a hypothetical
    equity (as if the basket had been kept) is computed from latest prices, and the robot
    re-enters once that recovers to the RESUME level. Real equity sits flat in cash during a
    halt, so it cannot be used to measure recovery.
    """
    dd = state["drawdown"] or 0.0
    halted = d.get_config("halted", "false") == "true"
    if not halted and dd <= -CB_HALT:
        pos = ex.get_positions()
        basket = {s: p["mv"] / p["qty"] for s, p in pos.items() if p.get("qty")}
        d.set_config("cb_ref", json.dumps({"basket": basket, "equity": state["equity"],
                                           "peak": state["peak"], "ts": _now().isoformat()}))
        ex.flatten()
        d.set_config("halted", "true")
        d.review("cb_halt", f"Circuit breaker HALT: drawdown {dd*100:.1f}% -> flatten", severity="critical")
        d.log("CRITICAL", "circuit_breaker", f"HALT dd={dd*100:.1f}% -> liquidated to cash")
        return True
    if halted:
        if dd >= -CB_RESUME:                  # real equity recovered (deposit, test, shallow dd)
            _cb_resume(d, f"real drawdown {dd*100:.1f}% above the threshold")
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
                    _cb_resume(d, f"sold basket recovered to {hypo/ref['peak']*100-100:+.1f}% of peak "
                                  f"(threshold -{CB_RESUME*100:.0f}%)")
                    return False
                d.log("INFO", "circuit_breaker", f"HALT in effect · hypothetical equity "
                      f"{hypo/ref['peak']*100-100:+.1f}% of peak (resume at -{CB_RESUME*100:.0f}%)")
    return halted


def place_trailing_stops(d: DB):
    """Place a native TRAIL_PCT% trailing stop for every position, so exits work even if the
    robot is down. rebalance() has already cancelled the old orders. Never raises."""
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
            else:   # HTTP rejection (e.g. the buy has not settled yet); the heartbeat repairs it
                d.log_error("orchestrator", f"trailing stop {s} rejected by Alpaca ({q} sh); "
                            "the heartbeat reconciliation will replace it")
        except Exception as e:
            d.log_error("orchestrator", f"trailing stop {s} failed", e)
    d.log("INFO", "orchestrator", f"trailing stops placed: {n} (at {allocator.TRAIL_PCT:.0f}%)")
    return n


def reconcile_stops(d: DB):
    """Safety net on every heartbeat: every position must be covered by live trailing stops for
    its whole shares. Missing coverage (rejected at rebalance, cancelled by hand, partial) is
    replaced and logged. Never raises."""
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
                d.log("WARN", "orchestrator", f"missing stop replaced: {s} {missing} sh at {allocator.TRAIL_PCT:.0f}%")
                d.review("stop_repaired", f"{s}: trailing stop replaced ({missing} uncovered shares)",
                         symbol=s, severity="warning")
            else:
                d.log_error("orchestrator", f"{s} has no trailing stop ({missing} sh) and replacement failed")
        return fixed
    except Exception as e:
        d.log_error("orchestrator", "stop reconciliation failed", e)
        return 0


def _iso19(ts):
    """Normalize ISO timestamps to 'YYYY-MM-DDTHH:MM:SS' (UTC) for stable comparison."""
    return (ts or "")[:19]


def persist_traceability(d: DB, rb_id, since_iso=None):
    """Copy this robot's 'inv-*' orders from Alpaca into order_log (idempotent) and snapshot
    live positions. Each order keeps its real created_at, and rb_id is attributed only to orders
    created since `since_iso` (the start of this rebalance). Never raises."""
    if ex.DRY_RUN:
        return
    try:
        mode = ex.mode_str()
        for o in ex.list_orders(limit=50):
            coid = o.get("client_order_id", "")
            if not coid.startswith("inv-"):      # only orders placed by this robot
                continue
            st = (o.get("status") or "new").upper()
            qty = float(o.get("qty") or o.get("filled_qty") or 0)
            created = _iso19(o.get("created_at"))
            rb = rb_id if (since_iso and created and created >= _iso19(since_iso)) else None
            d.record_order(coid, o["symbol"], o["side"], o["type"], qty, mode,
                           rebalance_id=rb, price=o.get("limit_price"),
                           status=st, exchange_order_id=o.get("id"), raw=o,
                           ts=created or None)
            d.update_order(coid, st,
                           filled_qty=(float(o["filled_qty"]) if o.get("filled_qty") else None),
                           avg_fill_price=(float(o["filled_avg_price"]) if o.get("filled_avg_price") else None),
                           ts=_iso19(o.get("updated_at")) or None)
        d.snapshot_positions({s: {"qty": p["qty"], "avg": p["avg"]}
                              for s, p in ex.get_positions().items()})
    except Exception as e:
        d.log_error("orchestrator", "persisting orders/positions failed", e)


def hysteresis_target(held, ranking):
    """Daily rank hysteresis (research/backtest_v9, exit_rank=12).

    Keep every held name still ranked within the top EXIT_RANK; fill the remaining slots up to
    TOPN with the best-ranked names, respecting the sector cap. Returns the target symbol list.
    Without the band, top-5 membership changed on most days and the churn erased the edge.
    """
    rank = {s: i + 1 for i, s in enumerate(ranking)}
    top = [s for s in held if rank.get(s, 10**9) <= EXIT_RANK]
    sec_count = {}
    for s in top:
        sec_count[allocator.SECTOR.get(s, "?")] = sec_count.get(allocator.SECTOR.get(s, "?"), 0) + 1
    for s in ranking:
        if len(top) >= allocator.TOPN:
            break
        sec = allocator.SECTOR.get(s, "?")
        if s in top or sec_count.get(sec, 0) >= allocator.MAX_PER_SECTOR:
            continue
        top.append(s); sec_count[sec] = sec_count.get(sec, 0) + 1
    return top[:allocator.TOPN]


def do_rebalance(d: DB, reason, equity, force=False):
    """Rebalance and place trailing stops.

    force=True (monthly): reset to the exact top 5 with the sector cap, unless membership is
    unchanged and drift is below REBAL_BAND. force=False (daily): rank hysteresis; rotate only
    when a holding falls outside the top EXIT_RANK. Returns orders placed or a skip reason.
    """
    if not ex.DRY_RUN and not ex.market_open():
        d.log("INFO", "orchestrator", f"{reason}: market closed, postponing")
        return "closed"
    prices = allocator.load_prices()
    if prices.empty or prices.shape[1] < 5:
        d.log_error("orchestrator", "not enough price data")
        return "no_data"
    cur = current_weights(equity)
    target, meta = allocator.compute_target(prices)
    held = {s for s, w in cur.items() if w > 0.01}
    if force:
        drift = max((abs(w - cur.get(s, 0.0)) for s, w in target.items()), default=1.0)
        if set(target) == held and drift < REBAL_BAND:
            d.log("INFO", "orchestrator", f"{reason}: top-5 unchanged ({sorted(held)}), "
                  f"drift {drift*100:.1f}% < band {REBAL_BAND*100:.0f}%, no trade")
            return "skip"
        final_syms = list(target)
    else:
        final_syms = hysteresis_target(held, meta["ranking"])
        if held and set(final_syms) == held:
            d.log("INFO", "orchestrator", f"{reason}: portfolio within the top-{EXIT_RANK} band "
                  f"({sorted(held)}), no rotation", {"exit_rank": EXIT_RANK})
            return "skip"
    final = {s: round(1.0 / len(final_syms), 4) for s in final_syms} if final_syms else {}
    reasons = {s: ("top-5 leader" if s in meta["leaders"] else f"within top-{EXIT_RANK} band")
               for s in final}
    t_start = _now()
    rb_id = f"rb-{t_start.strftime('%Y%m%d-%H%M')}"
    d.record_target(rb_id, final, reasons)
    placed = ex.rebalance(final, equity)
    if not ex.DRY_RUN:
        time.sleep(2)                 # let market orders fill before placing stops
        place_trailing_stops(d)
    persist_traceability(d, rb_id, since_iso=t_start.isoformat())
    md, struct = allocator.rationale(final, meta, prev=cur)
    prose = ai_explain.explain_prose(struct)
    summary = (f"_{prose}_\n\n{md}" if prose else md)
    d.record_ai_explanation(summary, rebalance_id=rb_id, model=("deepseek" if prose else "deterministic"),
                            inputs=struct)
    d.log("INFO", "orchestrator", f"{reason} rebalance: {placed} order(s) · "
          f"portfolio {sorted(final)} · top-5 {meta['leaders']}", {"rb": rb_id})
    return placed


# ── One full cycle (testable) ────────────────────────────────────────────────
def run_cycle(d: DB, now=None):
    """Run one heartbeat, plus the monthly or daily rebalance when due. Returns a status string."""
    now = now or _now()
    t0 = time.time()
    try:
        acc = ex.get_account()
        equity = float(acc["equity"]) if acc and acc.get("equity") is not None else None
        if equity is None:
            d.heartbeat("heartbeat", status="skipped", skip_reason="equity_unreadable")
            d.log("WARN", "orchestrator", "equity unreadable, skipping cycle (never trade on a wrong value)")
            return "skipped"
        cash = float(acc.get("cash", 0) or 0)
        exposure = (float(acc.get("long_market_value") or 0) / equity) if equity else 0.0
        state = d.record_equity(equity, cash=cash, exposure=exposure)
        halted = check_circuit_breaker(d, state)
        d.heartbeat("heartbeat", status="ok", duration_ms=int((time.time()-t0)*1000), equity=equity)
        touch_lock()
        if halted:
            d.log("WARN", "orchestrator", "HALT in effect (circuit breaker), not opening positions")
            return "halted"
        reconcile_stops(d)
        ym, today = now.strftime("%Y-%m"), now.date().isoformat()
        if d.get_config("last_monthly") != ym:
            r = do_rebalance(d, "monthly", equity, force=True)
            if r not in ("closed", "no_data"):
                d.set_config("last_monthly", ym)
            return f"monthly:{r}"
        if d.get_config("last_daily") != today:
            r = do_rebalance(d, "daily", equity, force=False)
            if r not in ("closed", "no_data"):
                d.set_config("last_daily", today)
            return f"daily:{r}"
        return "heartbeat_only"
    except Exception as e:
        d.log_error("orchestrator", "run_cycle failed", e)
        return "error"


def main():
    d = DB()
    d.set_config("mode", ex.mode_str())
    d.log("INFO", "orchestrator", f"start · mode {ex.mode_str()} · heartbeat {HEARTBEAT_S}s")
    loop = "--loop" in sys.argv
    if not loop:
        print("One cycle:", run_cycle(d)); d.close(); return
    acquire_lock()
    try:
        while True:
            print(_now().isoformat(), "->", run_cycle(d))
            time.sleep(HEARTBEAT_S)
    except KeyboardInterrupt:
        d.log("INFO", "orchestrator", "clean shutdown (KeyboardInterrupt)")
    finally:
        d.close()


if __name__ == "__main__":
    main()
