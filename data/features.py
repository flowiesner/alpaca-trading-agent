from datetime import timezone
import pandas as pd
import numpy as np
from zoneinfo import ZoneInfo

from data.market import get_daily_bars, get_hourly_bars, get_intraday_bars_today
import config

VIENNA = ZoneInfo("Europe/Vienna")
# US market open in UTC (15:30 UTC = 09:30 ET during EDT; fine as approximation)
MARKET_OPEN_UTC_HOUR = 13   # 09:30 ET = 13:30 UTC (EDT); adjust for EST if needed
MARKET_OPEN_UTC_MINUTE = 30


def _classify_trend(close: pd.Series, ma20: pd.Series, ma50: pd.Series) -> str:
    c, m20, m50 = close.iloc[-1], ma20.iloc[-1], ma50.iloc[-1]
    if c > m20 and m20 > m50:
        return "UPTREND"
    if c < m20 and m20 < m50:
        return "DOWNTREND"
    return "SIDEWAYS"


def compute_features() -> dict:
    # 100 calendar days ≈ 70 trading days, enough for 50-period MA with buffer
    daily = get_daily_bars(config.SYMBOL, days_back=100)
    hourly = get_hourly_bars(config.SYMBOL, days_back=10)
    intraday = get_intraday_bars_today(config.SYMBOL)

    # --- Price & Market Position ---
    current_price = float(daily["close"].iloc[-1])
    high_52w = float(daily["high"].rolling(252, min_periods=1).max().iloc[-1])
    low_52w = float(daily["low"].rolling(252, min_periods=1).min().iloc[-1])
    pct_from_52w_high = round((current_price - high_52w) / high_52w * 100, 2)
    pct_from_52w_low = round((current_price - low_52w) / low_52w * 100, 2)

    # --- Trend (daily) ---
    ma20 = daily["close"].rolling(20).mean()
    ma50 = daily["close"].rolling(50).mean()
    trend_daily = _classify_trend(daily["close"], ma20, ma50)
    distance_from_20ma = round((current_price - ma20.iloc[-1]) / ma20.iloc[-1] * 100, 2)
    distance_from_50ma = round((current_price - ma50.iloc[-1]) / ma50.iloc[-1] * 100, 2)

    # --- Trend (weekly) – aggregate daily bars to weekly Friday closes ---
    weekly = daily["close"].resample("W-FRI").last().dropna()
    if len(weekly) >= 50:
        wma20 = weekly.rolling(20).mean()
        wma50 = weekly.rolling(50).mean()
    else:
        # fallback with min_periods
        wma20 = weekly.rolling(20, min_periods=3).mean()
        wma50 = weekly.rolling(50, min_periods=5).mean()
    trend_weekly = _classify_trend(weekly, wma20, wma50)

    # --- Momentum ---
    closes = daily["close"]
    price_change_1d = round((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100, 2)
    price_change_5d = round((closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100, 2)
    price_change_20d = round((closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21] * 100, 2)
    gap_today = round((daily["open"].iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100, 2)

    # --- Volatility ---
    log_returns = np.log(closes / closes.shift(1)).dropna()
    realized_vol_14d = float(log_returns.iloc[-14:].std() * np.sqrt(252) * 100)
    avg_vol_60d = float(log_returns.iloc[-60:].std() * np.sqrt(252) * 100)
    vol_ratio = realized_vol_14d / avg_vol_60d if avg_vol_60d > 0 else 1.0
    if vol_ratio < 0.8:
        volatility_regime = "LOW"
    elif vol_ratio > 1.2:
        volatility_regime = "HIGH"
    else:
        volatility_regime = "NORMAL"

    last_5_days = daily.iloc[-5:]
    weekly_range_pct = round(
        (last_5_days["high"].max() - last_5_days["low"].min()) / last_5_days["low"].min() * 100, 2
    )

    # --- Volume ---
    vol_20d_avg = float(daily["volume"].iloc[-20:].mean())
    today_vol = float(daily["volume"].iloc[-1])
    volume_ratio_today = round(today_vol / vol_20d_avg, 2) if vol_20d_avg > 0 else 1.0

    last_5_vols = daily["volume"].iloc[-5:]
    if last_5_vols.iloc[-1] > last_5_vols.iloc[0] * 1.05:
        volume_trend_5d = "INCREASING"
    elif last_5_vols.iloc[-1] < last_5_vols.iloc[0] * 0.95:
        volume_trend_5d = "DECREASING"
    else:
        volume_trend_5d = "FLAT"

    # --- Intraday ---
    session_open_move, price_vs_open, intraday_range_pct = _compute_intraday(intraday, daily)

    # --- Macro (VIXY as VIX proxy) ---
    vix_current, vix_change_1d = _compute_vixy()

    # --- SPY vs QQQ 5D ---
    spy_vs_qqq_5d = _compute_spy_vs_qqq()

    return {
        "current_price": round(current_price, 2),
        "pct_from_52w_high": pct_from_52w_high,
        "pct_from_52w_low": pct_from_52w_low,
        "trend_daily": trend_daily,
        "trend_weekly": trend_weekly,
        "distance_from_20ma": distance_from_20ma,
        "distance_from_50ma": distance_from_50ma,
        "price_change_1d": price_change_1d,
        "price_change_5d": price_change_5d,
        "price_change_20d": price_change_20d,
        "gap_today": gap_today,
        "volatility_regime": volatility_regime,
        "weekly_range_pct": weekly_range_pct,
        "volume_ratio_today": volume_ratio_today,
        "volume_trend_5d": volume_trend_5d,
        "session_open_move": session_open_move,
        "price_vs_open": price_vs_open,
        "intraday_range_pct": intraday_range_pct,
        "vix_current": vix_current,
        "vix_change_1d": vix_change_1d,
        "spy_vs_qqq_5d": spy_vs_qqq_5d,
    }


def _compute_intraday(intraday: pd.DataFrame, daily: pd.DataFrame) -> tuple[float, float, float]:
    today_open = float(daily["open"].iloc[-1])
    current_price = float(daily["close"].iloc[-1])

    price_vs_open = round((current_price - today_open) / today_open * 100, 2)

    if intraday.empty:
        return 0.0, price_vs_open, 0.0

    # Filter to today's session only (UTC)
    now_utc = intraday.index[-1]
    today_date = now_utc.date()

    # Session open bars: 13:30–14:00 UTC (first 30 min)
    session_start = pd.Timestamp(today_date, tz=timezone.utc).replace(
        hour=MARKET_OPEN_UTC_HOUR, minute=MARKET_OPEN_UTC_MINUTE
    )
    session_open_end = session_start + pd.Timedelta(minutes=30)

    today_bars = intraday[intraday.index.date == today_date]
    open_bars = today_bars[(today_bars.index >= session_start) & (today_bars.index < session_open_end)]

    if not open_bars.empty:
        open_price_at_session = float(open_bars["open"].iloc[0])
        close_at_30min = float(open_bars["close"].iloc[-1])
        session_open_move = round((close_at_30min - open_price_at_session) / open_price_at_session * 100, 2)
    else:
        session_open_move = 0.0

    if not today_bars.empty:
        session_high = float(today_bars["high"].max())
        session_low = float(today_bars["low"].min())
        session_open_price = float(today_bars["open"].iloc[0])
        intraday_range_pct = round((session_high - session_low) / session_open_price * 100, 2)
    else:
        intraday_range_pct = 0.0

    return session_open_move, price_vs_open, intraday_range_pct


def _compute_vixy() -> tuple[float, float]:
    from data.market import get_daily_bars as _daily
    try:
        vixy = _daily("VIXY", days_back=5)
        if len(vixy) >= 2:
            vix_current = round(float(vixy["close"].iloc[-1]), 2)
            vix_change_1d = round(
                (vixy["close"].iloc[-1] - vixy["close"].iloc[-2]) / vixy["close"].iloc[-2] * 100, 2
            )
            return vix_current, vix_change_1d
    except Exception:
        pass
    return 0.0, 0.0


def _compute_spy_vs_qqq() -> str:
    from data.market import get_daily_bars as _daily
    try:
        spy = _daily(config.SYMBOL, days_back=10)
        qqq = _daily("QQQ", days_back=10)
        if len(spy) >= 6 and len(qqq) >= 6:
            spy_5d = (spy["close"].iloc[-1] - spy["close"].iloc[-6]) / spy["close"].iloc[-6]
            qqq_5d = (qqq["close"].iloc[-1] - qqq["close"].iloc[-6]) / qqq["close"].iloc[-6]
            diff = spy_5d - qqq_5d
            if diff > 0.002:
                return "SPY_OUTPERFORMS"
            if diff < -0.002:
                return "QQQ_OUTPERFORMS"
            return "EQUAL"
    except Exception:
        pass
    return "EQUAL"
