"""OHLC window source backed by ctrader-feed Redis bars."""

import json
from typing import Any

import pandas as pd

from app import redis_state
from app.symbols import SYMBOLS


def _bar_key(symbol: str, tf: str) -> str:
  return f"bars:{symbol.upper()}:{tf.upper()}"


def _legacy_price_factor(symbol: str) -> float:
  """Return the old bad cTrader decode factor for symbols below 5 digits."""
  digits = int(SYMBOLS.get(symbol.upper(), {}).get("digits", 5))
  return float(10 ** max(0, 5 - digits))


def _normalize_price(symbol: str, value: float) -> float:
  """Normalize bars written before ctrader-feed used Open API price scale.

  The old decoder divided trendbar prices by symbol display digits. For XAU
  that turned 4105.50 into 4105500. Keep normal values untouched while fixing
  obviously inflated legacy bars still present in Redis windows.
  """
  factor = _legacy_price_factor(symbol)
  if factor > 1 and abs(value) >= 100_000:
    return value / factor
  return value


class RedisOHLCSource:
  """Read closed OHLCV bars from Redis ZSETs populated by ctrader-feed."""

  def __init__(self, client: Any | None = None):
    self.client = client or redis_state.get_client()

  async def window(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
    rows = await self.client.zrevrange(
      _bar_key(symbol, tf),
      0,
      max(0, n - 1),
      withscores=True,
    )
    bars = []
    for member, score in rows:
      raw = member.decode() if isinstance(member, bytes) else member
      data = json.loads(raw)
      ts = data.get("t", score)
      open_ = _normalize_price(symbol, float(data["o"]))
      high = _normalize_price(symbol, float(data["h"]))
      low = _normalize_price(symbol, float(data["l"]))
      close = _normalize_price(symbol, float(data["c"]))
      bars.append({
        "t": float(ts),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": float(data.get("v", 0) or 0),
      })
    bars.sort(key=lambda row: row["t"])
    if not bars:
      return pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([], tz="UTC", name="time"),
      )
    df = pd.DataFrame(bars)
    index = pd.to_datetime(df.pop("t"), unit="s", utc=True)
    df.index = pd.DatetimeIndex(index, name="time")
    return df[["open", "high", "low", "close", "volume"]]
