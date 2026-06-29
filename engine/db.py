"""
investor — CAPA DE PERSISTENCIA (SQLite). La memoria del robot sobre db/schema.sql.
Fuente única de verdad: estado (equity/pico para el freno DD) + auditoría + logs de validación.

Patrones de robustez (heredados de kepler, probados en vivo):
  - WAL + busy_timeout → lecturas no bloquean escrituras, resiste cortes y locks.
  - Idempotencia en órdenes (client_order_id = PK, INSERT OR IGNORE).
  - Timestamps UTC ISO-8601. Context en JSON para reproducir cualquier caso.
  - Métodos cortos y blindados → el orquestador nunca se cae por un fallo de log.

Uso típico:
  d = DB()                      # crea/abre data/investor.db e inicializa el schema
  d.log("INFO","orchestrator","ciclo ok")
  st = d.record_equity(1000.0, cash=200.0)   # devuelve {equity,peak,drawdown}
  d.record_target("rb-2026-06-29", {"AMD":0.2,...}, {"AMD":"líder momentum"})
"""
from __future__ import annotations
import json, os, sqlite3, traceback as _tb
from datetime import datetime, timezone

_DEF_DB = r"D:\OSCAR\Documents\Trading Proyects\investor\data\investor.db"
_SCHEMA = r"D:\OSCAR\Documents\Trading Proyects\investor\db\schema.sql"

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class DB:
    def __init__(self, path: str = _DEF_DB, schema: str = _SCHEMA):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        if schema and os.path.exists(schema):
            with open(schema, encoding="utf-8") as f:
                self.conn.executescript(f.read())
        self.conn.commit()

    def _ex(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    # ── LOGS (lo que Oscar pidió: validar, detectar errores y señales a revisar) ──
    def log(self, level, component, message, context: dict | None = None):
        try:
            self._ex("INSERT INTO app_log(ts,level,component,message,context_json) VALUES(?,?,?,?,?)",
                     (_now(), level, component, message, json.dumps(context) if context else None))
        except Exception:
            pass  # un fallo de log JAMÁS debe tumbar el ciclo

    def log_error(self, component, message, exc: Exception | None = None, error_type=None):
        try:
            tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__)) if exc else None
            self._ex("INSERT INTO error_log(ts,component,error_type,message,traceback) VALUES(?,?,?,?,?)",
                     (_now(), component, error_type or (type(exc).__name__ if exc else None), message, tb))
        except Exception:
            pass

    def review(self, kind, detail, symbol=None, severity="info"):
        """Cola de 'señales que valen la pena revisar' (spread alto, gap, señal fuerte, CB…)."""
        try:
            self._ex("INSERT INTO review_queue(ts,kind,symbol,detail,severity) VALUES(?,?,?,?,?)",
                     (_now(), kind, symbol, detail, severity))
        except Exception:
            pass

    def heartbeat(self, cycle_type, status="ok", skip_reason=None, duration_ms=None, equity=None):
        self._ex("INSERT INTO heartbeat(ts,cycle_type,status,skip_reason,duration_ms,equity_mtm) "
                 "VALUES(?,?,?,?,?,?)", (_now(), cycle_type, status, skip_reason, duration_ms, equity))

    # ── ESTADO: equity MTM + pico (para el freno DD) ──
    def get_state(self):
        """Último equity, pico histórico y drawdown. Pico = base del freno −30%."""
        row = self.conn.execute("SELECT equity_mtm,peak FROM equity_history ORDER BY ts DESC LIMIT 1").fetchone()
        if not row:
            return {"equity": None, "peak": None, "drawdown": 0.0}
        eq, peak = row["equity_mtm"], row["peak"]
        return {"equity": eq, "peak": peak, "drawdown": (eq/peak - 1) if peak else 0.0}

    def record_equity(self, equity_mtm, cash=0.0, exposure=None, regime=None):
        """Guarda un punto de equity MTM, actualiza el pico y el drawdown. Devuelve el estado."""
        prev = self.conn.execute("SELECT MAX(peak) p FROM equity_history").fetchone()
        peak = max(equity_mtm, prev["p"] or equity_mtm)
        dd = equity_mtm/peak - 1 if peak else 0.0
        self._ex("INSERT OR REPLACE INTO equity_history(ts,equity_mtm,cash,peak,drawdown,exposure,regime,source) "
                 "VALUES(?,?,?,?,?,?,?,?)", (_now(), equity_mtm, cash, peak, dd,
                  exposure if exposure is not None else 0.0, regime, "mtm"))
        return {"equity": equity_mtm, "peak": peak, "drawdown": dd}

    # ── CARTERA: pesos objetivo, órdenes (idempotentes), posiciones ──
    def record_target(self, rebalance_id, weights: dict, reasons: dict | None = None):
        ts = _now(); reasons = reasons or {}
        for sym, w in weights.items():
            self._ex("INSERT OR REPLACE INTO target_weight(rebalance_id,ts,symbol,weight,reason) "
                     "VALUES(?,?,?,?,?)", (rebalance_id, ts, sym, float(w), reasons.get(sym)))

    def record_order(self, client_order_id, symbol, side, otype, qty, mode,
                     rebalance_id=None, price=None, status="NEW", exchange_order_id=None, raw=None):
        """Idempotente: si el client_order_id ya existe, NO se duplica."""
        self._ex("INSERT OR IGNORE INTO order_log(client_order_id,exchange_order_id,rebalance_id,ts_created,"
                 "symbol,side,type,qty,price,status,mode,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (client_order_id, exchange_order_id, rebalance_id, _now(), symbol, side, otype,
                  float(qty), price, status, mode, json.dumps(raw) if raw else None))

    def update_order(self, client_order_id, status, filled_qty=None, avg_fill_price=None, fee=None):
        self._ex("UPDATE order_log SET ts_updated=?,status=?,filled_qty=COALESCE(?,filled_qty),"
                 "avg_fill_price=COALESCE(?,avg_fill_price),fee=COALESCE(?,fee) WHERE client_order_id=?",
                 (_now(), status, filled_qty, avg_fill_price, fee, client_order_id))

    def snapshot_positions(self, positions: dict):
        """positions = {symbol:{qty,avg,...}}. Reemplaza el estado vivo de posiciones."""
        ts = _now()
        for sym, p in positions.items():
            self._ex("INSERT OR REPLACE INTO position(symbol,qty,avg_price,chandelier_stop,updated_at) "
                     "VALUES(?,?,?,?,?)", (sym, float(p.get("qty",0)), p.get("avg"), p.get("stop"), ts))

    # ── IA, eventos, aportes, config ──
    def record_ai_explanation(self, summary, rebalance_id=None, model="deepseek", inputs=None, published=0):
        self._ex("INSERT INTO ai_explanation(ts,rebalance_id,summary,model,inputs_json,published) "
                 "VALUES(?,?,?,?,?,?)", (_now(), rebalance_id, summary, model,
                                         json.dumps(inputs) if inputs else None, published))

    def record_event(self, headline, source=None, severity="info", symbols=None, action=None, raw=None):
        self._ex("INSERT INTO event_news(ts,headline,source,severity,symbols,action_taken,raw_json) "
                 "VALUES(?,?,?,?,?,?,?)", (_now(), headline, source, severity,
                 ",".join(symbols) if symbols else None, action, json.dumps(raw) if raw else None))

    def add_contribution(self, amount, currency="USDT", note=None):
        self._ex("INSERT INTO contribution(ts,amount,currency,note) VALUES(?,?,?,?)",
                 (_now(), float(amount), currency, note))

    def set_config(self, key, value, note=None):
        self._ex("INSERT OR REPLACE INTO config(key,value,updated_at,note) VALUES(?,?,?,?)",
                 (key, str(value), _now(), note))

    def get_config(self, key, default=None):
        row = self.conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ── lectura para dashboard / validación ──
    def pending_reviews(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM v_pending_review").fetchall()]

    def open_errors(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM v_open_errors").fetchall()]

    def close(self):
        try: self.conn.close()
        except Exception: pass
