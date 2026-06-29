# 📊 INVESTOR — Plan Maestro

**Gestor de patrimonio autónomo en Binance 24/7 · crecimiento direccional con techo de drawdown**
Dueño: Oscar Navarro · Planificación: 2026-06-28 · Estado: 🟡 DISEÑO (construcción desde 2026-06-29)

> Reusa el motor de ejecución/riesgo de **kepler** (probado en vivo) + detección de eventos, stops y
> dashboard de **opportunity_alert**. No se inventa infraestructura: se ensambla y se reorienta de
> market-neutral → crecimiento direccional.

---

## 1. 🎯 Visión

Un robot que hace crecer capital propio en Binance, **solo, 24/7**, llevando el dinero a lo que más crece
(semis/IA o cripto, lo que lidere), con un **tope duro de drawdown −30%**, que **sale ante caídas reales**
sin esperar al día siguiente, y que **explica cada movimiento con noticias resumidas por IA**.

A futuro: **proyecto público en GitHub** (portafolio de Oscar) que demuestre conocimiento en mercados,
cripto, sistemas e integración con IA — con una sección donde cualquiera puede **preguntarle a la IA**
sobre el mercado o cómo funciona el sistema.

**Principios honestos (fijos):**
| Principio | Significado |
|---|---|
| No hay "crecer sin caídas" | Meta = caídas más cortas y menos profundas, no cero |
| −30% es objetivo, no garantía | Un gap extremo puede romperlo; se gestiona, no se promete |
| Las noticias no predicen | Scoreboard real ~17% acierto direccional → la venta la jala el **precio**, no el titular |
| Nada a producción sin backtest | Regla de oro de kepler: el instinto sin números casi siempre se equivoca |

---

## 2. ✅ Decisiones tomadas (2026-06-28)

| Tema | Decisión |
|------|----------|
| Perfil | Agresivo, va al líder (semis/IA o cripto), **tope maxDD −30%** gobierna el sizing |
| Venue | **100% Binance** (cripto 24/7 + bStocks/Alpha tokenizados) |
| Autonomía | **Autónomo desde el inicio**, validado primero en demo |
| Capital | Arranque **$500**; subir tras **1 semana en demo sin errores** |
| Frecuencia | Rebalanceo diario; mover dinero **solo si cruza banda** (±5%); stops/breaker cada 15 min |
| Eventos 24/7 | **Cripto = canario**: si cae fuerte fuera de horario, recorta bStocks antes del gap |
| Elegibilidad activo | Que el token **exista en Binance**. Historial desde Alpaca, no Binance. Liquidez = salvaguarda de ejecución |
| Comunicación | Directo, tablas/emojis, siempre proponer siguiente paso, honesto con mitigación |

---

## 3. 🔍 Hallazgos verificados (2026-06-28)

**Binance — instrumentos**
| Producto | Detalle | Para nosotros |
|---|---|---|
| **bStocks** (12-jun-2026) | 1:1 respaldado, BEP-20 BNB Chain, **24/7**, precio por **oráculo** del subyacente | CRCLB, **MUB**(Micron), **NVDAB**, **SNDKB**(Sandisk), TSLAB → semis/IA = tesis. SPCXB candidato (SPCX IPO 12-jun) |
| **Binance Alpha** (Ondo) | ~10 tokens | AAPL, GOOGL, TSLA, NVDA, **QQQ** ETF |
| **Cripto** | majors 24/7, libro profundo | Motor + canario |

⚠️ **Límite honesto:** el bStock se peguea al subyacente por **oráculo** → con NYSE cerrado no se mueve y
**reabre con gap** (igual que eToro). El 24/7 real lo tiene **cripto**. Mitigación = canario cripto.
⚠️ bStocks **no para usuarios US** (Oscar en Perú — confirmar elegibilidad).

**Datos**
| Fuente | Qué | Dónde |
|---|---|---|
| Alpaca | Barras diarias acciones (subyacente) | `data.alpaca.markets/v2/stocks/bars` (ya en `pilot/momentum_signals.py`) |
| binance.vision | Cripto 1h OHLCV + funding | pipeline de kepler |
| Binance API | Lista de tokens listados (refresco diario) | exchangeInfo |

---

## 4. 🧩 Universo y bolsillos

| Bolsillo | Contenido | Rol | Motor |
|----------|-----------|-----|-------|
| 🪙 Cripto | BTC, ETH, majors | Motor 24/7 + **canario** de riesgo | Momentum XS + trend (kepler) |
| 📈 Acciones | bStocks semis/IA + Alpha (NVDA, QQQ) | Alto crecimiento (tesis) | Breakout + chandelier (opp_alert) |
| 🛡️ Defensivo | Binance Earn flexible + stable/cash | Refugio en risk-off | Destino del des-riesgo |

