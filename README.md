# investor 📈🤖

**Robot autónomo de inversión multi-sector** que rota su capital hacia los líderes de *momentum* en la bolsa de EE.UU., con control de riesgo por *trailing stops*, tope de concentración por sector y un disyuntor de drawdown — todo operado vía API en [Alpaca](https://alpaca.markets), con un dashboard en vivo.

> ⚠️ **Aviso:** proyecto personal y educativo. **No es asesoría financiera.** El trading conlleva riesgo de pérdida. Los resultados de backtest no garantizan resultados futuros.

---

## 🎯 Qué hace

Cada día evalúa un universo de **36 acciones líderes de 7 sectores** (semiconductores, software, energía, salud, finanzas, consumo, comunicación) y mantiene las **5 con mejor momentum ajustado por riesgo**, equiponderadas. Si un sector se debilita, el capital **rota solo** hacia el sector no correlacionado que está subiendo. Cada posición lleva un *trailing stop* que la vende "a tiempo" si se da vuelta.

El defensivo no es caja muerta: es **estar en el sector correcto + saber salir**. El colchón de seguridad vive fuera del robot (cuenta de ahorro), no en activos de bajo rendimiento.

## 🧠 La estrategia (validada con datos)

| Componente | Regla |
|---|---|
| **Universo** | 36 acciones, 7 sectores |
| **Selección** | Top-5 por momentum 90d ajustado por volatilidad (≈ Sharpe del activo) |
| **Pesos** | Equiponderado (20% c/u), siempre invertido |
| **Salir a tiempo** | Trailing stop 20% por posición (orden nativa en el exchange) |
| **Anti-concentración** | Tope 80% por sector (máx 4 de 5) |
| **Cadencia** | Heartbeat 15 min · rebalanceo diario por cambio de líderes · disyuntor intradía |
| **Tope de pérdida** | Drawdown gestionado + dimensionamiento del capital a riesgo |

**Validación (2018–2026, costos incluidos):** ~36% CAGR, Sharpe ~1.2, maxDD ~−30%. Confirmado **out-of-sample** por *walk-forward* (los parámetros elegidos en 2018–2022 también rindieron en 2023–2026). El *walk-forward* además **descartó un sobreajuste** (un trailing más ajustado se veía mejor in-sample pero fallaba fuera).

**Honestidad:** los retornos recientes (toro de IA) **no son sostenibles**; el número de ciclo completo es menor. Los *trailing stops* protegen en crashes pero sufren *whipsaw* en mercados laterales y no cubren *gaps* nocturnos. El backtest no alcanza a 2008 (sin datos públicos). Nada va a real sin pasar por demo en *paper*.

## 🏗️ Arquitectura

```
allocator.py     → cerebro: precios → momentum → top-5 con tope de sector
execution_alpaca → manos: órdenes notional + trailing stops nativos (Alpaca)
db.py            → memoria: SQLite (WAL) estado + auditoría + logs
orchestrator.py  → director: loop heartbeat/diario/mensual + circuit breaker
ai_explain.py    → justificación en prosa (DeepSeek) por cada redistribución
dashboard/       → FastAPI + frontend responsive (hora de Lima)
research/        → backtests, walk-forward, estudios de correlación
```

Patrones de robustez: equity *mark-to-market* (no wallet), "balance ilegible → omite ciclo" (nunca opera con un valor falso), idempotencia de órdenes, *single-instance lock*, modos `DRY_RUN/PAPER/REAL`, *heartbeat watchdog*.

## 🖥️ Dashboard "Mi Patrimonio"

Patrimonio, curva de equity, cartera con sector y P&L, drawdown vs tope, **justificación en lenguaje natural de cada cambio**, diversificación por sector, historial y salud del sistema. Responsive (móvil) y en **hora de Lima**.

## 🚀 Cómo correr

```bash
pip install -r requirements.txt
cp .env.example .env          # rellena ALPACA_API_KEY / ALPACA_SECRET_KEY (paper)
python engine/orchestrator.py --loop     # robot (DRY_RUN por defecto)
python dashboard/server.py               # dashboard → http://127.0.0.1:8000
```

Modos por `.env`: `DRY_RUN` (solo loguea) → `PAPER` (práctica) → `REAL` (tras validar en demo).

## 🛠️ Stack

Python · pandas/numpy · FastAPI · SQLite (WAL) · Chart.js · DeepSeek (justificación) · Alpaca API · Databento (datos históricos para research).

## 📊 Disciplina de validación

Regla de oro: **nada a producción sin backtest que lo confirme.** *Walk-forward* con separación temporal in/out-of-sample, costos de ejecución modelados, barridos de robustez de parámetros, y *gates* pre-registrados. La meta es crecimiento **ajustado por riesgo**, no el retorno más vistoso.
