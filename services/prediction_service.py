import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from services.stock_service import get_stock_history
from services.news_service import get_overall_sentiment

# ---------------------------------------------------------------------------
# Common ETF tickers used to detect whether a ticker is an ETF (for tighter
# prediction bounds) vs an individual stock.
# ---------------------------------------------------------------------------
_ETF_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "EFA", "EEM",
    "AGG", "BND", "TLT", "GLD", "SLV", "XLF", "XLK", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC", "ARKK", "SCHD",
    "VEA", "VWO", "VIG", "VYM", "IEMG", "IJR", "IJH", "VGT", "VNQ",
    "VTIP", "HYG", "LQD", "VCIT", "VCSH", "VXUS", "SPTM", "SPYG",
    "SPYV", "RSP",
}

# ---------------------------------------------------------------------------
# Helper: Simple / Exponential Moving Average
# ---------------------------------------------------------------------------

def _sma(series, window):
    return series.rolling(window=window, min_periods=1).mean()


def _ema(series, window):
    return series.ewm(span=window, adjust=False).mean()


# ---------------------------------------------------------------------------
# Helper: RSI
# ---------------------------------------------------------------------------

def _rsi(series, window=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=window, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=window, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# Helper: Stochastic Oscillator (%K, %D)
# ---------------------------------------------------------------------------

def _stochastic(high, low, close, k_window=14, d_window=3):
    lowest_low = low.rolling(window=k_window, min_periods=1).min()
    highest_high = high.rolling(window=k_window, min_periods=1).max()
    denom = highest_high - lowest_low
    pct_k = 100 * (close - lowest_low) / denom.replace(0, np.nan)
    pct_d = pct_k.rolling(window=d_window, min_periods=1).mean()
    return pct_k, pct_d


# ---------------------------------------------------------------------------
# Helper: Williams %R
# ---------------------------------------------------------------------------

def _williams_r(high, low, close, window=14):
    highest_high = high.rolling(window=window, min_periods=1).max()
    lowest_low = low.rolling(window=window, min_periods=1).min()
    denom = highest_high - lowest_low
    return -100 * (highest_high - close) / denom.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Helper: CCI (Commodity Channel Index)
# ---------------------------------------------------------------------------

def _cci(high, low, close, window=20):
    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(window=window, min_periods=1).mean()
    mad = tp.rolling(window=window, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    )
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


# ---------------------------------------------------------------------------
# Helper: ADX (Average Directional Index)
# ---------------------------------------------------------------------------

def _adx(high, low, close, window=14):
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=window, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=window, adjust=False).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(span=window, adjust=False).mean() / atr.replace(0, np.nan))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_val = dx.ewm(span=window, adjust=False).mean()
    return adx_val, plus_di, minus_di


# ---------------------------------------------------------------------------
# Helper: Rate of Change (ROC)
# ---------------------------------------------------------------------------

def _roc(series, window=10):
    shifted = series.shift(window)
    return 100 * (series - shifted) / shifted.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Helper: MFI-like volume pressure indicator
# (uses price direction + volume, without requiring actual money-flow)
# ---------------------------------------------------------------------------

def _volume_pressure(close, volume, window=14):
    direction = np.sign(close.diff())
    signed_vol = direction * volume
    pos_vol = signed_vol.where(signed_vol > 0, 0.0).rolling(window=window, min_periods=1).sum()
    neg_vol = (-signed_vol.where(signed_vol < 0, 0.0)).rolling(window=window, min_periods=1).sum()
    ratio = pos_vol / neg_vol.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


# ---------------------------------------------------------------------------
# Helper: Support / Resistance levels from recent highs/lows
# ---------------------------------------------------------------------------

def _support_resistance(high, low, close, lookback=60):
    recent_high = high.iloc[-lookback:]
    recent_low = low.iloc[-lookback:]
    current = float(close.iloc[-1])

    highs_sorted = sorted(recent_high.values, reverse=True)
    lows_sorted = sorted(recent_low.values)

    resistance_levels = []
    for h in highs_sorted:
        if h > current and (not resistance_levels or abs(h - resistance_levels[-1]) / current > 0.005):
            resistance_levels.append(float(h))
        if len(resistance_levels) >= 3:
            break

    support_levels = []
    for l_val in lows_sorted:
        if l_val < current and (not support_levels or abs(l_val - support_levels[-1]) / current > 0.005):
            support_levels.append(float(l_val))
        if len(support_levels) >= 3:
            break

    # If we couldn't find enough levels, use percentages of current price
    while len(resistance_levels) < 2:
        mult = 1.01 + 0.01 * len(resistance_levels)
        resistance_levels.append(round(current * mult, 2))
    while len(support_levels) < 2:
        mult = 0.99 - 0.01 * len(support_levels)
        support_levels.append(round(current * mult, 2))

    resistance_levels.sort()
    support_levels.sort(reverse=True)

    return {
        "support": [round(s, 2) for s in support_levels],
        "resistance": [round(r, 2) for r in resistance_levels],
    }


