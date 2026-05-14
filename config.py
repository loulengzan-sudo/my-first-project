import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "stock-tracker-dev-key")
    CACHE_TIMEOUT = 300  # 5 minutes
    DEFAULT_TICKERS = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "JPM", "V", "WMT",
    ]
    NEWS_RSS_FEEDS = {
        "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
        "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    }
    PREDICTION_LOOKBACK_DAYS = 365
    PREDICTION_HORIZON = 7
