"""Independent OHLC-only gate for high-frequency automatic XAU scalping."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from app.autotrade import units


ATR_LENGTH = 14
M1_WINDOW = 120
M5_WINDOW = 96
M15_WINDOW = 64
M1_TOUCH_ATR = 0.25
M1_BREAK_BUFFER_ATR = 0.06
BOX_LOOKBACK = 60
BOX_EDGE_QUANTILE = 0.05
BOX_MIN_WIDTH_PIPS = 55
BOX_MAX_WIDTH_PIPS = 120
BOX_MIN_INSIDE_RATIO = 0.82
BOX_MIN_TOUCH_EPISODES = 2
BOX_MAX_CLOSE_EFFICIENCY = 0.45
BOX_TOUCH_BAND_ATR = 0.18
BOX_MIN_TOUCH_BAND_PIPS = 2.5
BOX_MAX_TOUCH_BAND_PIPS = 6
BOX_RECOVERY_ATR = 0.15
BOX_MIN_WICK_FRACTION = 0.15
BOX_MIN_BODY_FRACTION = 0.15
BOX_BREAK_BUFFER_ATR = 0.12
BOX_BREAK_M1_CLOSES = 2
BOX_TP_BUFFER_PIPS = 5
BOX_TP_CHOICES = (70, 50)
MAX_ENTRY_DISTANCE_PIPS = 10
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
class AutoScalpBox:
  box_id: str
  lower: AutoScalpRail
  upper: AutoScalpRail
  width_pips: float
  inside_ratio: float = 0.0
  efficiency: float = 0.0


@dataclass(frozen=True)
class AutoScalpDecision:
  state: str
  direction: str | None = None
  trigger: str | None = None
  rail: AutoScalpRail | None = None
  target: AutoScalpRail | None = None
  target_room_pips: float | None = None
  full_tp_pips: int | None = None
  box: AutoScalpBox | None = None
  confluence: int = 0
  reasons: tuple[str, ...] = ()
  rail_count: int = 0
  sweep_low: float | None = None
  sweep_high: float | None = None


def evaluate_auto_scalp_gate(
  frames: dict[str, pd.DataFrame],
  *,
  symbol: str,
  spot_price: float | None = None,
) -> AutoScalpDecision:
  """Return one auto-only M1 trade decision from raw M1/M5/M15 OHLC.

  This module deliberately does not consume scanner detections, forming
  signals, Market Map entries, or Telegram state. A 60-bar M1 auction builds
  the executable box and owns entry timing. M5 confirms box acceptance;
  M15 is feed-health context and never directionally vetoes an M1 edge.
  """
  required = {"M1", "M5", "M15"}
  if not required <= frames.keys():
    missing = sorted(required - frames.keys())
    return AutoScalpDecision(
      "missing_frames", reasons=(f"missing timeframes: {', '.join(missing)}",),
    )
  m1 = _clean_frame(frames["M1"].tail(M1_WINDOW))
  m5 = _clean_frame(frames["M5"].tail(M5_WINDOW))
  m15 = _clean_frame(frames["M15"].tail(M15_WINDOW))
  if len(m1) < BOX_LOOKBACK + 1 or len(m5) < 12 or len(m15) < 8:
    return AutoScalpDecision(
      "insufficient_history",
      reasons=(
        f"insufficient history: M1={len(m1)} M5={len(m5)} M15={len(m15)}",
      ),
    )

  m1_atr = _atr(m1)
  m5_atr = _atr(m5)
  if m1_atr <= _EPS or m5_atr <= _EPS:
    return AutoScalpDecision(
      "invalid_atr", reasons=(f"invalid atr: M1={m1_atr:.4f} M5={m5_atr:.4f}",),
    )
  close = float(m1["close"].iloc[-1])
  pip_size = units.pip_size(symbol)
  box = _m1_consolidation_box(m1, m1_atr, symbol)
  if box is None:
    return AutoScalpDecision(
      "waiting_for_box",
      rail_count=0,
      reasons=("no valid M1 consolidation box in the lookback window",),
    )
  if _box_is_broken(m1, m5, box, m5_atr, pip_size):
    return AutoScalpDecision(
      "box_broken",
      box=box,
      rail_count=2,
      reasons=(f"range box {box.box_id} accepted outside",),
    )

  live_price = close if spot_price is None else float(spot_price)
  if not math.isfinite(live_price) or live_price <= 0:
    return AutoScalpDecision(
      "invalid_spot",
      box=box,
      rail_count=2,
      reasons=(f"invalid spot price: {spot_price!r}",),
    )
  triggered: list[tuple[AutoScalpRail, str, str, float]] = []
  for rail in (box.lower, box.upper):
    trigger = _m1_rail_trigger(m1, rail, m1_atr)
    if trigger is not None:
      triggered.append((rail, *trigger))
  if not triggered:
    nearest = min(
      (box.lower, box.upper),
      key=lambda rail: _rail_distance(rail, close),
    )
    state = (
      "waiting_rejection"
      if _rail_distance(nearest, close) <= M1_TOUCH_ATR * m1_atr
      else "waiting_for_touch"
    )
    return AutoScalpDecision(
      state,
      rail=nearest,
      box=box,
      rail_count=2,
      reasons=(
        f"{state.replace('_', ' ')} at {nearest.role} rail "
        f"{nearest.low:.2f}-{nearest.high:.2f}",
      ),
    )

  maximum_entry_distance = MAX_ENTRY_DISTANCE_PIPS * pip_size
  eligible: list[
    tuple[AutoScalpRail, str, str, float, AutoScalpRail, float, int]
  ] = []
  blocked: list[
    tuple[AutoScalpRail, str, str, AutoScalpRail, float]
  ] = []
  moved: list[tuple[AutoScalpRail, str, str, float]] = []
  for rail, direction, trigger, sweep_extreme in triggered:
    entry_distance = _rail_distance(rail, live_price)
    if entry_distance > maximum_entry_distance + _EPS:
      moved.append((rail, direction, trigger, entry_distance))
      continue
    target = box.upper if direction == "BUY" else box.lower
    room = _target_room(live_price, direction, target)
    room_pips = 0.0 if room is None else room / pip_size
    full_tp_pips = _full_tp_pips(room_pips)
    if full_tp_pips is None:
      blocked.append((rail, direction, trigger, target, room_pips))
      continue
    eligible.append((
      rail,
      direction,
      trigger,
      sweep_extreme,
      target,
      room_pips,
      full_tp_pips,
    ))

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
      box=box,
      rail_count=2,
      reasons=(f"target blocked: only {room:.0f} pips room to opposite edge",),
    )
  if not eligible:
    rail, direction, trigger, distance = min(moved, key=lambda item: item[3])
    return AutoScalpDecision(
      "entry_moved",
      direction=direction,
      trigger=trigger,
      rail=rail,
      box=box,
      rail_count=2,
      reasons=(
        f"entry moved {distance / pip_size:.1f} pips beyond "
        f"{MAX_ENTRY_DISTANCE_PIPS} pip limit from rail {rail.level:.2f}",
      ),
    )

  rail, direction, trigger, sweep_extreme, target, room_pips, full_tp_pips = max(
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
    f"range box {box.box_id} {box.width_pips:.0f} pips",
    f"{box.inside_ratio:.0%} closes held inside",
    f"range efficiency {box.efficiency:.2f}",
    f"full TP {full_tp_pips} pips",
  ]
  reasons.append(f"opposite edge {room_pips:.0f} pips away")
  return AutoScalpDecision(
    "candidate",
    direction=direction,
    trigger=trigger,
    rail=rail,
    target=target,
    target_room_pips=room_pips,
    full_tp_pips=full_tp_pips,
    box=box,
    confluence=confluence,
    reasons=tuple(reasons),
    rail_count=2,
    sweep_low=sweep_extreme if direction == "BUY" else None,
    sweep_high=sweep_extreme if direction == "SELL" else None,
  )


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


def _m1_rail_trigger(
  df: pd.DataFrame,
  rail: AutoScalpRail,
  atr: float,
) -> tuple[str, str, float] | None:
  row = df.iloc[-1]
  open_ = float(row["open"])
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  span = high - low
  if span <= _EPS:
    return None
  pip_size = units.pip_size("XAU")
  touch = min(
    BOX_MAX_TOUCH_BAND_PIPS * pip_size,
    max(BOX_MIN_TOUCH_BAND_PIPS * pip_size, BOX_TOUCH_BAND_ATR * atr),
  )
  buffer = M1_BREAK_BUFFER_ATR * atr
  recovery = BOX_RECOVERY_ATR * atr
  body = abs(close - open_) / span
  upper_wick = max(0.0, high - max(open_, close)) / span
  lower_wick = max(0.0, min(open_, close) - low) / span

  if rail.role == "support":
    touched = low <= rail.high + touch and high >= rail.low - touch
    accepted = close < rail.low - buffer
    reaction = (
      close > open_
      and lower_wick >= BOX_MIN_WICK_FRACTION
      and body >= BOX_MIN_BODY_FRACTION
      and close >= rail.level + recovery
    )
    if touched and not accepted and reaction:
      return "BUY", "range_rejection", low
  else:
    touched = high >= rail.low - touch and low <= rail.high + touch
    accepted = close > rail.high + buffer
    reaction = (
      close < open_
      and upper_wick >= BOX_MIN_WICK_FRACTION
      and body >= BOX_MIN_BODY_FRACTION
      and close <= rail.level - recovery
    )
    if touched and not accepted and reaction:
      return "SELL", "range_rejection", high

  return None


def _m1_consolidation_box(
  m1: pd.DataFrame,
  atr: float,
  symbol: str,
) -> AutoScalpBox | None:
  pip_size = units.pip_size(symbol)
  if len(m1) < BOX_LOOKBACK + 1 or atr <= _EPS:
    return None
  history = m1.iloc[-(BOX_LOOKBACK + 1):-1]
  lower_level = float(history["low"].quantile(BOX_EDGE_QUANTILE))
  upper_level = float(history["high"].quantile(1 - BOX_EDGE_QUANTILE))
  width_pips = (upper_level - lower_level) / pip_size
  if not BOX_MIN_WIDTH_PIPS <= width_pips <= BOX_MAX_WIDTH_PIPS:
    return None
  touch = min(
    BOX_MAX_TOUCH_BAND_PIPS * pip_size,
    max(BOX_MIN_TOUCH_BAND_PIPS * pip_size, BOX_TOUCH_BAND_ATR * atr),
  )
  lower_flags = history["low"].astype(float) <= lower_level + touch
  upper_flags = history["high"].astype(float) >= upper_level - touch
  lower_touches = _touch_episodes(lower_flags)
  upper_touches = _touch_episodes(upper_flags)
  if min(lower_touches, upper_touches) < BOX_MIN_TOUCH_EPISODES:
    return None
  inside_buffer = max(3 * pip_size, BOX_BREAK_BUFFER_ATR * atr)
  inside_ratio = float(history["close"].astype(float).between(
    lower_level - inside_buffer,
    upper_level + inside_buffer,
  ).mean())
  if inside_ratio + _EPS < BOX_MIN_INSIDE_RATIO:
    return None
  close_path = float(history["close"].astype(float).diff().abs().sum())
  efficiency = (
    abs(float(history["close"].iloc[-1] - history["close"].iloc[0]))
    / close_path
    if close_path > _EPS else 0.0
  )
  if efficiency > BOX_MAX_CLOSE_EFFICIENCY:
    return None
  lower = AutoScalpRail(
    role="support",
    low=lower_level,
    high=lower_level,
    level=lower_level,
    touches=lower_touches,
    score=round(lower_touches + inside_ratio, 3),
    timeframes=("M1",),
    sources=(f"M1 {BOX_LOOKBACK}-bar range-low",),
  )
  upper = AutoScalpRail(
    role="resistance",
    low=upper_level,
    high=upper_level,
    level=upper_level,
    touches=upper_touches,
    score=round(upper_touches + inside_ratio, 3),
    timeframes=("M1",),
    sources=(f"M1 {BOX_LOOKBACK}-bar range-high",),
  )
  bucket = 10 * pip_size
  low_bucket = round(lower_level / bucket)
  high_bucket = round(upper_level / bucket)
  return AutoScalpBox(
    f"{symbol.lower()}-{low_bucket}-{high_bucket}",
    lower,
    upper,
    width_pips,
    inside_ratio,
    efficiency,
  )


def _touch_episodes(flags: pd.Series) -> int:
  values = flags.astype(bool)
  previous = values.shift(1, fill_value=False)
  return int((values & ~previous).sum())


def _box_is_broken(
  m1: pd.DataFrame,
  m5: pd.DataFrame,
  box: AutoScalpBox,
  atr: float,
  pip_size: float,
) -> bool:
  buffer = max(3 * pip_size, BOX_BREAK_BUFFER_ATR * atr)
  lower_break = box.lower.low - buffer
  upper_break = box.upper.high + buffer
  recent_m1 = m1["close"].astype(float).tail(BOX_BREAK_M1_CLOSES)
  if len(recent_m1) >= BOX_BREAK_M1_CLOSES and (
    bool((recent_m1 < lower_break).all())
    or bool((recent_m1 > upper_break).all())
  ):
    return True
  m5_close = float(m5["close"].iloc[-1])
  return m5_close < lower_break or m5_close > upper_break


def _full_tp_pips(room_pips: float) -> int | None:
  for target in BOX_TP_CHOICES:
    if room_pips + _EPS >= target + BOX_TP_BUFFER_PIPS:
      return target
  return None


def _rail_distance(rail: AutoScalpRail, price: float) -> float:
  if rail.low <= price <= rail.high:
    return 0.0
  return min(abs(price - rail.low), abs(price - rail.high))


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
