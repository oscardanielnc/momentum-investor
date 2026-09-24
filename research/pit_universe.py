"""
Point-in-time S&P 500 universe (helpers for backtest_v10_pit and backtest_v13_value).

Provides:
  - load_ohlc()           O/H/L/C panels (dates x tickers) MASKED to each ticker's membership
                          windows, plus a 200-day buffer before inclusion for the momentum
                          lookback. The mask prevents a ticker that was later reused by a
                          different company from leaking that company's prices into the test.
  - Membership().asof(d)  frozenset of index members on date d (point-in-time).
  - SECTOR                ticker -> sector (current GICS sectors from Wikipedia plus a manual
                          mapping for delisted or renamed tickers; semiconductors are split from
                          tech, as in the live strategy).

Membership source: fja05680/sp500 (history since 1996, delisted members included).
Prices: pit_fetch.py.
"""
import json, os
import numpy as np, pandas as pd

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data_pit")
BUFFER_DAYS = 200          # days of history allowed before inclusion (for the lookback)

# ── Sectors: current GICS (Wikipedia) + manual mapping for delisted/renamed tickers ──────────
with open(os.path.join(CACHE, "sectors_wiki.json"), encoding="utf-8") as f:
    SECTOR = json.load(f)

SECTOR_DELISTED = {
 "AAL":"industrials","AAP":"consumer_disc","ABC":"health","ABMD":"health","ADS":"financials",
 "AET":"health","AGN":"health","AIV":"real_estate","ALK":"industrials","ALXN":"health",
 "AMG":"financials","AMTM":"industrials","ANDV":"energy","ANSS":"tech","ANTM":"health",
 "APC":"energy","ARNC":"industrials","ATVI":"comm","AYI":"industrials","BBT":"financials",
 "BBWI":"consumer_disc","BHF":"financials","BHGE":"energy","BIO":"health","BK":"financials",
 "BLL":"materials","BWA":"consumer_disc","CA":"tech","CAG":"consumer_staples","CBS":"comm",
 "CDAY":"tech","CE":"materials","CELG":"health","CERN":"health","CMA":"financials",
 "COG":"energy","COL":"industrials","COTY":"consumer_staples","CPB":"consumer_staples",
 "CPRI":"consumer_disc","CTL":"comm","CTLT":"health","CTRA":"energy","CTXS":"tech",
 "CXO":"energy","CZR":"consumer_disc","DAY":"tech","DFS":"financials","DISCA":"comm",
 "DISCK":"comm","DISH":"comm","DRE":"real_estate","DWDP":"materials","DXC":"tech",
 "EMN":"materials","ENPH":"tech","EPAM":"tech","ESRX":"health","ETFC":"financials",
 "ETSY":"consumer_disc","EVHC":"health","FB":"comm","FBHS":"industrials","FI":"financials",
 "FL":"consumer_disc","FLIR":"tech","FLR":"industrials","FLS":"industrials","FLT":"financials",
 "FMC":"materials","FRC":"financials","FTI":"energy","GGP":"real_estate","GPS":"consumer_disc",
 "GT":"consumer_disc","HBI":"consumer_disc","HCP":"real_estate","HES":"energy","HFC":"energy",
 "HOG":"consumer_disc","HOLX":"health","HP":"energy","HRB":"consumer_disc","HRS":"industrials",
 "ILMN":"health","INFO":"industrials","IPG":"comm","IPGP":"tech","JEC":"industrials",
 "JEF":"financials","JNPR":"tech","JWN":"consumer_disc","K":"consumer_staples","KMX":"consumer_disc",
 "KORS":"consumer_disc","KSS":"consumer_disc","KSU":"industrials","LB":"consumer_disc",
 "LEG":"consumer_disc","LKQ":"consumer_disc","LLL":"industrials","LNC":"financials","LUMN":"comm",
 "LW":"consumer_staples","M":"consumer_disc","MAC":"real_estate","MAT":"consumer_disc",
 "MHK":"consumer_disc","MKTX":"financials","MMC":"financials","MOH":"health","MON":"materials",
 "MRO":"energy","MTCH":"comm","MXIM":"semis","MYL":"health","NAVI":"financials","NBL":"energy",
 "NFX":"energy","NKTR":"health","NLOK":"tech","NLSN":"industrials","NOV":"energy",
 "NWL":"consumer_disc","OGN":"health","PARA":"comm","PAYC":"tech","PBCT":"financials",
 "PEAK":"real_estate","PENN":"consumer_disc","PKI":"health","POOL":"consumer_disc","PRGO":"health",
 "PVH":"consumer_disc","PX":"materials","PXD":"energy","QRVO":"semis","RE":"financials",
 "RHI":"industrials","RHT":"tech","RRC":"energy","RTN":"industrials","SATS":"comm",
 "SBNY":"financials","SCG":"utilities","SEDG":"tech","SEE":"materials","SIVB":"financials",
 "SLG":"real_estate","SOLS":"materials","SRCL":"industrials","STI":"financials","SYMC":"tech",
 "TFX":"health","TIF":"consumer_disc","TMK":"financials","TRIP":"comm","TSS":"financials",
 "TWTR":"comm","TWX":"comm","UA":"consumer_disc","UAA":"consumer_disc","UNM":"financials",
 "UTX":"industrials","VAR":"health","VFC":"consumer_disc","VIAB":"comm","VIAC":"comm",
 "VNO":"real_estate","VNT":"tech","WBA":"consumer_staples","WCG":"health","WHR":"consumer_disc",
 "WLTW":"financials","WRK":"materials","WU":"financials","WYND":"consumer_disc","XEC":"energy",
 "XL":"financials","XLNX":"semis","XRAY":"health","XRX":"tech","ZION":"financials",
}
SECTOR.update({k: v for k, v in SECTOR_DELISTED.items() if k not in SECTOR})


def _membership_windows():
    """{ticker: [(start, end), ...]} from sp500_start_end.csv (a ticker can have several windows)."""
    se = pd.read_csv(os.path.join(CACHE, "sp500_start_end.csv"), parse_dates=["start_date", "end_date"])
    se["end_date"] = se["end_date"].fillna(pd.Timestamp("2099-01-01"))
    win = {}
    for _, r in se.iterrows():
        win.setdefault(r["ticker"], []).append((r["start_date"], r["end_date"]))
    return win


def load_ohlc(verbose=True):
    """(O, H, L, C) date x ticker panels, masked to membership windows plus the buffer."""
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
        print(f"PIT panel: {C.shape[1]} tickers · {C.index[0].date()}->{C.index[-1].date()} · {len(C)} days")
    return out["open"], out["high"], out["low"], out["close"]


class Membership:
    """Index membership by date: asof(d) returns the frozenset of members on date d."""
    def __init__(self):
        rows = pd.read_csv(os.path.join(CACHE, "sp500_hist.csv"), parse_dates=["date"])
        rows = rows.sort_values("date")
        self.dates = rows["date"].to_numpy()
        self.sets = [frozenset(t.strip() for t in s.split(",")) for s in rows["tickers"]]

    def asof(self, d):
        """Members on date d: the latest membership snapshot on or before d."""
        i = np.searchsorted(self.dates, np.datetime64(d), side="right") - 1
        return self.sets[max(i, 0)]