# ---------------------------------------------------------------------------
# Trend analysis helpers
# ---------------------------------------------------------------------------

def _classify_trend(current_price, sma_short, sma_mid, sma_long):
    """Return a human-readable trend label and numeric score (-1..+1)."""
    score = 0.0
    if current_price > sma_short:
        score += 0.33
    else:
        score -= 0.33
    if current_price > sma_mid:
        score += 0.33
    else:
        score -= 0.33
    if current_price > sma_long:
        score += 0.34
    else:
        score -= 0.34

    if score > 0.5:
        label = "Strong Uptrend"
    elif score > 0:
        label = "Weak Uptrend"
    elif score > -0.5:
        label = "Weak Downtrend"
    else:
        label = "Strong Downtrend"
    return label, round(score, 2)


def _trend_analysis(close, sma5, sma20, sma50):
    current = float(close.iloc[-1])
    s5 = float(sma5.iloc[-1])
    s20 = float(sma20.iloc[-1])
    s50 = float(sma50.iloc[-1])

    short_label, short_score = _classify_trend(current, s5, s5, s5)
    mid_label, mid_score = _classify_trend(current, s5, s20, s20)
    long_label, long_score = _classify_trend(current, s5, s20, s50)

    # Trend alignment: all three agree?
    alignment = "aligned" if (short_score > 0 and mid_score > 0 and long_score > 0) or \
                             (short_score < 0 and mid_score < 0 and long_score < 0) else "mixed"

    return {
        "short_term": {"label": short_label, "score": float(short_score)},
        "medium_term": {"label": mid_label, "score": float(mid_score)},
        "long_term": {"label": long_label, "score": float(long_score)},
        "alignment": alignment,
    }


# ---------------------------------------------------------------------------
# Market context: fetch SPY / VIX and compute relative features
# ---------------------------------------------------------------------------

def _fetch_market_context():
    """Fetch SPY and VIX history.  Returns (spy_df, vix_df) or Nones on failure."""
    spy_df = None
    vix_df = None
    try:
        spy_df = get_stock_history("SPY", period="2y")
        if spy_df is not None and spy_df.empty:
            spy_df = None
    except Exception:
        spy_df = None
    try:
        vix_df = get_stock_history("^VIX", period="2y")
        if vix_df is not None and vix_df.empty:
            vix_df = None
    except Exception:
        vix_df = None
    return spy_df, vix_df


def _add_market_features(df, spy_df, vix_df):
    """Merge market-context columns into the feature dataframe."""
    df = df.copy()

    if spy_df is not None and len(spy_df) > 20:
        spy_close = spy_df["Close"].reindex(df.index, method="ffill")
        spy_ret = spy_close.pct_change()
        stock_ret = df["Close"].pct_change()
        # Rolling correlation with SPY (60-day window)
        df["Corr_SPY_60"] = stock_ret.rolling(window=60, min_periods=20).corr(spy_ret)
        # Relative strength vs SPY (20-day cumulative return ratio)
        stock_cum = (1 + stock_ret).rolling(20, min_periods=5).apply(np.prod, raw=True) - 1
        spy_cum = (1 + spy_ret).rolling(20, min_periods=5).apply(np.prod, raw=True) - 1
        df["Relative_Strength_SPY"] = stock_cum - spy_cum
        # SPY trend: price vs its 50-day SMA
        spy_sma50 = _sma(spy_close, 50)
        df["SPY_trend"] = spy_close / spy_sma50.replace(0, np.nan) - 1
    else:
        df["Corr_SPY_60"] = 0.0
        df["Relative_Strength_SPY"] = 0.0
        df["SPY_trend"] = 0.0

    if vix_df is not None and len(vix_df) > 5:
        vix_close = vix_df["Close"].reindex(df.index, method="ffill")
        df["VIX_level"] = vix_close
        df["VIX_change"] = vix_close.pct_change()
        # Market regime (simple proxy): VIX > 25 => high vol
        df["High_Vol_Regime"] = (vix_close > 25).astype(float)
    else:
        df["VIX_level"] = 20.0  # neutral default
        df["VIX_change"] = 0.0
        df["High_Vol_Regime"] = 0.0

    return df


