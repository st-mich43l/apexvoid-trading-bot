"""Owner-armed manual /algo intent -> TradePlan V8.

Manual /algo executes through the same TradePlan V8 path as every other trade.
The owner's exact entry zone, stop and take profits are declared as absolute
prices; cTrader Engine never re-derives them. The plan is tagged
``strategy_family="manual"`` so the executor labels its events ``algo_manual``,
keeps it out of autonomous exposure/accounting, and does not apply the
autonomous opposite-exposure fence (the owner's instruction is a direct
decision, not analysis output).
"""

from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.autotrade.trade_plan import (
  ENTRY_TYPE_LIMIT_LADDER,
  ENTRY_TYPE_SINGLE_LIMIT,
  TradePlan,
  TradePlanAnalysis,
  TradePlanEntry,
  TradePlanEntryLeg,
  TradePlanError,
  TradePlanExecutionPolicy,
  TradePlanManagement,
  TradePlanProvenance,
  TradePlanRisk,
  TradePlanSizing,
  TradePlanSourceStructure,
  TradePlanStop,
  TradePlanTarget,
)
from app.autotrade.trade_plan_builder import resolve_max_spread_ticks
from app.core.config import runtime_config
from app.runtime.instruments import for_instrument
from app.signals.manual_intent import ManualTradeIntent

MANUAL_STRATEGY = "Manual Algo"
MANUAL_FAMILY = "manual"
MANUAL_PLAN_PREFIX = "manual:"
RISK_LEG_DISABLED_TAG = "risk_leg:disabled"

# Shallow / deep split of a zone ladder (owner 2026-09-08: 80/20).
MANUAL_LADDER_RATIOS = (Decimal("0.8"), Decimal("0.2"))
_DEFAULT_PLAN_LIFETIME_SECONDS = 24 * 3600
_BE_BUFFER_TICKS = 6


def is_manual_plan_id(plan_id: object) -> bool:
  return str(plan_id or "").startswith(MANUAL_PLAN_PREFIX)


def _percentage_weights_from_ratios(ratios: tuple[float, ...]) -> list[int]:
  raw = [int(round(float(ratio) * 100)) for ratio in ratios]
  if raw:
    raw[-1] += 100 - sum(raw)
  return raw


def manual_target_weights(effective: Any, target_count: int) -> list[int]:
  """Resolve a valid 100% split for any owner-supplied TP count."""
  if target_count <= 0:
    raise ValueError("manual /algo requires at least one take profit")
  if target_count == 1:
    return [100]

  close_ratios = tuple(
    float(item) for item in effective.manual.target_close_ratios
  )
  if close_ratios and len(close_ratios) == target_count:
    weights = _percentage_weights_from_ratios(close_ratios)
    if all(weight > 0 for weight in weights) and sum(weights) == 100:
      return weights

  first_fraction = effective.manual.tp1_close_fraction
  if first_fraction is not None:
    first = int(round(float(first_fraction) * 100))
    first = max(1, min(99, first))
    remaining = 100 - first
    later_count = target_count - 1
    later = remaining // later_count
    weights = [first, *([later] * later_count)]
    weights[-1] += remaining - (later * later_count)
    if all(weight > 0 for weight in weights):
      return weights

  equal = 100 // target_count
  weights = [equal] * target_count
  weights[-1] += 100 - sum(weights)
  if any(weight <= 0 for weight in weights):
    raise ValueError("manual /algo supports at most 100 take profits")
  return weights


def _quantize(value: Decimal, digits: int) -> Decimal:
  # Midpoints round away from zero (the reviewed ladder spec's rule), never
  # half-to-even.
  return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _leg_prices(
  intent: ManualTradeIntent, digits: int,
) -> tuple[Decimal, Decimal]:
  """(shallow, deep) resting prices; mirrors the owner's zone semantics.

  Shallow is the proximal zone edge (BUY -> high, SELL -> low). Deep is the
  zone midpoint; a single-price zone has no span, so deep sits halfway to the
  stop and never beyond it.
  """
  low = Decimal(str(min(intent.entry_low, intent.entry_high)))
  high = Decimal(str(max(intent.entry_low, intent.entry_high)))
  if low != high:
    shallow = high if intent.direction == "BUY" else low
    deep = (low + high) / 2
  else:
    shallow = low
    deep = shallow + (Decimal(str(intent.sl)) - shallow) / 2
  return _quantize(shallow, digits), _quantize(deep, digits)


