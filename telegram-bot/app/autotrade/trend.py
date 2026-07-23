"""Trend-pullback / breakout-continuation gate for auto-trade.

This module is the trend-regime counterpart to ``app.autotrade.gate``'s
box-scalp gate. Where ``gate.py`` is deliberately OHLC-only and independent
of the scanner/detector stack, this module *does* reuse the shared
price-action primitives in ``app.analysis`` (swings, structure, zones,
session liquidity, trendlines, the engine's HTF-bias check) - it just never
imports the scanner, the detector functions themselves, or Market Map. See
``app.autotrade.worker`` for the private strategy selector, which resolves an
overlap by match confluence rather than treating regime as a global veto.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from app.analysis.engine import analyze, directional_trend_override
from app.analysis.math_utils import atr_series
from app.analysis.regime import displacement_grade
from app.analysis.session_liquidity import previous_week_levels, session_levels
from app.analysis.structure import market_structure, structure_breaks
from app.analysis.swings import find_swings
from app.analysis.zones import displacement, supply_demand
from app.autotrade import units
from app.autotrade.gate import AutoScalpBox, AutoScalpDecision


# Mirrors gate.py's BOX_LOOKBACK so the "is this a trending range" height
# check is comparable to the box-scalp gate's own consolidation window.
BOX_LOOKBACK_FOR_HEIGHT = 60
# Mirrors gate.py's BOX_BREAK_BUFFER_ATR. Duplicated intentionally: this
# module cannot import gate.py's private helpers, only its public
# dataclasses, so the breakout-recency scan below reimplements the same
# "beyond the edge by a buffer" idea independently.
_BREAK_BUFFER_ATR = 0.12
# Wick-fraction floor for a trend-direction rejection candle at a pullback
# zone. Deliberately a bit stricter than gate.py's BOX_MIN_WICK_FRACTION
# (0.15) since a trend pullback entry has no opposite rail to lean on.
_REJECTION_WICK_FRACTION = 0.3
_BREAKOUT_RETEST_TOUCH_ATR = 0.2
_TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900}
# Fixed fallback ladder, mirrors AUTO_TRADE_TP_PIPS's default value on the
# C# side (30,60,90,120,200) so a trend candidate never ships with an empty
# target list.
_FALLBACK_TP_PIPS = (30, 60, 90, 120, 200)
_EPS = 1e-9


@dataclass(frozen=True)
class RegimeInfo:
  state: str                    # "chop" | "trend" | "breakout"
  direction: str | None         # "up" | "down" | None (price-action vocabulary)
  bos_count: int
  atr_ratio: float
  htf_aligned: bool
  box_break_age_bars: int | None
  reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrendDecision:
  # "candidate" | "no_setup" | "waiting_retest" | "retest_rejected"
  # | "data_gap" | "target_blocked" | "missing_frames"
  # | "insufficient_history" | "invalid_atr" | "invalid_spot"
  state: str
  direction: str | None = None  # "BUY" | "SELL"
  mode: str | None = None       # "pullback" | "breakout_continuation" | "box_breakout"
  entry_zone: tuple[float, float] | None = None
  key_level: float | None = None
  atr: float | None = None
  structure_swing: float | None = None
  target_prices: tuple[float, ...] = ()
  targets_pips: tuple[int, ...] = ()
  confluence: int = 0
  reasons: tuple[str, ...] = ()


def classify_regime(
  frames: dict[str, pd.DataFrame],
  box_decision: AutoScalpDecision,
  cfg: Any,
) -> RegimeInfo:
  """Classify the current M1 regime as chop, trend, or breakout.

  Evaluation order: a fresh, accepted box break wins outright (breakout),
  then a structurally-confirmed, HTF-aligned, ATR-expanding trend, else
  chop. See module docstring for why this stays independent of the
  scanner/detector stack while still reusing analysis primitives.
  """
  m1_raw = frames.get("M1")
  if m1_raw is None or m1_raw.empty:
    return RegimeInfo("chop", None, 0, 0.0, False, None, ("missing M1 frame",))
  m1 = _clean(m1_raw)
  if len(m1) < BOX_LOOKBACK_FOR_HEIGHT + 1:
    return RegimeInfo("chop", None, 0, 0.0, False, None, ("insufficient M1 history",))

  atr_length = max(2, int(getattr(cfg, "atr_length", 14)))
  atr_series_full = atr_series(m1, atr_length)
  atr = float(atr_series_full.iloc[-1])
  if not math.isfinite(atr) or atr <= _EPS:
    return RegimeInfo("chop", None, 0, 0.0, False, None, ("invalid atr",))

  breakout = _classify_breakout(m1, box_decision, atr, atr_series_full, cfg)
  if breakout is not None:
    return breakout

  swings = find_swings(
    m1,
    max(1, int(getattr(cfg, "swing_fractal_n", 2))),
    max(0.0, float(getattr(cfg, "zigzag_pct", 0.0))),
    max(0.0, float(getattr(cfg, "zigzag_atr_mult", 1.0))),
    atr_series_full,
  )
  structure = market_structure(swings)
  atr_ratio = _atr_ratio(atr_series_full, atr, cfg)
  if structure not in ("up", "down"):
    return _maybe_directional_trend(
      m1,
      swings,
      atr,
      structure,
      0,
      atr_ratio,
      False,
      (f"structure is {structure}",),
      cfg,
    )

  breaks = structure_breaks(swings, m1)
  bos_count = _bos_count_since_choch(breaks)
  min_bos = max(0, int(getattr(cfg, "trend_min_bos", 2)))
  if bos_count < min_bos:
    return _maybe_directional_trend(
      m1,
      swings,
      atr,
      structure,
      bos_count,
      atr_ratio,
      False,
      (f"bos_count {bos_count} < required {min_bos}",),
      cfg,
    )

  window = m1.tail(BOX_LOOKBACK_FOR_HEIGHT)
  height = float(window["high"].max() - window["low"].min())
  min_height_atr = max(0.0, float(getattr(cfg, "trend_min_height_atr", 3.0)))
  if height < min_height_atr * atr:
    return _maybe_directional_trend(
      m1,
      swings,
      atr,
      structure,
      bos_count,
      atr_ratio,
      False,
      (f"range height {height:.2f} < {min_height_atr:.2f}x ATR",),
      cfg,
    )

  m15 = frames.get("M15")
  htf_bias = "range"
  if m15 is not None and not m15.empty:
    htf_bias = analyze({"M15": m15}, htf_order=["M15"]).htf_bias
  htf_aligned = htf_bias == structure or htf_bias == "range"
  if not htf_aligned:
    return _maybe_directional_trend(
      m1,
      swings,
      atr,
      structure,
      bos_count,
      atr_ratio,
      False,
      (f"HTF bias {htf_bias} opposes {structure}",),
      cfg,
    )

  expansion_needed = max(0.0, float(getattr(cfg, "trend_atr_expansion", 1.15)))
  if atr_ratio < expansion_needed:
    return _maybe_directional_trend(
      m1,
      swings,
      atr,
      structure,
      bos_count,
      atr_ratio,
      True,
      (
        f"atr_ratio {atr_ratio:.2f} < required {expansion_needed:.2f}",
      ),
      cfg,
    )

  return RegimeInfo(
    "trend", structure, bos_count, atr_ratio, True, None,
    (
      f"bos_count={bos_count}",
      f"range height {height:.1f} >= {min_height_atr:.2f}x ATR",
      f"htf_bias={htf_bias}",
      f"atr_ratio={atr_ratio:.2f}",
    ),
  )


def _maybe_directional_trend(
  m1: pd.DataFrame,
  swings: list,
  atr: float,
  structure: str | None,
  bos_count: int,
  atr_ratio: float,
  htf_aligned: bool,
  chop_reasons: tuple[str, ...],
  cfg: Any,
) -> RegimeInfo:
  """Apply the engine directional override when the private flag is on.

  Breakout classification is untouched; this only rescues chop→trend when
  successive LH/LL or HH/HL pairs show a directed market that the BOS/height
  path still called chop.
  """
  if not bool(getattr(cfg, "auto_trade_regime_direction_enabled", False)):
    return RegimeInfo(
      "chop", structure, bos_count, atr_ratio, htf_aligned, None, chop_reasons,
    )
  override = directional_trend_override(
    m1,
    swings,
    atr,
    lookback=int(getattr(cfg, "auto_trade_regime_direction_lookback", 120)),
    min_directional_swings=int(
      getattr(cfg, "auto_trade_regime_min_directional_swings", 3)
    ),
    min_displacement_atr=float(
      getattr(cfg, "auto_trade_regime_min_displacement_atr", 4.0)
    ),
  )
  if override is None:
    return RegimeInfo(
      "chop", structure, bos_count, atr_ratio, htf_aligned, None, chop_reasons,
    )
  pair_count, label, net_displacement, lookback = override
  direction = "down" if net_displacement < 0 else "up"
  return RegimeInfo(
    "trend",
    direction,
    bos_count,
    atr_ratio,
    True,
    None,
    (
      (
        f"trend (directional override): {pair_count} consecutive {label}, "
        f"net {net_displacement:.1f} ATR over {lookback} bars"
      ),
      f"  [{chop_reasons[0]} would have said chop]",
    ),
  )


def evaluate_trend_gate(
  frames: dict[str, pd.DataFrame],
  regime: RegimeInfo,
  box_decision: AutoScalpDecision,
  *,
  symbol: str,
  spot_price: float | None = None,
  cfg: Any,
) -> TrendDecision:
  """Route to box-breakout, pullback (Mode A), or breakout-continuation
  (Mode B) depending on ``regime.state``. Returns ``"no_setup"`` if the
  regime doesn't support a trend-family setup right now.
  """
  if regime.state not in ("trend", "breakout"):
    return TrendDecision("no_setup", reasons=(f"regime is {regime.state}",))
  m1_raw = frames.get("M1")
  if m1_raw is None or m1_raw.empty:
    return TrendDecision("missing_frames", reasons=("missing M1 frame",))
  m1 = _clean(m1_raw)
  if len(m1) < BOX_LOOKBACK_FOR_HEIGHT + 1:
    return TrendDecision(
      "insufficient_history",
      reasons=(f"insufficient M1 history: {len(m1)} bars",),
    )
  atr_length = max(2, int(getattr(cfg, "atr_length", 14)))
  atr_series_full = atr_series(m1, atr_length)
  atr = float(atr_series_full.iloc[-1])
  if not math.isfinite(atr) or atr <= _EPS:
    return TrendDecision("invalid_atr", reasons=(f"invalid atr: {atr!r}",))
  close = float(m1["close"].iloc[-1])
  live_price = close if spot_price is None else float(spot_price)
  if not math.isfinite(live_price) or live_price <= 0:
    return TrendDecision(
      "invalid_spot", reasons=(f"invalid spot price: {spot_price!r}",),
    )
  pip_size = units.pip_size(symbol)

  if regime.state == "breakout":
    return _evaluate_box_breakout(
      m1, box_decision, regime, atr, pip_size, live_price, frames, cfg,
    )

  direction_pa = regime.direction
  if direction_pa not in ("up", "down"):
    return TrendDecision("no_setup", reasons=("trend regime missing direction",))

  mode_a = _evaluate_mode_a(
    m1, atr_series_full, atr, direction_pa, pip_size, frames, cfg,
  )
  if mode_a.state == "candidate":
    return mode_a
  mode_b = _evaluate_mode_b(
    m1, atr_series_full, atr, direction_pa, pip_size, live_price, frames, cfg,
  )
  if mode_b.state == "candidate":
    return mode_b
  return TrendDecision("no_setup", reasons=(*mode_a.reasons, *mode_b.reasons))


def build_trend_targets(
  direction: str,
  entry: float,
  atr: float,
  m1: pd.DataFrame,
  frames: dict[str, pd.DataFrame],
  cfg: Any,
  *,
  leg_size: float | None = None,
  stop_distance: float | None = None,
) -> list[float]:
  """Build a level-anchored target ladder ahead of ``entry``.

  ``leg_size``/``stop_distance`` are additive keyword-only extensions
  beyond the literal spec signature - callers already know the impulse
  leg size (Mode A/B) or box width (box-breakout) and the raw stop
  distance, and recomputing either from scratch here would either be
  impossible (box width) or a redundant duplicate computation.
  """
  up = direction == "BUY"

  def _ahead(price: float) -> bool:
    if not math.isfinite(price):
      return False
    if stop_distance is not None and abs(price - entry) < stop_distance:
      return False
    return price > entry if up else price < entry

  major_candidates: list[float] = []
  major_candidates.extend(item.price for item in session_levels(m1, cfg))
  major_candidates.extend(item.price for item in previous_week_levels(m1))

  legs = displacement(
    m1,
    atr_series(m1, max(2, int(getattr(cfg, "atr_length", 14)))),
    max(0.1, float(getattr(cfg, "displacement_atr_mult", 1.5))),
    max(0.0, float(getattr(cfg, "momentum_body_frac", 0.6))),
  )
  zone_candidates: list[float] = []
  zones = supply_demand(m1, legs) if legs else []
  opposing_side = "supply" if up else "demand"
  for zone in zones:
    if zone.side == opposing_side:
      zone_candidates.append(zone.low if up else zone.high)

  min_spacing = max(0.0, float(getattr(cfg, "tp_min_spacing_atr", 0.5))) * atr

  def _add(price: float, bucket: list[float]) -> bool:
    if not _ahead(price):
      return False
    if bucket and abs(price - bucket[-1]) < min_spacing:
      return False
    bucket.append(price)
    return True

  level_pool = sorted(
    (price for price in (*major_candidates, *zone_candidates) if _ahead(price)),
    key=lambda price: abs(price - entry),
  )
  selected: list[float] = []
  for price in level_pool:
    _add(price, selected)
    if len(selected) >= 2:
      break

  if leg_size is None:
    pa_direction = "up" if up else "down"
    matching = [leg for leg in legs if leg.direction == pa_direction]
    leg_size = matching[-1].size if matching else None
  if leg_size is not None and leg_size > _EPS:
    measured_move = entry + leg_size if up else entry - leg_size
    _add(measured_move, selected)

  # TP4 must be strictly beyond whatever the last surviving target is (not
  # just "not too close to it") - otherwise a nearer major level already
  # used for TP1/TP2 could re-qualify here purely on spacing and produce a
  # nonsensical TP4 < TP3.
  if selected:
    last_price = selected[-1]
    beyond_last = [
      price for price in major_candidates
      if (price > last_price if up else price < last_price)
    ]
    major_only_ahead = sorted(beyond_last, key=lambda price: abs(price - entry))
    for price in major_only_ahead:
      if _add(price, selected):
        break

  return selected


def _classify_breakout(
  m1: pd.DataFrame,
  box_decision: AutoScalpDecision,
  atr: float,
  atr_series_full: pd.Series,
  cfg: Any,
) -> RegimeInfo | None:
  if box_decision.state != "box_broken" or box_decision.box is None:
    return None
  max_age = max(1, int(getattr(cfg, "trend_breakout_max_age_bars", 5)))
  direction_pa, age = _breakout_direction_and_age(
    m1, box_decision.box, atr, max_age,
  )
  if direction_pa is None or age is None or age >= max_age:
    return None
  accept_bars = max(1, int(getattr(cfg, "trend_breakout_accept_bars", 2)))
  consecutive = age + 1
  last_row = m1.iloc[-1]
  accepted_by_displacement = displacement_grade(last_row, atr, direction_pa)
  accepted_by_closes = consecutive >= accept_bars
  if not (accepted_by_displacement or accepted_by_closes):
    return None
  reasons = (
    "box break accepted by "
    + ("displacement" if accepted_by_displacement else f"{consecutive} closes"),
    f"break age {age} bars",
  )
  return RegimeInfo(
    "breakout",
    direction_pa,
    0,
    _atr_ratio(atr_series_full, atr, cfg),
    True,
    age,
    reasons,
  )


def _breakout_direction_and_age(
  m1: pd.DataFrame,
  box: AutoScalpBox,
  atr: float,
  max_age_bars: int,
) -> tuple[str | None, int | None]:
  pip_size = units.pip_size("XAU")
  buffer = max(3 * pip_size, _BREAK_BUFFER_ATR * atr)
  lower_break = box.lower.low - buffer
  upper_break = box.upper.high + buffer
  tail = m1["close"].astype(float).tail(max_age_bars + 1)
  if tail.empty:
    return None, None
  last_close = float(tail.iloc[-1])
  if last_close > upper_break:
    direction = "up"
  elif last_close < lower_break:
    direction = "down"
  else:
    return None, None
  values = tail.to_numpy()
  n = len(values)
  age = 0
  for index in range(n - 1, -1, -1):
    beyond = (
      values[index] > upper_break
      if direction == "up"
      else values[index] < lower_break
    )
    if not beyond:
      break
    age = n - 1 - index
  return direction, age


def _evaluate_box_breakout(
  m1: pd.DataFrame,
  box_decision: AutoScalpDecision,
  regime: RegimeInfo,
  atr: float,
  pip_size: float,
  live_price: float,
  frames: dict[str, pd.DataFrame],
  cfg: Any,
) -> TrendDecision:
  box = box_decision.box
  if box is None or regime.direction not in ("up", "down"):
    return TrendDecision("no_setup", reasons=("breakout: no box context",))
  direction = "BUY" if regime.direction == "up" else "SELL"
  stop_rail = box.lower if direction == "BUY" else box.upper
  key_rail = box.upper if direction == "BUY" else box.lower
  key_level = key_rail.level
  structure_swing = stop_rail.level

  break_age = regime.box_break_age_bars
  if break_age is None or break_age < 1:
    return TrendDecision(
      "waiting_retest",
      reasons=("breakout: waiting for a closed M1 retest",),
    )
  if not _m1_breakout_tail_is_contiguous(m1, break_age):
    return TrendDecision(
      "data_gap",
      reasons=("breakout: missing M1 bar inside acceptance sequence",),
    )
  retest_reason = _breakout_retest_rejection_reason(
    m1,
    key_rail,
    direction,
    atr,
    pip_size,
  )
  if retest_reason is not None:
    return TrendDecision("retest_rejected", reasons=(retest_reason,))

  entry_reference = live_price
  band = max(0.05 * atr, pip_size)
  entry_zone = (entry_reference - band, entry_reference + band)
  stop_distance = abs(entry_reference - structure_swing)
  if stop_distance <= _EPS:
    return TrendDecision("no_setup", reasons=("breakout: degenerate stop distance",))

  obstacle = _nearest_prebreak_obstacle(
    direction,
    entry_reference,
    m1,
    frames,
    break_age,
  )
  min_room_pips = max(
    0,
    int(getattr(cfg, "trend_breakout_min_room_pips", 35)),
  )
  if obstacle is not None:
    room_pips = abs(obstacle - entry_reference) / pip_size
    if room_pips + _EPS < min_room_pips:
      return TrendDecision(
        "target_blocked",
        reasons=(
          f"breakout: only {room_pips:.1f} pips room to prior barrier "
          f"{obstacle:.2f} (need {min_room_pips})",
        ),
      )

  targets = build_trend_targets(
    direction,
    entry_reference,
    atr,
    m1,
    frames,
    cfg,
    leg_size=box.width_pips * pip_size,
    stop_distance=stop_distance,
  )
  reasons = [
    f"box breakout {box.box_id} accepted",
    f"break age {regime.box_break_age_bars} bars",
  ]
  if not targets:
    targets = _fixed_fallback_targets(direction, entry_reference, pip_size)
    reasons.append("targets: fixed-fallback")
  if obstacle is not None:
    targets = _merge_breakout_obstacle_target(
      direction,
      entry_reference,
      obstacle,
      targets,
      max(0.0, float(getattr(cfg, "tp_min_spacing_atr", 0.5))) * atr,
    )
    reasons.append(f"prior barrier {obstacle:.2f}")
  targets_pips = tuple(
    round(abs(price - entry_reference) / pip_size) for price in targets
  )
  confluence = 3 if (regime.box_break_age_bars or 0) <= 1 else 2
  return TrendDecision(
    "candidate",
    direction=direction,
    mode="box_breakout",
    entry_zone=entry_zone,
    key_level=key_level,
    atr=atr,
    structure_swing=structure_swing,
    target_prices=tuple(targets),
    targets_pips=targets_pips,
    confluence=confluence,
    reasons=tuple(reasons),
  )


def _m1_breakout_tail_is_contiguous(m1: pd.DataFrame, break_age: int) -> bool:
  count = break_age + 1
  if count < 2:
    return True
  tail = m1.index[-count:]
  if not isinstance(tail, pd.DatetimeIndex) or len(tail) != count:
    return False
  deltas = tail.to_series().diff().dropna().dt.total_seconds()
  return bool((deltas == _TIMEFRAME_SECONDS["M1"]).all())


def _breakout_retest_rejection_reason(
  m1: pd.DataFrame,
  key_rail: AutoScalpRail,
  direction: str,
  atr: float,
  pip_size: float,
) -> str | None:
  row = m1.iloc[-1]
  low = float(row["low"])
  high = float(row["high"])
  close = float(row["close"])
  break_buffer = max(3 * pip_size, _BREAK_BUFFER_ATR * atr)
  touch = max(3 * pip_size, _BREAKOUT_RETEST_TOUCH_ATR * atr)
  if direction == "BUY":
    threshold = key_rail.high + break_buffer
    touched = low <= threshold + touch
    held = close > threshold
    direction_pa = "up"
  else:
    threshold = key_rail.low - break_buffer
    touched = high >= threshold - touch
    held = close < threshold
    direction_pa = "down"
  if not touched:
    return f"breakout: waiting for retest of {key_rail.level:.2f}"
  if not held:
    return f"breakout: retest failed to hold {key_rail.level:.2f}"
  if not _is_rejection(row, direction_pa):
    return "breakout: retest candle lacks directional wick rejection"
  return None


def _nearest_prebreak_obstacle(
  direction: str,
  entry: float,
  m1: pd.DataFrame,
  frames: dict[str, pd.DataFrame],
  break_age: int,
) -> float | None:
  break_position = len(m1) - break_age - 1
  if break_position <= 0:
    return None
  break_time = m1.index[break_position]
  candidates: list[float] = []
  source_frames = {**frames, "M1": m1}
  for timeframe, seconds in _TIMEFRAME_SECONDS.items():
    raw = source_frames.get(timeframe)
    if raw is None or raw.empty:
      continue
    frame = _clean(raw)
    if not isinstance(frame.index, pd.DatetimeIndex):
      continue
    closed_before_break = frame.index + pd.to_timedelta(seconds, unit="s")
    history = frame.loc[closed_before_break <= break_time].tail(60)
    if len(history) < 5:
      continue
    column = "high" if direction == "BUY" else "low"
    values = history[column].astype(float).to_numpy()
    for index in range(2, len(values) - 2):
      center = float(values[index])
      left = values[index - 2:index]
      right = values[index + 1:index + 3]
      is_fractal = (
        center >= float(left.max()) and center > float(right.max())
        if direction == "BUY"
        else center <= float(left.min()) and center < float(right.min())
      )
      if is_fractal and (
        center > entry if direction == "BUY" else center < entry
      ):
        candidates.append(center)
  if not candidates:
    return None
  return min(candidates) if direction == "BUY" else max(candidates)


def _merge_breakout_obstacle_target(
  direction: str,
  entry: float,
  obstacle: float,
  targets: list[float],
  min_spacing: float,
) -> list[float]:
  limit = max(1, len(targets))
  ahead = [
    price for price in (obstacle, *targets)
    if (price > entry if direction == "BUY" else price < entry)
  ]
  ordered = sorted(ahead, key=lambda price: abs(price - entry))
  merged: list[float] = []
  for price in ordered:
    if merged and abs(price - merged[-1]) < min_spacing:
      continue
    merged.append(price)
  return merged[:limit]


def _evaluate_mode_a(
  m1: pd.DataFrame,
  atr_series_full: pd.Series,
  atr: float,
  direction_pa: str,
  pip_size: float,
  frames: dict[str, pd.DataFrame],
  cfg: Any,
) -> TrendDecision:
  legs = displacement(
    m1,
    atr_series_full,
    max(0.1, float(getattr(cfg, "displacement_atr_mult", 1.5))),
    max(0.0, float(getattr(cfg, "momentum_body_frac", 0.6))),
  )
  matching_legs = [leg for leg in legs if leg.direction == direction_pa]
  if not matching_legs:
    return TrendDecision(
      "no_setup", reasons=("mode a: no matching displacement leg",),
    )
  leg = matching_legs[-1]
  zones = supply_demand(m1, [leg])
  if not zones:
    return TrendDecision("no_setup", reasons=("mode a: no origin zone",))
  zone = zones[0]

  last = m1.iloc[-1]
  low = float(last["low"])
  high = float(last["high"])
  overlaps_zone = low <= zone.high and high >= zone.low
  if not overlaps_zone:
    return TrendDecision(
      "no_setup", reasons=("mode a: price has not returned to origin zone",),
    )
  if not _is_rejection(last, direction_pa):
    return TrendDecision(
      "no_setup", reasons=("mode a: no rejection candle at origin zone",),
    )

  direction = "BUY" if direction_pa == "up" else "SELL"
  entry_reference = (zone.low + zone.high) / 2
  origin_index = int(leg.start)
  origin_row = m1.iloc[origin_index] if 0 <= origin_index < len(m1) else last
  structure_swing = float(
    origin_row["low"] if direction == "BUY" else origin_row["high"]
  )
  stop_distance = abs(entry_reference - structure_swing)
  if stop_distance <= _EPS:
    return TrendDecision("no_setup", reasons=("mode a: degenerate stop distance",))

  targets = build_trend_targets(
    direction,
    entry_reference,
    atr,
    m1,
    frames,
    cfg,
    leg_size=leg.size,
    stop_distance=stop_distance,
  )
  reasons = [
    "trend pullback into displacement origin zone",
    f"rejection candle at {zone.low:.2f}-{zone.high:.2f}",
  ]
  if not targets:
    targets = _fixed_fallback_targets(direction, entry_reference, pip_size)
    reasons.append("targets: fixed-fallback")
  targets_pips = tuple(
    round(abs(price - entry_reference) / pip_size) for price in targets
  )
  confluence = 3 if zone.touches > 0 else 2
  return TrendDecision(
    "candidate",
    direction=direction,
    mode="pullback",
    entry_zone=(min(zone.low, zone.high), max(zone.low, zone.high)),
    key_level=entry_reference,
    atr=atr,
    structure_swing=structure_swing,
    target_prices=tuple(targets),
    targets_pips=targets_pips,
    confluence=confluence,
    reasons=tuple(reasons),
  )


def _evaluate_mode_b(
  m1: pd.DataFrame,
  atr_series_full: pd.Series,
  atr: float,
  direction_pa: str,
  pip_size: float,
  live_price: float,
  frames: dict[str, pd.DataFrame],
  cfg: Any,
) -> TrendDecision:
  swings = find_swings(
    m1,
    max(1, int(getattr(cfg, "swing_fractal_n", 2))),
    max(0.0, float(getattr(cfg, "zigzag_pct", 0.0))),
    max(0.0, float(getattr(cfg, "zigzag_atr_mult", 1.0))),
    atr_series_full,
  )
  broken_kind = "high" if direction_pa == "up" else "low"
  broken_candidates = [item for item in swings if item.kind == broken_kind]
  if not broken_candidates:
    return TrendDecision(
      "no_setup", reasons=("mode b: no swing extreme to break",),
    )
  broken_swing = broken_candidates[-1]
  level = float(broken_swing.price)

  last = m1.iloc[-1]
  close = float(last["close"])
  crossed = close > level if direction_pa == "up" else close < level
  if not crossed:
    return TrendDecision("no_setup", reasons=("mode b: level not broken yet",))
  if not displacement_grade(last, atr, direction_pa):
    return TrendDecision(
      "no_setup", reasons=("mode b: break lacks displacement acceptance",),
    )

  base_kind = "low" if direction_pa == "up" else "high"
  base_candidates = [
    item for item in swings
    if item.kind == base_kind and int(item.index) < int(broken_swing.index)
  ]
  if not base_candidates:
    return TrendDecision(
      "no_setup", reasons=("mode b: no base swing before impulse",),
    )
  base_swing = base_candidates[-1]
  structure_swing = float(base_swing.price)

  reaction_window = max(0.0, float(getattr(cfg, "reaction_max_atr", 0.5))) * atr
  pulled_back = abs(live_price - level) <= reaction_window
  if not pulled_back and not bool(getattr(cfg, "trend_allow_chase", True)):
    return TrendDecision(
      "no_setup", reasons=("mode b: no pullback yet and chase disabled",),
    )

  direction = "BUY" if direction_pa == "up" else "SELL"
  entry_reference = level if pulled_back else close
  band = max(reaction_window, pip_size)
  entry_zone = (entry_reference - band, entry_reference + band)

  opposing_levels = _opposing_levels(m1, cfg)
  opposing = _nearest_opposing_level(direction_pa, entry_reference, opposing_levels)
  level_buffer = max(0.0, float(getattr(cfg, "trend_level_buffer_atr", 1.0))) * atr
  if opposing is not None and abs(opposing - entry_reference) <= level_buffer:
    return TrendDecision(
      "no_setup",
      reasons=(f"mode b: opposing major level {opposing:.2f} inside buffer",),
    )

  stop_distance = abs(entry_reference - structure_swing)
  if stop_distance <= _EPS:
    return TrendDecision("no_setup", reasons=("mode b: degenerate stop distance",))

  leg_size = abs(level - structure_swing)
  targets = build_trend_targets(
    direction,
    entry_reference,
    atr,
    m1,
    frames,
    cfg,
    leg_size=leg_size,
    stop_distance=stop_distance,
  )
  reasons = [
    f"displacement break of {broken_kind} swing {level:.2f}",
    "pullback entry" if pulled_back else "chase entry (no pullback yet)",
  ]
  if not targets:
    targets = _fixed_fallback_targets(direction, entry_reference, pip_size)
    reasons.append("targets: fixed-fallback")
  targets_pips = tuple(
    round(abs(price - entry_reference) / pip_size) for price in targets
  )
  confluence = 3 if pulled_back else 2
  return TrendDecision(
    "candidate",
    direction=direction,
    mode="breakout_continuation",
    entry_zone=entry_zone,
    key_level=level,
    atr=atr,
    structure_swing=structure_swing,
    target_prices=tuple(targets),
    targets_pips=targets_pips,
    confluence=confluence,
    reasons=tuple(reasons),
  )


def _is_rejection(bar: pd.Series, direction_pa: str) -> bool:
  open_ = float(bar["open"])
  high = float(bar["high"])
  low = float(bar["low"])
  close = float(bar["close"])
  span = high - low
  if span <= _EPS:
    return False
  if direction_pa == "up":
    lower_wick = max(0.0, min(open_, close) - low) / span
    return close > open_ and lower_wick >= _REJECTION_WICK_FRACTION
  upper_wick = max(0.0, high - max(open_, close)) / span
  return close < open_ and upper_wick >= _REJECTION_WICK_FRACTION


def _opposing_levels(m1: pd.DataFrame, cfg: Any) -> list[float]:
  levels: list[float] = []
  levels.extend(item.price for item in session_levels(m1, cfg))
  levels.extend(item.price for item in previous_week_levels(m1))
  return levels


def _nearest_opposing_level(
  direction_pa: str,
  reference: float,
  levels: list[float],
) -> float | None:
  candidates = [
    price for price in levels
    if math.isfinite(price)
    and (price > reference if direction_pa == "up" else price < reference)
  ]
  if not candidates:
    return None
  return min(candidates, key=lambda price: abs(price - reference))


def _fixed_fallback_targets(
  direction: str,
  entry: float,
  pip_size: float,
) -> list[float]:
  sign = 1.0 if direction == "BUY" else -1.0
  return [entry + sign * pips * pip_size for pips in _FALLBACK_TP_PIPS]


def _bos_count_since_choch(breaks: list) -> int:
  count = 0
  for item in reversed(breaks):
    if item.kind == "CHoCH":
      break
    if item.kind == "BOS":
      count += 1
  return count


def _atr_ratio(atr_series_full: pd.Series, atr: float, cfg: Any) -> float:
  baseline_bars = max(1, int(getattr(cfg, "trend_atr_baseline_bars", 48)))
  baseline = float(atr_series_full.tail(baseline_bars).mean())
  if not math.isfinite(baseline) or baseline <= _EPS:
    return 0.0
  return atr / baseline


def _clean(df: pd.DataFrame) -> pd.DataFrame:
  required = ["open", "high", "low", "close"]
  if df.empty or any(column not in df.columns for column in required):
    return pd.DataFrame(columns=required)
  clean = df.copy()
  for column in required:
    clean[column] = pd.to_numeric(clean[column], errors="coerce")
  clean = clean.dropna(subset=required)
  finite = clean[required].map(math.isfinite).all(axis=1)
  return clean.loc[finite]
