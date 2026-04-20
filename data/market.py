from datetime import datetime, timedelta, timezone
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import config

_client: StockHistoricalDataClient | None = None


def _get_client() -> StockHistoricalDataClient:
    global _client
    if _client is None:
        _client = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )
    return _client


def _fetch_bars(symbol: str, timeframe: TimeFrame, days_back: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = _get_client().get_stock_bars(req)
    df = bars.df
    if df.empty:
        return df
    # alpaca-py returns MultiIndex (symbol, timestamp) – drop symbol level
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.dropna()


def get_daily_bars(symbol: str, days_back: int = 65) -> pd.DataFrame:
    return _fetch_bars(symbol, TimeFrame.Day, days_back)


def get_hourly_bars(symbol: str, days_back: int = 10) -> pd.DataFrame:
    return _fetch_bars(symbol, TimeFrame.Hour, days_back)


def get_4h_bars(symbol: str, days_back: int = 25) -> pd.DataFrame:
    hourly = get_hourly_bars(symbol, days_back)
    if hourly.empty:
        return hourly
    resampled = hourly.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return resampled.dropna()


def get_intraday_bars_today(symbol: str) -> pd.DataFrame:
    """Return all 1-minute bars for today's session (used for session_open_move)."""
    return _fetch_bars(symbol, TimeFrame.Minute, days_back=2)
