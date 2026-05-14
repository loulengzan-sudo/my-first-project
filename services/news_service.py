import re
import xml.etree.ElementTree as ET
from datetime import datetime
from textblob import TextBlob
import requests
from config import Config


def _parse_rss(url):
    entries = []
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "StockPulse/1.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.iter("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            summary = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "")
            published = ""
            if pub_date:
                for fmt in (
                    "%a, %d %b %Y %H:%M:%S %z",
                    "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z",
                ):
                    try:
                        dt = datetime.strptime(pub_date.strip(), fmt)
                        published = dt.strftime("%Y-%m-%d %H:%M")
                        break
                    except ValueError:
                        continue
            entries.append({
                "title": title,
                "link": link,
                "summary": _clean_html(summary),
                "published": published,
            })
    except Exception:
        pass
    return entries


def fetch_news(ticker=None, limit=20):
    all_entries = []
    for source_name, url in Config.NEWS_RSS_FEEDS.items():
        items = _parse_rss(url)
        for item in items[:15]:
            item["source"] = source_name
            all_entries.append(item)

    if ticker:
        ticker_upper = ticker.upper()
        filtered = [
            e for e in all_entries
            if ticker_upper in e["title"].upper()
            or ticker_upper in e["summary"].upper()
        ]
        if filtered:
            all_entries = filtered

    all_entries.sort(key=lambda x: x["published"], reverse=True)
    return all_entries[:limit]


def analyze_sentiment(text):
    if not text:
        return {"polarity": 0, "subjectivity": 0, "label": "Neutral"}
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    subjectivity = blob.sentiment.subjectivity

    if polarity > 0.1:
        label = "Positive"
    elif polarity < -0.1:
        label = "Negative"
    else:
        label = "Neutral"

    return {
        "polarity": round(polarity, 3),
        "subjectivity": round(subjectivity, 3),
        "label": label,
    }


def get_news_sentiment(ticker=None, limit=20):
    news = fetch_news(ticker, limit)
    for article in news:
        text = f"{article['title']}. {article['summary']}"
        article["sentiment"] = analyze_sentiment(text)
    return news


def get_overall_sentiment(ticker):
    news = get_news_sentiment(ticker, limit=15)
    if not news:
        return {"score": 0, "label": "Neutral", "article_count": 0}

    avg_polarity = sum(a["sentiment"]["polarity"] for a in news) / len(news)

    if avg_polarity > 0.1:
        label = "Bullish"
    elif avg_polarity < -0.1:
        label = "Bearish"
    else:
        label = "Neutral"

    return {
        "score": round(avg_polarity, 3),
        "label": label,
        "article_count": len(news),
    }


def _clean_html(text):
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:300] if len(clean) > 300 else clean
