from flask import Flask, render_template, jsonify, request
from config import Config
from services.stock_service import (
    get_stock_info,
    get_stock_history,
    get_market_overview,
    search_ticker,
    get_intraday_data,
)
from services.news_service import get_news_sentiment, get_overall_sentiment
from services.prediction_service import predict_stock

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    return render_template("index.html", tickers=Config.DEFAULT_TICKERS)


@app.route("/stock/<ticker>")
def stock_detail(ticker):
    ticker = ticker.upper()
    return render_template("stock_detail.html", ticker=ticker)


# --- API ---


@app.route("/api/market")
def api_market():
    tickers = request.args.get("tickers", "").split(",")
    if not tickers or tickers == [""]:
        tickers = Config.DEFAULT_TICKERS
    data = get_market_overview(tickers)
    return jsonify(data)


@app.route("/api/stock/<ticker>")
def api_stock(ticker):
    try:
        info = get_stock_info(ticker.upper())
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock/<ticker>/history")
def api_stock_history(ticker):
    period = request.args.get("period", "1y")
    interval = request.args.get("interval", "1d")
    allowed_periods = ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
    if period not in allowed_periods:
        period = "1y"
    try:
        df = get_stock_history(ticker.upper(), period=period, interval=interval)
        if df.empty:
            return jsonify({"error": "No data available"}), 404
        data = {
            "dates": df.index.strftime("%Y-%m-%d").tolist(),
            "open": df["Open"].round(2).tolist(),
            "high": df["High"].round(2).tolist(),
            "low": df["Low"].round(2).tolist(),
            "close": df["Close"].round(2).tolist(),
            "volume": df["Volume"].tolist(),
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock/<ticker>/intraday")
def api_intraday(ticker):
    try:
        data = get_intraday_data(ticker.upper())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock/<ticker>/news")
def api_news(ticker):
    try:
        news = get_news_sentiment(ticker.upper(), limit=15)
        return jsonify(news)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock/<ticker>/sentiment")
def api_sentiment(ticker):
    try:
        sentiment = get_overall_sentiment(ticker.upper())
        return jsonify(sentiment)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock/<ticker>/predict")
def api_predict(ticker):
    days = request.args.get("days", 7, type=int)
    if days < 1 or days > 7:
        days = 7
    try:
        result = predict_stock(ticker.upper(), days=days)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    results = search_ticker(query)
    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