def build_manual_trade_plan(
  intent: ManualTradeIntent,
  *,
  now_ts: int | None = None,
) -> TradePlan:
  """Translate one ManualTradeIntent into a validated TradePlan V8."""
  effective = for_instrument(runtime_config, intent.symbol)
  manual = effective.manual
  if not manual.enabled:
    raise ValueError(f"manual trading is disabled for {intent.symbol}")
  if not manual.algo_enabled:
    raise ValueError(f"manual /algo is disabled for {intent.symbol}")
  digits = int(effective.units.price_digits)
  pip_size = Decimal(str(effective.units.pip_size))
  now = int(time.time()) if now_ts is None else int(now_ts)
  direction = intent.direction

  shallow, deep = _leg_prices(intent, digits)
  single = manual.entry_mode.value == "single" or intent.single_entry_override
  stop_price = _quantize(Decimal(str(intent.sl)), digits)
  weights = manual_target_weights(effective, len(intent.tps))
  targets = tuple(
    TradePlanTarget(
      target_id=f"TP{index + 1}",
      type="absolute",
      price=_quantize(Decimal(str(price)), digits),
      close_ratio=Decimal(weight) / Decimal(100),
    )
    for index, (price, weight) in enumerate(zip(intent.tps, weights))
  )

  expires_at = int(intent.expires_at or 0)
  if expires_at <= now:
    expires_at = int(intent.created_at) + _DEFAULT_PLAN_LIFETIME_SECONDS
  expires_at = max(expires_at, now + 60)

  max_spread_pips = float(effective.execution.entry.max_spread_pips or 5)
  max_spread_ticks = resolve_max_spread_ticks(
    max_spread_pips=max_spread_pips, pip_size=pip_size, price_digits=digits,
  )
  if single:
    entry = TradePlanEntry(
      type=ENTRY_TYPE_SINGLE_LIMIT,
      order_price=shallow,
      expires_at=expires_at,
      max_spread_ticks=max_spread_ticks,
    )
    leg_ratios: tuple[Decimal, ...] = (Decimal("1"),)
  else:
    entry = TradePlanEntry(
      type=ENTRY_TYPE_LIMIT_LADDER,
      expires_at=expires_at,
      max_spread_ticks=max_spread_ticks,
      legs=(
        TradePlanEntryLeg("L1", shallow, MANUAL_LADDER_RATIOS[0]),
        TradePlanEntryLeg("L2", deep, MANUAL_LADDER_RATIOS[1]),
      ),
    )
    leg_ratios = MANUAL_LADDER_RATIOS

  zone_low = Decimal(str(min(intent.entry_low, intent.entry_high)))
  zone_high = Decimal(str(max(intent.entry_low, intent.entry_high)))
  if zone_low >= zone_high:
    tick = Decimal(1).scaleb(-digits)
    zone_low, zone_high = zone_low - tick, zone_high + tick
  signal_id = intent.manual_signal_id
  zone_id = f"manual-zone:{signal_id}"
  tags = (RISK_LEG_DISABLED_TAG,) if single else ()

  plan = TradePlan(
    plan_id=intent.intent_id,
    thesis_id=f"manual-thesis:{signal_id}",
    setup_id=intent.intent_id,
    symbol=intent.symbol,
    created_at=int(intent.created_at),
    expires_at=expires_at,
    analysis=TradePlanAnalysis(
      strategy=MANUAL_STRATEGY,
      strategy_family=MANUAL_FAMILY,
      direction=direction,
      context_timeframes=("M1",),
      formation_timeframe="M1",
      confirmation_timeframe="M1",
      formation_bar_ts=int(intent.created_at),
      confirmation_bar_ts=int(intent.created_at),
      score=0.0,
      confluence=int(intent.confluence or 1),
      bias="neutral",
      regime="manual",
      reasons=("manual /algo signal",),
      tags=tags,
    ),
    source_structure=TradePlanSourceStructure(
      structure_id=zone_id,
      kind="owner_instruction",
      timeframe="M1",
      low=zone_low,
      high=zone_high,
      invalidation_price=stop_price,
    ),
    entry=entry,
    stop=TradePlanStop(
      type="absolute",
      price=stop_price,
      source="owner_instruction",
      structure_id=zone_id,
      reason="owner-entered /algo stop",
    ),
    targets=targets,
    risk=TradePlanRisk(
      risk_percent=Decimal("1.0"),
      risk_multiplier=Decimal(str(manual.risk_multiplier)),
      max_volume=int(effective.units.plan_max_volume()),
      max_group_risk_percent=Decimal("2.0"),
    ),
    management=TradePlanManagement(
      be_after_target_id="TP1" if len(targets) > 1 else None,
      be_buffer_ticks=_BE_BUFFER_TICKS,
      never_worsen_stop=True,
      # Owner rule: after TP2 every surviving leg's stop advances to the
      # owner's own TP1 price (needs a target after TP2 to be valid).
      trail_after_target_id="TP2" if len(targets) >= 3 else None,
      trail_to_target_id="TP1" if len(targets) >= 3 else None,
    ),
    execution_policy=TradePlanExecutionPolicy(
      allow_market=False,
      allow_limit=True,
      allow_partial_fill=True,
      cancel_on_expiry=True,
    ),
    provenance=TradePlanProvenance(
      analysis_engine_version="manual",
      market_map_id="",
      config_fingerprint="",
    ),
    sizing=TradePlanSizing(
      mode="equity_table",
      table_version="owner_equity_v1",
      entry_distribution="single" if single else "zone_scale",
      leg_ratios=leg_ratios,
    ),
  )
  try:
    plan.validate()
  except TradePlanError as exc:
    raise ValueError(f"manual /algo plan is invalid: {exc}") from exc
  return plan
