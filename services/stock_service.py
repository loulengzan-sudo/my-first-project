import pandas as pd
import time
import os

DEMO_MODE = os.environ.get("STOCK_DEMO_MODE", "false")

_cache = {}
_cache_timestamps = {}
CACHE_TTL = 300


def _get_cached(key, fetcher):
    now = time.time()
    if key in _cache and now - _cache_timestamps.get(key, 0) < CACHE_TTL:
        return _cache[key]
    result = fetcher()
    _cache[key] = result
    _cache_timestamps[key] = now
    return result


def _use_mock():
    if DEMO_MODE == "true":
        return True
    if DEMO_MODE == "false":
        return False
    try:
        import yfinance as yf
        stock = yf.Ticker("AAPL")
        _ = stock.info.get("shortName")
        return False
    except Exception:
        return True


_is_mock = None


def _check_mock():
    global _is_mock
    if _is_mock is None:
        _is_mock = _use_mock()
    return _is_mock


def get_stock_info(ticker):
    if _check_mock():
        from services.mock_data import get_mock_stock_info
        return get_mock_stock_info(ticker)

    def fetch():
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "ticker": ticker,
            "name": info.get("shortName", ticker),
            "price": info.get("currentPrice") or info.get("regularMarketPrice", 0),
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
    return _get_cached(f"info_{ticker}", fetch)


def get_stock_history(ticker, period="1y", interval="1d"):
    if _check_mock():
        from services.mock_data import generate_mock_history
        days_map = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
        days = days_map.get(period, 252)
        return generate_mock_history(ticker, days)

    def fetch():
        import yfinance as yf
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty:
            return df
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        return df
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
    if _check_mock():
        from services.mock_data import MOCK_STOCKS
        q = query.upper()
        results = []
        for t, info in MOCK_STOCKS.items():
            if q in t or q in info["name"].upper():
                results.append({"ticker": t, "name": info["name"]})
        return results

    try:
        import yfinance as yf
        stock = yf.Ticker(query.upper())
        info = stock.info
        if info.get("shortName"):
            return [{"ticker": query.upper(), "name": info["shortName"]}]
    except Exception:
        pass
    return []


def get_intraday_data(ticker):
    if _check_mock():
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

    def fetch():
        import yfinance as yf
        stock = yf.Ticker(ticker)
        df = stock.history(period="1d", interval="5m")
        if df.empty:
            return []
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        return [
            {
                "time": idx.strftime("%H:%M"),
                "price": round(row["Close"], 2),
                "volume": int(row["Volume"]),
            }
            for idx, row in df.iterrows()
        ]
    return _get_cached(f"intraday_{ticker}", fetch)
