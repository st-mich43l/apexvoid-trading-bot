"""Build TradePlan V7 from an already-confirmed StrategyMatch (Phase 2).

Per docs/adr-trade-plan-v7-boundary.md, this is a pure translation, not a
second decision: `StrategyMatch` already carries Python's confirmed
strategy/direction/entry-zone/structural-invalidation-price/target-pip-ladder
(the scanner "owns the complete price-action decision", per
strategy_match.py's own docstring). This module only reshapes that already-
decided data into the V7 contract shape - it does not classify regime,
resolve a route, or compute a stop; `stop.price` is exactly
`match.structure_swing`, the same structural swing price the scanner already
used to confirm the setup.

Not yet wired into worker.py's live publish path - callers use this
explicitly (initially: shadow-mode comparison and tests). Wiring this into
production candidate publishing is a later phase, gated by
AUTO_TRADE_CONTRACT_MODE.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from app.autotrade.strategy_match import StrategyMatch
from app.autotrade.trade_plan import (
  TradePlan,
  TradePlanAnalysis,
  TradePlanEntry,
  TradePlanError,
  TradePlanExecutionPolicy,
  TradePlanManagement,
  TradePlanProvenance,
  TradePlanRisk,
  TradePlanSourceStructure,
  TradePlanStop,
  TradePlanTarget,
)


def _parse_bar_ts(event_ts: str, fallback: int) -> int:
  try:
    return int(event_ts)
  except (TypeError, ValueError):
    return fallback


def _equal_close_ratios(count: int) -> tuple[Decimal, ...]:
  if count == 0:
    return ()
  base = (Decimal("1") / count).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
  ratios = [base] * count
  # Ratios must sum to exactly 1.0 (TradePlan.validate enforces <= 1.0001);
  # any quantization remainder goes to the final (furthest, smallest-size)
  # target rather than being silently dropped.
  remainder = Decimal("1") - base * count
  ratios[-1] += remainder
  return tuple(ratios)


def build_trade_plan_from_strategy_match(
  match: StrategyMatch,
  *,
  plan_id: str,
  setup_id: str,
  pip_size: Decimal,
  entry_reference_price: Decimal,
  price_side: str,
  max_volume: int,
  activation: str = "quote_inside_zone",
  max_spread_ticks: int = 8,
  max_slippage_ticks: int = 10,
  be_after_target_index: int = 0,
  be_buffer_ticks: int = 6,
  risk_percent: Decimal = Decimal("1.0"),
  risk_multiplier: Decimal | None = None,
  max_group_risk_percent: Decimal = Decimal("2.0"),
  close_ratios: Sequence[Decimal] | None = None,
  score: float = 0.0,
  analysis_engine_version: str = "",
  market_map_id: str = "",
  config_fingerprint: str = "",
) -> TradePlan:
  if not match.targets_pips:
    raise TradePlanError(
      f"StrategyMatch {match.match_id!r} has no targets_pips to build "
      "TradePlan targets from",
    )
  if price_side not in ("bid", "ask"):
    raise TradePlanError(f"price_side must be 'bid' or 'ask': {price_side!r}")

  direction = match.direction
  sign = Decimal("1") if direction == "BUY" else Decimal("-1")

  ratios = (
    tuple(Decimal(str(r)) for r in close_ratios)
    if close_ratios is not None
    else _equal_close_ratios(len(match.targets_pips))
  )
  if len(ratios) != len(match.targets_pips):
    raise TradePlanError(
      f"close_ratios length {len(ratios)} does not match targets_pips "
      f"length {len(match.targets_pips)}",
    )

  targets = tuple(
    TradePlanTarget(
      target_id=f"TP{index + 1}",
      type="absolute",
      price=entry_reference_price + sign * (Decimal(pips) * pip_size),
      close_ratio=ratio,
    )
    for index, (pips, ratio) in enumerate(zip(match.targets_pips, ratios))
  )

  structure_id = (
    match.zone_id or match.level_id or match.reaction_id or match.match_id
  )
  formation_bar_ts = _parse_bar_ts(match.event_ts, match.issued_at)

  analysis = TradePlanAnalysis(
    strategy=match.strategy,
    strategy_family=match.family or match.strategy_mode,
    direction=direction,
    context_timeframes=(match.source_tf,),
    formation_timeframe=match.source_tf,
    confirmation_timeframe=match.source_tf,
    formation_bar_ts=formation_bar_ts,
    confirmation_bar_ts=formation_bar_ts,
    score=score,
    confluence=match.confluence,
    bias="up" if direction == "BUY" else "down",
    regime=match.range_state or "trend",
    reasons=match.reasons,
    tags=match.tags,
  )

  source_structure = TradePlanSourceStructure(
    structure_id=structure_id,
    kind="demand" if direction == "BUY" else "supply",
    timeframe=match.source_tf,
    low=Decimal(str(match.entry_low)),
    high=Decimal(str(match.entry_high)),
    invalidation_price=Decimal(str(match.structure_swing)),
  )

  entry = TradePlanEntry(
    type="market_watch",
    expires_at=match.expires_at,
    zone_low=Decimal(str(match.entry_low)),
    zone_high=Decimal(str(match.entry_high)),
    activation=activation,
    price_side=price_side,
    max_spread_ticks=max_spread_ticks,
    max_slippage_ticks=max_slippage_ticks,
  )

  stop = TradePlanStop(
    type="absolute",
    price=Decimal(str(match.structure_swing)),
    source="structural_invalidation",
    structure_id=structure_id,
    reason="StrategyMatch.structure_swing, the same structural swing "
    "price the scanner used to confirm this setup",
  )

  management = TradePlanManagement(
    be_after_target_id=targets[be_after_target_index].target_id if targets else None,
    be_buffer_ticks=be_buffer_ticks,
    never_worsen_stop=True,
  )

  risk = TradePlanRisk(
    risk_percent=risk_percent,
    risk_multiplier=(
      risk_multiplier
      if risk_multiplier is not None
      else Decimal(str(match.risk_multiplier))
    ),
    max_volume=max_volume,
    max_group_risk_percent=max_group_risk_percent,
  )

  plan = TradePlan(
    plan_id=plan_id,
    thesis_id=match.thesis_id or match.match_id,
    setup_id=setup_id,
    symbol=match.symbol,
    created_at=match.issued_at,
    expires_at=match.expires_at,
    analysis=analysis,
    source_structure=source_structure,
    entry=entry,
    stop=stop,
    targets=targets,
    risk=risk,
    management=management,
    execution_policy=TradePlanExecutionPolicy(
      allow_market=True,
      allow_limit=False,
      allow_partial_fill=True,
      cancel_on_expiry=True,
    ),
    provenance=TradePlanProvenance(
      analysis_engine_version=analysis_engine_version,
      market_map_id=market_map_id,
      config_fingerprint=config_fingerprint,
    ),
  )
  plan.validate()
  return plan