# ---------------------------------------------------------------------------
# Market regime detection (trending vs ranging) based on ADX + volatility
# ---------------------------------------------------------------------------

def _market_regime_features(df, adx_series):
    """Add a trending-vs-ranging indicator."""
    df = df.copy()
    # ADX > 25 typically indicates a trend
    df["Is_Trending"] = (adx_series > 25).astype(float)
    # Ratio of recent ATR to longer-term ATR (volatility expansion/contraction)
    atr_short = df["ATR"].rolling(5, min_periods=1).mean()
    atr_long = df["ATR"].rolling(40, min_periods=1).mean()
    df["ATR_ratio"] = atr_short / atr_long.replace(0, np.nan)
    return df


# ---------------------------------------------------------------------------
# Compute ALL features
# ---------------------------------------------------------------------------

def _compute_features(df, spy_df=None, vix_df=None):
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # ---------- Original indicators ----------
    df["SMA_5"] = _sma(close, 5)
    df["SMA_20"] = _sma(close, 20)
    df["SMA_50"] = _sma(close, 50)
    df["EMA_12"] = _ema(close, 12)
    df["EMA_26"] = _ema(close, 26)
    df["RSI"] = _rsi(close, 14)

    df["MACD"] = df["EMA_12"] - df["EMA_26"]
    df["MACD_signal"] = _ema(df["MACD"], 9)
    df["MACD_diff"] = df["MACD"] - df["MACD_signal"]

    sma20 = df["SMA_20"]
    std20 = close.rolling(window=20, min_periods=1).std()
    df["BB_upper"] = sma20 + 2 * std20
    df["BB_lower"] = sma20 - 2 * std20
    df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / sma20.replace(0, np.nan)
    # Bollinger Band %B
    df["BB_pctB"] = (close - df["BB_lower"]) / (df["BB_upper"] - df["BB_lower"]).replace(0, np.nan)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(window=14, min_periods=1).mean()

    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    df["OBV"] = obv
    df["OBV_slope"] = obv.diff(5) / (obv.rolling(20, min_periods=1).mean().replace(0, np.nan))

    df["Return_1d"] = close.pct_change(1)
    df["Return_5d"] = close.pct_change(5)
    df["Return_10d"] = close.pct_change(10)
    df["Volatility_10d"] = df["Return_1d"].rolling(window=10, min_periods=2).std()
    df["Volatility_20d"] = df["Return_1d"].rolling(window=20, min_periods=2).std()

    vol_sma = volume.rolling(window=20, min_periods=1).mean()
    df["Volume_ratio"] = volume / vol_sma.replace(0, np.nan)
    df["Price_to_SMA20"] = close / sma20.replace(0, np.nan)
    df["Day_of_week"] = df.index.dayofweek
    df["Month"] = df.index.month

    # ---------- New technical indicators ----------
    stoch_k, stoch_d = _stochastic(high, low, close)
    df["Stoch_K"] = stoch_k
    df["Stoch_D"] = stoch_d

    df["Williams_R"] = _williams_r(high, low, close)
    df["CCI"] = _cci(high, low, close)

    adx_val, plus_di, minus_di = _adx(high, low, close)
    df["ADX"] = adx_val
    df["Plus_DI"] = plus_di
    df["Minus_DI"] = minus_di

    df["ROC_10"] = _roc(close, 10)
    df["ROC_20"] = _roc(close, 20)

    df["Volume_Pressure"] = _volume_pressure(close, volume)

    # ---------- Multi-timeframe trend features ----------
    df["Trend_5"] = (close / df["SMA_5"].replace(0, np.nan) - 1)
    df["Trend_20"] = (close / df["SMA_20"].replace(0, np.nan) - 1)
    df["Trend_50"] = (close / df["SMA_50"].replace(0, np.nan) - 1)
    df["Trend_Alignment"] = (
        np.sign(df["Trend_5"]) + np.sign(df["Trend_20"]) + np.sign(df["Trend_50"])
    ) / 3.0

    # ---------- Market context features ----------
    df = _add_market_features(df, spy_df, vix_df)

    # ---------- Market regime features ----------
    df = _market_regime_features(df, adx_val)

    return df


# ---------------------------------------------------------------------------
# Feature column list (all features the models will use)
# ---------------------------------------------------------------------------

