import numpy as np
import pandas as pd
from datetime import datetime, timedelta

MOCK_STOCKS = {
    "AAPL": {"name": "Apple Inc.", "price": 198.50, "sector": "Technology", "industry": "Consumer Electronics", "pe": 32.1, "cap": 3050000000000},
    "MSFT": {"name": "Microsoft Corp.", "price": 430.20, "sector": "Technology", "industry": "Software", "pe": 37.5, "cap": 3200000000000},
    "GOOGL": {"name": "Alphabet Inc.", "price": 175.80, "sector": "Technology", "industry": "Internet", "pe": 27.3, "cap": 2180000000000},
    "AMZN": {"name": "Amazon.com Inc.", "price": 192.30, "sector": "Consumer Cyclical", "industry": "E-Commerce", "pe": 62.8, "cap": 2000000000000},
    "NVDA": {"name": "NVIDIA Corp.", "price": 135.40, "sector": "Technology", "industry": "Semiconductors", "pe": 65.2, "cap": 3330000000000},
    "META": {"name": "Meta Platforms Inc.", "price": 510.60, "sector": "Technology", "industry": "Social Media", "pe": 28.9, "cap": 1300000000000},
    "TSLA": {"name": "Tesla Inc.", "price": 245.70, "sector": "Consumer Cyclical", "industry": "Auto Manufacturers", "pe": 72.1, "cap": 782000000000},
    "JPM": {"name": "JPMorgan Chase & Co.", "price": 205.40, "sector": "Financial", "industry": "Banks", "pe": 12.3, "cap": 592000000000},
    "V": {"name": "Visa Inc.", "price": 282.90, "sector": "Financial", "industry": "Credit Services", "pe": 31.4, "cap": 580000000000},
    "WMT": {"name": "Walmart Inc.", "price": 67.80, "sector": "Consumer Defensive", "industry": "Retail", "pe": 29.8, "cap": 544000000000},
}

MOCK_NEWS = [
    {"title": "Tech Stocks Rally on Strong Earnings Reports", "summary": "Major technology companies exceeded analyst expectations, driving broad market gains.", "source": "MarketWatch", "sentiment": "Positive"},
    {"title": "Federal Reserve Signals Potential Rate Cuts", "summary": "Fed officials hinted at possible interest rate reductions in upcoming meetings, boosting investor confidence.", "source": "CNBC", "sentiment": "Positive"},
    {"title": "AI Chip Demand Continues to Surge", "summary": "NVIDIA and other semiconductor makers report unprecedented demand for AI accelerators.", "source": "Yahoo Finance", "sentiment": "Positive"},
    {"title": "Trade Tensions Rise Between US and China", "summary": "New tariff proposals create uncertainty in global markets, weighing on export-heavy stocks.", "source": "CNBC", "sentiment": "Negative"},
    {"title": "Retail Sales Data Shows Consumer Resilience", "summary": "Better-than-expected retail figures suggest the consumer economy remains strong.", "source": "MarketWatch", "sentiment": "Positive"},
    {"title": "Electric Vehicle Market Faces Growing Competition", "summary": "New entrants challenge Tesla's dominance as EV adoption accelerates globally.", "source": "Yahoo Finance", "sentiment": "Neutral"},
    {"title": "Big Banks Report Record Quarterly Profits", "summary": "JPMorgan and other major banks benefit from higher interest rates and strong trading revenue.", "source": "CNBC", "sentiment": "Positive"},
    {"title": "Cloud Computing Growth Exceeds Expectations", "summary": "AWS, Azure, and Google Cloud all report double-digit revenue growth in latest quarter.", "source": "MarketWatch", "sentiment": "Positive"},
    {"title": "Oil Prices Drop on Demand Concerns", "summary": "Crude oil falls as economic slowdown fears weigh on energy sector outlook.", "source": "Yahoo Finance", "sentiment": "Negative"},
    {"title": "Social Media Advertising Revenue Surges", "summary": "Meta and other social platforms see strong ad spending recovery from major brands.", "source": "CNBC", "sentiment": "Positive"},
]


def generate_mock_history(ticker, days=504):
    np.random.seed(hash(ticker) % 2**31)
    base = MOCK_STOCKS.get(ticker, MOCK_STOCKS["AAPL"])
    end_price = base["price"]

    returns = np.random.normal(0.0004, 0.018, days)
    prices = [end_price]
    for r in reversed(returns):
        prices.insert(0, prices[0] / (1 + r))

    dates = pd.bdate_range(end=datetime.now(), periods=days)
    df = pd.DataFrame(index=dates)
    df["Close"] = prices[:days]
    df["Open"] = df["Close"] * (1 + np.random.normal(0, 0.005, days))
    df["High"] = df[["Open", "Close"]].max(axis=1) * (1 + np.abs(np.random.normal(0, 0.008, days)))
    df["Low"] = df[["Open", "Close"]].min(axis=1) * (1 - np.abs(np.random.normal(0, 0.008, days)))
    df["Volume"] = np.random.randint(20_000_000, 120_000_000, days)

    return df


def get_mock_stock_info(ticker):
    ticker = ticker.upper()
    base = MOCK_STOCKS.get(ticker)
    if not base:
        base = {"name": ticker, "price": 100 + hash(ticker) % 200, "sector": "Unknown", "industry": "Unknown", "pe": 20, "cap": 100000000000}

    change_pct = (hash(ticker + "change") % 600 - 300) / 100
    price = base["price"]
    prev_close = price / (1 + change_pct / 100)

    return {
        "ticker": ticker,
        "name": base["name"],
        "price": round(price, 2),
        "previous_close": round(prev_close, 2),
        "open": round(price * (1 + (hash(ticker + "open") % 100 - 50) / 10000), 2),
        "day_high": round(price * 1.012, 2),
        "day_low": round(price * 0.988, 2),
        "volume": 45_000_000 + hash(ticker) % 80_000_000,
        "market_cap": base["cap"],
        "pe_ratio": base["pe"],
        "week_52_high": round(price * 1.25, 2),
        "week_52_low": round(price * 0.72, 2),
        "sector": base["sector"],
        "industry": base["industry"],
    }
