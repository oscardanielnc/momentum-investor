"""
investor — Universo POINT-IN-TIME del S&P 500 (helpers para backtest_v10_pit).

Provee:
  - load_ohlc()      → paneles O/H/L/C (fechas × tickers) ENMASCARADOS por ventana de membresía
                       (con buffer de 200d previos para el lookback de momentum). La máscara evita
                       el veneno de los tickers REUSADOS por otra empresa tras un delisting.
  - members_asof(d)  → set de tickers miembros del índice en la fecha d (point-in-time).
  - SECTOR           → mapeo ticker → sector (Wikipedia GICS actual + curado manual de deslistados;
                       semis separado de tech, como en la estrategia live).

Fuente membresía: fja05680/sp500 (histórico 1996+, incluye deslistados). Precios: pit_fetch.py.
"""
import json, os
import numpy as np, pandas as pd

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_pit")
BUFFER_DAYS = 200          # días de historia previos a la inclusión permitidos (para el lookback)

# ── Sectores: GICS actual (Wikipedia) + curado manual de deslistados/renombrados ─────────────
with open(os.path.join(CACHE, "sectors_wiki.json"), encoding="utf-8") as f:
    SECTOR = json.load(f)

SECTOR_DELISTED = {
 "AAL":"industrial","AAP":"consumo_disc","ABC":"salud","ABMD":"salud","ADS":"finanzas",
 "AET":"salud","AGN":"salud","AIV":"realestate","ALK":"industrial","ALXN":"salud",
 "AMG":"finanzas","AMTM":"industrial","ANDV":"energia","ANSS":"tech","ANTM":"salud",
 "APC":"energia","ARNC":"industrial","ATVI":"comm","AYI":"industrial","BBT":"finanzas",
 "BBWI":"consumo_disc","BHF":"finanzas","BHGE":"energia","BIO":"salud","BK":"finanzas",
 "BLL":"materiales","BWA":"consumo_disc","CA":"tech","CAG":"consumo_bas","CBS":"comm",
 "CDAY":"tech","CE":"materiales","CELG":"salud","CERN":"salud","CMA":"finanzas",
 "COG":"energia","COL":"industrial","COTY":"consumo_bas","CPB":"consumo_bas",
 "CPRI":"consumo_disc","CTL":"comm","CTLT":"salud","CTRA":"energia","CTXS":"tech",
 "CXO":"energia","CZR":"consumo_disc","DAY":"tech","DFS":"finanzas","DISCA":"comm",
 "DISCK":"comm","DISH":"comm","DRE":"realestate","DWDP":"materiales","DXC":"tech",
 "EMN":"materiales","ENPH":"tech","EPAM":"tech","ESRX":"salud","ETFC":"finanzas",
 "ETSY":"consumo_disc","EVHC":"salud","FB":"comm","FBHS":"industrial","FI":"finanzas",
 "FL":"consumo_disc","FLIR":"tech","FLR":"industrial","FLS":"industrial","FLT":"finanzas",
 "FMC":"materiales","FRC":"finanzas","FTI":"energia","GGP":"realestate","GPS":"consumo_disc",
 "GT":"consumo_disc","HBI":"consumo_disc","HCP":"realestate","HES":"energia","HFC":"energia",
 "HOG":"consumo_disc","HOLX":"salud","HP":"energia","HRB":"consumo_disc","HRS":"industrial",
 "ILMN":"salud","INFO":"industrial","IPG":"comm","IPGP":"tech","JEC":"industrial",
 "JEF":"finanzas","JNPR":"tech","JWN":"consumo_disc","K":"consumo_bas","KMX":"consumo_disc",
 "KORS":"consumo_disc","KSS":"consumo_disc","KSU":"industrial","LB":"consumo_disc",
 "LEG":"consumo_disc","LKQ":"consumo_disc","LLL":"industrial","LNC":"finanzas","LUMN":"comm",
 "LW":"consumo_bas","M":"consumo_disc","MAC":"realestate","MAT":"consumo_disc",
 "MHK":"consumo_disc","MKTX":"finanzas","MMC":"finanzas","MOH":"salud","MON":"materiales",
 "MRO":"energia","MTCH":"comm","MXIM":"semis","MYL":"salud","NAVI":"finanzas","NBL":"energia",
 "NFX":"energia","NKTR":"salud","NLOK":"tech","NLSN":"industrial","NOV":"energia",
 "NWL":"consumo_disc","OGN":"salud","PARA":"comm","PAYC":"tech","PBCT":"finanzas",
 "PEAK":"realestate","PENN":"consumo_disc","PKI":"salud","POOL":"consumo_disc","PRGO":"salud",
 "PVH":"consumo_disc","PX":"materiales","PXD":"energia","QRVO":"semis","RE":"finanzas",
 "RHI":"industrial","RHT":"tech","RRC":"energia","RTN":"industrial","SATS":"comm",
 "SBNY":"finanzas","SCG":"utilities","SEDG":"tech","SEE":"materiales","SIVB":"finanzas",
 "SLG":"realestate","SOLS":"materiales","SRCL":"industrial","STI":"finanzas","SYMC":"tech",
 "TFX":"salud","TIF":"consumo_disc","TMK":"finanzas","TRIP":"comm","TSS":"finanzas",
 "TWTR":"comm","TWX":"comm","UA":"consumo_disc","UAA":"consumo_disc","UNM":"finanzas",
 "UTX":"industrial","VAR":"salud","VFC":"consumo_disc","VIAB":"comm","VIAC":"comm",
 "VNO":"realestate","VNT":"tech","WBA":"consumo_bas","WCG":"salud","WHR":"consumo_disc",
 "WLTW":"finanzas","WRK":"materiales","WU":"finanzas","WYND":"consumo_disc","XEC":"energia",
 "XL":"finanzas","XLNX":"semis","XRAY":"salud","XRX":"tech","ZION":"finanzas",
}
SECTOR.update({k: v for k, v in SECTOR_DELISTED.items() if k not in SECTOR})


