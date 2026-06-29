"""
Test de humo de engine/execution_spot.py — valida la MECÁNICA sin tocar dinero ni llaves.

Dos bloques:
  OFFLINE (siempre corre, sin red ni API keys): firma, idempotencia, MTM, capital-drop,
          payloads de órdenes, guard long-only, modos DRY_RUN.
  ONLINE  (best-effort, salta si no hay red): exchangeInfo y bookTicker reales de Binance.

Uso:  python tests/smoke_execution.py
Salida: lista de checks PASS/FAIL + resumen. Exit code 1 si algún check OFFLINE falla.
"""
import importlib, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")   # consola Windows (cp1252) → permitir emojis
except Exception:
    pass

# DRY_RUN obligatorio ANTES de importar (el módulo lee env al cargar) → cero envíos reales.
os.environ["INVESTOR_DRY_RUN"] = "true"
os.environ["INVESTOR_USE_DEMO"] = "true"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
ex = importlib.import_module("execution_spot")

import pandas as pd

_pass = _fail = _skip = 0

def check(name, cond, detail=""):
    global _pass, _fail
    mark = "✅ PASS" if cond else "❌ FAIL"
    if cond: _pass += 1
    else:    _fail += 1
    print(f"{mark}  {name}" + (f"   · {detail}" if detail else ""))

def skip(name, why):
    global _skip
    _skip += 1
    print(f"⏭️  SKIP  {name}   · {why}")

print("=" * 70)
print(f"SMOKE TEST execution_spot · modo={ex.mode_str()} · base={ex._base()}")
print("=" * 70)

# ── OFFLINE ────────────────────────────────────────────────────────────────────
print("\n[OFFLINE]")

# 1. Modo seguro por defecto
check("DRY_RUN activo (no envía órdenes)", ex.DRY_RUN is True)
check("base = testnet (demo)", "testnet" in ex._base())

# 2. Firma HMAC determinista (secreto de prueba)
ex.API_SECRET = "testsecret"
s1 = ex._sign({"a": "1"})["signature"]
ex.API_SECRET = "testsecret"
# misma entrada lógica → firma de 64 hex (sha256)
check("firma HMAC es sha256 hex de 64", len(s1) == 64 and all(c in "0123456789abcdef" for c in s1), s1[:12] + "…")

# 3. Idempotencia: client_order_id ≤ 36 chars
coid = ex._coid("mk", "NVDABUSDT")
check("client_order_id ≤ 36 chars", len(coid) <= 36, coid)

# 4. get_balance en DRY_RUN = capital fallback
check("get_balance DRY_RUN = fallback", ex.get_balance() == ex.CAPITAL_FALLBACK, f"${ex.CAPITAL_FALLBACK:.0f}")

# 5. get_positions en DRY_RUN = {}
check("get_positions DRY_RUN vacío", ex.get_positions() == {})

# 6. MTM: equity = USDT + Σ qty×precio  (monkeypatch saldos+precios)
ex.DRY_RUN = False   # forzar el cálculo real (sin red: parcheamos las fuentes)
ex.get_balances = lambda retries=3: {"USDT": 100.0, "NVDAB": 2.0, "TSLAB": 1.0}
prices = {"NVDABUSDT": 200.0, "TSLABUSDT": 300.0}
eq = ex.get_balance(prices=prices)
check("equity MTM = 100 + 2×200 + 1×300 = 800", abs(eq - 800.0) < 1e-6, f"={eq}")

# 6b. balance ilegible → None (mejor hueco que valor falso)
ex.get_balances = lambda retries=3: {}
check("cuenta ilegible → get_balance None", ex.get_balance(prices=prices) is None)
ex.DRY_RUN = True   # restaurar modo seguro

