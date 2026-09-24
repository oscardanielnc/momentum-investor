"""
Execution layer for the Alpaca Trading API.

Alpaca offers commission-free stocks and ETFs, a native trading API, and fractional orders by
dollar amount, which maps directly to target weights. Account equity comes from the broker
already marked to market.

Modes (environment):
  INVESTOR_DRY_RUN=true (default)       log only, send nothing.
  INVESTOR_ALPACA_LIVE=false (default)  paper-api.alpaca.markets (paper account).
  INVESTOR_ALPACA_LIVE=true             api.alpaca.markets (live account).

Keys: ALPACA_API_KEY / ALPACA_SECRET_KEY from the environment (see engine/_env.py).

Alpaca specifics:
  - Notional (dollar) and fractional orders must be type=market, tif=day.
  - Trailing stops are native orders (type=trailing_stop, trail_percent), so they live at the
    broker and still work if the robot is down.
  - flatten() is DELETE /v2/positions, used by the circuit breaker.
  - Long only: never sells more than it holds.
"""
from __future__ import annotations
import logging, os, time
import requests

log = logging.getLogger("investor.exec.alpaca")

from _env import load_env
load_env()

def _envstr(name, default=""):
    return os.environ.get(name, default).split("#")[0].strip().strip('"').strip("'")

def _load_keys():
    return _envstr("ALPACA_API_KEY"), _envstr("ALPACA_SECRET_KEY")

DRY_RUN = _envstr("INVESTOR_DRY_RUN", "true").lower() != "false"
LIVE    = _envstr("INVESTOR_ALPACA_LIVE", "false").lower() == "true"
API_KEY, API_SECRET = _load_keys()
MIN_ORDER_USD = float(_envstr("INVESTOR_MIN_ORDER_USD", "1"))
CAPITAL_FALLBACK = float(_envstr("INVESTOR_CAPITAL_FALLBACK", "500"))   # equity reported in DRY_RUN

_BASE_PAPER = "https://paper-api.alpaca.markets"
_BASE_LIVE  = "https://api.alpaca.markets"

def _base():
    return _BASE_LIVE if LIVE else _BASE_PAPER

def _hdr():
    return {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}

def _req(method, path, **kw):
    try:
        r = requests.request(method, _base()+path, headers=_hdr(), timeout=15, **kw)
        if r.status_code >= 400:
            log.warning(f"[alpaca] {method} {path}: {r.status_code} {r.text[:150]}")
            return None
        return r.json() if r.text else {}
    except Exception as e:
        log.warning(f"[alpaca] {method} {path}: {e}")
        return None

# ─── Account state ──────────────────────────────────────────────────────────
def get_account(retries=3, backoff=1.0):
    """Raw account dict, retried with linear backoff. None if unreadable."""
    if DRY_RUN:
        return {"equity": CAPITAL_FALLBACK, "cash": CAPITAL_FALLBACK, "status": "DRY_RUN"}
    for i in range(max(1, retries)):
        d = _req("GET", "/v2/account")
        if isinstance(d, dict) and "equity" in d:
            return d
        if i < retries-1:
            time.sleep(backoff*(i+1))
    return None

def get_equity(retries=3):
    """Mark-to-market portfolio value, or None if unreadable (the cycle is then skipped:
    a gap is better than a wrong value)."""
    if DRY_RUN:
        return CAPITAL_FALLBACK
    d = get_account(retries)
    return float(d["equity"]) if d and d.get("equity") is not None else None

def get_positions():
    """{symbol: {'qty': float, 'mv': float, 'avg': float}} for open positions."""
    if DRY_RUN:
        return {}
    d = _req("GET", "/v2/positions")
    if not isinstance(d, list):
        return {}
    return {p["symbol"]: {"qty": float(p["qty"]), "mv": float(p["market_value"]),
                          "avg": float(p["avg_entry_price"])} for p in d}

def market_open():
    d = _req("GET", "/v2/clock")
    return bool(d.get("is_open")) if isinstance(d, dict) else False

def list_orders(limit=100, status="all"):
    """Recent orders (used to persist order history). Empty in DRY_RUN."""
    if DRY_RUN:
        return []
    d = _req("GET", f"/v2/orders?status={status}&limit={int(limit)}&direction=desc&nested=true")
    return d if isinstance(d, list) else []

def open_trailing_stops():
    """{symbol: shares covered} by open SELL trailing stops (checked on every heartbeat)."""
    out = {}
    for o in list_orders(limit=100, status="open"):
        if o.get("type") == "trailing_stop" and o.get("side") == "sell":
            out[o["symbol"]] = out.get(o["symbol"], 0.0) + float(o.get("qty") or 0)
    return out

