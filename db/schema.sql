-- ============================================================================
-- investor — Esquema de base de datos (SQLite)
-- Fuente única de verdad: estado + auditoría + logs de validación.
-- Diseño heredado de kepler (WAL, MTM, sombras) + ledger de aportes (TWR).
-- Fecha: 2026-06-29 (F1). Todos los timestamps en UTC, ISO-8601 (texto).
-- ============================================================================

-- --- PRAGMAs de robustez (kepler los probó en vivo, 0 bugs/18d) -------------
PRAGMA journal_mode = WAL;        -- lecturas no bloquean escrituras; resiste cortes
PRAGMA busy_timeout = 5000;       -- reintenta 5s en vez de fallar por lock
PRAGMA foreign_keys = ON;
PRAGMA synchronous  = NORMAL;     -- durable con WAL, sin el coste de FULL

-- ============================================================================
-- 1. CONFIGURACIÓN Y UNIVERSO
-- ============================================================================

-- Config hot-reload: el orquestador relee esto entre ciclos (sin reiniciar).
CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,            -- guardado como texto; el código castea
    updated_at  TEXT NOT NULL,
    note        TEXT
);

-- ============================================================================
-- 3. CARTERA: OBJETIVO, POSICIONES, ÓRDENES, EQUITY
-- ============================================================================

-- Pesos objetivo por ciclo (salida del allocator + disyuntor de DD).
CREATE TABLE IF NOT EXISTS target_weight (
    rebalance_id TEXT NOT NULL,           -- agrupa un ciclo de rebalanceo (idempotencia)
    ts           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    weight       REAL NOT NULL,
    reason       TEXT,                     -- por qué este peso (régimen, escalón DD...)
    PRIMARY KEY (rebalance_id, symbol)
);

-- Posiciones actuales (estado vivo).
CREATE TABLE IF NOT EXISTS position (
    symbol        TEXT PRIMARY KEY,
    qty           REAL NOT NULL DEFAULT 0,
    avg_price     REAL,
    chandelier_stop REAL,                  -- stop por volatilidad vigente
    updated_at    TEXT NOT NULL
);