Universo **dinámico**: refresco diario de tokens en Binance con historial obtenible en Alpaca/binance.vision.
Allocator fija pesos-objetivo por régimen, modulados por el throttle de maxDD. Bandas ±5% anti-whipsaw.

---

## 5. 🛡️ Control de riesgo — techo −30% (3 capas)

Direccional = te comes los crashes. El control es **cuánto capital está en crecimiento vs defensivo**.

1. **Stop por posición** — chandelier `máx − N×ATR` (opp_alert). Corta el perdedor individual.
2. **Disyuntor escalonado** — adapta `kepler/circuit_breaker.py`, en heartbeat de 15 min (no 24h):

   | Drawdown desde pico | Crecimiento | Defensivo |
   |---|---|---|
   | 0–10% | 100% | 0% |
   | 10–18% | ~65% | ~35% |
   | 18–25% | ~35% | ~65% |
   | 25→30% | ~10% | ~90% |

3. **Throttle de exposición** — `leverage_for_maxdd_anchor()` (kepler/portfolio.py): fija maxDD, **deriva**
   el tamaño por bisección. **Spot, sin apalancar** (sin liquidación). Equity = **MTM**, nunca wallet.

---

## 6. 📰 Eventos + justificación por IA

- La **noticia** NO dispara venta → aprieta stops y baja exposición objetivo (dedo en gatillo).
  Detección + resumen reusa pipeline opp_alert (EDGAR/Finnhub/feeds + DeepSeek).
