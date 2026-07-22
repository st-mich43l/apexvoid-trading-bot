"""M1 execution strategy for structural levels published by Market Map.

Market Map is context, not a global gate.  This module promotes one mapped
zone to an executable strategy only when the latest M1 candle actually touches
and rejects it in the direction of the higher-timeframe bias.  Display-only
fallback levels (for example a lone round number) are never executable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
_ACTIONABLE_TAGS = {
  "breakout-retest",
  "demand",
  "flip",
  "fresh",
  "fvg",
  "ob",
  "supply",
}


@dataclass(frozen=True)
class MarketMapStrategyDecision:
  state: str
  reasons: tuple[str, ...] = ()
  match: StrategyMatch | None = None
  mapped_zone: tuple[float, float] | None = None


def evaluate_market_map_strategy(
  frames: dict[str, pd.DataFrame],
  *,
  symbol: str,
  event_ts: str,
  spot_price: float | None,
  cfg: Any,
  market_map: MarketMap | None = None,
  now: int | None = None,
) -> MarketMapStrategyDecision:
  """Match an HTF-aligned mapped zone with an immediate M1 rejection."""
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

  selected, state, reasons = _select_reaction(
    market_map,
    m1,
    float(spot_price),
    atr,
    float(getattr(cfg, "proximal_band_atr", 0.5)),
  )
  if selected is None:
    return MarketMapStrategyDecision(state, reasons)
  entry, direction, entry_low, entry_high = selected

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
    )

  issued_at = (
    int(datetime.now(timezone.utc).timestamp())
    if now is None else int(now)
  )
  ttl = max(
    60,
    int(getattr(cfg, "auto_trade_strategy_match_max_age_seconds", 420)),
  )
  targets = _targets(cfg)
  if not targets:
    return MarketMapStrategyDecision(
      "invalid_targets",
      ("ApexVoid Algo has no configured profit targets",),
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
    f"{market_map.bias_tf or 'HTF'} bias {market_map.bias}",
    f"mapped {direction} zone {entry.lo:.2f}-{entry.hi:.2f}",
    *([tag_text] if tag_text else []),
    "M1 touch + rejection",
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
  )
  return MarketMapStrategyDecision(
    "candidate",
    match_reasons,
    match,
    (entry.lo, entry.hi),
  )


def _select_reaction(
  market_map: MarketMap,
  m1: pd.DataFrame,
  price: float,
  atr: float,
  proximal_band_atr: float,
) -> tuple[tuple[MapEntry, str, float, float] | None, str, tuple[str, ...]]:
  direction = "BUY" if market_map.bias == "up" else "SELL" if market_map.bias == "down" else None
  if direction is None:
    return None, "waiting_for_bias", ("Market Map HTF bias is range",)
  side = "buy" if direction == "BUY" else "sell"
  actionable = [
    entry for entry in market_map.entries
    if entry.side == side and _actionable(entry)
  ]
  if not actionable:
    return (
      None,
      "waiting_for_zone",
      (f"no structural mapped {direction} zone aligned with HTF bias",),
    )

  tolerance = max(0.05, max(0.0, proximal_band_atr) * atr)
  ordered = sorted(
    actionable,
    key=lambda entry: (_band_distance(price, entry.lo, entry.hi), -entry.score),
  )
  for entry in ordered:
    if not _touches(m1.iloc[-1], entry, tolerance):
      continue
    if not _rejects(m1.iloc[-1], direction, atr):
      return (
        None,
        "waiting_for_reaction",
        (
          f"price touched mapped {direction} zone "
          f"{entry.lo:.2f}-{entry.hi:.2f}; waiting for M1 rejection",
        ),
      )
    reaction_distance = _band_distance(price, entry.lo, entry.hi)
    if reaction_distance > 1.5 * atr:
      continue
    # The executable band includes the rejection close. Waiting for M1 to
    # confirm necessarily means entry happens after price leaves the raw HTF
    # zone; the structure stop remains anchored beyond the mapped zone.
    entry_low = float(min(entry.lo - tolerance, price))
    entry_high = float(max(entry.hi + tolerance, price))
    return (entry, direction, entry_low, entry_high), "candidate", ()

  nearest = ordered[0]
  return (
    None,
    "waiting_for_touch",
    (
      f"nearest mapped {direction} zone "
      f"{nearest.lo:.2f}-{nearest.hi:.2f}; waiting for M1 touch",
    ),
  )


def _actionable(entry: MapEntry) -> bool:
  tags = {tag.lower() for tag in entry.tags}
  return entry.tier in {"zone", "major"} and bool(tags & _ACTIONABLE_TAGS)


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


def decode_market_map(raw: object) -> MarketMap | None:
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    return market_map_from_payload(text)
  except (KeyError, TypeError, ValueError):
    return None
