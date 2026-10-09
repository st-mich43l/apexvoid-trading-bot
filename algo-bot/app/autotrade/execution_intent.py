"""The one place a StrategyMatch becomes the ExecutionIntent arbitration ranks.

The worker's decision cycle and the offline arbitration evaluation (tools/arbitration_eval)
both call :func:`execution_intent_for_match`, so what the evaluation ranks is built exactly as
production builds it - never a second copy that can drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.autotrade import units
from app.autotrade.arbitration import ExecutionIntent


def intent_freshness(raw: object, fallback: int = 0) -> float:
  text = str(raw or "").strip()
  if text:
    try:
      value = float(text)
      return value / 1000 if value > 1e12 else value
    except ValueError:
      try:
        return datetime.fromisoformat(
          text.replace("Z", "+00:00")
        ).timestamp()
      except ValueError:
        pass
  return float(fallback)


def band_distance_pips(
  price: float | None,
  low: float,
  high: float,
  symbol: str,
) -> float:
  if price is None or low <= price <= high:
    return 0.0
  return (
    min(abs(price - low), abs(price - high))
    / units.pip_size(symbol)
  )


def execution_intent_for_match(
  match: Any,
  *,
  symbol: str,
  spot_price: float | None,
  executable_now: bool,
  cycle_id: str = "",
  proposed_group_id: str | None = None,
) -> ExecutionIntent:
  """Build the intent for one routed match.

  ``executable_now`` is decided by the caller from the live quote
  (``worker._execution_quote_access``); ``proposed_group_id`` is the worker's
  ``_strategy_group_id(match)``.
  """
  return ExecutionIntent(
    intent_id=f"strategy:{match.match_id}",
    source=(
      "market_map_strategy"
      if match.strategy_mode == "mapped_zone_reaction"
      else "go_analysis_engine"
    ),
    strategy=match.strategy,
    direction=match.direction,
    confluence=match.confluence,
    freshness=intent_freshness(
      match.confirmation_bar_ts or match.event_ts,
      match.issued_at,
    ),
    distance_pips=band_distance_pips(
      spot_price, match.entry_low, match.entry_high, symbol,
    ),
    symbol=symbol.upper(),
    timeframe=match.source_tf,
    family=match.family,
    entry_low=match.entry_low,
    entry_high=match.entry_high,
    structural_id=str(
      match.structural_zone_id
      or match.zone_id
      or match.level_id
      or match.match_id
    ),
    match_id=match.match_id,
    reaction_id=match.reaction_id,
    thesis_id=match.thesis_id,
    go_thesis_id=match.go_thesis_id,
    current_price=spot_price,
    target_model=match.target_model,
    targets_pips=match.targets_pips,
    absolute_target_price=(
      match.absolute_target_price
      if match.absolute_target_price is not None
      else match.target_price
    ),
    target_reference_price=match.target_reference_price,
    proposed_group_id=proposed_group_id,
    cycle_id=str(cycle_id or ""),
    quality_overall=match.quality_overall,
    structural_quality=match.confluence_v2_raw,
    atr=float(match.atr or 0.0),
    bias_relationship=match.bias_relationship,
    executable_now=bool(executable_now),
  )
