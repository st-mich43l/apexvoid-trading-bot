"""Closed OHLC windows backed by ctrader-feed Redis bars.

This is market-data access for execution, reporting and manual tooling. It is
not a technical-analysis implementation; automatic technical decisions come
from the Go Analysis Engine.
"""

import json
from typing import Any

import pandas as pd

from app.core.config import runtime_config
from app.persistence import redis_state
from app.core.symbols import digits_for


def _bar_key(symbol: str, tf: str) -> str:
  return f"bars:{symbol.upper()}:{tf.upper()}"


def window_for_timeframe(
  tf: str,
  *,
  default: int | None = None,
  root: Any | None = None,
) -> int:
  """Resolve the configured closed-bar lookback for one timeframe."""
  cfg = runtime_config if root is None else root
  lookbacks = getattr(cfg, "market_data", None)
  if lookbacks is not None:
    lookbacks = lookbacks.lookbacks
  else:
    # Native YAML keeps shared analysis inputs below ``analysis``.  Per-
    # instrument lookbacks are applied by instrument composition where a
    # symbol is known; this helper has historically been symbol-less, so use
    # the shared scanner window as its safe fallback.
    scanner = cfg.analysis.scanner
    lookbacks = None
  key = tf.upper()
  if lookbacks is not None and key == "H1":
    return max(50, int(lookbacks.h1_bars))
  if lookbacks is not None and key == "M15":
    return max(50, int(lookbacks.m15_bars))
  if lookbacks is not None and key == "M5":
    return max(50, int(lookbacks.m5_bars))
  if lookbacks is not None and key == "M1":
    return max(50, int(lookbacks.m1_bars))
  fallback = (
    cfg.market_data.scanner.window
    if hasattr(cfg, "market_data")
    else scanner.window
  )
  return max(50, int(default if default is not None else fallback))


def _legacy_price_factor(symbol: str) -> float:
  """Return the old cTrader decode factor for symbols below five digits."""
  try:
    digits = digits_for(symbol)
  except KeyError:
    digits = 5
  return float(10 ** max(0, 5 - digits))


def _normalize_price(symbol: str, value: float) -> float:
  """Normalize bars written before ctrader-feed used Open API price scale."""
  factor = _legacy_price_factor(symbol)
  if factor > 1 and abs(value) >= 100_000:
    return value / factor
  return value


CLOSED_BAR_TIMEFRAMES = ("M1", "M5", "M15", "H1")


class RedisOHLCSource:
  """Read closed OHLCV bars from Redis ZSETs populated by ctrader-feed."""

  def __init__(self, client: Any | None = None):
    self.client = client or redis_state.get_client()
    self._bar_cache: dict[tuple[str, str], tuple[int, pd.DataFrame]] | None = None

  def begin_closed_bar_cache(self) -> None:
    self._bar_cache = {}

  def end_closed_bar_cache(self) -> None:
    self._bar_cache = None

  async def window(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
    count = max(0, int(n))
    key = (symbol.upper(), tf.upper())
    cache = self._bar_cache
    if cache is not None:
      hit = cache.get(key)
      if hit is not None and hit[0] >= count:
        df = hit[1]
        if count <= 0 or df.empty:
          return df.copy()
        return df.tail(count).copy()

    df = await self._fetch_window(symbol, tf, count)
    if cache is not None:
      prev = cache.get(key)
      if prev is None or count > prev[0]:
        cache[key] = (count, df)
    return df.copy()

  async def _fetch_window(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
    rows = await self.client.zrevrange(
      _bar_key(symbol, tf), 0, max(0, n - 1), withscores=True,
    )
    bars = []
    for member, score in rows:
      raw = member.decode() if isinstance(member, bytes) else member
      data = json.loads(raw)
      ts = data.get("t", score)
      bars.append({
        "t": float(ts),
        "open": _normalize_price(symbol, float(data["o"])),
        "high": _normalize_price(symbol, float(data["h"])),
        "low": _normalize_price(symbol, float(data["l"])),
        "close": _normalize_price(symbol, float(data["c"])),
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
