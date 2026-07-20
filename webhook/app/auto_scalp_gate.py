"""Independent OHLC-only gate for high-frequency automatic XAU scalping."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


ATR_LENGTH = 14
M1_WINDOW = 120
M5_WINDOW = 96
M15_WINDOW = 64
M5_PIVOT_SPAN = 1
M15_PIVOT_SPAN = 1
RAIL_CLUSTER_ATR = 0.28
RAIL_HALF_WIDTH_ATR = 0.12
RAIL_MERGE_ATR = 0.35
MAX_MERGED_RAIL_WIDTH_ATR = 0.70
MAX_RAIL_HALF_WIDTH_PIPS = 8
MAX_MERGED_RAIL_WIDTH_PIPS = 16
M1_TOUCH_ATR = 0.25
M1_BREAK_BUFFER_ATR = 0.06
M5_CONTEXT_LOOKBACK = 12
M5_MAX_RANGE_EFFICIENCY = 0.75
M5_MAX_ADVERSE_MOMENTUM_ATR = 1.20
MIN_TARGET_PIPS = 30
MAX_ENTRY_DISTANCE_PIPS = 10
_PIP_SIZE = {"XAU": 0.1}
_EPS = 1e-9


@dataclass(frozen=True)
class AutoScalpRail:
  role: str
  low: float
  high: float
  level: float
  touches: int
  score: float
  timeframes: tuple[str, ...]
  sources: tuple[str, ...]


@dataclass(frozen=True)
class AutoScalpDecision:
  state: str
  direction: str | None = None
  trigger: str | None = None
  rail: AutoScalpRail | None = None
  target: AutoScalpRail | None = None
  target_room_pips: float | None = None
  confluence: int = 0
  reasons: tuple[str, ...] = ()
  rail_count: int = 0


def evaluate_auto_scalp_gate(
  frames: dict[str, pd.DataFrame],
  *,
  symbol: str,
  spot_price: float | None = None,
) -> AutoScalpDecision:
  """Return one auto-only M1 trade decision from raw M1/M5/M15 OHLC.

  This module deliberately does not consume scanner detections, forming
  signals, Market Map entries, or Telegram state. M5 supplies executable
  range rails, M15 only strengthens overlapping rails, and M1 owns entry
  timing.
  """
  required = {"M1", "M5", "M15"}
  if not required <= frames.keys():
    return AutoScalpDecision("missing_frames")
  m1 = _clean_frame(frames["M1"].tail(M1_WINDOW))
  m5 = _clean_frame(frames["M5"].tail(M5_WINDOW))
  m15 = _clean_frame(frames["M15"].tail(M15_WINDOW))
  if len(m1) < ATR_LENGTH + 3 or len(m5) < 12 or len(m15) < 8:
    return AutoScalpDecision("insufficient_history")

  m1_atr = _atr(m1)
  m5_atr = _atr(m5)
  if m1_atr <= _EPS or m5_atr <= _EPS:
    return AutoScalpDecision("invalid_atr")
  rails = build_auto_scalp_rails(m5, m15)
  if not rails:
    return AutoScalpDecision("waiting_for_rails")

  close = float(m1["close"].iloc[-1])
  live_price = close if spot_price is None else float(spot_price)
  if not math.isfinite(live_price) or live_price <= 0:
    return AutoScalpDecision("invalid_spot", rail_count=len(rails))
  triggered: list[tuple[AutoScalpRail, str, str]] = []
  for rail in rails:
    trigger = _m1_rail_trigger(m1, rail, m1_atr)
    if trigger is not None:
      triggered.append((rail, *trigger))
  if not triggered:
    nearest = min(rails, key=lambda rail: _rail_distance(rail, close))
    state = (
      "waiting_rejection"
      if _rail_distance(nearest, close) <= M1_TOUCH_ATR * m1_atr
      else "waiting_for_touch"
    )
    return AutoScalpDecision(
      state,
      rail=nearest,
      rail_count=len(rails),
    )

  pip_size = _PIP_SIZE.get(symbol.upper(), 1.0)
  maximum_entry_distance = MAX_ENTRY_DISTANCE_PIPS * pip_size
  eligible: list[
    tuple[AutoScalpRail, str, str, AutoScalpRail | None, float | None]
  ] = []
  blocked: list[
    tuple[AutoScalpRail, str, str, AutoScalpRail, float]
  ] = []
  moved: list[tuple[AutoScalpRail, str, str, float]] = []
  for rail, direction, trigger in triggered:
    if _m5_countertrend_blocks(m5, direction, m5_atr):
      continue
    entry_distance = _rail_distance(rail, live_price)
    if entry_distance > maximum_entry_distance + _EPS:
      moved.append((rail, direction, trigger, entry_distance))
      continue
    target = _opposite_target(rails, live_price, direction, m5_atr)
    room = _target_room(live_price, direction, target)
    room_pips = None if room is None else room / pip_size
    if room_pips is not None and room_pips + _EPS < MIN_TARGET_PIPS:
      blocked.append((rail, direction, trigger, target, room_pips))
      continue
    eligible.append((rail, direction, trigger, target, room_pips))

  if not eligible and blocked:
    rail, direction, trigger, target, room = max(
      blocked,
      key=lambda item: (item[4], item[0].score),
    )
    return AutoScalpDecision(
      "target_blocked",
      direction=direction,
      trigger=trigger,
      rail=rail,
      target=target,
      target_room_pips=room,
      rail_count=len(rails),
    )
  if not eligible:
    if not moved:
      rail, direction, trigger = max(
        triggered,
        key=lambda item: item[0].score,
      )
      return AutoScalpDecision(
        "trend_blocked",
        direction=direction,
        trigger=trigger,
        rail=rail,
        rail_count=len(rails),
      )
    rail, direction, trigger, _ = min(moved, key=lambda item: item[3])
    return AutoScalpDecision(
      "entry_moved",
      direction=direction,
      trigger=trigger,
      rail=rail,
      rail_count=len(rails),
    )

  rail, direction, trigger, target, room_pips = max(
    eligible,
    key=lambda item: (
      item[0].score,
      item[0].touches,
      -_rail_distance(item[0], live_price),
    ),
  )
  confluence = 3 if len(rail.timeframes) > 1 or rail.touches >= 3 else 2
  reasons = [
    f"M1 {trigger.replace('_', ' ')}",
    f"{rail.role} rail {rail.low:.2f}-{rail.high:.2f}",
    f"rail touches {rail.touches}",
  ]
  if room_pips is not None:
    reasons.append(f"opposite rail {room_pips:.0f} pips away")
  else:
    reasons.append("open room beyond rail")
  return AutoScalpDecision(
    "candidate",
    direction=direction,
    trigger=trigger,
    rail=rail,
    target=target,
    target_room_pips=room_pips,
    confluence=confluence,
    reasons=tuple(reasons),
    rail_count=len(rails),
  )


def build_auto_scalp_rails(
  m5: pd.DataFrame,
  m15: pd.DataFrame,
) -> list[AutoScalpRail]:
  """Build independent support/resistance rails from raw closed bars."""
  m5 = _clean_frame(m5.tail(M5_WINDOW))
  m15 = _clean_frame(m15.tail(M15_WINDOW))
  if len(m5) < 5:
    return []
  m5_atr = _atr(m5)
  if m5_atr <= _EPS:
    return []
  points = [
    *_pivot_points(m5, "M5", M5_PIVOT_SPAN, 2.0),
    *_pivot_points(m15, "M15", M15_PIVOT_SPAN, 2.6),
    *_rolling_extremes(m5, "M5", 24, 1.5),
    *_rolling_extremes(m15, "M15", 16, 2.0),
  ]
  clustered: list[AutoScalpRail] = []
  for role in ("support", "resistance"):
    role_points = [point for point in points if point[0] == role]
    clustered.extend(_cluster_rails(role_points, role, m5_atr))
  return _merge_same_role_rails(clustered, m5_atr)


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
  required = ["open", "high", "low", "close"]
  if df.empty or any(column not in df.columns for column in required):
    return pd.DataFrame(columns=required)
  clean = df.copy()
  for column in required:
    clean[column] = pd.to_numeric(clean[column], errors="coerce")
  clean = clean.dropna(subset=required)
  finite = clean[required].map(math.isfinite).all(axis=1)
  return clean.loc[finite]


def _atr(df: pd.DataFrame) -> float:
  if df.empty:
    return 0.0
  high = df["high"].astype(float)
  low = df["low"].astype(float)
  close = df["close"].astype(float)
  previous = close.shift(1)
  true_range = pd.concat([
    high - low,
    (high - previous).abs(),
    (low - previous).abs(),
  ], axis=1).max(axis=1)
  value = float(true_range.tail(ATR_LENGTH).mean())
  return value if math.isfinite(value) and value > 0 else 0.0


def _pivot_points(
  df: pd.DataFrame,
  timeframe: str,
  span: int,
  weight: float,
) -> list[tuple[str, float, int, float, str]]:
  if len(df) < 2 * span + 1:
    return []
  points: list[tuple[str, float, int, float, str]] = []
  for index in range(span, len(df) - span):
    window = df.iloc[index - span:index + span + 1]
    high = float(df["high"].iloc[index])
    low = float(df["low"].iloc[index])
    if high == float(window["high"].max()) and window["high"].eq(high).sum() == 1:
      points.append(("resistance", high, index, weight, f"{timeframe} swing-high"))
    if low == float(window["low"].min()) and window["low"].eq(low).sum() == 1:
      points.append(("support", low, index, weight, f"{timeframe} swing-low"))
  return points


def _rolling_extremes(
  df: pd.DataFrame,
  timeframe: str,
  lookback: int,
  weight: float,
) -> list[tuple[str, float, int, float, str]]:
  if len(df) < 4:
    return []
  frame = df.iloc[-min(len(df), max(4, lookback)):-1]
  low_index = int(df.index.get_loc(frame["low"].idxmin()))
  high_index = int(df.index.get_loc(frame["high"].idxmax()))
  return [
    ("support", float(frame["low"].min()), low_index, weight, f"{timeframe} range-low"),
    ("resistance", float(frame["high"].max()), high_index, weight, f"{timeframe} range-high"),
  ]


def _cluster_rails(
  points: list[tuple[str, float, int, float, str]],
  role: str,
  atr: float,
) -> list[AutoScalpRail]:
  tolerance = max(0.1, RAIL_CLUSTER_ATR * atr)
  clusters: list[list[tuple[str, float, int, float, str]]] = []
  for point in sorted(points, key=lambda item: item[1]):
    if not clusters:
      clusters.append([point])
      continue
    cluster = clusters[-1]
    weight = sum(item[3] for item in cluster)
    center = sum(item[1] * item[3] for item in cluster) / max(weight, _EPS)
    if abs(point[1] - center) <= tolerance:
      cluster.append(point)
    else:
      clusters.append([point])
  xau_pip = _PIP_SIZE["XAU"]
  half_width = min(
    max(xau_pip, RAIL_HALF_WIDTH_ATR * atr),
    MAX_RAIL_HALF_WIDTH_PIPS * xau_pip,
  )
  rails: list[AutoScalpRail] = []
  for cluster in clusters:
    weight = sum(item[3] for item in cluster)
    level = sum(item[1] * item[3] for item in cluster) / max(weight, _EPS)
    timeframes = tuple(sorted({item[4].split()[0] for item in cluster}))
    recency = max(item[2] for item in cluster) / max(1, max(item[2] for item in points))
    rails.append(AutoScalpRail(
      role=role,
      low=level - half_width,
      high=level + half_width,
      level=level,
      touches=len(cluster),
      score=round(weight + recency, 3),
      timeframes=timeframes,
      sources=tuple(sorted({item[4] for item in cluster})),
    ))
  return rails


def _merge_same_role_rails(
  rails: list[AutoScalpRail],
  atr: float,
) -> list[AutoScalpRail]:
  tolerance = max(0.1, RAIL_MERGE_ATR * atr)
  maximum_width = min(
    MAX_MERGED_RAIL_WIDTH_ATR * atr,
    MAX_MERGED_RAIL_WIDTH_PIPS * _PIP_SIZE["XAU"],
  )
  merged: list[AutoScalpRail] = []
  for role in ("support", "resistance"):
    for rail in sorted(
      (item for item in rails if item.role == role),
      key=lambda item: item.level,
    ):
      if not merged or merged[-1].role != role or abs(
        rail.level - merged[-1].level
      ) > tolerance:
        merged.append(rail)
        continue
      current = merged.pop()
      if max(current.high, rail.high) - min(
        current.low,
        rail.low,
      ) > maximum_width:
        merged.extend([current, rail])
        continue
      total = current.score + rail.score
      level = (
        current.level * current.score + rail.level * rail.score
      ) / max(total, _EPS)
      merged.append(AutoScalpRail(
        role=role,
        low=min(current.low, rail.low),
        high=max(current.high, rail.high),
        level=level,
        touches=current.touches + rail.touches,
        score=round(total, 3),
        timeframes=tuple(sorted(set(current.timeframes + rail.timeframes))),
        sources=tuple(sorted(set(current.sources + rail.sources))),
      ))
  return merged


def _m1_rail_trigger(
  df: pd.DataFrame,
  rail: AutoScalpRail,
  atr: float,
) -> tuple[str, str] | None:
  row = df.iloc[-1]
  open_ = float(row["open"])
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  span = high - low
  if span <= _EPS:
    return None
  touch = M1_TOUCH_ATR * atr
  buffer = M1_BREAK_BUFFER_ATR * atr
  upper_wick = max(0.0, high - max(open_, close)) / span
  lower_wick = max(0.0, min(open_, close) - low) / span

  if rail.role == "support":
    touched = low <= rail.high + touch and high >= rail.low - touch
    accepted = close < rail.low - buffer
    reaction = (
      (close > open_ or lower_wick >= 0.30)
      and close >= low + 0.60 * span
      and close >= rail.level - buffer
    )
    if touched and not accepted and reaction:
      return "BUY", "range_rejection"
  else:
    touched = high >= rail.low - touch and low <= rail.high + touch
    accepted = close > rail.high + buffer
    reaction = (
      (close < open_ or upper_wick >= 0.30)
      and close <= low + 0.40 * span
      and close <= rail.level + buffer
    )
    if touched and not accepted and reaction:
      return "SELL", "range_rejection"

  return None


def _m5_countertrend_blocks(
  m5: pd.DataFrame,
  direction: str,
  atr: float,
) -> bool:
  frame = m5.tail(M5_CONTEXT_LOOKBACK)
  if len(frame) < 5 or atr <= _EPS:
    return True
  close = frame["close"].astype(float)
  path = float(close.diff().abs().sum())
  efficiency = (
    abs(float(close.iloc[-1] - close.iloc[0])) / path
    if path > _EPS else 0.0
  )
  net_move = float(close.iloc[-1] - close.iloc[0])
  if efficiency > M5_MAX_RANGE_EFFICIENCY:
    if direction == "BUY" and net_move < 0:
      return True
    if direction == "SELL" and net_move > 0:
      return True
  momentum = float(close.iloc[-1] - close.iloc[-4]) / atr
  if direction == "BUY" and momentum < -M5_MAX_ADVERSE_MOMENTUM_ATR:
    return True
  if direction == "SELL" and momentum > M5_MAX_ADVERSE_MOMENTUM_ATR:
    return True
  return False


def _rail_distance(rail: AutoScalpRail, price: float) -> float:
  if rail.low <= price <= rail.high:
    return 0.0
  return min(abs(price - rail.low), abs(price - rail.high))


def _opposite_target(
  rails: list[AutoScalpRail],
  price: float,
  direction: str,
  atr: float,
) -> AutoScalpRail | None:
  separation = max(0.1, 0.35 * atr)
  if direction == "BUY":
    candidates = [
      rail for rail in rails
      if rail.role == "resistance" and rail.level > price + separation
    ]
    return min(candidates, key=lambda item: item.level) if candidates else None
  candidates = [
    rail for rail in rails
    if rail.role == "support" and rail.level < price - separation
  ]
  return max(candidates, key=lambda item: item.level) if candidates else None


def _target_room(
  price: float,
  direction: str,
  target: AutoScalpRail | None,
) -> float | None:
  if target is None:
    return None
  if direction == "BUY":
    return max(0.0, target.low - price)
  return max(0.0, price - target.high)
