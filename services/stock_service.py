import pandas as pd
import pickle
import time
import os
import requests
import json

DEMO_MODE = os.environ.get("STOCK_DEMO_MODE", "auto")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_cache = {}
_cache_timestamps = {}
CACHE_TTL = 1800
DISK_CACHE_TTL = 86400

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _cache_file(key):
    safe = "".join(c if c.isalnum() else "_" for c in key)
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def _load_disk(key):
    path = _cache_file(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            ts, value = pickle.load(f)
        if time.time() - ts < DISK_CACHE_TTL:
            return value
    except Exception:
        pass
    return None


def _save_disk(key, value):
    try:
        with open(_cache_file(key), "wb") as f:
            pickle.dump((time.time(), value), f)
    except Exception:
        pass


def _get_cached(key, fetcher):
    now = time.time()
    if key in _cache and now - _cache_timestamps.get(key, 0) < CACHE_TTL:
        return _cache[key]
    try:
        result = fetcher()
        if result is not None and not (hasattr(result, "empty") and result.empty):
            _cache[key] = result
            _cache_timestamps[key] = now
            _save_disk(key, result)
            return result
    except Exception:
        pass
    disk_val = _load_disk(key)
    if disk_val is not None:
        _cache[key] = disk_val
        _cache_timestamps[key] = now
        return disk_val
    from services.mock_data import get_mock_stock_info
    return get_mock_stock_info(key.replace("info_", ""))


# ---- Yahoo Chart API (free, no key, different rate limit than yfinance) ----

def _yahoo_chart(ticker, range_str="5d", interval="1d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"range": range_str, "interval": interval, "includePrePost": "false"}
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        return result[0]
    except Exception:
        return None


def _chart_to_df(chart_data):
    if not chart_data:
        return None
    timestamps = chart_data.get("timestamp")
    quotes = chart_data.get("indicators", {}).get("quote", [{}])[0]
    if not timestamps or not quotes:
        return None

    df = pd.DataFrame({
        "Open": quotes.get("open", []),
        "High": quotes.get("high", []),
        "Low": quotes.get("low", []),
        "Close": quotes.get("close", []),
        "Volume": quotes.get("volume", []),
    }, index=pd.to_datetime(timestamps, unit="s"))

    df = df.dropna(subset=["Close"])
    if df.empty:
        return None
    df.index.name = "Date"
    df.index = df.index.normalize()
    return df


def _chart_to_info(ticker, chart_data):
    if not chart_data:
        return None
    meta = chart_data.get("meta", {})
    timestamps = chart_data.get("timestamp", [])
    quotes = chart_data.get("indicators", {}).get("quote", [{}])[0]

    closes = [c for c in (quotes.get("close") or []) if c is not None]
    if len(closes) < 2:
        return None

    price = closes[-1]
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or closes[-2]

    highs = [h for h in (quotes.get("high") or []) if h is not None]
    lows = [l for l in (quotes.get("low") or []) if l is not None]
    opens = [o for o in (quotes.get("open") or []) if o is not None]
    volumes = [v for v in (quotes.get("volume") or []) if v is not None]

    return {
        "ticker": ticker.upper(),
        "name": meta.get("shortName") or meta.get("longName") or ticker.upper(),
        "price": round(price, 2),
        "previous_close": round(prev_close, 2),
        "open": round(opens[-1], 2) if opens else 0,
        "day_high": round(highs[-1], 2) if highs else 0,
        "day_low": round(lows[-1], 2) if lows else 0,
        "volume": int(volumes[-1]) if volumes else 0,
        "market_cap": 0,
        "pe_ratio": 0,
        "week_52_high": round(meta.get("fiftyTwoWeekHigh", 0), 2),
        "week_52_low": round(meta.get("fiftyTwoWeekLow", 0), 2),
        "sector": "N/A",
        "industry": "N/A",
    }


def get_stock_info(ticker):
    if DEMO_MODE == "true":
        from services.mock_data import get_mock_stock_info
        return get_mock_stock_info(ticker)

    def fetch():
        chart = _yahoo_chart(ticker, range_str="5d", interval="1d")
        info = _chart_to_info(ticker, chart)
        if info:
            return info
        from services.mock_data import get_mock_stock_info
        return get_mock_stock_info(ticker)

    return _get_cached(f"info_{ticker}", fetch)


def get_stock_history(ticker, period="1y", interval="1d"):
    if DEMO_MODE == "true":
        from services.mock_data import generate_mock_history
        days_map = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
        return generate_mock_history(ticker, days_map.get(period, 252))

    days_map = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
    days = days_map.get(period, 252)

    def fetch():
        chart = _yahoo_chart(ticker, range_str=period, interval=interval)
        df = _chart_to_df(chart)
        if df is not None and not df.empty:
            return df
        from services.mock_data import generate_mock_history
        return generate_mock_history(ticker, days)

    return _get_cached(f"hist_{ticker}_{period}_{interval}", fetch)


def get_market_overview(tickers):
    cache_key = f"market_overview_{'_'.join(tickers)}"
    now = time.time()
    if cache_key in _cache and now - _cache_timestamps.get(cache_key, 0) < CACHE_TTL:
        return _cache[cache_key]

    results = []
    for ticker in tickers:
        info = None
        try:
            info = get_stock_info(ticker)
        except Exception:
            pass

        if info is None:
            from services.mock_data import get_mock_stock_info
            info = get_mock_stock_info(ticker)

        price = info["price"]
        prev = info["previous_close"]
        change = price - prev if prev else 0
        change_pct = (change / prev * 100) if prev else 0
        results.append({
            **info,
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
        })

    _cache[cache_key] = results
    _cache_timestamps[cache_key] = now
    return results


def search_ticker(query):
    q = query.upper()
    chart = _yahoo_chart(q, range_str="5d", interval="1d")
    if chart and chart.get("timestamp"):
        name = chart.get("meta", {}).get("shortName") or q
        return [{"ticker": q, "name": name}]
    from services.mock_data import MOCK_STOCKS
    results = []
    for t, info in MOCK_STOCKS.items():
        if q in t or q in info["name"].upper():
            results.append({"ticker": t, "name": info["name"]})
    return results


def get_intraday_data(ticker):
    def fetch():
        chart = _yahoo_chart(ticker, range_str="1d", interval="5m")
        if chart and chart.get("timestamp"):
            timestamps = chart["timestamp"]
            quotes = chart.get("indicators", {}).get("quote", [{}])[0]
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])
            result = []
            for i, ts in enumerate(timestamps):
                if i < len(closes) and closes[i] is not None:
                    t = pd.Timestamp(ts, unit="s")
                    result.append({
                        "time": t.strftime("%H:%M"),
                        "price": round(closes[i], 2),
                        "volume": int(volumes[i]) if i < len(volumes) and volumes[i] else 0,
                    })
            if result:
                return result

        import numpy as np
        np.random.seed(hash(ticker + "intraday") % 2**31)
        from services.mock_data import MOCK_STOCKS
        base = MOCK_STOCKS.get(ticker.upper(), {"price": 150})
        prices = []
        p = base["price"] * 0.998
        for i in range(78):
            p += np.random.normal(0, base["price"] * 0.001)
            hour = 9 + (i * 5 + 30) // 60
            minute = (i * 5 + 30) % 60
            prices.append({
                "time": f"{hour:02d}:{minute:02d}",
                "price": round(p, 2),
                "volume": int(np.random.randint(100000, 2000000)),
            })
        return prices

    return _get_cached(f"intraday_{ticker}", fetch)
