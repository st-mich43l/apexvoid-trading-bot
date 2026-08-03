"""Structural levels published by Market Map, and the M1 reaction helpers
shared by other guards.

Market Map is context, not a global gate. `_select_reaction_detailed` reports
the nearest actionable mapped zone (HTF-aligned by default; an opt-in
counter-bias path adds stricter freshness, score, and structural-confluence
rules; display-only fallback levels such as a lone round number are never
executable) but no longer promotes any zone to a trade candidate on its own
- the M1 touch/rejection reaction detector that used to do that is retired
as a setup source (H1->M15->M5 single-analysis-source cutover, P2). M1 is
still read here: `_reaction_in_lookback`/`_touches`/`_rejects` remain, since
`worker.py`'s overlap-thesis guard and `scale_context.py` still use them to
disambiguate an already-formed candidate's direction, which is a distinct
concern from originating a setup.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from app.autotrade.strategy_match import StrategyMatch


# Phase 2I-A: legacy field names the market-map strategy subtree reads
# (evaluate_market_map_strategy + _reaction_in_lookback). When ``cfg`` is
# omitted in production it builds a narrow snapshot of exactly these fields off
# the canonical ``runtime_config`` (replacing the retired
# per-call flat legacy config facade). Tests still inject a flat SimpleNamespace.
_RUNTIME_MAP_STRATEGY_CFG_FIELDS = (
  "auto_trade_mapped_zone_enabled",
  "atr_length",
  "proximal_band_atr",
  "auto_trade_map_counter_bias_enabled",
  "auto_trade_map_track_distance_atr",
  "auto_trade_map_execute_tolerance_pips",
  "auto_trade_map_execute_tolerance_atr",
  "auto_trade_map_reaction_lookback_bars",
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
  track_limit: float | None = None
  execute_limit: float | None = None
  map_id: str | None = None
  touch_bar_ts: str | None = None
  confirmation_bar_ts: str | None = None
  reaction_age_bars: int | None = None
  reaction_type: str | None = None


@dataclass(frozen=True)
class _ReactionHit:
  entry: MapEntry
  direction: str
  entry_low: float
  entry_high: float
  counter_bias: bool
  touch_bar_ts: str | None
  confirmation_bar_ts: str | None
  reaction_age_bars: int
  reaction_type: str


@dataclass(frozen=True)
class _ReactionSelection:
  selected: _ReactionHit | None
  state: str
  reasons: tuple[str, ...]
  entries_seen: int
  actionable_entries: tuple[ActionableMapEntry, ...]
  filter_counts: tuple[tuple[str, int], ...]
  track_limit: float | None = None
  execute_limit: float | None = None


def evaluate_market_map_strategy(
  frames: dict[str, pd.DataFrame],
  *,
  symbol: str,
  event_ts: str,
  spot_price: float | None,
  cfg: Any | None = None,
  market_map: MarketMap | None = None,
  rendered_map: MarketMap | None = None,
  now: int | None = None,
) -> MarketMapStrategyDecision:
  """Report the nearest actionable mapped zone; never itself promotes one to
  a trade candidate. The M1 touch/rejection reaction detector that used to
  do that is retired as a setup source (H1->M15->M5 single-analysis-source
  cutover, P2) - Market Map stays a structure snapshot for whatever consumes
  it next (the M1 candlestick trigger, P3).
  """
  if cfg is None:
    from app.core.runtime_projection import project_runtime_config
    cfg = project_runtime_config(_RUNTIME_MAP_STRATEGY_CFG_FIELDS)
  if not bool(getattr(cfg, "auto_trade_mapped_zone_enabled", True)):
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
  map_id = str(getattr(market_map, "map_id", "") or "") or None
  if (
    rendered_map is not None
    and map_id
    and getattr(rendered_map, "map_id", None)
    and rendered_map.map_id
    and rendered_map.map_id != map_id
  ):
    log.warning(
      "Market Map strategy/render map_id mismatch strategy=%s render=%s",
      map_id,
      rendered_map.map_id,
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
  # selection.selected is always None now: _select_reaction_detailed no
  # longer has any mechanism to promote a tracked zone to a hit (the M1
  # reaction detector that used to do that is removed - see its docstring).
  # This function therefore only ever reports the nearest tracked/executable
  # zone; it never itself produces a StrategyMatch/candidate.
  return MarketMapStrategyDecision(
    selection.state,
    selection.reasons,
    entries_seen=selection.entries_seen,
    actionable_entries=selection.actionable_entries,
    filter_counts=selection.filter_counts,
    track_limit=selection.track_limit,
    execute_limit=selection.execute_limit,
    map_id=map_id,
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
  selected = None if result.selected is None else (
    result.selected.entry,
    result.selected.direction,
    result.selected.entry_low,
    result.selected.entry_high,
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
  bias_side = (
    "buy" if market_map.bias == "up"
    else "sell" if market_map.bias == "down"
    else None
  )
  direction = (
    "BUY/SELL"
    if bias_side is None
    else "BUY" if bias_side == "buy" else "SELL"
  )
  counter_enabled = bool(
    getattr(
      cfg,
      "auto_trade_allow_counter_bias",
      getattr(cfg, "auto_trade_map_counter_bias_enabled", False),
    )
  )
  counts = {
    "side": 0,
    "actionable": 0,
    "degenerate_width": 0,
    "distance": 0,
  }
  candidates: list[tuple[MapEntry, str, bool]] = []
  # Selection must run over the uncapped structural pool, not the
  # Telegram-display-capped `entries` list - otherwise a genuinely
  # structural zone that lost the per-side display ranking (or a
  # round-number display fallback that never should have been executable)
  # can silently change which zone gets traded. Falls back to `entries`
  # only for a MarketMap deserialized from a pre-actionable_entries cached
  # payload (see market_map_from_payload), never in a freshly built map.
  structural_pool = market_map.actionable_entries or market_map.entries
  for entry in structural_pool:
    counter_bias = bias_side is not None and entry.side != bias_side
    if counter_bias and not counter_enabled:
      counts["side"] += 1
      continue
    if not (
      _semantic_actionable(entry)
      or _counter_bias_quality(entry, market_map, atr, cfg)
    ):
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
  track_limit_atr = float(getattr(cfg, "auto_trade_map_track_distance_atr", 8.0))
  track_limit = track_limit_atr * atr
  execute_limit_atr = float(
    getattr(
      cfg,
      "auto_trade_map_execute_distance_atr",
      MAP_REACTION_REACH_ATR,
    )
  )
  execute_limit = execute_limit_atr * atr
  pip_size = max(
    1e-9,
    float(getattr(cfg, "pip_size", 0.1) or 0.1),
  )
  # Small execution tolerance absorbs bid/ask, ATR recalc, and bar-vs-spot
  # rounding without turning distant tracked zones into market entries.
  exec_tol_pips = max(
    0.0,
    float(getattr(cfg, "auto_trade_map_execute_tolerance_pips", 3.0)),
  )
  exec_tol_atr = max(
    0.0,
    float(getattr(cfg, "auto_trade_map_execute_tolerance_atr", 0.15)),
  )
  execute_tolerance = max(exec_tol_pips * pip_size, exec_tol_atr * atr)
  effective_execute_limit = execute_limit + execute_tolerance

  tracked = [
    item for item in ordered
    if _band_distance(price, item[0].lo, item[0].hi) <= track_limit
  ]
  counts["distance"] = len(ordered) - len(tracked)
  if not tracked:
    nearest, nearest_direction, _ = ordered[0]
    distance = _band_distance(price, nearest.lo, nearest.hi)
    divergence = _render_divergence(nearest, rendered_map)
    return _ReactionSelection(
      None,
      "no_zone_in_range",
      (
        f"no mapped {nearest_direction} zone within track distance "
        f"(nearest {nearest.lo:.2f}-{nearest.hi:.2f} at {distance:.1f} price, "
        f"track limit {track_limit_atr:.1f}×ATR = {track_limit:.1f})"
        f"{divergence}{_filter_summary(counts)}",
      ),
      len(market_map.entries),
      tuple(actionable),
      _filter_counts(**counts),
      track_limit,
      effective_execute_limit,
    )

  executable = [
    item for item in tracked
    if _band_distance(price, item[0].lo, item[0].hi) <= effective_execute_limit
  ]

  if not executable:
    nearest, nearest_direction, _ = tracked[0]
    distance = _band_distance(price, nearest.lo, nearest.hi)
    divergence = _render_divergence(nearest, rendered_map)
    return _ReactionSelection(
      None,
      "waiting_for_touch",
      (
        f"nearest mapped {nearest_direction} zone {nearest.lo:.2f}-{nearest.hi:.2f} "
        f"({distance:.1f} away · tracked, execute within {effective_execute_limit:.1f}"
        f" = base {execute_limit:.1f} + tol {execute_tolerance:.1f})"
        f"{divergence}{_filter_summary(counts)}",
      ),
      len(market_map.entries),
      tuple(actionable),
      _filter_counts(**counts),
      track_limit,
      effective_execute_limit,
    )

  # The M1 touch/rejection reaction detector (and the live-quote zone
  # widening it fed - entry_low/entry_high used to fold `price` into the
  # executable band) is retired as a setup source (H1->M15->M5 single-
  # analysis-source cutover, P2): a mapped zone being within execute range no
  # longer, by itself or via an M1 confirmation, produces a "candidate".
  # Market Map's structural pool stays the execution source of zones for
  # whatever consumes it next (the M1 candlestick trigger, P3) - this
  # function only ever reports the nearest tracked/executable zone now.
  nearest, nearest_direction, _ = executable[0]
  distance = _band_distance(price, nearest.lo, nearest.hi)
  divergence = _render_divergence(nearest, rendered_map)
  return _ReactionSelection(
    None,
    "waiting_for_touch",
    (
      f"nearest mapped {nearest_direction} zone {nearest.lo:.2f}-{nearest.hi:.2f} "
      f"({distance:.1f} away · tracked, execute within {effective_execute_limit:.1f}); "
      f"no entry trigger source configured"
      f"{divergence}{_filter_summary(counts)}",
    ),
    len(market_map.entries),
    tuple(actionable),
    _filter_counts(**counts),
    track_limit,
    effective_execute_limit,
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
  # Same rationale as the selection loop above: a corroborating trendline
  # must not be missed just because Telegram's display cap dropped it.
  for entry in market_map.actionable_entries or market_map.entries:
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
    for candidate in [
      *rendered_map.entries,
      *getattr(rendered_map, "actionable_entries", []),
    ]
  )
  return "" if present else " · ⚠ nearest band absent from rendered Market Map"


@dataclass(frozen=True)
class _LookbackReaction:
  reaction_type: str
  touch_bar_ts: str | None
  confirmation_bar_ts: str | None
  reaction_age_bars: int


def _bar_ts(index_value: object) -> str | None:
  if index_value is None:
    return None
  if hasattr(index_value, "isoformat"):
    try:
      return index_value.isoformat()
    except (TypeError, ValueError):
      return str(index_value)
  return str(index_value)


def _closes_away(
  row: pd.Series,
  entry: MapEntry,
  direction: str,
  tolerance: float,
) -> bool:
  close = float(row["close"])
  if direction == "SELL":
    return close < entry.lo - tolerance
  return close > entry.hi + tolerance


def _reaction_in_lookback(
  m1: pd.DataFrame,
  entry: MapEntry,
  direction: str,
  atr: float,
  tolerance: float,
  cfg: Any,
  price: float,
) -> _LookbackReaction | None:
  lookback = max(
    1,
    int(getattr(cfg, "auto_trade_map_reaction_lookback_bars", 5)),
  )
  window = m1.tail(lookback)
  if window.empty:
    return None
  touch_positions = [
    offset
    for offset in range(len(window))
    if _touches(window.iloc[offset], entry, tolerance)
  ]
  if not touch_positions:
    return None
  # Prefer the most recent touch that still has a later confirmation.
  for touch_pos in reversed(touch_positions):
    touch_row = window.iloc[touch_pos]
    touch_ts = _bar_ts(window.index[touch_pos])
    age = len(window) - 1 - touch_pos
    if age >= lookback:
      continue
    same_bar_reject = _rejects(touch_row, direction, atr)
    if same_bar_reject:
      return _LookbackReaction(
        "rejection",
        touch_ts,
        touch_ts,
        age,
      )
    for confirm_pos in range(touch_pos + 1, len(window)):
      confirm_row = window.iloc[confirm_pos]
      confirm_ts = _bar_ts(window.index[confirm_pos])
      if _rejects(confirm_row, direction, atr):
        return _LookbackReaction(
          "rejection",
          touch_ts,
          confirm_ts,
          age,
        )
      if _closes_away(confirm_row, entry, direction, tolerance):
        reclaim = (
          direction == "BUY"
          and float(confirm_row["close"]) > entry.hi
        ) or (
          direction == "SELL"
          and float(confirm_row["close"]) < entry.lo
        )
        return _LookbackReaction(
          "reclaim" if reclaim else "close_away",
          touch_ts,
          confirm_ts,
          age,
        )
  # Touched inside lookback but never confirmed.
  last_touch = touch_positions[-1]
  return _LookbackReaction(
    "touch_only",
    _bar_ts(window.index[last_touch]),
    None,
    len(window) - 1 - last_touch,
  )


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