def _membership_windows():
    """{ticker: [(start,end), ...]} desde sp500_ticker_start_end.csv (puede haber varias ventanas)."""
    se = pd.read_csv(os.path.join(CACHE, "sp500_start_end.csv"), parse_dates=["start_date", "end_date"])
    se["end_date"] = se["end_date"].fillna(pd.Timestamp("2099-01-01"))
    win = {}
    for _, r in se.iterrows():
        win.setdefault(r["ticker"], []).append((r["start_date"], r["end_date"]))
    return win


def load_ohlc(verbose=True):
    """Paneles (O,H,L,C) fechas×tickers, enmascarados a las ventanas de membresía + buffer."""
    win = _membership_windows()
    frames = {"open": {}, "high": {}, "low": {}, "close": {}}
    n = 0
    for f in sorted(os.listdir(CACHE)):
        if not f.endswith(".parquet"):
            continue
        sym = f[:-8]
        if sym not in win:
            continue
        df = pd.read_parquet(os.path.join(CACHE, f))
        mask = pd.Series(False, index=df.index)
        for s, e in win[sym]:
            mask |= (df.index >= s - pd.Timedelta(days=BUFFER_DAYS)) & (df.index <= e)
        df = df[mask]
        if len(df) < 30:
            continue
        for col in frames:
            frames[col][sym] = df[col]
        n += 1
    out = {col: pd.DataFrame(d).sort_index() for col, d in frames.items()}
    if verbose:
        C = out["close"]
        print(f"panel PIT: {C.shape[1]} tickers · {C.index[0].date()}→{C.index[-1].date()} · {len(C)} días")
    return out["open"], out["high"], out["low"], out["close"]


class Membership:
    """members_asof(d) → frozenset de tickers miembros en la fecha d (point-in-time)."""
    def __init__(self):
        rows = pd.read_csv(os.path.join(CACHE, "sp500_hist.csv"), parse_dates=["date"])
        rows = rows.sort_values("date")
        self.dates = rows["date"].to_numpy()
        self.sets = [frozenset(t.strip() for t in s.split(",")) for s in rows["tickers"]]

    def asof(self, d):
        i = np.searchsorted(self.dates, np.datetime64(d), side="right") - 1
        return self.sets[max(i, 0)]
