"""OHLC window source backed by ctrader-feed Redis bars."""

import json
from typing import Any

import pandas as pd

from app.core.config import runtime_config
from app.persistence import redis_state
from app.core.symbols import digits_for, is_known_symbol


def _bar_key(symbol: str, tf: str) -> str:
  return f"bars:{symbol.upper()}:{tf.upper()}"


_LOOKBACK_BY_TF = {
  "H1": lambda: runtime_config.market_data.lookbacks.h1_bars,
  "M15": lambda: runtime_config.market_data.lookbacks.m15_bars,
  "M5": lambda: runtime_config.market_data.lookbacks.m5_bars,
  "M1": lambda: runtime_config.market_data.lookbacks.m1_bars,
}


def window_for_timeframe(tf: str, *, default: int | None = None) -> int:
  """Configured closed-bar lookback for one timeframe (H1/M15/M5/M1).

  The single place that resolves a timeframe string to a bar count -
  detectors and callers must never hardcode a per-timeframe lookback
  themselves. Falls back to `default` (or the canonical scanner window) for any
  timeframe with no dedicated XAU_LOOKBACK_*_BARS setting (e.g. a symbol
  extension that hasn't been given its own lookback tuning yet).
  """
  lookback = _LOOKBACK_BY_TF.get(tf.upper())
  if lookback is not None:
    return max(50, int(lookback()))
  fallback = runtime_config.market_data.scanner.window
  return max(50, int(default if default is not None else fallback))


def _legacy_price_factor(symbol: str) -> float:
  """Return the old bad cTrader decode factor for symbols below 5 digits."""
  try:
    digits = digits_for(symbol)
  except KeyError:
    digits = 5
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


CLOSED_BAR_TIMEFRAMES = ("M1", "M5", "M15", "H1")


def prefetch_timeframes_for_closed_bar(closed_tf: str) -> tuple[str, ...]:
  """HTF windows are for scanner/M5. M1 handlers fill the cache on demand."""
  if str(closed_tf or "").upper() == "M1":
    return ()
  return CLOSED_BAR_TIMEFRAMES


async def prefetch_closed_bar_windows(
  source: Any,
  symbol: str,
  *,
  closed_tf: str | None = None,
) -> None:
  """Warm the shared bar cache for handlers that need HTF this tick."""
  window = getattr(source, "window", None)
  if not callable(window):
    return
  for tf in prefetch_timeframes_for_closed_bar(closed_tf or "M5"):
    await window(symbol, tf, window_for_timeframe(tf))


class RedisOHLCSource:
  """Read closed OHLCV bars from Redis ZSETs populated by ctrader-feed."""

  def __init__(self, client: Any | None = None):
    self.client = client or redis_state.get_client()
    self._bar_cache: dict[tuple[str, str], tuple[int, pd.DataFrame]] | None = None

  def begin_closed_bar_cache(self) -> None:
    """Reuse ZRANGE results across dispatcher handlers for one closed bar."""
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