-- Órdenes. client_order_id = clave de IDEMPOTENCIA (no duplicar al reintentar).
CREATE TABLE IF NOT EXISTS order_log (
    client_order_id TEXT PRIMARY KEY,      -- generado por nosotros, determinista
    exchange_order_id TEXT,                -- id que devuelve Binance
    rebalance_id    TEXT,
    ts_created      TEXT NOT NULL,
    ts_updated      TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,         -- 'BUY' | 'SELL'
    type            TEXT NOT NULL,         -- 'LIMIT_MAKER' | 'MARKET' | 'STOP_LOSS_LIMIT' ...
    qty             REAL NOT NULL,
    price           REAL,
    status          TEXT NOT NULL,         -- 'NEW'|'PARTIALLY_FILLED'|'FILLED'|'CANCELED'|'REJECTED'|'EXPIRED'
    filled_qty      REAL DEFAULT 0,
    avg_fill_price  REAL,
    fee             REAL DEFAULT 0,
    fee_asset       TEXT,
    retries         INTEGER DEFAULT 0,
    mode            TEXT NOT NULL,         -- 'DRY_RUN'|'DEMO'|'REAL' (nunca confundir entornos)
    raw_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_order_ts ON order_log(ts_created);
CREATE INDEX IF NOT EXISTS idx_order_status ON order_log(status);

-- Historia de equity = MTM (mark-to-market), NUNCA wallet (kepler §1.4).
-- Sin esto el maxDD intradía se subestima y el tope −30% miente.
CREATE TABLE IF NOT EXISTS equity_history (
    ts            TEXT PRIMARY KEY,
    equity_mtm    REAL NOT NULL,           -- valor total marcado a mercado
    cash          REAL NOT NULL,
    peak          REAL NOT NULL,           -- pico para el disyuntor de DD
    drawdown      REAL NOT NULL,           -- (equity/peak)-1
    exposure      REAL NOT NULL,           -- % en activos de crecimiento (escalón DD)
    regime        TEXT,
    source        TEXT NOT NULL DEFAULT 'mtm'
);

-- ============================================================================
-- 5. EVENTOS, NOTICIAS Y JUSTIFICACIÓN IA
-- ============================================================================

-- Justificación IA de cada cambio (para dashboard público y auditoría).
CREATE TABLE IF NOT EXISTS ai_explanation (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    rebalance_id  TEXT,                    -- enlaza con el rebalanceo explicado
    summary       TEXT NOT NULL,           -- texto IA: por qué cambió la cartera
    model         TEXT,                    -- p.ej. 'deepseek'
    inputs_json   TEXT,                    -- datos que recibió (reproducibilidad)
    published     INTEGER NOT NULL DEFAULT 0  -- visible en la capa pública
);

-- ============================================================================
-- 6. LOGS DE VALIDACIÓN  (lo que Oscar pidió expresamente)
--    Objetivo: validar funcionamiento, detectar errores y señales a revisar.
-- ============================================================================

-- Log estructurado de aplicación. Niveles para filtrar rápido.
CREATE TABLE IF NOT EXISTS app_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,               -- 'DEBUG'|'INFO'|'WARN'|'ERROR'|'CRITICAL'
    component TEXT NOT NULL,               -- 'fetch'|'allocator'|'execution'|'cb'|'orchestrator'...
    message   TEXT NOT NULL,
    context_json TEXT                      -- datos del momento (para reproducir el caso)
);
CREATE INDEX IF NOT EXISTS idx_log_ts    ON app_log(ts);
CREATE INDEX IF NOT EXISTS idx_log_level ON app_log(level);

-- Errores con traza completa (separados para revisión rápida y alertas).
CREATE TABLE IF NOT EXISTS error_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    component  TEXT NOT NULL,
    error_type TEXT,
    message    TEXT NOT NULL,
    traceback  TEXT,
    resolved   INTEGER NOT NULL DEFAULT 0,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_error_ts ON error_log(ts);

-- Cola de "señales que valen la pena revisar" (anomalías, no errores).
-- El robot marca aquí lo raro; tú lo revisas en el dashboard.
CREATE TABLE IF NOT EXISTS review_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,              -- 'spread_alto'|'gap'|'señal_fuerte'|'cb_activado'|'omision_ciclo'
    symbol     TEXT,
    detail     TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'info',
    reviewed   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_review_reviewed ON review_queue(reviewed);

-- Latido del orquestador (watchdog). Si se queda mudo, lo detectamos.
-- Patrón kepler: "balance ilegible → omite ciclo, no opera con valor falso".
CREATE TABLE IF NOT EXISTS heartbeat (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    cycle_type   TEXT NOT NULL,            -- 'heartbeat_15m' | 'rebalance_24h'
    status       TEXT NOT NULL,            -- 'ok' | 'skipped' | 'error'
    skip_reason  TEXT,                     -- p.ej. 'balance_ilegible'
    duration_ms  INTEGER,
    equity_mtm   REAL
);
CREATE INDEX IF NOT EXISTS idx_heartbeat_ts ON heartbeat(ts);

-- ============================================================================
-- 7. VISTAS DE CONVENIENCIA (lectura rápida para dashboard / validación)
-- ============================================================================

-- Últimos errores sin resolver.
CREATE VIEW IF NOT EXISTS v_open_errors AS
SELECT ts, component, error_type, message
FROM error_log WHERE resolved = 0 ORDER BY ts DESC;

-- Cosas pendientes de revisar.
CREATE VIEW IF NOT EXISTS v_pending_review AS
SELECT ts, kind, symbol, severity, detail
FROM review_queue WHERE reviewed = 0 ORDER BY ts DESC;

