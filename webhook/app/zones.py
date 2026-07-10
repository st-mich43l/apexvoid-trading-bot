"""Displacement, supply/demand, order-block, and mitigation logic."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from app.pa_math import atr_at, atr_series, body_fraction, candle_direction
from app.pa_types import Break, Leg, Level, Zone


def displacement(
  df: pd.DataFrame,
  atr: pd.Series | None = None,
  k: float = 1.5,
  body_frac: float = 0.55,
) -> list[Leg]:
  if df.empty:
    return []
  atr = atr if atr is not None else atr_series(df)
  legs: list[Leg] = []
  start: int | None = None
  direction: str | None = None
  for i, row in enumerate(df.itertuples()):
    current = "up" if row.close > row.open else "down" if row.close < row.open else None
    if current is None:
      _append_leg(df, atr, legs, start, i - 1, direction, k, body_frac)
      start, direction = None, None
      continue
    if direction is None:
      start, direction = i, current
      continue
    if current != direction:
      _append_leg(df, atr, legs, start, i - 1, direction, k, body_frac)
      start, direction = i, current
  _append_leg(df, atr, legs, start, len(df) - 1, direction, k, body_frac)
  return legs


def supply_demand(df: pd.DataFrame, legs: list[Leg]) -> list[Zone]:
  zones: list[Zone] = []
  for leg in legs:
    if leg.start <= 0:
      continue
    base_start = max(0, leg.start - 3)
    base = df.iloc[base_start:leg.start]
    if base.empty:
      continue
    side = "demand" if leg.direction == "up" else "supply"
    origin = leg.start - 1
    zones.append(Zone(
      bottom=float(base["low"].min()),
      top=float(base["high"].max()),
      side=side,
      origin_index=origin,
      created_ts=df.index[origin],
      source="supply_demand",
    ))
  return zones


def order_blocks(
  df: pd.DataFrame,
  legs: list[Leg],
  breaks: list[Break],
  zone_width: str = "body",
) -> list[Zone]:
  zones: list[Zone] = []
  for leg in legs:
    bos = _causing_bos(leg, breaks)
    if bos is None:
      continue
    origin = _last_opposite_candle(df, leg)
    if origin is None:
      continue
    row = df.iloc[origin]
    bottom, top = _zone_band(row, zone_width)
    side = "demand" if leg.direction == "up" else "supply"
    zones.append(Zone(
      bottom=bottom,
      top=top,
      side=side,
      origin_index=origin,
      created_ts=df.index[origin],
      source="order_block",
      break_kind=bos.kind,
      break_index=bos.index,
    ))
  return zones


def flip_zones(levels: list[Level], breaks: list[Break]) -> list[Zone]:
  zones: list[Zone] = []
  seen: set[tuple[float, str]] = set()
  for item in breaks:
    for level in levels:
      if abs(item.level - level.price) > max(level.band, 0.0):
        continue
      side = "demand" if item.direction == "up" else "supply"
      key = (round(level.price, 6), side)
      if key in seen:
        continue
      seen.add(key)
      zones.append(Zone(
        bottom=level.price - level.band,
        top=level.price + level.band,
        side=side,
        origin_index=item.index,
        created_ts=item.ts,
        source="flip_zone",
        break_kind=item.kind,
        break_index=item.index,
      ))
  return zones


def mark_mitigation(zones: list[Zone], df: pd.DataFrame) -> list[Zone]:
  stamped: list[Zone] = []
  for zone in zones:
    touches = 0
    in_touch = False
    start_from = zone.break_index if zone.break_index is not None else zone.origin_index
    start = max(0, start_from + 1)
    for i in range(start, len(df)):
      row = df.iloc[i]
      touched = float(row["low"]) <= zone.top and float(row["high"]) >= zone.bottom
      if touched and not in_touch:
        touches += 1
      in_touch = touched
    stamped.append(replace(
      zone,
      touches=touches,
      mitigated=touches > 0,
    ))
  return stamped


def fvg(df: pd.DataFrame) -> list[Zone]:
  zones: list[Zone] = []
  for i in range(2, len(df)):
    older = df.iloc[i - 2]
    cur = df.iloc[i]
    if float(older["high"]) < float(cur["low"]):
      zones.append(Zone(
        float(older["high"]),
        float(cur["low"]),
        "demand",
        i,
        df.index[i],
        source="bullish_fvg",
      ))
    if float(older["low"]) > float(cur["high"]):
      zones.append(Zone(
        float(cur["high"]),
        float(older["low"]),
        "supply",
        i,
        df.index[i],
        source="bearish_fvg",
      ))
  return zones


def _append_leg(
  df: pd.DataFrame,
  atr: pd.Series,
  legs: list[Leg],
  start: int | None,
  end: int,
  direction: str | None,
  k: float,
  body_frac: float,
) -> None:
  if start is None or direction is None or end < start:
    return
  open_ = float(df["open"].iloc[start])
  close = float(df["close"].iloc[end])
  size = close - open_ if direction == "up" else open_ - close
  if size < atr_at(atr, end) * k:
    return
  run = df.iloc[start:end + 1]
  strong = sum(1 for _, row in run.iterrows() if body_fraction(row) >= body_frac)
  if strong < max(1, len(run) // 2):
    return
  legs.append(Leg(start, end, direction, size))


def _causing_bos(leg: Leg, breaks: list[Break]) -> Break | None:
  for item in breaks:
    if item.kind != "BOS" or item.direction != leg.direction:
      continue
    if leg.start <= item.index <= leg.end:
      return item
  return None


def _last_opposite_candle(df: pd.DataFrame, leg: Leg) -> int | None:
  opposite = "down" if leg.direction == "up" else "up"
  for i in range(leg.start - 1, -1, -1):
    if candle_direction(df.iloc[i]) == opposite:
      return i
  return None


def _zone_band(row: pd.Series, zone_width: str) -> tuple[float, float]:
  if zone_width == "range":
    return float(row["low"]), float(row["high"])
  return (
    min(float(row["open"]), float(row["close"])),
    max(float(row["open"]), float(row["close"])),
  )
