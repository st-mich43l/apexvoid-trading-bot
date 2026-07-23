"""Displacement, supply/demand, order-block, and mitigation logic."""

from __future__ import annotations

from dataclasses import replace
import logging
import math

import pandas as pd

from app.analysis.math_utils import atr_at, atr_series, body_fraction, candle_direction
from app.analysis.types import (
  Break,
  DealingRange,
  Grab,
  Leg,
  Level,
  Pool,
  SessionLevel,
  Zone,
)
from app.analysis.trendlines import Trendline, value_at

ZONE_MERGE_OVERLAP = 0.5
# Opposing-side reconciliation (reconcile_opposing) uses the same bar as
# same-side merging above: partial overlap between adjacent FVGs is normal
# market structure, not a contradiction. Only a genuinely substantial
# overlap (or full containment, which scores 1.0 - see _overlap_ratio)
# should trigger a trim. 22 Jul 2026 regression: the original
# implementation used a bare "any overlap > 0" test, which on dense M5 FVG
# output treated nearly every opposing pair as a conflict and, combined
# with the cascading trim loop, emptied the zone map.
ZONE_RECONCILE_OVERLAP = 0.5
# Circuit breaker: if a single reconcile_opposing() call would trim or drop
# more than this fraction of the input zones, the map has a different
# problem than reconciliation can safely resolve - discard the
# reconciliation results and fail open (return the input unchanged) rather
# than let a runaway cascade strip the map, per the 22 Jul regression.
ZONE_RECONCILE_MAX_FRACTION = 0.20
# Below this many input zones, a fraction is not a meaningful signal - one
# legitimate trim out of 2-4 zones is already >20%, and the circuit breaker
# firing there would block the exact kind of small, correct reconciliation
# (e.g. the 23 Jul incident's isolated pair) the fix exists to perform.
# 5 is the smallest sample where a single affected zone (1/5 = 20%, not
# > 20%) doesn't itself trip the breaker.
ZONE_RECONCILE_MIN_SAMPLE = 5
# Floor for reconcile_opposing's trim-vs-drop decision - a remainder this
# narrow (or narrower) carries no tradeable information and is dropped
# rather than kept as a sliver. Callers scale this against ATR (see
# engine.py's reconcile_opposing call site) and take the smaller of the two.
ZONE_MIN_WIDTH = 2.0
# Prefix stamped into a trimmed zone's score_reasons so downstream readers
# (market_map.py's tag rendering, scanner.py's reconciliation counter) can
# recognize a reconciled zone without a dedicated Zone field.
ZONE_RECONCILED_TAG_PREFIX = "reconciled vs "
FRESH_SCORE = 3.0
SINGLE_TOUCH_SCORE = 1.0
SOURCE_SCORE_CAP = 5.0
SOURCE_SCORES = {
  "order_block": 3.0,
  "breaker": 2.0,
  "flip_zone": 2.0,
  "supply_demand": 1.5,
  "bullish_fvg": 1.0,
  "bearish_fvg": 1.0,
  "box_breakout": 5.0,
}
KEY_LEVEL_SCORE = 2.0
ROUND_NUMBER_SCORE = 1.0
LIQUIDITY_SCORE = 2.0
HTF_SCORE = 3.0
SESSION_LEVEL_SCORE = 2.0
PD_POSITION_SCORE = 2.0
GRAB_A_SCORE = 2.0
TRENDLINE_SCORE = 1.5

log = logging.getLogger(__name__)


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


