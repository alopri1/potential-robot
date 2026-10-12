"""Données de marché gratuites : Yahoo Finance (principal) + Twelve Data (secours optionnel, clé gratuite)."""
import os, time, threading
import requests
import pandas as pd

CACHE_TTL = max(10, min(int(os.getenv("CACHE_TTL_SECONDS", "45")), 600))
TD_KEY = os.getenv("TWELVEDATA_KEY", "").strip()
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}
DEFAULT_RANGE = {"1m": "2d", "5m": "5d", "15m": "10d", "30m": "60d", "60m": "1mo", "1d": "1y"}
TD_INTERVAL = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "1h", "1d": "1day"}
_cache, _lock = {}, threading.Lock()


class MarketDataError(Exception):
    pass


def _clean(df, minimum=50):
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    df.index.name = "time"
    if len(df) < minimum:
        raise MarketDataError(f"Historique insuffisant ({len(df)} bougies, {minimum} minimum).")
    return df


def _yahoo(symbol, interval, period):
    last = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}",
                             params={"range": period, "interval": interval, "events": "history"},
                             headers=HEADERS, timeout=12)
            r.raise_for_status()
            chart = r.json().get("chart", {})
            if chart.get("error") or not chart.get("result"):
                raise MarketDataError("Yahoo ne renvoie pas de bougies.")
            obj = chart["result"][0]
            ts = obj.get("timestamp") or []
            if not ts:
                raise MarketDataError("Historique vide.")
            q = obj["indicators"]["quote"][0]
            data = {k: q.get(k, [None] * len(ts)) for k in ("open", "high", "low", "close", "volume")}
            return _clean(pd.DataFrame(data, index=pd.to_datetime(ts, unit="s", utc=True)),
                          minimum=10 if interval == "1d" else 50)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, MarketDataError) as exc:
            last = exc
    raise MarketDataError(f"Yahoo indisponible ({type(last).__name__}).")


def _twelve(symbol, interval):
    if not TD_KEY or symbol != "EURUSD=X":
        raise MarketDataError("Pas de source de secours pour ce symbole.")
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": "EUR/USD", "interval": TD_INTERVAL[interval], "outputsize": 600,
                             "timezone": "UTC", "order": "ASC", "apikey": TD_KEY}, timeout=15)
    j = r.json()
    if "values" not in j:
        raise MarketDataError("Twelve Data: " + str(j.get("message", "erreur"))[:80])
    df = pd.DataFrame(j["values"])
    df.index = pd.to_datetime(df["datetime"], utc=True)
    if "volume" not in df:
        df["volume"] = 0
    return _clean(df[["open", "high", "low", "close", "volume"]])


def get_candles(symbol="EURUSD=X", interval="5m", period=None):
    if interval not in DEFAULT_RANGE:
        raise MarketDataError("Unité de temps non prise en charge.")
    period = period or DEFAULT_RANGE[interval]
    key = f"{symbol}:{interval}:{period}"
    now = time.time()
    with _lock:
        item = _cache.get(key)
    if item and now - item[0] < CACHE_TTL:
        return item[1].copy()
    try:
        try:
            df = _yahoo(symbol, interval, period)
        except MarketDataError:
            df = _twelve(symbol, interval)
    except (MarketDataError, requests.RequestException, ValueError, KeyError) as exc:
        if item and now - item[0] < 900:       # données légèrement périmées plutôt que rien
            return item[1].copy()
        raise exc if isinstance(exc, MarketDataError) else MarketDataError(f"Source indisponible ({type(exc).__name__}).")
    with _lock:
        _cache[key] = (now, df)
    return df.copy()