- La **venta la jala el precio** (breaker + chandelier, 15 min). Cripto = inmediato; bStocks = canario.
- **Cada cambio de cartera genera una justificación IA automática** ("vendí NVDAB porque el sector cayó
  −X% y rompió su chandelier; noticia asociada: …") → se guarda y se muestra en el dashboard. Es también
  la base de la futura **sección pública de noticias**.

---

## 7. 🏗️ Arquitectura (reuso vs nuevo)

```
investor/
├─ COPIAR ~tal cual de KEPLER:
│   orchestrator.py     loop heartbeat 15min + rebalance 24h
│   circuit_breaker.py  → adaptar a escalones −30%
│   portfolio.py        leverage_for_maxdd_anchor, vol_parity, metrics
│   db.py               auditoría + shadow_signal (sombras)
│   config.py           universo/fees/riesgo + modos DRY/DEMO/REAL
│   research/           harness: walk-forward purgado, costos reales, deflated Sharpe
│   alphas.py           sleeves momentum/trend (si universo encaja)
├─ ADAPTAR de KEPLER:
│   execution.py        PORTAR Futures(fapi) → SPOT(api.binance.com); sin set_leverage;
│                       mantener maker GTX + retry + _capital_aware_drop (clave a $500)
├─ ADAPTAR de OPPORTUNITY_ALERT:
│   chandelier/ATR stops · detección eventos + resumen IA (DeepSeek) ·
│   dashboard + ledger de aportes (vista "Mi Patrimonio")
├─ NUEVO (poco):
│   allocator.py        pesos por régimen + escalones + canario cripto
│   universe_binance.py refresco diario tokens + match historial Alpaca
│   ai_explain.py       justificación automática de cada cambio
│   sleeves/            crypto · equities(bStocks+Alpha) · defensive(Earn)
```

---

## 8. ⚙️ Robustez — qué hace que NO se caiga (lo crítico)

Estos patrones ya salvaron cuentas en kepler/opp_alert. Son requisito, no extra.

### 8.1 Anti-errores en ejecución
| Patrón | Regla | Origen |
|---|---|---|
| 🚫 Balance ilegible → **omitir ciclo** | "Mejor un hueco que una curva falsa". Nunca operar con valor falso | kepler |
| 🔁 **Idempotencia** | `if last_run_date == hoy: return` + dedup de órdenes/noticias | ambos |
| 🎯 **Maker GTX post-only** | No cruza el libro → ~1 bps slippage; órdenes a mercado matarían el maxDD | kepler |
| ✂️ **Capital-aware drop** | Si capital chico, dropea patas < min-notional y re-neutraliza | kepler |
| 🩺 **Sanity gates pre-trade** | Datos frescos, concentración ≤ tope, exposición ≤ throttle → si falla, no opera | ambos |
| 🔌 **Circuit breaker eToro/exchange** | N fallos consecutivos → pausa 30 min (no spamear API caída) | opp_alert |

### 8.2 Vigilancia (que no se quede mudo)
| Mecanismo | Qué hace |
|---|---|
| ❤️ Heartbeat watchdog | Detecta threads muertos y los reinicia; resumen periódico |
| 🚨 Alarma de escalada | "Rebalanceo en riesgo: N omisiones, M min sin operar" → push (ntfy/WhatsApp) |
| 📓 DB = fuente de verdad | SQLite (WAL + busy_timeout) loguea cada señal/orden/snapshot/evento → auditable |
| 👻 Shadow signals | Loguea pesos de sleeves candidatos sin operarlos → validación OOS honesta |

### 8.3 Actualizar SIN interrumpir (hot-update seguro)
El miedo de Oscar: desplegar código y romper un rebalanceo a medias. Solución por diseño:

| Garantía | Cómo |
|---|---|
| ✅ Estado **persistido** fuera del proceso | Todo en SQLite/JSON → reiniciar reanuda exacto, sin perder posición |
| ✅ Rebalanceo **atómico por ciclo** | Un ciclo termina o se descarta entero; nunca queda a mitad |
| ✅ **Graceful shutdown** | Señal de parada = termina el ciclo actual, NO arranca otro, luego sale |
| ✅ Deploy **entre ciclos** | `systemctl restart` toma efecto en el próximo heartbeat, no interrumpe cálculo en curso |
| ✅ **Config hot-reload** | Cambios de parámetros (umbrales, universo) se releen sin reiniciar el proceso |
| ✅ **Lock de instancia única** | Un solo orchestrator activo (evita doble ejecución de órdenes) |
| ✅ Modo **DRY_RUN/DEMO** | Probar el código nuevo sin tocar dinero antes de promover a real |

### 8.4 Disciplina de validación (que el edge sea real)
Walk-forward purgado + embargo · costos reales (maker/taker + slippage por ADV) · Leave-One-Out ·
deflated Sharpe · **gates pre-registrados** (reglas de continuar/parar ANTES de ver datos) ·
gate de madurez (no publicar Sharpe con N<30 días).

---

## 9. 🌐 Capa pública / portafolio (futuro)

| Componente | Estado | Nota |
|---|---|---|
| Dashboard read-only público | Futuro | Equity, mezcla, justificaciones IA — SIN claves ni órdenes |
| Sección de noticias + por qué del cambio | Futuro | Alimentada por `ai_explain.py` |
| 🤖 "Pregúntale a la IA" | Futuro | Chat que explica el mercado / cómo funciona el sistema |
| Repo GitHub | Futuro | Separar **núcleo público** (engine, research, docs) de **secretos** (claves, capital real) desde el día 1 → diseño con esto en mente |

> 🔐 Regla desde ya: claves y estado de cuenta real **nunca** en el repo; `.env` + `.gitignore` estrictos.
> Así la apertura pública futura no requiere reescribir nada.

---

## 10. 🗺️ Fases (con entregable y decisión por fase)

Al cerrar **cada fase** entrego: ✅ qué se hizo · ➡️ qué sigue · 🔵 qué decides/observas.

| Fase | Objetivo | Gate para avanzar |
|------|----------|-------------------|
| **F0 · Verificar** (día 1) | API spot coloca órdenes en bStocks · profundidad libro · elegibilidad Perú · conectar Alpaca+binance.vision | Confirmado que se puede operar bStocks por API |
| **F1 · Ensamblar** (sem 1) | Portar execution→spot · allocator + universo dinámico · ledger + vista patrimonio · correr en DRY_RUN | Sistema corre end-to-end en DRY sin errores |
| **F2 · Backtest/valida** (sem 1-2) | Validar sleeves direccionales + escalones −30% con harness kepler (costos reales) | Edge sobrevive costos y walk-forward |
| **F3 · Demo** (sem 2) | Autónomo en testnet/demo Binance | **1 semana sin errores de ejecución** |
| **F4 · Real chico** (sem 3+) | `DRY_RUN=false` con **$500** | Comportamiento real ≈ demo; escalar gradual |
| **F5 · Capa pública** (futuro) | Dashboard público + noticias IA + "pregúntale a la IA" + GitHub | Track real verificable acumulado |

---

## 11. 📋 Pendientes F0 y decisiones abiertas

**Verificar (F0):**
1. API spot Binance → ¿coloca órdenes en `NVDAB/USDT` etc. programáticamente?
2. Profundidad/liquidez order-books bStock (2 semanas de vida).
3. Elegibilidad/KYC región Perú para bStocks/Alpha.
4. Precio bStock fuera de horario (¿descuenta 24/7 o congelado?).
5. Cobertura Alpaca del subyacente de cada token (incl. CRCL, SPCX recién listados).

**Decidir (al construir):**
- N×ATR del chandelier y % exactos de escalones (afinar con backtest).
- Defensivo: ¿solo Earn/stable o sumar token tipo TLT si existe en Binance?
- ¿Motor IA para justificaciones: DeepSeek (ya integrado) u otro?

---
*Fuentes: coindesk/fortune/morningstar/prnewswire (bStocks 12-jun-2026), nasdaq/cnbc (SPCX IPO 12-jun-2026). Datos: Alpaca (opportunity_alert/pilot/momentum_signals.py). Robustez: kepler/LESSONS.md, opportunity_alert/CLAUDE.md.*
