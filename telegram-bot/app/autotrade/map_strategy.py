"""M1 execution strategy for structural levels published by Market Map.

Market Map is context, not a global gate.  This module promotes one mapped
zone to an executable strategy only when the latest M1 candle actually touches
and rejects it. HTF-aligned zones are the default; an opt-in counter-bias path
adds stricter freshness, score, and structural-confluence rules. Display-only
fallback levels (for example a lone round number) are never executable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Any

import pandas as pd

from app.analysis.indicators import atr as atr_indicator
from app.analysis.market_map import (
  MapEntry,
  MarketMap,
  market_map_from_payload,
)
from app.autotrade import units
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
)


EXECUTION_TIMEFRAME = "M1"
MARKET_MAP_KEY_PREFIX = "auto_trade:market_map"
MARKET_MAP_DISPLAY_KEY_PREFIX = "auto_trade:market_map_display"
MARKET_MAP_ACTIONABLE_KEY_PREFIX = "auto_trade:map_strategy:actionable"
MAP_ZONE_MIN_WIDTH_ATR = 0.15
MAP_ZONE_MIN_WIDTH_ABS = 1.0
MAP_COUNTER_BIAS_MIN_SCORE = 6.0
MAP_COUNTER_BIAS_MIN_CONFLUENCE = 2
MAP_REACTION_REACH_ATR = 1.5
_ACTIONABLE_TAGS = {
  "breakout-retest",
  "demand",
  "flip",
  "fresh",
  "fvg",
  "ob",
  "supply",
}
_STRUCTURAL_TAGS = {
  "breaker",
  "breakout-retest",
  "demand",
  "flip",
  "fvg",
  "ob",
  "supply",
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionableMapEntry:
  side: str
  lo: float
  hi: float
  tier: str
  score: float
  contains_price: bool
  distance: float

  def payload(self) -> dict[str, object]:
    return {
      "side": self.side,
      "lo": self.lo,
      "hi": self.hi,
      "tier": self.tier,
      "score": self.score,
      "contains_price": self.contains_price,
    }


@dataclass(frozen=True)
class MarketMapStrategyDecision:
  state: str
  reasons: tuple[str, ...] = ()
  match: StrategyMatch | None = None
  mapped_zone: tuple[float, float] | None = None
  entries_seen: int = 0
  actionable_entries: tuple[ActionableMapEntry, ...] = ()
  filter_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class _ReactionSelection:
  selected: tuple[MapEntry, str, float, float, bool] | None
  state: str
  reasons: tuple[str, ...]
  entries_seen: int
  actionable_entries: tuple[ActionableMapEntry, ...]
  filter_counts: tuple[tuple[str, int], ...]


def evaluate_market_map_strategy(
  frames: dict[str, pd.DataFrame],
  *,
  symbol: str,
  event_ts: str,
  spot_price: float | None,
  cfg: Any,
  market_map: MarketMap | None = None,
  rendered_map: MarketMap | None = None,
  now: int | None = None,
) -> MarketMapStrategyDecision:
  """Match an actionable mapped zone with an immediate M1 rejection."""
  if not bool(getattr(cfg, "auto_trade_market_map_strategy_enabled", True)):
    return MarketMapStrategyDecision("disabled")
  if spot_price is None or not math.isfinite(float(spot_price)):
    return MarketMapStrategyDecision("waiting_for_spot")
  if EXECUTION_TIMEFRAME not in frames or frames[EXECUTION_TIMEFRAME].empty:
    return MarketMapStrategyDecision(
      "warming_up",
      ("M1 execution context is not complete",),
    )
  if market_map is None:
    return MarketMapStrategyDecision(
      "warming_up",
      ("waiting for the next structural M5 Market Map",),
    )

  m1 = frames[EXECUTION_TIMEFRAME]
  atr_values = atr_indicator(m1, max(2, int(getattr(cfg, "atr_length", 14))))
  atr = _last_positive(atr_values)
  if atr is None:
    return MarketMapStrategyDecision("warming_up", ("M1 ATR is not ready",))

  selection = _select_reaction_detailed(
    market_map,
    m1,
    float(spot_price),
    atr,
    float(getattr(cfg, "proximal_band_atr", 0.5)),
    cfg,
    rendered_map,
  )
  if selection.selected is None:
    return MarketMapStrategyDecision(
      selection.state,
      selection.reasons,
      entries_seen=selection.entries_seen,
      actionable_entries=selection.actionable_entries,
      filter_counts=selection.filter_counts,
    )
  entry, direction, entry_low, entry_high, counter_bias = selection.selected

  pip_size = units.pip_size(symbol)
  drift = _band_distance(float(spot_price), entry_low, entry_high) / pip_size
  drift_limit = max(
    0.0,
    float(getattr(cfg, "auto_trade_max_entry_distance_pips", 10)),
  )
  if drift > drift_limit:
    return MarketMapStrategyDecision(
      "entry_moved",
      (
        f"M1 rejected mapped {direction} zone but moved {drift:.1f} pips "
        f"beyond the {drift_limit:.1f}-pip execution limit",
      ),
      mapped_zone=(entry.lo, entry.hi),
      entries_seen=selection.entries_seen,
      actionable_entries=selection.actionable_entries,
      filter_counts=selection.filter_counts,
    )

  issued_at = (
    int(datetime.now(timezone.utc).timestamp())
    if now is None else int(now)
  )
  ttl = max(
    60,
    int(getattr(cfg, "auto_trade_strategy_match_max_age_seconds", 420)),
  )
  targets = (
    _counter_bias_targets(cfg, symbol, direction, float(spot_price), market_map.eq)
    if counter_bias else _targets(cfg)
  )
  if not targets:
    return MarketMapStrategyDecision(
      "invalid_targets",
      (
        "counter-bias target is not ahead at box EQ"
        if counter_bias
        else "ApexVoid Algo has no configured profit targets",
      ),
      mapped_zone=(entry.lo, entry.hi),
      entries_seen=selection.entries_seen,
      actionable_entries=selection.actionable_entries,
      filter_counts=selection.filter_counts,
    )
  strategy = "Mapped Zone Reaction"
  match_id = strategy_match_id(
    symbol,
    EXECUTION_TIMEFRAME,
    str(event_ts),
    strategy,
    direction,
    entry_low,
    entry_high,
  )
  confluence = _confluence(entry, market_map)
  tag_text = " · ".join(entry.tags[:4])
  match_reasons = (
    (
      f"{market_map.bias_tf or 'HTF'} bias {market_map.bias} · counter_bias"
      if counter_bias
      else f"{market_map.bias_tf or 'HTF'} bias {market_map.bias}"
    ),
    f"mapped {direction} zone {entry.lo:.2f}-{entry.hi:.2f}",
    *([tag_text] if tag_text else []),
    "M1 touch + rejection",
    *(
      [f"target capped at box EQ {market_map.eq:.2f}"]
      if counter_bias and market_map.eq is not None else []
    ),
  )
  match = StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol=symbol.upper(),
    source_tf=EXECUTION_TIMEFRAME,
    event_ts=str(event_ts),
    issued_at=issued_at,
    expires_at=issued_at + ttl,
    strategy=strategy,
    strategy_mode="mapped_zone_reaction",
    direction=direction,
    key_level=float(entry.lo if direction == "SELL" else entry.hi),
    entry_low=entry_low,
    entry_high=entry_high,
    current_price=float(spot_price),
    confluence=confluence,
    reasons=match_reasons,
    atr=atr,
    structure_swing=(
      float(entry.hi) if direction == "SELL" else float(entry.lo)
    ),
    targets_pips=targets,
    tags=("counter_bias",) if counter_bias else (),
    target_price=float(market_map.eq) if counter_bias and market_map.eq is not None else None,
  )
  return MarketMapStrategyDecision(
    "candidate",
    match_reasons,
    match,
    (entry.lo, entry.hi),
    selection.entries_seen,
    selection.actionable_entries,
    selection.filter_counts,
  )


def _select_reaction(
  market_map: MarketMap,
  m1: pd.DataFrame,
  price: float,
  atr: float,
  proximal_band_atr: float,
  cfg: Any = None,
  rendered_map: MarketMap | None = None,
) -> tuple[tuple[MapEntry, str, float, float] | None, str, tuple[str, ...]]:
  result = _select_reaction_detailed(
    market_map,
    m1,
    price,
    atr,
    proximal_band_atr,
    cfg,
    rendered_map,
  )
  selected = (
    None
    if result.selected is None
    else result.selected[:4]
  )
  return selected, result.state, result.reasons


def _select_reaction_detailed(
  market_map: MarketMap,
  m1: pd.DataFrame,
  price: float,
  atr: float,
  proximal_band_atr: float,
  cfg: Any = None,
  rendered_map: MarketMap | None = None,
) -> _ReactionSelection:
  direction = "BUY" if market_map.bias == "up" else "SELL" if market_map.bias == "down" else None
  if direction is None:
    return _ReactionSelection(
      None,
      "waiting_for_bias",
      ("Market Map HTF bias is range",),
      len(market_map.entries),
      (),
      _filter_counts(side=len(market_map.entries)),
    )
  side = "buy" if direction == "BUY" else "sell"
  counter_enabled = bool(
    getattr(cfg, "auto_trade_map_counter_bias_enabled", False)
  )
  counter_side = "sell" if side == "buy" else "buy"
  counts = {
    "side": 0,
    "actionable": 0,
    "degenerate_width": 0,
    "distance": 0,
  }
  candidates: list[tuple[MapEntry, str, bool]] = []
  for entry in market_map.entries:
    aligned = entry.side == side
    counter_bias = counter_enabled and entry.side == counter_side
    if not aligned and not counter_bias:
      counts["side"] += 1
      continue
    if aligned:
      if not _semantic_actionable(entry):
        counts["actionable"] += 1
        continue
      if not _actionable(entry, atr, cfg):
        counts["degenerate_width"] += 1
        continue
    else:
      if not _counter_bias_quality(entry, market_map, atr, cfg):
        counts["actionable"] += 1
        continue
      if not _width_is_actionable(entry, atr, cfg, warn=True):
        counts["degenerate_width"] += 1
        continue
    candidate_direction = "BUY" if entry.side == "buy" else "SELL"
    candidates.append((entry, candidate_direction, counter_bias))

  actionable = [
    ActionableMapEntry(
      entry.side,
      float(entry.lo),
      float(entry.hi),
      entry.tier,
      float(entry.score),
      bool(entry.contains_price),
      _band_distance(price, entry.lo, entry.hi),
    )
    for entry, _, _ in sorted(
      candidates,
      key=lambda item: (
        _band_distance(price, item[0].lo, item[0].hi),
        -item[0].score,
      ),
    )
  ]
  if not actionable:
    return _ReactionSelection(
      None,
      "waiting_for_zone",
      (
        f"no structural mapped {direction} zone aligned with HTF bias"
        f"{_filter_summary(counts)}",
      ),
      len(market_map.entries),
      (),
      _filter_counts(**counts),
    )

  tolerance = max(0.05, max(0.0, proximal_band_atr) * atr)
  ordered = sorted(
    candidates,
    key=lambda item: (
      _band_distance(price, item[0].lo, item[0].hi),
      -item[0].score,
    ),
  )
  reach_limit = MAP_REACTION_REACH_ATR * atr
  reachable = [
    item for item in ordered
    if _band_distance(price, item[0].lo, item[0].hi) <= reach_limit
  ]
  counts["distance"] = len(ordered) - len(reachable)
  if not reachable:
    nearest, nearest_direction, _ = ordered[0]
    distance = _band_distance(price, nearest.lo, nearest.hi)
    divergence = _render_divergence(nearest, rendered_map)
    return _ReactionSelection(
      None,
      "waiting_for_touch",
      (
        f"no mapped {nearest_direction} zone within reach "
        f"(nearest {nearest.lo:.2f}-{nearest.hi:.2f} at {distance:.1f} price, "
        f"limit {MAP_REACTION_REACH_ATR:.1f}×ATR = {reach_limit:.1f})"
        f"{divergence}{_filter_summary(counts)}",
      ),
      len(market_map.entries),
      tuple(actionable),
      _filter_counts(**counts),
    )

  for entry, candidate_direction, counter_bias in reachable:
    if not _touches(m1.iloc[-1], entry, tolerance):
      continue
    if not _rejects(m1.iloc[-1], candidate_direction, atr):
      return _ReactionSelection(
        None,
        "waiting_for_reaction",
        (
          f"price touched mapped {candidate_direction} zone "
          f"{entry.lo:.2f}-{entry.hi:.2f}; waiting for M1 rejection"
          f"{_filter_summary(counts)}",
        ),
        len(market_map.entries),
        tuple(actionable),
        _filter_counts(**counts),
      )
    # The executable band includes the rejection close. Waiting for M1 to
    # confirm necessarily means entry happens after price leaves the raw HTF
    # zone; the structure stop remains anchored beyond the mapped zone.
    entry_low = float(min(entry.lo - tolerance, price))
    entry_high = float(max(entry.hi + tolerance, price))
    return _ReactionSelection(
      (entry, candidate_direction, entry_low, entry_high, counter_bias),
      "candidate",
      (),
      len(market_map.entries),
      tuple(actionable),
      _filter_counts(**counts),
    )

  nearest, nearest_direction, _ = reachable[0]
  divergence = _render_divergence(nearest, rendered_map)
  return _ReactionSelection(
    None,
    "waiting_for_touch",
    (
      f"nearest mapped {nearest_direction} zone "
      f"{nearest.lo:.2f}-{nearest.hi:.2f}; waiting for M1 touch"
      f"{divergence}{_filter_summary(counts)}",
    ),
    len(market_map.entries),
    tuple(actionable),
    _filter_counts(**counts),
  )


def _semantic_actionable(entry: MapEntry) -> bool:
  tags = {tag.lower() for tag in entry.tags}
  return entry.tier in {"zone", "major"} and bool(tags & _ACTIONABLE_TAGS)


def _actionable(entry: MapEntry, atr: float, cfg: Any = None) -> bool:
  return (
    _semantic_actionable(entry)
    and _width_is_actionable(entry, atr, cfg, warn=True)
  )


def _width_is_actionable(
  entry: MapEntry,
  atr: float,
  cfg: Any,
  *,
  warn: bool,
) -> bool:
  atr_multiple = max(
    0.0,
    float(getattr(
      cfg,
      "auto_trade_map_zone_min_width_atr",
      MAP_ZONE_MIN_WIDTH_ATR,
    )),
  )
  absolute_minimum = max(
    0.0,
    float(getattr(
      cfg,
      "auto_trade_map_zone_min_width_abs",
      MAP_ZONE_MIN_WIDTH_ABS,
    )),
  )
  minimum = max(
    atr_multiple * atr,
    absolute_minimum,
  )
  width = float(entry.hi - entry.lo)
  if width + 1e-9 >= minimum:
    return True
  if warn:
    log.warning(
      "Market Map strategy rejected degenerate zone "
      "lo=%.5f hi=%.5f tier=%s tags=%s score=%.2f width=%.5f minimum=%.5f",
      entry.lo,
      entry.hi,
      entry.tier,
      entry.tags,
      entry.score,
      width,
      minimum,
    )
  return False


def _counter_bias_quality(
  entry: MapEntry,
  market_map: MarketMap,
  atr: float,
  cfg: Any,
) -> bool:
  tags = {tag.casefold() for tag in entry.tags}
  if "fresh" not in tags:
    return False
  minimum_score = max(
    0.0,
    float(getattr(
      cfg,
      "auto_trade_map_counter_bias_min_score",
      MAP_COUNTER_BIAS_MIN_SCORE,
    )),
  )
  if entry.score < minimum_score:
    return False
  minimum_confluence = max(
    1,
    int(getattr(
      cfg,
      "auto_trade_map_counter_bias_min_confluence",
      MAP_COUNTER_BIAS_MIN_CONFLUENCE,
    )),
  )
  structural_count = len(tags & _STRUCTURAL_TAGS)
  return (
    structural_count >= minimum_confluence
    or _has_nearby_trendline_level(entry, market_map, atr)
  )


def _has_nearby_trendline_level(
  zone: MapEntry,
  market_map: MarketMap,
  atr: float,
) -> bool:
  prefix = "tl support" if zone.side == "buy" else "tl resistance"
  for entry in market_map.entries:
    if entry is zone or entry.tier != "level" or entry.side != zone.side:
      continue
    if not any(tag.casefold().startswith(prefix) for tag in entry.tags):
      continue
    if _bands_distance(zone.lo, zone.hi, entry.lo, entry.hi) <= 0.5 * atr:
      return True
  return False


def _bands_distance(
  first_low: float,
  first_high: float,
  second_low: float,
  second_high: float,
) -> float:
  if first_low <= second_high and second_low <= first_high:
    return 0.0
  return second_low - first_high if first_high < second_low else first_low - second_high


def _filter_counts(
  *,
  side: int = 0,
  actionable: int = 0,
  degenerate_width: int = 0,
  distance: int = 0,
) -> tuple[tuple[str, int], ...]:
  return (
    ("side", int(side)),
    ("actionable", int(actionable)),
    ("degenerate_width", int(degenerate_width)),
    ("distance", int(distance)),
  )


def _filter_summary(counts: dict[str, int]) -> str:
  return (
    " · filters: "
    f"side={counts['side']}, actionable={counts['actionable']}, "
    f"degenerate_width={counts['degenerate_width']}, "
    f"distance={counts['distance']}"
  )


def _render_divergence(
  entry: MapEntry,
  rendered_map: MarketMap | None,
) -> str:
  if rendered_map is None:
    return ""
  present = any(
    candidate.side == entry.side
    and math.isclose(candidate.lo, entry.lo, abs_tol=1e-6)
    and math.isclose(candidate.hi, entry.hi, abs_tol=1e-6)
    for candidate in rendered_map.entries
  )
  return "" if present else " · ⚠ nearest band absent from rendered Market Map"


def _touches(row: pd.Series, entry: MapEntry, tolerance: float) -> bool:
  return (
    float(row["low"]) <= entry.hi + tolerance
    and float(row["high"]) >= entry.lo - tolerance
  )


def _rejects(row: pd.Series, direction: str, atr: float) -> bool:
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  candle_range = high - low
  if candle_range <= 0:
    return False
  minimum = max(0.15 * atr, 0.2 * candle_range)
  if direction == "SELL":
    return high - close >= minimum and close <= low + 0.6 * candle_range
  return close - low >= minimum and close >= high - 0.6 * candle_range


def _confluence(entry: MapEntry, market_map: MarketMap) -> int:
  tags = {tag.lower() for tag in entry.tags}
  score = 2  # structural map zone + M1 rejection
  if entry.tier == "major" or "fresh" in tags:
    score += 1
  if market_map.bias in {"up", "down"}:
    score += 1
  return min(3, score)


def _targets(cfg: Any) -> tuple[int, ...]:
  values = {
    int(item.strip())
    for item in str(getattr(cfg, "auto_trade_tp_pips", "")).split(",")
    if item.strip().isdigit() and int(item.strip()) > 0
  }
  return tuple(sorted(values))


def _counter_bias_targets(
  cfg: Any,
  symbol: str,
  direction: str,
  price: float,
  eq: float | None,
) -> tuple[int, ...]:
  if eq is None or not math.isfinite(eq):
    return ()
  room = eq - price if direction == "BUY" else price - eq
  cap = int(math.floor(room / units.pip_size(symbol) + 1e-9))
  if cap <= 0:
    return ()
  configured = _targets(cfg)
  if not configured:
    return ()
  return tuple(sorted({min(target, cap) for target in configured}))


def _last_positive(values: pd.Series) -> float | None:
  clean = values.dropna()
  if clean.empty:
    return None
  value = float(clean.iloc[-1])
  return value if math.isfinite(value) and value > 0 else None


def _band_distance(price: float, low: float, high: float) -> float:
  if low <= price <= high:
    return 0.0
  return low - price if price < low else price - high


def market_map_key(symbol: str) -> str:
  return f"{MARKET_MAP_KEY_PREFIX}:{symbol.upper()}"


def market_map_display_key(symbol: str) -> str:
  return f"{MARKET_MAP_DISPLAY_KEY_PREFIX}:{symbol.upper()}"


def market_map_actionable_key(symbol: str) -> str:
  return f"{MARKET_MAP_ACTIONABLE_KEY_PREFIX}:{symbol.upper()}"


def decode_market_map(raw: object) -> MarketMap | None:
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    return market_map_from_payload(text)
  except (KeyError, TypeError, ValueError):
    return None
