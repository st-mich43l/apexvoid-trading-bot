"""Pure micro-barrier and local-range analysis for two-sided scalping."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from app.analysis.math_utils import atr_scalar
from app.analysis.swings import find_swings
from app.analysis.trendlines import value_at

RANGE_SCALP_LOOKBACK = 36
RANGE_SCALP_CLUSTER_ATR = 0.20
RANGE_SCALP_MIN_TOUCHES = 3
RANGE_SCALP_MIN_WICK_FRAC = 0.35
RANGE_SCALP_ENTRY_TOL_ATR = 0.15
RANGE_SCALP_MIN_WIDTH_ATR = 1.2
RANGE_SCALP_MAX_WIDTH_ATR = 6.0
RANGE_SCALP_MIN_ROOM_ATR = 1.0
RANGE_SCALP_BREAK_CLOSES = 2
_EPS = 1e-9
_SESSION_NAMES = {
  "ASIA_H",
  "ASIA_L",
  "LONDON_H",
  "LONDON_L",
  "NY_H",
  "NY_L",
  "PDH",
  "PDL",
  "PWH",
  "PWL",
}


@dataclass(frozen=True)
class ScalpBarrier:
  side: str
  level: float
  low: float
  high: float
  touches: int
  wick_rejections: int
  accepted_closes: int
  last_touch_index: int
  tags: list[str]
  score: float


@dataclass(frozen=True)
class ScalpRange:
  lower: ScalpBarrier
  upper: ScalpBarrier
  eq: float
  width_atr: float
  quality: float


@dataclass(frozen=True)
class _Contact:
  index: int
  price: float
  wick_fraction: float
  rejected: bool


def build_scalp_structure(
  df: pd.DataFrame,
  atr: pd.Series | float,
  session_levels: list,
  trendlines: list,
  regime,
  cfg,
) -> tuple[list[ScalpBarrier], ScalpRange | None]:
  if df.empty:
    return [], None
  atr_value = _last_atr(atr)
  if atr_value <= 0:
    return [], None
  lookback = max(5, int(getattr(cfg, "range_scalp_lookback", RANGE_SCALP_LOOKBACK)))
  frame = df.tail(lookback)
  offset = len(df) - len(frame)
  cluster_tolerance = max(
    _EPS,
    atr_value
    * max(0.0, float(getattr(cfg, "range_scalp_cluster_atr", RANGE_SCALP_CLUSTER_ATR))),
  )
  entry_tolerance = max(
    _EPS,
    atr_value
    * max(0.0, float(getattr(cfg, "range_scalp_entry_tol_atr", RANGE_SCALP_ENTRY_TOL_ATR))),
  )
  minimum_touches = max(
    2,
    int(getattr(cfg, "range_scalp_min_touches", RANGE_SCALP_MIN_TOUCHES)),
  )
  minimum_wick = max(
    0.0,
    min(1.0, float(getattr(cfg, "range_scalp_min_wick_frac", RANGE_SCALP_MIN_WICK_FRAC))),
  )
  break_closes = max(
    1,
    int(getattr(cfg, "range_scalp_break_closes", RANGE_SCALP_BREAK_CLOSES)),
  )
  contacts = _contacts(frame, offset, atr, minimum_wick)
  barriers: list[ScalpBarrier] = []
  for side in ("support", "resistance"):
    side_contacts = contacts[side]
    for cluster in _cluster_contacts(side_contacts, cluster_tolerance):
      episodes = _touch_episodes(cluster)
      # Matches market_map.py's _validated_scalp_pair touch/wick thresholds
      # (converged after the two subsystems were found to disagree on range
      # validity - see B4). No relaxation here: a barrier that wouldn't
      # validate on the map shouldn't validate for a live scalp alert either.
      if len(episodes) < minimum_touches:
        continue
      level = sum(contact.price for contact in episodes) / len(episodes)
      wick_rejections = sum(contact.rejected for contact in episodes)
      if wick_rejections < 2:
        continue
      accepted = _max_accepted_close_run(
        df,
        level,
        entry_tolerance,
        side,
        episodes[0].index,
      )
      if accepted >= break_closes:
        continue
      tags = _barrier_tags(
        side,
        level,
        len(episodes),
        wick_rejections,
        cluster_tolerance,
        len(df) - 1,
        session_levels,
        trendlines,
        regime,
        float(getattr(cfg, "round_step", 5.0)),
      )
      score = _barrier_score(
        len(episodes),
        wick_rejections,
        accepted,
        len(tags) - 2,
        episodes[-1].index,
        len(df),
      )
      barriers.append(ScalpBarrier(
        side=side,
        level=float(level),
        low=float(level - entry_tolerance),
        high=float(level + entry_tolerance),
        touches=len(episodes),
        wick_rejections=wick_rejections,
        accepted_closes=accepted,
        last_touch_index=episodes[-1].index,
        tags=tags,
        score=score,
      ))

  barriers = _dedup_barriers(barriers, cluster_tolerance)
  current_price = float(df["close"].iloc[-1])
  scalp_range = _best_range(barriers, current_price, atr_value, cfg)
  return barriers, scalp_range


def _contacts(
  frame: pd.DataFrame,
  offset: int,
  atr,
  minimum_wick: float,
) -> dict[str, list[_Contact]]:
  result: dict[str, list[_Contact]] = {"support": [], "resistance": []}
  micro = find_swings(
    frame,
    fractal_n=1,
    zigzag_pct=0.0,
    zigzag_atr_mult=0.0,
    atr=_tail_atr(atr, len(frame)),
  )
  micro_by_bar = {
    (int(swing.index) + offset, swing.kind)
    for swing in micro
  }
  for local_index, row in enumerate(frame.itertuples(index=False)):
    index = local_index + offset
    open_ = float(row.open)
    high = float(row.high)
    low = float(row.low)
    close = float(row.close)
    span = high - low
    if not all(math.isfinite(value) for value in (open_, high, low, close)):
      continue
    if span <= _EPS:
      continue
    upper_fraction = max(0.0, high - max(open_, close)) / span
    lower_fraction = max(0.0, min(open_, close) - low) / span
    upper_rejected = upper_fraction >= minimum_wick and close < high
    lower_rejected = lower_fraction >= minimum_wick and close > low
    if upper_rejected or (index, "high") in micro_by_bar:
      result["resistance"].append(_Contact(
        index,
        high,
        upper_fraction,
        upper_rejected,
      ))
    if lower_rejected or (index, "low") in micro_by_bar:
      result["support"].append(_Contact(
        index,
        low,
        lower_fraction,
        lower_rejected,
      ))
  return result


def _tail_atr(atr, length: int):
  if hasattr(atr, "tail"):
    return atr.tail(length).reset_index(drop=True)
  return atr


def _cluster_contacts(
  contacts: list[_Contact],
  tolerance: float,
) -> list[list[_Contact]]:
  clusters: list[list[_Contact]] = []
  for contact in sorted(contacts, key=lambda item: (item.price, item.index)):
    if not clusters:
      clusters.append([contact])
      continue
    current = clusters[-1]
    center = sum(item.price for item in current) / len(current)
    union_width = max(item.price for item in [*current, contact]) - min(
      item.price for item in [*current, contact]
    )
    if abs(contact.price - center) <= tolerance and union_width <= 2 * tolerance:
      current.append(contact)
    else:
      clusters.append([contact])
  return clusters


def _touch_episodes(cluster: list[_Contact]) -> list[_Contact]:
  episodes: list[list[_Contact]] = []
  for contact in sorted(cluster, key=lambda item: item.index):
    if episodes and contact.index <= episodes[-1][-1].index + 1:
      episodes[-1].append(contact)
    else:
      episodes.append([contact])
  return [
    max(
      episode,
      key=lambda item: (item.rejected, item.wick_fraction, -item.index),
    )
    for episode in episodes
  ]


def _max_accepted_close_run(
  df: pd.DataFrame,
  level: float,
  tolerance: float,
  side: str,
  start: int,
) -> int:
  longest = 0
  current = 0
  for close in df["close"].iloc[max(0, start):].astype(float):
    accepted = (
      close < level - tolerance
      if side == "support"
      else close > level + tolerance
    )
    current = current + 1 if accepted else 0
    longest = max(longest, current)
  return longest


def _barrier_tags(
  side: str,
  level: float,
  touches: int,
  wick_rejections: int,
  tolerance: float,
  bar_index: int,
  session_levels: list,
  trendlines: list,
  regime,
  round_step: float,
) -> list[str]:
  tags = [f"micro ×{touches}", f"wick ×{wick_rejections}"]
  for session in session_levels:
    name = str(getattr(session, "name", "")).upper()
    if name in _SESSION_NAMES and abs(float(session.price) - level) <= tolerance:
      tags.append(f"session {name}")
  if regime is not None:
    if side == "resistance" and abs(float(regime.range_high) - level) <= tolerance:
      tags.append("box-top")
    if side == "support" and abs(float(regime.range_low) - level) <= tolerance:
      tags.append("box-bottom")
  for line in trendlines:
    if bool(getattr(line, "broken", False)) or str(line.kind) != side:
      continue
    if abs(value_at(line, bar_index) - level) <= tolerance:
      tags.append(f"TL {side} ×{line.touches}")
  if round_step > 0:
    nearest = round(level / round_step) * round_step
    if abs(nearest - level) <= tolerance:
      tags.append("round")
  return _unique(tags)


def _barrier_score(
  touches: int,
  wick_rejections: int,
  accepted_closes: int,
  confluences: int,
  last_touch: int,
  bar_count: int,
) -> float:
  score = min(5, touches) * 1.2
  score += min(4, wick_rejections)
  score += min(3, max(0, confluences)) * 0.75
  if last_touch >= bar_count - 3:
    score += 1.0
  score -= max(0, accepted_closes) * 2.0
  return max(0.0, round(score, 3))


def _dedup_barriers(
  barriers: list[ScalpBarrier],
  tolerance: float,
) -> list[ScalpBarrier]:
  result: list[ScalpBarrier] = []
  for barrier in sorted(
    barriers,
    key=lambda item: (item.side, item.level, -item.score),
  ):
    if result and result[-1].side == barrier.side and abs(
      result[-1].level - barrier.level
    ) <= tolerance:
      if _barrier_rank(barrier) > _barrier_rank(result[-1]):
        result[-1] = barrier
      continue
    result.append(barrier)
  return result


def _barrier_rank(barrier: ScalpBarrier) -> tuple:
  return (
    barrier.score,
    barrier.touches,
    barrier.wick_rejections,
    barrier.last_touch_index,
    -barrier.level,
  )


def _best_range(
  barriers: list[ScalpBarrier],
  price: float,
  atr: float,
  cfg,
) -> ScalpRange | None:
  minimum_room = max(
    0.0,
    float(getattr(cfg, "range_scalp_min_room_atr", RANGE_SCALP_MIN_ROOM_ATR)),
  )
  # Matches market_map.py's _validated_scalp_pair minimum-width formula
  # (converged after B4 found the two subsystems disagreed on range
  # validity) - a range too narrow to hold two rooms either side of EQ was
  # never a valid two-sided range in the first place.
  minimum_width = max(
    0.0,
    float(getattr(cfg, "range_scalp_min_width_atr", RANGE_SCALP_MIN_WIDTH_ATR)),
    2.0 * minimum_room,
  )
  maximum_width = max(
    minimum_width,
    float(getattr(cfg, "range_scalp_max_width_atr", RANGE_SCALP_MAX_WIDTH_ATR)),
  )
  supports = [
    barrier for barrier in barriers
    if barrier.side == "support" and barrier.low <= price + _EPS
  ]
  resistances = [
    barrier for barrier in barriers
    if barrier.side == "resistance" and barrier.high >= price - _EPS
  ]
  candidates: list[ScalpRange] = []
  for lower in supports:
    for upper in resistances:
      width = upper.level - lower.level
      if width <= 0:
        continue
      width_atr = width / atr
      if not minimum_width <= width_atr <= maximum_width:
        continue
      if price < lower.low - _EPS or price > upper.high + _EPS:
        continue
      eq = (lower.level + upper.level) / 2
      room = min(eq - lower.level, upper.level - eq) / atr
      if room < minimum_room:
        continue
      quality = lower.score + upper.score
      candidates.append(ScalpRange(lower, upper, eq, width_atr, quality))
  if not candidates:
    return None
  return min(
    candidates,
    key=lambda item: (
      -item.quality,
      abs(price - item.eq),
      item.width_atr,
      item.lower.level,
      item.upper.level,
    ),
  )


def _last_atr(atr) -> float:
  if hasattr(atr, "dropna"):
    clean = atr.dropna()
    value = float(clean.iloc[-1]) if not clean.empty else 0.0
  else:
    value = atr_scalar(atr, fallback=0.0)
  return value if math.isfinite(value) and value > 0 else 0.0


def _unique(tags: list[str]) -> list[str]:
  result: list[str] = []
  seen: set[str] = set()
  for tag in tags:
    key = tag.casefold()
    if tag and key not in seen:
      result.append(tag)
      seen.add(key)
  return result