def latest_prices(symbols):
    """Last trade per symbol (data API, IEX feed). {} on failure; the caller decides."""
    if DRY_RUN or not symbols:
        return {}
    try:
        r = requests.get("https://data.alpaca.markets/v2/stocks/trades/latest",
                         params={"symbols": ",".join(symbols), "feed": "iex"},
                         headers=_hdr(), timeout=15)
        if r.status_code >= 400:
            log.warning(f"[alpaca] latest_prices: {r.status_code} {r.text[:120]}")
            return {}
        return {s: float(t["p"]) for s, t in (r.json().get("trades") or {}).items()}
    except Exception as e:
        log.warning(f"[alpaca] latest_prices: {e}")
        return {}

# ─── Orders ─────────────────────────────────────────────────────────────────
def _coid(tag, symbol):
    return f"inv-{tag}-{symbol}-{int(time.time())}"[:48]

def submit_notional(symbol, side, usd, coid=None):
    """Market order by dollar amount (fractional). side: 'buy' | 'sell'."""
    body = {"symbol": symbol, "notional": round(abs(usd), 2), "side": side,
            "type": "market", "time_in_force": "day",
            "client_order_id": coid or _coid("mk", symbol)}
    if DRY_RUN:
        log.info(f"[alpaca] DRY order {side} ${abs(usd):.2f} {symbol}")
        return {"dry_run": True, **body}
    return _req("POST", "/v2/orders", json=body)

def place_trailing_stop(symbol, qty, trail_percent, coid=None):
    """Native SELL trailing stop (GTC). Alpaca requires whole shares for stops, so the quantity
    is rounded down and nothing is placed below one share."""
    q = int(abs(qty))
    if q < 1:
        return None
    body = {"symbol": symbol, "qty": q, "side": "sell",
            "type": "trailing_stop", "trail_percent": round(trail_percent, 2),
            "time_in_force": "gtc", "client_order_id": coid or _coid("ts", symbol)}
    if DRY_RUN:
        log.info(f"[alpaca] DRY trailing_stop {symbol} {q}sh {trail_percent}%")
        return {"dry_run": True, **body}
    return _req("POST", "/v2/orders", json=body)

def close_position(symbol):
    """Close the whole position (exact quantity, avoids 'insufficient qty' rounding errors)."""
    if DRY_RUN:
        log.info(f"[alpaca] DRY close_position {symbol}"); return {"dry_run": True}
    return _req("DELETE", f"/v2/positions/{symbol}")

def cancel_all_orders():
    if DRY_RUN:
        log.info("[alpaca] DRY cancel_all_orders"); return {"dry_run": True}
    return _req("DELETE", "/v2/orders")

# ─── Rebalancing ────────────────────────────────────────────────────────────
def rebalance(target_weights, equity=None):
    """Move the book to the target weights. Long only.

    Sells first to free cash before buying, so cash never goes negative. Full exits use
    close_position (exact). Positions not in the target are closed. Returns orders placed.
    """
    equity = equity or get_equity() or CAPITAL_FALLBACK
    cur = get_positions()
    target = {s: w for s, w in target_weights.items() if w > 1e-4}
    syms = set(target) | set(cur)
    cancel_all_orders()
    sells, buys = [], []
    for s in syms:
        delta = target.get(s, 0.0) * equity - cur.get(s, {}).get("mv", 0.0)
        if abs(delta) < MIN_ORDER_USD:
            continue
        (buys if delta > 0 else sells).append((s, delta))
    placed = 0
    for s, delta in sells:
        if target.get(s, 0.0) * equity < MIN_ORDER_USD and s in cur:
            if close_position(s) is not None: placed += 1
        elif submit_notional(s, "sell", -delta) is not None:
            placed += 1
    if sells and not DRY_RUN:
        time.sleep(2)                                               # let the sells settle
    for s, delta in buys:
        if submit_notional(s, "buy", delta) is not None: placed += 1
    log.info(f"[alpaca] rebalance: {placed} order(s) ({len(sells)} sell/{len(buys)} buy) · "
             f"equity ${equity:.0f} · {mode_str()}")
    return placed

def flatten():
    """Liquidate every position (circuit-breaker halt)."""
    if DRY_RUN:
        log.info("[alpaca] DRY flatten (close all)"); return {"dry_run": True}
    cancel_all_orders()
    return _req("DELETE", "/v2/positions")

def mode_str():
    return "DRY_RUN" if DRY_RUN else ("LIVE-REAL" if LIVE else "PAPER")
