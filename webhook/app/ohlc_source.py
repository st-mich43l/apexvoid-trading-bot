"""OHLC window source backed by ctrader-feed Redis bars."""

import json
from typing import Any

import pandas as pd

from app import redis_state


def _bar_key(symbol: str, tf: str) -> str:
  return f"bars:{symbol.upper()}:{tf.upper()}"


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
      bars.append({
        "t": float(ts),
        "open": float(data["o"]),
        "high": float(data["h"]),
        "low": float(data["l"]),
        "close": float(data["c"]),
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