FEATURE_COLUMNS = [
    # Original
    "SMA_5", "SMA_20", "SMA_50", "EMA_12", "EMA_26",
    "RSI", "MACD", "MACD_signal", "MACD_diff",
    "BB_upper", "BB_lower", "BB_width", "BB_pctB",
    "ATR", "OBV", "OBV_slope",
    "Return_1d", "Return_5d", "Return_10d",
    "Volatility_10d", "Volatility_20d",
    "Volume_ratio", "Price_to_SMA20",
    "Day_of_week", "Month",
    # New technical
    "Stoch_K", "Stoch_D", "Williams_R", "CCI",
    "ADX", "Plus_DI", "Minus_DI",
    "ROC_10", "ROC_20",
    "Volume_Pressure",
    # Multi-timeframe
    "Trend_5", "Trend_20", "Trend_50", "Trend_Alignment",
    # Market context
    "Corr_SPY_60", "Relative_Strength_SPY", "SPY_trend",
    "VIX_level", "VIX_change", "High_Vol_Regime",
    # Regime
    "Is_Trending", "ATR_ratio",
]


# ---------------------------------------------------------------------------
# Walk-forward validation helpers
# ---------------------------------------------------------------------------

def _walk_forward_train(X, y, n_splits=5, min_train=120):
    """
    Generator that yields (train_idx, test_idx) in a walk-forward manner.
    Each fold uses all data up to a point for training and the next chunk for
    testing.
    """
    n = len(X)
    if n < min_train + n_splits:
        # Fall back: use everything except the last chunk
        split = max(min_train, int(n * 0.8))
        yield np.arange(split), np.arange(split, n)
        return

    test_size = max(1, (n - min_train) // n_splits)
    for i in range(n_splits):
        test_end = n - i * test_size
        test_start = test_end - test_size
        if test_start < min_train:
            break
        train_idx = np.arange(0, test_start)
        test_idx = np.arange(test_start, test_end)
        yield train_idx, test_idx


# ---------------------------------------------------------------------------
# Ensemble prediction for a single horizon
# ---------------------------------------------------------------------------

def _ensemble_predict(X_all, y_all, X_latest, n_splits=5):
    """
    Train an ensemble of GBR, RF, and Ridge via walk-forward validation.
    Returns (predicted_return, confidence_score, per_model_predictions,
    feature_importances).
    """
    models_configs = [
        ("GBR", GradientBoostingRegressor(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=42,
        )),
        ("RF", RandomForestRegressor(
            n_estimators=150, max_depth=5, random_state=42, n_jobs=-1,
        )),
        ("Ridge", Ridge(alpha=1.0)),
    ]

    model_preds = {}
    importances = np.zeros(X_all.shape[1])
    n_importance_models = 0

    # Keep track of walk-forward errors for confidence estimation
    wf_errors = []

    for name, model in models_configs:
        fold_preds_latest = []
        for train_idx, test_idx in _walk_forward_train(X_all, y_all, n_splits=n_splits):
            X_train, y_train = X_all[train_idx], y_all[train_idx]
            X_test, y_test = X_all[test_idx], y_all[test_idx]

            scaler = StandardScaler()
            X_train_s = scaler.fit_transform(X_train)
            X_test_s = scaler.transform(X_test)

            model_clone = _clone_model(model)
            model_clone.fit(X_train_s, y_train)

            test_pred = model_clone.predict(X_test_s)
            fold_error = np.mean(np.abs(test_pred - y_test))
            wf_errors.append(fold_error)

        # Final model: train on ALL available data
        scaler = StandardScaler()
        X_all_s = scaler.fit_transform(X_all)
        X_latest_s = scaler.transform(X_latest)

        final_model = _clone_model(model)
        final_model.fit(X_all_s, y_all)
        pred = final_model.predict(X_latest_s)[0]
        model_preds[name] = pred

        if hasattr(final_model, "feature_importances_"):
            importances += final_model.feature_importances_
            n_importance_models += 1

    if n_importance_models > 0:
        importances /= n_importance_models

    # Ensemble: weighted average (equal weights)
    all_preds = np.array(list(model_preds.values()))
    ensemble_pred = float(np.mean(all_preds))

    # Confidence: based on model agreement (lower spread = higher confidence)
    # and walk-forward accuracy
    pred_std = float(np.std(all_preds))
    avg_wf_error = float(np.mean(wf_errors)) if wf_errors else 0.01

    # Convert to 0-100 scale
    # Agreement component: if models agree perfectly, std=0 → 100
    agreement_score = max(0, 100 - pred_std * 5000)  # scale: 0.02 std ≈ 0 confidence
    # Accuracy component: lower WF error → higher confidence
    accuracy_score = max(0, 100 - avg_wf_error * 3000)

    confidence = min(95, max(5, 0.5 * agreement_score + 0.5 * accuracy_score))

    return ensemble_pred, confidence, model_preds, importances


def _clone_model(model):
    """Create a fresh copy of a scikit-learn estimator with the same params."""
    from sklearn.base import clone
    return clone(model)


# ---------------------------------------------------------------------------
# Prediction bounds
# ---------------------------------------------------------------------------

def _clamp_prediction(pred_return, historical_vol, horizon, is_etf):
    """
    Constrain the predicted return to reasonable bounds based on historical
    volatility and whether the ticker is an ETF or individual stock.
    """
    # Daily vol → horizon vol (simple sqrt-of-time scaling)
    horizon_vol = historical_vol * np.sqrt(horizon) if historical_vol > 0 else 0.02

    # Hard caps: ETFs are tighter than individual stocks
    if is_etf:
        daily_cap = 0.03  # 3% per day for ETFs
    else:
        daily_cap = 0.05  # 5% per day for stocks

    max_move = min(daily_cap * np.sqrt(horizon), horizon_vol * 2.5)
    # Floor at a tiny value so we don't clamp to zero
    max_move = max(max_move, 0.001)

    clamped = np.clip(pred_return, -max_move, max_move)
    return float(clamped)


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------

def _risk_assessment(volatility_10d, volatility_20d, atr, close, vix_level, adx):
    """Produce a simple risk assessment dict."""
    daily_vol_pct = float(volatility_20d) if not np.isnan(volatility_20d) else 0.01
    atr_pct = float(atr / close) if close > 0 else 0.01

    # Risk level
    if daily_vol_pct > 0.03 or atr_pct > 0.04:
        level = "High"
    elif daily_vol_pct > 0.015 or atr_pct > 0.02:
        level = "Medium"
    else:
        level = "Low"

    vix_val = float(vix_level) if not np.isnan(vix_level) else 20.0
    if vix_val > 30:
        market_stress = "Elevated"
    elif vix_val > 20:
        market_stress = "Moderate"
    else:
        market_stress = "Low"

    adx_val = float(adx) if not np.isnan(adx) else 20.0
    if adx_val > 25:
        trend_strength = "Strong"
    else:
        trend_strength = "Weak / Ranging"

    return {
        "level": level,
        "daily_volatility_pct": round(daily_vol_pct * 100, 2),
        "atr_pct": round(atr_pct * 100, 2),
        "market_stress": market_stress,
        "trend_strength": trend_strength,
    }


# ---------------------------------------------------------------------------
# Main prediction function
# ---------------------------------------------------------------------------

def predict_stock(ticker, days=7):
    """
    Generate stock price predictions for the next *days* trading days.

    Returns a dict with predictions, confidence, trend analysis,
    support/resistance, risk assessment, and sentiment data.
    """
    # ------------------------------------------------------------------
    # 1. Fetch data
    # ------------------------------------------------------------------
    df = get_stock_history(ticker, period="2y")
    if df is None or df.empty or len(df) < 100:
        return {"error": "Insufficient data for prediction"}

    spy_df, vix_df = _fetch_market_context()

    # ------------------------------------------------------------------
    # 2. Compute features
    # ------------------------------------------------------------------
    df = _compute_features(df, spy_df, vix_df)

    # Replace inf/-inf with NaN then fill
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.ffill()
    df = df.fillna(0)

    if len(df) < 60:
        return {"error": "Insufficient data after feature computation"}

    # ------------------------------------------------------------------
    # 3. Determine if ETF and historical vol
    # ------------------------------------------------------------------
    is_etf = ticker.upper() in _ETF_TICKERS
    hist_vol = float(df["Volatility_20d"].iloc[-1])
    if np.isnan(hist_vol) or hist_vol <= 0:
        hist_vol = 0.015  # default ~1.5% daily vol

    current_price = float(df["Close"].iloc[-1])

    # ------------------------------------------------------------------
    # 4. Generate predictions for each horizon
    # ------------------------------------------------------------------
    predictions = []
    all_confidences = []
    last_importances = np.zeros(len(FEATURE_COLUMNS))

    for horizon in range(1, days + 1):
        # Target: percentage return over this horizon
        target_col = f"Target_{horizon}d"
        df[target_col] = df["Close"].shift(-horizon) / df["Close"] - 1

        temp = df.dropna(subset=[target_col]).copy()
        if len(temp) < 80:
            # Not enough data for this horizon — extrapolate from previous
            if predictions:
                prev = predictions[-1]
                pred_pct = prev["predicted_change_pct"] / 100.0
                # Slightly decay toward zero for longer horizons with no data
                pred_pct *= 0.95
                pred_pct = _clamp_prediction(pred_pct, hist_vol, horizon, is_etf)
                predicted_price = current_price * (1 + pred_pct)
                predictions.append({
                    "day": horizon,
                    "predicted_price": round(float(predicted_price), 2),
                    "predicted_change_pct": round(float(pred_pct * 100), 2),
                    "direction": "up" if pred_pct > 0 else "down",
                })
                all_confidences.append(10.0)  # very low confidence
            continue

        # Ensure feature columns are present and in order
        available_features = [c for c in FEATURE_COLUMNS if c in temp.columns]
        X = temp[available_features].values
        y = temp[target_col].values

        latest_features = df[available_features].iloc[-1:].values

        ensemble_pred, confidence, model_preds, importances = _ensemble_predict(
            X, y, latest_features, n_splits=5,
        )

        # Clamp to reasonable bounds
        clamped_pred = _clamp_prediction(ensemble_pred, hist_vol, horizon, is_etf)

        predicted_price = current_price * (1 + clamped_pred)

        # If clamping significantly changed the prediction, reduce confidence
        if abs(ensemble_pred) > 0 and abs(clamped_pred / ensemble_pred) < 0.5:
            confidence *= 0.6

        predictions.append({
            "day": horizon,
            "predicted_price": round(float(predicted_price), 2),
            "predicted_change_pct": round(float(clamped_pred * 100), 2),
            "direction": "up" if clamped_pred > 0 else "down",
        })
        all_confidences.append(float(confidence))
        last_importances = importances

    if not predictions:
        return {"error": "Could not generate predictions"}

    # ------------------------------------------------------------------
    # 5. Overall confidence
    # ------------------------------------------------------------------
    overall_confidence = round(float(np.mean(all_confidences)), 1) if all_confidences else 0.0

    # ------------------------------------------------------------------
    # 6. Feature importances (top factors)
    # ------------------------------------------------------------------
    available_features = [c for c in FEATURE_COLUMNS if c in df.columns]
    n_feats = min(len(available_features), len(last_importances))
    feature_importance = dict(zip(available_features[:n_feats], last_importances[:n_feats]))
    top_features = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)[:5]

    # ------------------------------------------------------------------
    # 7. Trend analysis
    # ------------------------------------------------------------------
    trend = _trend_analysis(df["Close"], df["SMA_5"], df["SMA_20"], df["SMA_50"])

    # ------------------------------------------------------------------
    # 8. Support / Resistance
    # ------------------------------------------------------------------
    sup_res = _support_resistance(df["High"], df["Low"], df["Close"])

    # ------------------------------------------------------------------
    # 9. Risk assessment
    # ------------------------------------------------------------------
    vix_level = float(df["VIX_level"].iloc[-1]) if "VIX_level" in df.columns else 20.0
    adx_val = float(df["ADX"].iloc[-1]) if "ADX" in df.columns else 20.0
    risk = _risk_assessment(
        float(df["Volatility_10d"].iloc[-1]),
        float(df["Volatility_20d"].iloc[-1]),
        float(df["ATR"].iloc[-1]),
        current_price,
        vix_level,
        adx_val,
    )

    # ------------------------------------------------------------------
    # 10. Sentiment
    # ------------------------------------------------------------------
    try:
        sentiment = get_overall_sentiment(ticker)
    except Exception:
        sentiment = {"score": 0, "label": "Neutral", "article_count": 0}

    # ------------------------------------------------------------------
    # 11. Assemble result
    # ------------------------------------------------------------------
    return {
        "ticker": ticker.upper(),
        "current_price": round(float(current_price), 2),
        "predictions": predictions,
        "confidence": overall_confidence,
        "sentiment": sentiment,
        "top_factors": [
            {"feature": f, "importance": round(float(v), 4)} for f, v in top_features
        ],
        "trend_analysis": trend,
        "support_resistance": sup_res,
        "risk_assessment": risk,
        "disclaimer": (
            "This prediction is generated by a statistical model for informational "
            "purposes only and should NOT be used as investment advice. Past performance "
            "does not guarantee future results."
        ),
    }
