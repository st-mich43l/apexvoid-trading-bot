"""Pure price-action structure helpers over closed-bar windows."""

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class Swing:
  index: pd.Timestamp
  kind: str
  price: float
  label: str


@dataclass(frozen=True)
class Level:
  price: float
  kind: str
  touches: int = 1


@dataclass(frozen=True)
class Zone:
  low: float
  high: float
  kind: str


def _tol(df: pd.DataFrame) -> float:
  if df.empty:
    return 0.0
  span = float(df["high"].max() - df["low"].min())
  return max(span * 0.003, 0.1)


def swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> list[Swing]:
  result: list[Swing] = []
  last_high: float | None = None
  last_low: float | None = None
  for i in range(left, max(left, len(df) - right)):
    window = df.iloc[i - left:i + right + 1]
    row = df.iloc[i]
    high = float(row["high"])
    low = float(row["low"])
    idx = df.index[i]
    if high >= float(window["high"].max()) and (
      window["high"].eq(high).sum() == 1
    ):
      label = "HH" if last_high is None or high > last_high else "LH"
      last_high = high
      result.append(Swing(idx, "high", high, label))
    if low <= float(window["low"].min()) and (
      window["low"].eq(low).sum() == 1
    ):
      label = "HL" if last_low is None or low > last_low else "LL"
      last_low = low
      result.append(Swing(idx, "low", low, label))
  return sorted(result, key=lambda item: item.index)


def market_structure(items: list[Swing]) -> str:
  highs = [s for s in items if s.kind == "high"]
  lows = [s for s in items if s.kind == "low"]
  if len(highs) >= 2 and len(lows) >= 2:
    if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
      return "up"
    if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
      return "down"
  labels = {s.label for s in items[-4:]}
  if {"HH", "HL"} <= labels:
    return "up"
  if {"LH", "LL"} <= labels:
    return "down"
  return "range"


def key_levels(df: pd.DataFrame) -> list[Level]:
  tolerance = _tol(df)
  raw = [
    *(float(s.price) for s in swings(df, 2, 2)),
  ]
  levels: list[Level] = []
  for price in raw:
    for idx, level in enumerate(levels):
      if abs(level.price - price) <= tolerance:
        touches = level.touches + 1
        merged = ((level.price * level.touches) + price) / touches
        levels[idx] = Level(merged, level.kind, touches)
        break
    else:
      levels.append(Level(price, "reaction", 1))
  if not df.empty:
    close = float(df["close"].iloc[-1])
    base = round(close / 10) * 10
    for price in (base - 10, base, base + 10):
      levels.append(Level(float(price), "round", 1))
  return sorted(levels, key=lambda level: level.price)


def flip_zones(df: pd.DataFrame) -> list[Zone]:
  zones = []
  for level in key_levels(df):
    retest = find_retest(df, level.price)
    if retest:
      zones.append(retest)
  return zones


def order_blocks(df: pd.DataFrame) -> list[Zone]:
  zones: list[Zone] = []
  for i in range(1, len(df)):
    prev = df.iloc[i - 1]
    cur = df.iloc[i]
    impulse = abs(float(cur["close"] - cur["open"]))
    avg_range = float((df["high"] - df["low"]).rolling(10).mean().iloc[i] or 0)
    if not math.isfinite(avg_range):
      avg_range = 0
    if avg_range and impulse < avg_range:
      continue
    if cur["close"] > cur["open"] and prev["close"] < prev["open"]:
      zones.append(Zone(float(prev["low"]), float(prev["high"]), "bullish_ob"))
    if cur["close"] < cur["open"] and prev["close"] > prev["open"]:
      zones.append(Zone(float(prev["low"]), float(prev["high"]), "bearish_ob"))
  return zones


def fvg(df: pd.DataFrame) -> list[Zone]:
  zones: list[Zone] = []
  for i in range(2, len(df)):
    older = df.iloc[i - 2]
    cur = df.iloc[i]
    if float(older["high"]) < float(cur["low"]):
      zones.append(Zone(float(older["high"]), float(cur["low"]), "bullish_fvg"))
    if float(older["low"]) > float(cur["high"]):
      zones.append(Zone(float(cur["high"]), float(older["low"]), "bearish_fvg"))
  return zones


def equal_highs_lows(df: pd.DataFrame) -> list[Level]:
  tolerance = _tol(df)
  levels: list[Level] = []
  highs = [s for s in swings(df, 1, 1) if s.kind == "high"]
  lows = [s for s in swings(df, 1, 1) if s.kind == "low"]
  for kind, items in (("equal_high", highs), ("equal_low", lows)):
    for i, item in enumerate(items):
      touches = 1 + sum(
        1 for other in items[i + 1:]
        if abs(other.price - item.price) <= tolerance
      )
      if touches >= 2:
        levels.append(Level(item.price, kind, touches))
  return levels


def liquidity_sweep(df: pd.DataFrame, level: float | Level) -> str | None:
  if df.empty:
    return None
  price = level.price if isinstance(level, Level) else float(level)
  row = df.iloc[-1]
  if float(row["high"]) > price and float(row["close"]) < price:
    return "sell"
  if float(row["low"]) < price and float(row["close"]) > price:
    return "buy"
  return None


def is_break(df: pd.DataFrame, level: float | Level) -> str | None:
  if len(df) < 2:
    return None
  price = level.price if isinstance(level, Level) else float(level)
  prev_close = float(df["close"].iloc[-2])
  close = float(df["close"].iloc[-1])
  if prev_close <= price < close:
    return "up"
  if prev_close >= price > close:
    return "down"
  return None


def find_retest(df: pd.DataFrame, level: float | Level) -> Zone | None:
  if len(df) < 3:
    return None
  price = level.price if isinstance(level, Level) else float(level)
  tolerance = _tol(df)
  closes = df["close"].astype(float)
  break_idx: int | None = None
  direction: str | None = None
  for i in range(1, len(df) - 1):
    if closes.iloc[i - 1] <= price < closes.iloc[i]:
      break_idx, direction = i, "buy"
    elif closes.iloc[i - 1] >= price > closes.iloc[i]:
      break_idx, direction = i, "sell"
  if break_idx is None or direction is None:
    return None
  for i in range(break_idx + 1, len(df)):
    row = df.iloc[i]
    touched = float(row["low"]) - tolerance <= price <= float(row["high"]) + tolerance
    if not touched:
      continue
    if direction == "buy" and float(row["close"]) >= price:
      return Zone(price - tolerance, price + tolerance, "retest_support")
    if direction == "sell" and float(row["close"]) <= price:
      return Zone(price - tolerance, price + tolerance, "retest_resistance")
  return None


def entry_zone(
  df: pd.DataFrame,
  level: float | Level,
  direction: str,
) -> Zone:
  price = level.price if isinstance(level, Level) else float(level)
  tolerance = _tol(df)
  zones = order_blocks(df) + flip_zones(df) + fvg(df)
  for zone in reversed(zones):
    if zone.low - tolerance <= price <= zone.high + tolerance:
      return zone
  kind = "demand" if direction.upper() == "BUY" else "supply"
  return Zone(price - tolerance, price + tolerance, kind)