# 7. capital-aware drop: suelta patas < min-notional y PRESERVA el gross
filt = {"AAA": {"minnot": 5}, "BBB": {"minnot": 5}, "CCC": {"minnot": 5}}
tw = pd.Series({"AAA": 0.5, "BBB": 0.49, "CCC": 0.01})   # CCC: 0.01×500=$5... usemos equity bajo
equity = 100.0   # CCC notional = 0.01×100 = $1 < $5 → debe soltarse
out, dropped = ex._capital_aware_drop(tw, equity, filt)
check("capital-drop suelta la pata sub-min-notional", dropped == 1, f"dropped={dropped}")
check("capital-drop preserva el gross", abs(out.abs().sum() - tw.abs().sum()) < 1e-6,
      f"gross {tw.abs().sum():.3f}→{out.abs().sum():.3f}")
check("capital-drop deja CCC en 0", abs(out.get("CCC", 0.0)) < 1e-9)

# 8. payload LIMIT_MAKER correcto (capturar el POST)
captured = {}
ex._post = lambda path, p: captured.update({"path": path, **p}) or {"ok": True}
ex.DRY_RUN = False
ex.place_limit_maker("NVDABUSDT", "BUY", 1.23456, 199.987, qp=2, pp=2, coid="inv-mk-test")
check("LIMIT_MAKER usa type=LIMIT_MAKER", captured.get("type") == "LIMIT_MAKER")
check("LIMIT_MAKER respeta precisión qty/price", captured.get("quantity") == "1.23" and captured.get("price") == "199.99",
      f"qty={captured.get('quantity')} px={captured.get('price')}")
check("LIMIT_MAKER lleva newClientOrderId", captured.get("newClientOrderId") == "inv-mk-test")

# 9. payload STOP_LOSS_LIMIT (chandelier en exchange) es SELL
captured.clear()
ex.place_stop_loss("NVDABUSDT", 1.0, 180.0, 179.5, qp=2, pp=2, coid="inv-sl-test")
check("stop_loss es STOP_LOSS_LIMIT SELL", captured.get("type") == "STOP_LOSS_LIMIT" and captured.get("side") == "SELL")

# 10. guard long-only: nunca vende más de lo que se tiene
orders = []
ex._post = lambda path, p: orders.append(p) or {"ok": True}
ex.get_positions = lambda prices=None: {"NVDABUSDT": 0.5}   # tenemos 0.5
ex.book_mid = lambda s: 200.0
# target 0 → quiere vender; held=0.5 → no puede vender más de 0.5
n = ex._place_deltas(pd.Series({"NVDABUSDT": 0.0}), equity=1000.0, filt={"NVDABUSDT": {"qp": 2, "pp": 2}},
                     prices={"NVDABUSDT": 200.0}, attempt=0)
sell = next((o for o in orders if o.get("side") == "SELL"), None)
check("long-only: venta ≤ holdings", sell is not None and float(sell["quantity"]) <= 0.5 + 1e-9,
      f"vende {sell['quantity'] if sell else '—'} de 0.5")
ex.DRY_RUN = True

# ── ONLINE (best-effort) ─────────────────────────────────────────────────────
print("\n[ONLINE · best-effort]")
ex._FILTERS = {}   # limpiar cache
try:
    # usar producción para exchangeInfo (testnet no lista bStocks)
    ex.USE_DEMO = False
    filt = ex.load_filters()
    if not filt:
        skip("exchangeInfo real", "sin red / bloqueado")
    else:
        check("exchangeInfo trae NVDABUSDT", "NVDABUSDT" in filt,
              f"qp={filt.get('NVDABUSDT',{}).get('qp')} pp={filt.get('NVDABUSDT',{}).get('pp')}")
        tick = ex.all_book_tickers()
        if tick and "NVDABUSDT" in tick:
            check("bookTicker trae precio NVDABUSDT", tick["NVDABUSDT"] > 0, f"mid≈{tick['NVDABUSDT']:.2f}")
        else:
            skip("bookTicker NVDABUSDT", "sin dato")
except Exception as e:
    skip("bloque online", f"{type(e).__name__}: {e}")

# ── RESUMEN ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"RESUMEN:  {_pass} PASS · {_fail} FAIL · {_skip} SKIP")
print("=" * 70)
sys.exit(1 if _fail else 0)