def breaker_blocks(order_blocks: list[Zone], df: pd.DataFrame) -> list[Zone]:
  zones: list[Zone] = []
  for zone in order_blocks:
    violation = _breaker_violation(zone, df)
    if violation is None:
      zones.append(zone)
      continue
    dead = replace(
      zone,
      touches=max(zone.touches, 1),
      mitigated=True,
    )
    zones.append(dead)
    side = "supply" if zone.side == "demand" else "demand"
    zones.append(Zone(
      bottom=zone.low,
      top=zone.high,
      side=side,
      origin_index=violation,
      created_ts=df.index[violation],
      source="breaker",
      break_kind="breaker",
      break_index=violation,
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


def mark_mitigation(
  zones: list[Zone],
  df: pd.DataFrame,
  cutoff: int | None = None,
) -> list[Zone]:
  """Stamp touches with as-of semantics.

  ``cutoff`` is exclusive. With ``cutoff=len(df)-1``, a zone tapped for the
  first time by the current trigger bar still has ``touches == 0`` and is
  considered fresh. Pass ``cutoff=None`` for full-history reporting.
  """
  stamped: list[Zone] = []
  end = len(df) if cutoff is None else max(0, min(cutoff, len(df)))
  for zone in zones:
    touches = 0
    in_touch = False
    start_from = zone.break_index if zone.break_index is not None else zone.origin_index
    start = max(0, start_from + 1)
    for i in range(start, end):
      row = df.iloc[i]
      touched = float(row["low"]) <= zone.top and float(row["high"]) >= zone.bottom
      if touched and not in_touch:
        touches += 1
      in_touch = touched
    final_touches = max(touches, zone.touches)
    stamped.append(replace(
      zone,
      touches=final_touches,
      mitigated=zone.mitigated or final_touches > 0,
    ))
  return stamped


def merge_zones(
  zones: list[Zone],
  min_overlap: float = ZONE_MERGE_OVERLAP,
  max_width: float | None = None,
) -> list[Zone]:
  groups: list[list[Zone]] = []
  for zone in sorted(zones, key=lambda item: (item.side, item.low, item.high)):
    for group in groups:
      if group[0].side != zone.side:
        continue
      if any(_overlap_ratio(zone, member) >= min_overlap for member in group):
        if max_width is not None and _merged_width([*group, zone]) > max_width:
          continue
        group.append(zone)
        break
    else:
      groups.append([zone])
  return [_composite_zone(group) for group in groups]


def score_zones(
  zones: list[Zone],
  key_levels: list[Level],
  pools: list[Pool],
  round_step: float,
  htf_zones: list[Zone] | None = None,
  session_levels: list[SessionLevel] | None = None,
  dealing_range: DealingRange | None = None,
  grabs: list[Grab] | None = None,
  trendlines: list[Trendline] | None = None,
  bar_index: int | None = None,
) -> list[Zone]:
  scored = [
    _score_zone(
      zone,
      key_levels,
      pools,
      round_step,
      htf_zones or [],
      session_levels or [],
      dealing_range,
      grabs or [],
      trendlines or [],
      bar_index,
    )
    for zone in zones
  ]
  return sorted(
    scored,
    key=lambda zone: (zone.score, -zone.touches, -zone.low),
    reverse=True,
  )


def reconcile_opposing(
  zones: list[Zone],
  min_width: float,
  *,
  min_overlap: float = ZONE_RECONCILE_OVERLAP,
  max_fraction: float = ZONE_RECONCILE_MAX_FRACTION,
  stats: dict | None = None,
) -> list[Zone]:
  """Trim the lower-scored side of any *substantially* overlapping
  supply/demand pair to the non-overlapping remainder (23 Jul 2026
  incident: a published SELL and BUY band overlapped six price wide, and
  price sat inside both at once).

  merge_zones only reconciles same-side overlaps by construction; opposing
  sides pass through untouched. This runs strictly after merge_zones and
  score_zones (scores must already exist for the keep/trim comparison) and
  only ever looks at supply-vs-demand pairs, so same-side behaviour is
  exactly what merge_zones already produced.

  ``min_overlap`` uses the same overlap-*ratio* bar as ``merge_zones``
  (``_overlap_ratio``, normalised by the narrower zone - full containment
  scores 1.0) rather than a bare "any overlap" test: dense FVG output on a
  fast timeframe routinely produces small opposing overlaps that are
  normal market structure, not a genuine contradiction (22 Jul 2026
  regression - a zero-threshold test treated nearly every pair as a
  conflict and, compounded by repeated trims of the same zone, emptied the
  map). Each zone may be the *trim target* at most once per invocation
  (it may still stand in as the untouched *keep* side of a later
  comparison) - this caps total shrinkage per zone and removes the
  compounding path to ``min_width``.

  If the fraction of input zones trimmed-or-dropped in one call exceeds
  ``max_fraction`` (only evaluated once there are at least
  ``ZONE_RECONCILE_MIN_SAMPLE`` input zones - below that, a single trim is
  already a large fraction and isn't evidence of a cascade), the map has a
  different problem than reconciliation can safely resolve on its own: the
  whole result is discarded and the original ``zones`` are returned
  unchanged (fail open - an unreconciled map with a known defect is still
  covered by the trade-time vetoes; an empty map is not recoverable by
  anything downstream).

  Deterministic: both sides are sorted independently before any comparison
  (so caller list order never matters), the keep/trim decision is a pure
  rank function with no ties left undecided, and the outer loop always
  terminates because every pass either trims/drops a not-yet-exhausted
  zone (bounded by the supply/demand counts) or finds nothing left to do.

  ``stats``, if given a dict, is filled in place with
  ``{"input", "trimmed", "dropped", "output", "aborted", "min_overlap",
  "min_width"}`` for callers (engine.py) that want to surface counters
  without this function touching Redis/logging config itself beyond the
  one summary log line it always emits.
  """
  supply: list[Zone | None] = sorted(
    (zone for zone in zones if zone.side == "supply"), key=_reconcile_sort_key,
  )
  demand: list[Zone | None] = sorted(
    (zone for zone in zones if zone.side == "demand"), key=_reconcile_sort_key,
  )
  other = [zone for zone in zones if zone.side not in ("supply", "demand")]
  input_count = len(supply) + len(demand)

  # (side, origin_index) pairs already used as a TRIM target this
  # invocation - exhausted from being trimmed again, but still eligible to
  # be compared against / act as the keep side for a different pair.
  exhausted: set[tuple[str, int]] = set()
  trimmed_count = 0
  dropped_count = 0

  changed = True
  while changed:
    changed = False
    for s_index in range(len(supply)):
      s_zone = supply[s_index]
      if s_zone is None:
        continue
      for d_index in range(len(demand)):
        d_zone = demand[d_index]
        if d_zone is None:
          continue
        if _overlap_ratio(s_zone, d_zone) < min_overlap:
          continue
        supply_is_target = _reconcile_rank(s_zone) <= _reconcile_rank(d_zone)
        target_key = (
          ("supply", s_zone.origin_index)
          if supply_is_target
          else ("demand", d_zone.origin_index)
        )
        if target_key in exhausted:
          continue
        exhausted.add(target_key)
        if supply_is_target:
          result = _trim_outside(s_zone, d_zone, min_width)
          supply[s_index] = result
        else:
          result = _trim_outside(d_zone, s_zone, min_width)
          demand[d_index] = result
        if result is None:
          dropped_count += 1
        else:
          trimmed_count += 1
        changed = True
        break
      if changed:
        break

  affected = trimmed_count + dropped_count
  aborted = (
    input_count >= ZONE_RECONCILE_MIN_SAMPLE
    and (affected / input_count) > max_fraction
  )
  if aborted:
    log.warning(
      "zone reconcile aborted: in=%d affected=%d (%.0f%% > max %.0f%%) "
      "min_overlap=%.2f min_width=%.2f",
      input_count, affected, 100 * affected / input_count,
      100 * max_fraction, min_overlap, min_width,
    )
    if stats is not None:
      stats.update(
        input=input_count, trimmed=0, dropped=0, output=input_count,
        aborted=True, min_overlap=min_overlap, min_width=min_width,
      )
    return zones

  output_count = input_count - dropped_count
  log_fn = log.info if dropped_count > 0 else log.debug
  log_fn(
    "zone reconcile: in=%d trimmed=%d dropped=%d out=%d min_overlap=%.2f min_width=%.2f",
    input_count, trimmed_count, dropped_count, output_count, min_overlap, min_width,
  )
  if stats is not None:
    stats.update(
      input=input_count, trimmed=trimmed_count, dropped=dropped_count,
      output=output_count, aborted=False, min_overlap=min_overlap,
      min_width=min_width,
    )

  return [
    *(zone for zone in supply if zone is not None),
    *(zone for zone in demand if zone is not None),
    *other,
  ]


def _reconcile_sort_key(zone: Zone) -> tuple[float, float, int]:
  return (zone.low, zone.high, zone.origin_index)


def _reconcile_rank(zone: Zone) -> tuple[float, float, int, str]:
  # Higher score wins; tie -> wider zone; tie -> lower (older) origin_index.
  # The final element (side) guarantees a decidable comparison even in the
  # vanishingly unlikely case a supply and demand zone otherwise tie -
  # opposing zones always differ on side, so two ranks here never truly tie.
  return (zone.score, zone.high - zone.low, -zone.origin_index, zone.side)


def _trim_outside(trim: Zone, keep: Zone, min_width: float) -> Zone | None:
  covers_low = keep.low <= trim.low
  covers_high = keep.high >= trim.high
  if covers_low and covers_high:
    return None
  if covers_low:
    remainder_low, remainder_high = keep.high, trim.high
  elif covers_high:
    remainder_low, remainder_high = trim.low, keep.low
  else:
    # keep sits strictly inside trim - splitting would produce two disjoint
    # fragments; keep only the larger remaining side instead.
    lower_width = keep.low - trim.low
    upper_width = trim.high - keep.high
    if lower_width >= upper_width:
      remainder_low, remainder_high = trim.low, keep.low
    else:
      remainder_low, remainder_high = keep.high, trim.high
  if remainder_high - remainder_low < min_width:
    return None
  reason = f"{ZONE_RECONCILED_TAG_PREFIX}{keep.side} {keep.low:.2f}-{keep.high:.2f}"
  return replace(
    trim,
    bottom=remainder_low,
    top=remainder_high,
    score_reasons=[*trim.score_reasons, reason],
  )


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


def _overlap_ratio(first: Zone, second: Zone) -> float:
  overlap = min(first.high, second.high) - max(first.low, second.low)
  if overlap <= 0:
    return 0.0
  smaller = min(first.high - first.low, second.high - second.low)
  if smaller <= 0:
    return 1.0 if first.low <= second.high and second.low <= first.high else 0.0
  return overlap / smaller


def _merged_width(zones: list[Zone]) -> float:
  return max(zone.high for zone in zones) - min(zone.low for zone in zones)


def _composite_zone(group: list[Zone]) -> Zone:
  if len(group) == 1:
    zone = group[0]
    sources = _unique_sources([zone])
    return replace(zone, sources=sources)
  best = max(group, key=_source_quality)
  earliest = min(group, key=lambda item: item.origin_index)
  touches = min(item.touches for item in group)
  return Zone(
    bottom=min(item.low for item in group),
    top=max(item.high for item in group),
    side=group[0].side,
    origin_index=earliest.origin_index,
    created_ts=earliest.created_ts,
    touches=touches,
    mitigated=touches > 0,
    source=best.source,
    sources=_unique_sources(group),
    break_kind=best.break_kind,
    break_index=best.break_index,
  )


def _unique_sources(zones: list[Zone]) -> list[str]:
  result: list[str] = []
  for zone in zones:
    for source in zone.sources or ([zone.source] if zone.source else []):
      if source and source not in result:
        result.append(source)
  return result


def _score_zone(
  zone: Zone,
  levels: list[Level],
  pools: list[Pool],
  round_step: float,
  htf_zones: list[Zone],
  session_levels: list[SessionLevel],
  dealing_range: DealingRange | None,
  grabs: list[Grab],
  trendlines: list[Trendline],
  bar_index: int | None,
) -> Zone:
  score = 0.0
  reasons: list[str] = []
  if zone.touches == 0:
    score += FRESH_SCORE
    reasons.append("fresh")
  elif zone.touches == 1:
    score += SINGLE_TOUCH_SCORE
    reasons.append("1 touch")

  source_score, source_reasons = _source_score(zone)
  score += source_score
  reasons.extend(source_reasons)

  if any(_zone_overlaps_level(zone, level) for level in levels):
    score += KEY_LEVEL_SCORE
    reasons.append("key level")
  round_number = _round_number_inside(zone, round_step)
  if round_number is not None:
    score += ROUND_NUMBER_SCORE
    reasons.append(f"round {_number(round_number)}")
  if _has_liquidity_confluence(zone, pools):
    score += LIQUIDITY_SCORE
    reasons.append("liquidity pool")
  for session_name in _session_level_confluences(zone, session_levels):
    score += SESSION_LEVEL_SCORE
    reasons.append(session_name)
  pd_reason = _pd_position_reason(zone, dealing_range)
  if pd_reason is not None:
    score += PD_POSITION_SCORE
    reasons.append(pd_reason)
  if _has_grade_a_grab(zone, grabs):
    score += GRAB_A_SCORE
    reasons.append("sweep A")
  if any(_inside_htf_zone(zone, htf) for htf in htf_zones):
    score += HTF_SCORE
    reasons.append("HTF zone")
  if _has_trendline_confluence(zone, trendlines, bar_index):
    score += TRENDLINE_SCORE
    reasons.append("TL confluence")
  return replace(zone, score=score, score_reasons=reasons)


def _source_score(zone: Zone) -> tuple[float, list[str]]:
  total = 0.0
  reasons: list[str] = []
  for source in zone.sources or ([zone.source] if zone.source else []):
    value = SOURCE_SCORES.get(source, 0.0)
    if source == "order_block" and zone.break_kind is None:
      value = 0.0
    if value <= 0:
      continue
    total += value
    reasons.append(_source_reason(source))
  return min(total, SOURCE_SCORE_CAP), reasons


def _source_quality(zone: Zone) -> float:
  return _source_score(zone)[0]


def _source_reason(source: str) -> str:
  if source == "order_block":
    return "OB"
  if source == "breaker":
    return "breaker"
  if source == "flip_zone":
    return "flip"
  if source == "supply_demand":
    return "S/D"
  if source.endswith("_fvg"):
    return "FVG"
  if source == "box_breakout":
    return "box breakout"
  return source


def _has_trendline_confluence(
  zone: Zone,
  trendlines: list[Trendline],
  bar_index: int | None,
) -> bool:
  if bar_index is None:
    return False
  return any(
    not line.broken and zone.low <= value_at(line, bar_index) <= zone.high
    for line in trendlines
  )


def _zone_overlaps_level(zone: Zone, level: Level) -> bool:
  band = max(level.band, 0.0)
  low = level.price - band
  high = level.price + band
  if band == 0:
    return zone.low <= level.price <= zone.high
  return zone.low <= high and zone.high >= low


def _round_number_inside(zone: Zone, round_step: float) -> float | None:
  if round_step <= 0:
    return None
  first = math.ceil(zone.low / round_step) * round_step
  if first <= zone.high:
    return first
  return None


def _has_liquidity_confluence(zone: Zone, pools: list[Pool]) -> bool:
  width = max(zone.high - zone.low, 0.0)
  for pool in pools:
    tolerance = max(pool.band, width, 0.1)
    if zone.side == "demand" and pool.side == "sell":
      if zone.low - tolerance <= pool.level <= zone.low:
        return True
    if zone.side == "supply" and pool.side == "buy":
      if zone.high <= pool.level <= zone.high + tolerance:
        return True
  return False


def _session_level_confluences(
  zone: Zone,
  session_levels: list[SessionLevel],
) -> list[str]:
  result: list[str] = []
  width = max(zone.high - zone.low, 0.0)
  tolerance = max(width, 0.1)
  for level in session_levels:
    if level.swept:
      continue
    if level.name in result:
      continue
    if zone.side == "demand" and _is_low_session_level(level.name):
      if (
        zone.low <= level.price <= zone.high
        or zone.low - tolerance <= level.price <= zone.low
      ):
        result.append(level.name)
    if zone.side == "supply" and _is_high_session_level(level.name):
      if (
        zone.low <= level.price <= zone.high
        or zone.high <= level.price <= zone.high + tolerance
      ):
        result.append(level.name)
  return result


def _pd_position_reason(
  zone: Zone,
  dealing_range: DealingRange | None,
) -> str | None:
  if dealing_range is None:
    return None
  if zone.side == "demand" and zone.high <= dealing_range.eq:
    return "discount"
  if zone.side == "supply" and zone.low >= dealing_range.eq:
    return "premium"
  return None


def _has_grade_a_grab(zone: Zone, grabs: list[Grab]) -> bool:
  for grab in grabs:
    if grab.grade != "A":
      continue
    if zone.side == "demand" and grab.direction == "bull":
      if _pool_points_into_zone(zone, grab.pool):
        return True
    if zone.side == "supply" and grab.direction == "bear":
      if _pool_points_into_zone(zone, grab.pool):
        return True
  return False


def _pool_points_into_zone(zone: Zone, pool: Pool) -> bool:
  width = max(zone.high - zone.low, 0.0)
  tolerance = max(pool.band, width, 0.1)
  if zone.side == "demand" and pool.side == "sell":
    return zone.low - tolerance <= pool.level <= zone.high
  if zone.side == "supply" and pool.side == "buy":
    return zone.low <= pool.level <= zone.high + tolerance
  return False


def _is_low_session_level(name: str) -> bool:
  return name.endswith("_L") or name in {"PDL", "PWL"}


def _is_high_session_level(name: str) -> bool:
  return name.endswith("_H") or name in {"PDH", "PWH"}


def _inside_htf_zone(zone: Zone, htf: Zone) -> bool:
  return (
    zone.side == htf.side
    and zone.low >= htf.low
    and zone.high <= htf.high
  )


def _number(value: float) -> str:
  return f"{value:.2f}".rstrip("0").rstrip(".")


def _breaker_violation(zone: Zone, df: pd.DataFrame) -> int | None:
  start = max(0, zone.origin_index + 1)
  for i in range(start, len(df)):
    close = float(df["close"].iloc[i])
    if zone.side == "demand" and close < zone.low:
      return i
    if zone.side == "supply" and close > zone.high:
      return i
  return None


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
