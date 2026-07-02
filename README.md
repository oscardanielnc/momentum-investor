# investor 📈🔬

**Robot de inversión + laboratorio de backtesting honesto.** Este proyecto construyó un robot autónomo de *momentum* multi-sector sobre [Alpaca](https://alpaca.markets), lo desplegó 24/7… y luego **lo auditó con el rigor suficiente para descubrir que su propio backtest estaba inflado por sesgo de supervivencia**. La historia completa — construir, validar, dudar, re-testear y pivotar — está en el código de `research/`.

> ⚠️ **Aviso:** proyecto personal y educativo. **No es asesoría financiera.** El trading conlleva riesgo de pérdida. Los resultados de backtest no garantizan resultados futuros.

---

## 🎯 La lección central del proyecto

La estrategia original (top-5 momentum sobre 36 acciones líderes, trailing stops, tope por sector) backtesteaba **36% CAGR / Calmar 1.2**. Una auditoría con **universo point-in-time** — los 681 miembros históricos del S&P 500, incluidos los que quebraron o fueron deslistados (SIVB, FRC, TWTR…) — reveló la verdad:

| Config (2018–2026, mismos datos y motor) | CAGR | maxDD | Calmar |
|---|---|---|---|
| Universo de 36 "líderes" elegidos en 2026 | 29.2% | −30% | 0.98 |
| **Universo honesto (S&P 500 point-in-time)** | **14.4%** | **−35%** | **0.41** |
| QQQ buy & hold | 18.8% | −36% | 0.53 |

**+15 puntos de CAGR eran el universo, no la estrategia.** Elegir hoy las acciones que backtestear es mirar el examen con las respuestas. La rotación de momentum, medida sin trampa, no le gana a comprar QQQ.

## 🔬 Qué más se testeó (todo reproducible en `research/`)

| Test | Script | Veredicto |
|---|---|---|
| Momentum multi-sector, universo PIT + mecánica fiel (stop intradía, ejecución t+1) | `backtest_v10_pit.py` | ❌ No supera a QQQ |
| Rotación de sectores (11 SPDR) y países (incl. China/Asia), 1999–2026 total return | `backtest_v11_etf.py` | ❌ Pierde en toda ventana |
| Value "calidad castigada" con fundamentales SEC EDGAR point-in-time | `backtest_v13_value.py` | ❌ −0.6% CAGR (falling knives) |
| **Overlay de régimen SMA200 sobre QQQ + satélite apalancado (QLD)** | `backtest_v11/v12` | ✅ **Única familia que sobrevive walk-forward** |

El ganador — validado con walk-forward anclado 2008–2026 (el sistema nunca ve el futuro) y robusto en las 20 celdas de su grilla de parámetros: **80% QQQ + 20% QLD mientras QQQ cierre sobre su SMA200 (banda ±1%); 100% caja debajo.** Mantiene el drawdown histórico en −30% (2008 incluido) donde QQQ solo se hunde −53%, con retorno igual o mejor en régimen alcista.

## 🖥️ Qué corre hoy

- **Dashboard "Mi Patrimonio"** (FastAPI + Chart.js, hora de Lima): sección **"Mi cartera manual QQQ/QLD"** con la señal SMA200 en vivo, niveles exactos de disparo, gráfico y **calculadora de órdenes** (ingresas tus posiciones → te dice qué comprar/vender hoy). El endpoint `/api/estrategia` calcula todo server-side con precios ajustados.
- **Robot momentum en PAPER** como laboratorio: heartbeat 15 min, rebalanceo con banda de histéresis, trailing stops nativos con reconciliación automática, circuit breaker con reanudación automática, trazabilidad completa en SQLite. No opera dinero real — los datos que registra alimentan la investigación.

## 🏗️ Arquitectura

```
engine/          allocator (señal) · execution_alpaca (órdenes/stops) · orchestrator (loop+CB) · db (SQLite WAL)
dashboard/       FastAPI + frontend responsive · /api/estrategia (señal QQQ/QLD manual)
research/        los 13 backtests: momentum, PIT, overlays, rotaciones, walk-forward, value/EDGAR
  pit_fetch.py   descarga Databento de los 681 miembros históricos del S&P 500 (sin survivorship)
  pit_universe.py  membresía point-in-time + máscaras anti ticker-reuse + sectores GICS
tests/           smoke tests del motor (42 asserts)
```

Patrones de robustez: equity mark-to-market, "balance ilegible → omite ciclo", idempotencia de órdenes, single-instance lock, reconciliación de stops por heartbeat, modos `DRY_RUN/PAPER/REAL`.

## 📊 Disciplina de validación (lo que este repo demuestra)

1. **Universo point-in-time o nada** — backtestear sobre los ganadores de hoy infla el CAGR ~15 pp.
2. **Mecánica fiel al robot** — stop intradía vs cierres, dividendos, ejecución al día siguiente: cada atajo sesga.
3. **Walk-forward y pre-registro** — los parámetros se eligen con el pasado y se juzgan en el futuro; las reglas del test se escriben antes de ver los resultados.
4. **Benchmark pasivo siempre** — toda estrategia se compara contra QQQ/SPY buy & hold en la misma ventana; la mayoría pierde.

## 🚀 Cómo correr

```bash
pip install -r requirements.txt
cp .env.example .env                      # ALPACA_API_KEY / ALPACA_SECRET_KEY (paper)
python engine/orchestrator.py --loop      # robot (DRY_RUN por defecto)
python dashboard/server.py                # dashboard → http://127.0.0.1:8080
python research/backtest_v10_pit.py       # reproducir la auditoría (requiere DATABENTO_API_KEY)
```

## 🛠️ Stack

Python · pandas/numpy · FastAPI · SQLite (WAL) · Chart.js · Alpaca API · Databento (S&P 500 histórico) · SEC EDGAR XBRL (fundamentales point-in-time) · DeepSeek (justificaciones IA).
