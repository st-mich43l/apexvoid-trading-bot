"""Pure micro-barrier and local-range analysis for two-sided scalping.

Symmetric support/resistance detection with dynamic clustering, controlled
fallback barriers for one-sided structure, and explicit range states
(no_range / provisional / confirmed / post_impulse / broken).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any

import pandas as pd

from app.analysis.math_utils import atr_scalar
from app.analysis.swings import find_swings
from app.analysis.trendlines import value_at

RANGE_SCALP_LOOKBACK = 36
RANGE_SCALP_CLUSTER_ATR = 0.20
RANGE_SCALP_CLUSTER_MIN_ABS = 0.0
RANGE_SCALP_CLUSTER_PIP_MULT = 2.0
RANGE_SCALP_MIN_TOUCHES = 3
RANGE_SCALP_MIN_WICK_FRAC = 0.35
RANGE_SCALP_ENTRY_TOL_ATR = 0.15
RANGE_SCALP_MIN_WIDTH_ATR = 1.2
RANGE_SCALP_MAX_WIDTH_ATR = 6.0
RANGE_SCALP_MAX_EDGE_WIDTH_ATR = 0.75
RANGE_SCALP_MIN_ROOM_ATR = 1.0
RANGE_SCALP_BREAK_CLOSES = 2
RANGE_SCALP_MIN_INSIDE_CLOSES = 3
RANGE_SCALP_FALLBACK_MIN_CONFIRMATIONS = 1
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
RANGE_STATE_NO_RANGE = "no_range"
RANGE_STATE_PROVISIONAL = "provisional_range"
RANGE_STATE_CONFIRMED = "confirmed_range"
RANGE_STATE_POST_IMPULSE = "post_impulse_range"
RANGE_STATE_BROKEN = "broken_range"
BARRIER_GRADES = ("A", "B", "C", "invalid")


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
  # Extended structure metadata (backward-compatible defaults).
  grade: str = "B"
  sources: tuple[str, ...] = ("wick_cluster",)
  first_touch_index: int = 0
  body_holds: int = 0
  age: int = 0
  invalidated: bool = False
  reclaimed: bool = False
  tested: bool = True
  confidence_grade: str = "B"
  class_name: str = "local"  # micro | local | structural | major
  source_timeframe: str = ""
  fallback: bool = False

  @property
  def central(self) -> float:
    return self.level

  @property
  def executable(self) -> bool:
    grade = (self.confidence_grade or self.grade or "C").upper()
    if grade == "INVALID" or self.invalidated:
      return False
    if self.fallback and grade == "C":
      return False
    return grade in {"A", "B"}


@dataclass(frozen=True)
class ScalpRange:
  lower: ScalpBarrier
  upper: ScalpBarrier
  eq: float
  width_atr: float
  quality: float
  state: str = RANGE_STATE_CONFIRMED
  range_id: str = ""
  inside_closes: int = 0
  one_sided: bool = False
  post_impulse: bool = False
  reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScalpStructureResult:
  barriers: list[ScalpBarrier]
  scalp_range: ScalpRange | None
  range_state: str
  missing_side_reason: str | None = None
  fallback_applied: tuple[str, ...] = ()
  telemetry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Contact:
  index: int
  price: float
  wick_fraction: float
  rejected: bool
  body_hold: bool = False
  source: str = "wick"


def build_scalp_structure(
  df: pd.DataFrame,
  atr: pd.Series | float,
  session_levels: list,
  trendlines: list,
  regime,
  cfg,
) -> tuple[list[ScalpBarrier], ScalpRange | None]:
  """Backward-compatible entry point used by engine.py and tests."""
  result = build_scalp_structure_detailed(
    df, atr, session_levels, trendlines, regime, cfg,
  )
  return result.barriers, result.scalp_range


def build_scalp_structure_detailed(
  df: pd.DataFrame,
  atr: pd.Series | float,
  session_levels: list,
  trendlines: list,
  regime,
  cfg,
) -> ScalpStructureResult:
  if df.empty:
    return ScalpStructureResult(
      [], None, RANGE_STATE_NO_RANGE, "empty_frame",
    )
  atr_value = _last_atr(atr)
  if atr_value <= 0:
    return ScalpStructureResult(
      [], None, RANGE_STATE_NO_RANGE, "invalid_atr",
    )
  lookback = max(5, int(getattr(cfg, "range_scalp_lookback", RANGE_SCALP_LOOKBACK)))
  frame = df.tail(lookback)
  offset = len(df) - len(frame)
  cluster_tolerance = _cluster_tolerance(atr_value, frame, cfg)
  entry_tolerance = max(
    _EPS,
    atr_value
    * max(0.0, float(getattr(cfg, "range_scalp_entry_tol_atr", RANGE_SCALP_ENTRY_TOL_ATR))),
  )
  max_edge_width = max(
    entry_tolerance,
    atr_value * max(
      0.05,
      float(getattr(
        cfg, "range_scalp_max_edge_width_atr", RANGE_SCALP_MAX_EDGE_WIDTH_ATR,
      )),
    ),
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
      if len(episodes) < minimum_touches:
        continue
      level = sum(contact.price for contact in episodes) / len(episodes)
      wick_rejections = sum(1 for contact in episodes if contact.rejected)
      body_holds = sum(1 for contact in episodes if contact.body_hold)
      # Primary barriers still require two wick rejections (symmetric for both
      # sides). Body holds raise score/grade but do not replace wick evidence.
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
      half_width = min(entry_tolerance, max_edge_width / 2.0)
      sources = tuple(sorted({contact.source for contact in episodes}))
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
        body_holds=body_holds,
      )
      grade = _barrier_grade(score, len(episodes), wick_rejections, tags)
      barriers.append(ScalpBarrier(
        side=side,
        level=float(level),
        low=float(level - half_width),
        high=float(level + half_width),
        touches=len(episodes),
        wick_rejections=wick_rejections,
        accepted_closes=accepted,
        last_touch_index=episodes[-1].index,
        first_touch_index=episodes[0].index,
        body_holds=body_holds,
        age=max(0, len(df) - 1 - episodes[-1].index),
        tags=tags,
        score=score,
        grade=grade,
        confidence_grade=grade,
        sources=sources or ("wick_cluster",),
        class_name=_barrier_class(grade, tags),
        tested=True,
      ))

  barriers = _dedup_barriers(barriers, cluster_tolerance)
  current_price = float(df["close"].iloc[-1])
  if _recent_breakout_displacement(df, atr_value, cfg):
    return ScalpStructureResult(
      barriers=barriers,
      scalp_range=None,
      range_state=RANGE_STATE_BROKEN,
      missing_side_reason="accepted_breakout_displacement",
      fallback_applied=(),
      telemetry={
        "cluster_tolerance": round(cluster_tolerance, 5),
        "supports": len([b for b in barriers if b.side == "support"]),
        "resistances": len([b for b in barriers if b.side == "resistance"]),
        "range_state": RANGE_STATE_BROKEN,
      },
    )
  fallback_applied: list[str] = []
  missing_side_reason: str | None = None
  supports = [b for b in barriers if b.side == "support"]
  resistances = [b for b in barriers if b.side == "resistance"]
  if bool(getattr(cfg, "scalp_barrier_fallback_enabled", True)):
    if resistances and not supports:
      fallback = _fallback_barrier(
        "support", frame, offset, df, atr_value, entry_tolerance,
        max_edge_width, current_price, session_levels, resistances, cfg,
      )
      if fallback is not None:
        barriers.append(fallback)
        supports.append(fallback)
        fallback_applied.append("support")
      else:
        missing_side_reason = "no_support_after_fallback"
    elif supports and not resistances:
      fallback = _fallback_barrier(
        "resistance", frame, offset, df, atr_value, entry_tolerance,
        max_edge_width, current_price, session_levels, supports, cfg,
      )
      if fallback is not None:
        barriers.append(fallback)
        resistances.append(fallback)
        fallback_applied.append("resistance")
      else:
        missing_side_reason = "no_resistance_after_fallback"
    elif not supports and not resistances:
      missing_side_reason = "no_barriers"
  elif not supports and resistances:
    missing_side_reason = "no_support_clustering"
  elif supports and not resistances:
    missing_side_reason = "no_resistance_clustering"

  barriers = _dedup_barriers(barriers, cluster_tolerance)
  scalp_range, range_state = _best_range_with_state(
    barriers, current_price, atr_value, cfg, df, atr,
  )
  if scalp_range is None and missing_side_reason is None:
    missing_side_reason = "range_geometry_rejected"
  telemetry = {
    "cluster_tolerance": round(cluster_tolerance, 5),
    "supports": len([b for b in barriers if b.side == "support"]),
    "resistances": len([b for b in barriers if b.side == "resistance"]),
    "fallback_applied": list(fallback_applied),
    "range_state": range_state,
    "missing_side_reason": missing_side_reason,
  }
  return ScalpStructureResult(
    barriers=barriers,
    scalp_range=scalp_range,
    range_state=range_state,
    missing_side_reason=missing_side_reason,
    fallback_applied=tuple(fallback_applied),
    telemetry=telemetry,
  )


def _recent_breakout_displacement(
  df: pd.DataFrame,
  atr_value: float,
  cfg,
) -> bool:
  """True when the latest closes show decisive displacement, not consolidation."""
  break_closes = max(
    1,
    int(getattr(cfg, "range_scalp_break_closes", RANGE_SCALP_BREAK_CLOSES)),
  )
  if len(df) < break_closes + 6 or atr_value <= 0:
    return False
  prior = df.iloc[-(break_closes + 12):-break_closes]
  recent = df.tail(break_closes)
  if prior.empty or recent.empty:
    return False
  prior_high = float(prior["high"].max())
  prior_low = float(prior["low"].min())
  recent_closes = recent["close"].astype(float)
  up_break = all(float(close) > prior_high + 0.15 * atr_value for close in recent_closes)
  down_break = all(float(close) < prior_low - 0.15 * atr_value for close in recent_closes)
  if not (up_break or down_break):
    return False
  span = float(recent["high"].max() - recent["low"].min()) / atr_value
  return span >= 0.8


def _cluster_tolerance(
  atr_value: float,
  frame: pd.DataFrame,
  cfg,
) -> float:
  atr_frac = max(
    0.0,
    float(getattr(cfg, "range_scalp_cluster_atr", RANGE_SCALP_CLUSTER_ATR)),
  )
  min_abs = max(
    0.0,
    float(getattr(cfg, "range_scalp_cluster_min_abs", RANGE_SCALP_CLUSTER_MIN_ABS)),
  )
  pip_size = max(0.0, float(getattr(cfg, "pip_size", 0.1) or 0.1))
  pip_mult = max(
    0.0,
    float(getattr(
      cfg, "range_scalp_cluster_pip_mult", RANGE_SCALP_CLUSTER_PIP_MULT,
    )),
  )
  atr_component = atr_value * atr_frac
  candle_noise = 0.0
  if not frame.empty and atr_component > 0:
    spreads = (frame["high"].astype(float) - frame["low"].astype(float)).dropna()
    if not spreads.empty:
      # Tiny lift from local noise only - never dominate ATR clustering.
      candle_noise = min(float(spreads.median()) * 0.10, atr_component)
  return max(_EPS, min_abs, atr_component, pip_size * pip_mult, candle_noise)


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
    # Body-close proximity is recorded on wick/swing contacts only - it must
    # not invent new contact prices that shift cluster centres.
    upper_body_hold = close >= high - span * 0.15 and close <= open_
    lower_body_hold = close <= low + span * 0.15 and close >= open_
    if upper_rejected or (index, "high") in micro_by_bar:
      source = "wick" if upper_rejected else "swing"
      result["resistance"].append(_Contact(
        index, high, upper_fraction, upper_rejected,
        body_hold=upper_body_hold, source=source,
      ))
    if lower_rejected or (index, "low") in micro_by_bar:
      source = "wick" if lower_rejected else "swing"
      result["support"].append(_Contact(
        index, low, lower_fraction, lower_rejected,
        body_hold=lower_body_hold, source=source,
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
    # Cap barrier width so a single wide wick cannot absorb every nearby level.
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
      key=lambda item: (
        item.rejected, item.body_hold, item.wick_fraction, -item.index,
      ),
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
  *,
  body_holds: int = 0,
  fallback: bool = False,
) -> float:
  score = min(5, touches) * 1.2
  score += min(4, wick_rejections)
  score += min(3, body_holds) * 0.5
  score += min(3, max(0, confluences)) * 0.75
  if last_touch >= bar_count - 3:
    score += 1.0
  score -= max(0, accepted_closes) * 2.0
  if fallback:
    score *= 0.65
  return max(0.0, round(score, 3))


def _barrier_grade(
  score: float,
  touches: int,
  wick_rejections: int,
  tags: list[str],
) -> str:
  structural = any(
    tag.startswith("session ") or tag.startswith("TL ") or tag in {
      "box-top", "box-bottom",
    }
    for tag in tags
  )
  if score >= 8.0 and touches >= 3 and (wick_rejections >= 2 or structural):
    return "A"
  if score >= 4.0 and touches >= 2:
    return "B"
  if score > 0:
    return "C"
  return "invalid"


def _barrier_class(grade: str, tags: list[str]) -> str:
  if any(tag.startswith("session ") for tag in tags):
    return "major"
  if grade == "A":
    return "structural"
  if grade == "B":
    return "local"
  return "micro"


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
    0 if barrier.fallback else 1,
    barrier.score,
    barrier.touches,
    barrier.wick_rejections,
    barrier.last_touch_index,
    -barrier.level,
  )


def _fallback_barrier(
  side: str,
  frame: pd.DataFrame,
  offset: int,
  df: pd.DataFrame,
  atr_value: float,
  entry_tolerance: float,
  max_edge_width: float,
  price: float,
  session_levels: list,
  opposite: list[ScalpBarrier],
  cfg,
) -> ScalpBarrier | None:
  """Controlled missing-side fallback from local extrema / session / range edge.

  Never invents a barrier from a round number alone. Executable only when
  confirmed by touches, wick rejection, session overlap, or opposing-edge
  consolidation geometry.
  """
  if frame.empty or not opposite:
    return None
  min_confirmations = max(
    1,
    int(getattr(
      cfg,
      "scalp_barrier_fallback_min_confirmations",
      RANGE_SCALP_FALLBACK_MIN_CONFIRMATIONS,
    )),
  )
  lows = frame["low"].astype(float)
  highs = frame["high"].astype(float)
  closes = frame["close"].astype(float)
  if side == "support":
    extreme_idx = int(lows.values.argmin())
    level = float(lows.iloc[extreme_idx])
    if level >= price:
      # Prefer dealing-range / consolidation floor below price.
      below = lows[lows < price - _EPS]
      if below.empty:
        return None
      level = float(below.min())
      extreme_idx = int(lows[lows == level].index[0] - frame.index[0])
  else:
    extreme_idx = int(highs.values.argmax())
    level = float(highs.iloc[extreme_idx])
    if level <= price:
      above = highs[highs > price + _EPS]
      if above.empty:
        return None
      level = float(above.max())
      extreme_idx = int(highs[highs == level].index[0] - frame.index[0])

  opp = opposite[0]
  # Opposite edge of recent consolidation: use mid-range distance sanity.
  width = abs(opp.level - level)
  if width < atr_value * 0.8 or width > atr_value * 8.0:
    return None

  abs_index = extreme_idx + offset
  tolerance = entry_tolerance
  touches = 0
  wick_rejections = 0
  for local_index, row in enumerate(frame.itertuples(index=False)):
    high = float(row.high)
    low = float(row.low)
    close = float(row.close)
    open_ = float(row.open)
    span = high - low
    if span <= _EPS:
      continue
    if side == "support":
      touched = low <= level + tolerance and high >= level - tolerance
      rejected = (
        touched
        and (min(open_, close) - low) / span >= 0.25
        and close > level
      )
    else:
      touched = high >= level - tolerance and low <= level + tolerance
      rejected = (
        touched
        and (high - max(open_, close)) / span >= 0.25
        and close < level
      )
    if touched:
      touches += 1
      if rejected:
        wick_rejections += 1

  session_overlap = False
  for session in session_levels:
    name = str(getattr(session, "name", "")).upper()
    if name not in _SESSION_NAMES:
      continue
    if abs(float(session.price) - level) <= tolerance:
      session_overlap = True
      break

  confirmations = 0
  if touches >= 2:
    confirmations += 1
  if wick_rejections >= 1:
    confirmations += 1
  if session_overlap:
    confirmations += 1
  # Consolidation opposite-edge counts as one confirmation when price spent
  # multiple closes between the fallback and the strong opposite barrier.
  inside = 0
  lo, hi = sorted((level, opp.level))
  for close in closes:
    if lo - _EPS <= float(close) <= hi + _EPS:
      inside += 1
  if inside >= max(
    3,
    int(getattr(cfg, "range_scalp_min_inside_closes", RANGE_SCALP_MIN_INSIDE_CLOSES)),
  ):
    confirmations += 1

  if confirmations < min_confirmations:
    return None

  half_width = min(entry_tolerance, max_edge_width / 2.0)
  tags = [
    "fallback_local_extreme",
    f"micro ×{max(1, touches)}",
    f"wick ×{wick_rejections}",
  ]
  if session_overlap:
    tags.append("session overlap")
  score = _barrier_score(
    max(1, touches),
    wick_rejections,
    0,
    1 if session_overlap else 0,
    abs_index,
    len(df),
    fallback=True,
  )
  # Fallback starts as C; promote to B only with confirmation density.
  grade = "B" if confirmations >= 2 and score >= 3.0 else "C"
  return ScalpBarrier(
    side=side,
    level=float(level),
    low=float(level - half_width),
    high=float(level + half_width),
    touches=max(1, touches),
    wick_rejections=wick_rejections,
    accepted_closes=0,
    last_touch_index=abs_index,
    first_touch_index=abs_index,
    age=max(0, len(df) - 1 - abs_index),
    tags=tags,
    score=score,
    grade=grade,
    confidence_grade=grade,
    sources=("fallback_local_extreme",),
    class_name="micro",
    fallback=True,
    tested=touches >= 2,
  )


def _best_range(
  barriers: list[ScalpBarrier],
  price: float,
  atr: float,
  cfg,
) -> ScalpRange | None:
  scalp_range, _ = _best_range_with_state(barriers, price, atr, cfg, None, None)
  return scalp_range


def _best_range_with_state(
  barriers: list[ScalpBarrier],
  price: float,
  atr: float,
  cfg,
  df: pd.DataFrame | None,
  atr_series,
) -> tuple[ScalpRange | None, str]:
  if atr <= 0:
    return None, RANGE_STATE_NO_RANGE
  minimum_room = max(
    0.0,
    float(getattr(cfg, "range_scalp_min_room_atr", RANGE_SCALP_MIN_ROOM_ATR)),
  )
  minimum_width = max(
    0.0,
    float(getattr(cfg, "range_scalp_min_width_atr", RANGE_SCALP_MIN_WIDTH_ATR)),
    2.0 * minimum_room,
  )
  maximum_width = max(
    minimum_width,
    float(getattr(cfg, "range_scalp_max_width_atr", RANGE_SCALP_MAX_WIDTH_ATR)),
  )
  provisional_enabled = bool(
    getattr(cfg, "scalp_range_provisional_enabled", True),
  )
  post_impulse_enabled = bool(
    getattr(cfg, "scalp_post_impulse_range_enabled", True),
  )
  min_inside = max(
    1,
    int(getattr(cfg, "range_scalp_min_inside_closes", RANGE_SCALP_MIN_INSIDE_CLOSES)),
  )
  supports = [
    barrier for barrier in barriers
    if barrier.side == "support"
    and not barrier.invalidated
    and barrier.low <= price + _EPS
  ]
  resistances = [
    barrier for barrier in barriers
    if barrier.side == "resistance"
    and not barrier.invalidated
    and barrier.high >= price - _EPS
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
      inside_closes = _inside_closes(df, lower.level, upper.level) if df is not None else min_inside
      post_impulse = (
        post_impulse_enabled
        and df is not None
        and _is_post_impulse(df, atr_series, atr, lower.level, upper.level)
      )
      lower_ok = _executable_edge(lower)
      upper_ok = _executable_edge(upper)
      both_strong = lower_ok and upper_ok and not lower.fallback and not upper.fallback
      one_fallback = (lower.fallback ^ upper.fallback) and (lower_ok or upper_ok)
      rejection_ok = (
        lower.wick_rejections >= 1 or upper.wick_rejections >= 1
        or lower.body_holds >= 1 or upper.body_holds >= 1
      )
      if both_strong and inside_closes >= min_inside and rejection_ok:
        state = (
          RANGE_STATE_POST_IMPULSE if post_impulse else RANGE_STATE_CONFIRMED
        )
      elif (
        provisional_enabled
        and one_fallback
        and inside_closes >= min_inside
        and rejection_ok
      ):
        state = RANGE_STATE_PROVISIONAL
      elif (
        provisional_enabled
        and (lower_ok or upper_ok)
        and inside_closes >= min_inside
        and rejection_ok
        and (
          (lower.fallback and upper.confidence_grade in {"A", "B"})
          or (upper.fallback and lower.confidence_grade in {"A", "B"})
          or (
            lower.confidence_grade in {"A", "B"}
            and upper.confidence_grade in {"A", "B"}
          )
        )
      ):
        state = (
          RANGE_STATE_POST_IMPULSE if post_impulse else RANGE_STATE_PROVISIONAL
        )
      else:
        continue
      # Broken barriers (accepted closes) never form a live range.
      if (
        lower.accepted_closes >= max(1, int(getattr(cfg, "range_scalp_break_closes", RANGE_SCALP_BREAK_CLOSES)))
        or upper.accepted_closes >= max(1, int(getattr(cfg, "range_scalp_break_closes", RANGE_SCALP_BREAK_CLOSES)))
      ):
        continue
      quality = lower.score + upper.score
      if state == RANGE_STATE_PROVISIONAL:
        quality *= 0.75
      if state == RANGE_STATE_POST_IMPULSE:
        quality *= 0.85
      range_id = f"{lower.level:.2f}-{upper.level:.2f}-{state}"
      candidates.append(ScalpRange(
        lower,
        upper,
        eq,
        width_atr,
        quality,
        state=state,
        range_id=range_id,
        inside_closes=inside_closes,
        one_sided=lower.fallback or upper.fallback,
        post_impulse=state == RANGE_STATE_POST_IMPULSE,
        reasons=(state,),
      ))
  if not candidates:
    return None, RANGE_STATE_NO_RANGE
  best = min(
    candidates,
    key=lambda item: (
      0 if item.state == RANGE_STATE_CONFIRMED
      else 1 if item.state == RANGE_STATE_POST_IMPULSE
      else 2,
      -item.quality,
      abs(price - item.eq),
      item.width_atr,
      item.lower.level,
      item.upper.level,
    ),
  )
  return best, best.state


def _executable_edge(barrier: ScalpBarrier) -> bool:
  grade = (barrier.confidence_grade or barrier.grade or "C").upper()
  if barrier.invalidated or grade == "INVALID":
    return False
  if barrier.fallback:
    return grade in {"A", "B"} and (
      barrier.touches >= 2
      or barrier.wick_rejections >= 1
      or any(tag.startswith("session") for tag in barrier.tags)
    )
  return grade in {"A", "B"}


def _inside_closes(df: pd.DataFrame, lower: float, upper: float) -> int:
  count = 0
  for close in df["close"].astype(float).tail(24):
    if lower - _EPS <= float(close) <= upper + _EPS:
      count += 1
  return count


def _is_post_impulse(
  df: pd.DataFrame,
  atr_series,
  atr_value: float,
  lower: float,
  upper: float,
) -> bool:
  """Detect a strong impulse still inside lookback followed by contraction."""
  if df is None or len(df) < 8 or atr_value <= 0:
    return False
  closes = df["close"].astype(float)
  window = closes.tail(min(36, len(closes)))
  displacement = float(window.max() - window.min()) / atr_value
  if displacement < 3.0:
    return False
  recent = df.tail(6)
  recent_width = float(recent["high"].max() - recent["low"].min()) / atr_value
  if recent_width > 2.2:
    return False
  # Recent closes rotate inside the candidate range.
  inside = sum(
    1 for close in recent["close"].astype(float)
    if lower - _EPS <= float(close) <= upper + _EPS
  )
  return inside >= 4


def role_flip_barrier(
  barrier: ScalpBarrier,
  *,
  accepted_break: bool,
  retest_held: bool,
) -> ScalpBarrier | None:
  """Resistance→support (or support→resistance) after accepted break + retest."""
  if not accepted_break or not retest_held:
    return None
  new_side = "support" if barrier.side == "resistance" else "resistance"
  tags = _unique([*barrier.tags, "role-flip", f"was-{barrier.side}"])
  return replace(
    barrier,
    side=new_side,
    tags=tags,
    sources=tuple(sorted({*barrier.sources, "role_flip"})),
    reclaimed=True,
    grade="B",
    confidence_grade="B",
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
