import pandas as pd
import pickle
import time
import os

DEMO_MODE = os.environ.get("STOCK_DEMO_MODE", "auto")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_cache = {}
_cache_timestamps = {}
CACHE_TTL = 1800  # 30 minutes to reduce Yahoo rate-limit hits
DISK_CACHE_TTL = 86400  # 1 day disk cache as fallback


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
        return None
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


def _try_real_stock_info(ticker):
    import yfinance as yf
    stock = yf.Ticker(ticker)
    try:
        fi = stock.fast_info
        last_price = float(fi.get("last_price") or fi.get("lastPrice") or 0)
        prev_close = float(fi.get("previous_close") or fi.get("previousClose") or 0)
        if last_price and prev_close:
            return {
                "ticker": ticker,
                "name": ticker,
                "price": last_price,
                "previous_close": prev_close,
                "open": float(fi.get("open") or 0),
                "day_high": float(fi.get("day_high") or fi.get("dayHigh") or 0),
                "day_low": float(fi.get("day_low") or fi.get("dayLow") or 0),
                "volume": int(fi.get("last_volume") or fi.get("lastVolume") or 0),
                "market_cap": int(fi.get("market_cap") or fi.get("marketCap") or 0),
                "pe_ratio": 0,
                "week_52_high": float(fi.get("year_high") or fi.get("yearHigh") or 0),
                "week_52_low": float(fi.get("year_low") or fi.get("yearLow") or 0),
                "sector": "N/A",
                "industry": "N/A",
            }
    except Exception:
        pass

    info = stock.info
    name = info.get("shortName") or info.get("longName")
    if not name:
        return None
    return {
        "ticker": ticker,
        "name": name,
        "price": info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0),
        "previous_close": info.get("previousClose", 0),
        "open": info.get("open") or info.get("regularMarketOpen", 0),
        "day_high": info.get("dayHigh") or info.get("regularMarketDayHigh", 0),
        "day_low": info.get("dayLow") or info.get("regularMarketDayLow", 0),
        "volume": info.get("volume") or info.get("regularMarketVolume", 0),
        "market_cap": info.get("marketCap", 0),
        "pe_ratio": info.get("trailingPE", 0),
        "week_52_high": info.get("fiftyTwoWeekHigh", 0),
        "week_52_low": info.get("fiftyTwoWeekLow", 0),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
    }


def get_stock_info(ticker):
    if DEMO_MODE == "true":
        from services.mock_data import get_mock_stock_info
        return get_mock_stock_info(ticker)

    def fetch():
        try:
            result = _try_real_stock_info(ticker)
            if result:
                return result
        except Exception:
            pass
        from services.mock_data import get_mock_stock_info
        return get_mock_stock_info(ticker)

    return _get_cached(f"info_{ticker}", fetch)


def get_stock_history(ticker, period="1y", interval="1d"):
    if DEMO_MODE == "true":
        from services.mock_data import generate_mock_history
        days_map = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
        return generate_mock_history(ticker, days_map.get(period, 252))

    def fetch():
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            df = stock.history(period=period, interval=interval)
            if not df.empty:
                df.index = df.index.tz_localize(None) if df.index.tz else df.index
                return df
        except Exception:
            pass
        from services.mock_data import generate_mock_history
        days_map = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
        return generate_mock_history(ticker, days_map.get(period, 252))

    return _get_cached(f"hist_{ticker}_{period}_{interval}", fetch)


def get_market_overview(tickers):
    results = []
    for ticker in tickers:
        try:
            info = get_stock_info(ticker)
            price = info["price"]
            prev = info["previous_close"]
            change = price - prev if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            results.append({
                **info,
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            })
        except Exception:
            results.append({
                "ticker": ticker,
                "name": ticker,
                "price": 0,
                "change": 0,
                "change_pct": 0,
                "error": True,
            })
    return results


def search_ticker(query):
    try:
        import yfinance as yf
        stock = yf.Ticker(query.upper())
        info = stock.info
        name = info.get("shortName") or info.get("longName")
        if name:
            return [{"ticker": query.upper(), "name": name}]
    except Exception:
        pass
    from services.mock_data import MOCK_STOCKS
    q = query.upper()
    results = []
    for t, info in MOCK_STOCKS.items():
        if q in t or q in info["name"].upper():
            results.append({"ticker": t, "name": info["name"]})
    return results


def get_intraday_data(ticker):
    def fetch():
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            df = stock.history(period="1d", interval="5m")
            if not df.empty:
                df.index = df.index.tz_localize(None) if df.index.tz else df.index
                return [
                    {
                        "time": idx.strftime("%H:%M"),
                        "price": round(row["Close"], 2),
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
