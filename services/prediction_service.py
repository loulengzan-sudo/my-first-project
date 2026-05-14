import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from services.stock_service import get_stock_history
from services.news_service import get_overall_sentiment


def _sma(series, window):
    return series.rolling(window=window).mean()


def _ema(series, window):
    return series.ewm(span=window, adjust=False).mean()


def _rsi(series, window=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_features(df):
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    df["SMA_5"] = _sma(close, 5)
    df["SMA_20"] = _sma(close, 20)
    df["SMA_50"] = _sma(close, 50)
    df["EMA_12"] = _ema(close, 12)
    df["EMA_26"] = _ema(close, 26)
    df["RSI"] = _rsi(close, 14)

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = _ema(df["MACD"], 9)
    df["MACD_diff"] = df["MACD"] - df["MACD_signal"]

    sma20 = _sma(close, 20)
    std20 = close.rolling(window=20).std()
    df["BB_upper"] = sma20 + 2 * std20
    df["BB_lower"] = sma20 - 2 * std20
    df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / sma20

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(window=14).mean()

    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    df["OBV"] = obv

    df["Return_1d"] = close.pct_change(1)
    df["Return_5d"] = close.pct_change(5)
    df["Return_10d"] = close.pct_change(10)
    df["Volatility_10d"] = df["Return_1d"].rolling(window=10).std()

    vol_sma = volume.rolling(window=20).mean()
    df["Volume_ratio"] = volume / vol_sma
    df["Price_to_SMA20"] = close / sma20
    df["Day_of_week"] = df.index.dayofweek
    df["Month"] = df.index.month

    return df


FEATURE_COLUMNS = [
    "SMA_5", "SMA_20", "SMA_50", "EMA_12", "EMA_26",
    "RSI", "MACD", "MACD_signal", "MACD_diff",
    "BB_upper", "BB_lower", "BB_width", "ATR", "OBV",
    "Return_1d", "Return_5d", "Return_10d", "Volatility_10d",
    "Volume_ratio", "Price_to_SMA20", "Day_of_week", "Month",
]


def predict_stock(ticker, days=7):
    df = get_stock_history(ticker, period="2y")
    if df.empty or len(df) < 100:
        return {"error": "Insufficient data for prediction"}

    df = _compute_features(df)
    df = df.dropna()

    if len(df) < 60:
        return {"error": "Insufficient data after feature computation"}

    predictions = []
    current_price = df["Close"].iloc[-1]

    for horizon in range(1, days + 1):
        df[f"Target_{horizon}d"] = df["Close"].shift(-horizon) / df["Close"] - 1
        temp = df.dropna(subset=[f"Target_{horizon}d"])

        X = temp[FEATURE_COLUMNS].values
        y = temp[f"Target_{horizon}d"].values

        split = int(len(X) * 0.8)
        X_train, y_train = X[:split], y[:split]

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)

        model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42,
        )
        model.fit(X_train_scaled, y_train)

        latest_features = df[FEATURE_COLUMNS].iloc[-1:].values
        latest_scaled = scaler.transform(latest_features)
        pred_return = model.predict(latest_scaled)[0]

        predicted_price = current_price * (1 + pred_return)

        predictions.append({
            "day": horizon,
            "predicted_price": round(float(predicted_price), 2),
            "predicted_change_pct": round(float(pred_return * 100), 2),
            "direction": "up" if pred_return > 0 else "down",
        })

    sentiment = get_overall_sentiment(ticker)

    feature_importance = dict(
        zip(FEATURE_COLUMNS, model.feature_importances_)
    )
    top_features = sorted(
        feature_importance.items(), key=lambda x: x[1], reverse=True
    )[:5]

    return {
        "ticker": ticker,
        "current_price": round(float(current_price), 2),
        "predictions": predictions,
        "sentiment": sentiment,
        "top_factors": [
            {"feature": f, "importance": round(float(v), 4)} for f, v in top_features
        ],
        "disclaimer": "This prediction is for reference only and should NOT be used as investment advice.",
    }
