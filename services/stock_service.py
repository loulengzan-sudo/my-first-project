import pandas as pd
import pickle
import time
import os
import requests

DEMO_MODE = os.environ.get("STOCK_DEMO_MODE", "auto")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_cache = {}
_cache_timestamps = {}
CACHE_TTL = 1800
DISK_CACHE_TTL = 86400


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
    raise RuntimeError(f"No data available for {key}")


# ---- Stooq: free, no API key, no rate limit ----

def _fetch_stooq(ticker, days=504):
    """Fetch historical data from Stooq (free, no key, no rate limit)."""
    stooq_ticker = f"{ticker.upper()}.US"
    url = f"https://stooq.com/q/d/l/?s={stooq_ticker}&i=d"
    try:
        df = pd.read_csv(url)
        if df.empty or "Close" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        if len(df) > days:
            df = df.iloc[-days:]
        return df
    except Exception:
        return None


def _fetch_yfinance(ticker, period="5d"):
    """Fetch data from yfinance (may be rate-limited)."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, progress=False)
        if df is not None and not df.empty:
            df.index = df.index.tz_localize(None) if df.index.tz else df.index
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
    except Exception:
        pass
    return None


def _info_from_df(ticker, df):
    """Extract stock info dict from a DataFrame with OHLCV data."""
    if df is None or df.empty or len(df) < 2:
        return None
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price = float(latest["Close"])
    prev_close = float(prev["Close"])
    if price <= 0:
        return None

    return {
        "ticker": ticker.upper(),
        "name": ticker.upper(),
        "price": round(price, 2),
        "previous_close": round(prev_close, 2),
        "open": round(float(latest["Open"]), 2),
        "day_high": round(float(latest["High"]), 2),
        "day_low": round(float(latest["Low"]), 2),
        "volume": int(latest["Volume"]),
        "market_cap": 0,
        "pe_ratio": 0,
        "week_52_high": round(float(df["High"].iloc[-252:].max()), 2) if len(df) >= 252 else round(float(df["High"].max()), 2),
        "week_52_low": round(float(df["Low"].iloc[-252:].min()), 2) if len(df) >= 252 else round(float(df["Low"].min()), 2),
        "sector": "N/A",
        "industry": "N/A",
    }


def get_stock_info(ticker):
    if DEMO_MODE == "true":
        from services.mock_data import get_mock_stock_info
        return get_mock_stock_info(ticker)

    def fetch():
        # Try yfinance first
        df = _fetch_yfinance(ticker, period="5d")
        info = _info_from_df(ticker, df)
        if info:
            return info

        # Fallback to Stooq
        df = _fetch_stooq(ticker, days=10)
        info = _info_from_df(ticker, df)
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
        # Try yfinance first
        df = _fetch_yfinance(ticker, period=period)
        if df is not None and not df.empty:
            return df

        # Fallback to Stooq
        df = _fetch_stooq(ticker, days=days)
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

    # Try batch yfinance download first (1 API call for all tickers)
    batch_df = None
    if DEMO_MODE != "true":
        batch_df = _fetch_yfinance(tickers, period="5d") if len(tickers) > 1 else None

    results = []
    for ticker in tickers:
        info = None

        # Try extracting from batch download
        if batch_df is not None and not batch_df.empty:
            try:
                if isinstance(batch_df.columns, pd.MultiIndex) and ticker in batch_df.columns.get_level_values(1):
                    ticker_df = batch_df.xs(ticker, level=1, axis=1).dropna(how="all")
                    info = _info_from_df(ticker, ticker_df)
            except Exception:
                pass

        # Try individual Stooq fetch
        if info is None:
            stooq_df = _fetch_stooq(ticker, days=10)
            info = _info_from_df(ticker, stooq_df)

        # Try disk cache
        if info is None:
            info = _load_disk(f"info_{ticker}")

        # Last resort: mock
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

        _cache[f"info_{ticker}"] = info
        _cache_timestamps[f"info_{ticker}"] = now
        _save_disk(f"info_{ticker}", info)

    _cache[cache_key] = results
    _cache_timestamps[cache_key] = now
    return results


def search_ticker(query):
    q = query.upper()
    # Quick test: try Stooq (no rate limit)
    df = _fetch_stooq(q, days=5)
    if df is not None and not df.empty:
        return [{"ticker": q, "name": q}]

    from services.mock_data import MOCK_STOCKS
    results = []
    for t, info in MOCK_STOCKS.items():
        if q in t or q in info["name"].upper():
            results.append({"ticker": t, "name": info["name"]})
    return results


def get_intraday_data(ticker):
    def fetch():
        # Intraday only available from yfinance
        try:
            import yfinance as yf
            df = yf.download(ticker, period="1d", interval="5m", progress=False)
            if not df.empty:
                df.index = df.index.tz_localize(None) if df.index.tz else df.index
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return [
                    {
                        "time": idx.strftime("%H:%M"),
                        "price": round(float(row["Close"]), 2),
                        "volume": int(row["Volume"]),
                    }
                    for idx, row in df.iterrows()
                ]
        except Exception:
            pass
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
