"""Scalp activation — fail-closed location + fresh M1 evidence + costs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.analysis.entry_location import (
  build_entry_location_context,
  evaluate_entry_location,
)
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  STRATEGY_DISPLAY,
  ScalpContextSnapshot,
  ScalpDecision,
  ScalpOpportunity,
)


def _hfs(cfg: Any) -> Any:
  return getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None)


def _enforce_location_cfg(hfs: Any) -> SimpleNamespace:
  """Scalp subsystem always evaluates location in enforce mode."""
  loc = getattr(hfs, "location", None)
  return SimpleNamespace(
    actionability=SimpleNamespace(
      entry_location=SimpleNamespace(
        mode="enforce",
        missing_context_policy="block",
        reversal=SimpleNamespace(
          buy_maximum_position=float(getattr(loc, "pullback_buy_maximum_position", 0.75) or 0.75),
          sell_minimum_position=float(getattr(loc, "pullback_sell_minimum_position", 0.25) or 0.25),
          extreme_buy_block_position=0.85,
          extreme_sell_block_position=0.15,
        ),
        range_reversion=SimpleNamespace(
          buy_maximum_position=float(getattr(loc, "range_buy_maximum_position", 0.35) or 0.35),
          sell_minimum_position=float(getattr(loc, "range_sell_minimum_position", 0.65) or 0.65),
          equilibrium_exclusion_width=0.20,
        ),
        trend_pullback=SimpleNamespace(
          buy_maximum_position=float(getattr(loc, "pullback_buy_maximum_position", 0.75) or 0.75),
          sell_minimum_position=float(getattr(loc, "pullback_sell_minimum_position", 0.25) or 0.25),
        ),
        breakout_retest=SimpleNamespace(allow_directional_expansion=True),
      ),
    ),
  )


def _strategy_name(opportunity: ScalpOpportunity) -> str:
  if opportunity.archetype == ARCHETYPE_RANGE_SWEEP:
    return "Range Edge Scalp"
  if opportunity.archetype == ARCHETYPE_IMPULSE_PULLBACK:
    return "Trend Pullback"
  if opportunity.archetype == ARCHETYPE_BREAKOUT_RETEST:
    return "Break & Retest"
  return STRATEGY_DISPLAY.get(opportunity.archetype, opportunity.archetype)


def evaluate_scalp_activation(
  opportunity: ScalpOpportunity,
  context: ScalpContextSnapshot,
  *,
  quote_bid: float,
  quote_ask: float,
  quote_ts: int,
  now: int,
  pip_size: float,
  cfg: Any,
  expected_slippage_pips: float = 1.0,
) -> ScalpDecision:
  hfs = _hfs(cfg)
  measured: dict[str, Any] = {
    "opportunity_id": opportunity.opportunity_id,
    "archetype": opportunity.archetype,
    "direction": opportunity.direction,
  }

  if opportunity.symbol.upper() != "XAU":
    return ScalpDecision(False, True, "scalp_symbol_not_enabled", 0.0, measured)

  # Quote freshness (60s hard cap inside HFS)
  if int(now) - int(quote_ts) > 60:
    return ScalpDecision(False, True, "scalp_quote_stale", 0.0, measured)

  spread_pips = abs(float(quote_ask) - float(quote_bid)) / max(pip_size, 1e-9)
  measured["spread_pips"] = spread_pips
  max_spread = float(getattr(getattr(hfs, "policy", None), "maximum_spread_pips", 5.0) or 5.0)
  if spread_pips > max_spread:
    return ScalpDecision(False, True, "scalp_spread_too_wide", 0.0, measured)

  executable = float(quote_ask) if opportunity.direction == "BUY" else float(quote_bid)
  measured["executable_quote"] = executable

  act = getattr(hfs, "activation", None)
  max_age = int(getattr(act, "trigger_maximum_age_bars", 2) or 2)
  age_bars = (int(now) - int(opportunity.trigger_bar_ts)) / 60.0
  if age_bars > max_age:
    return ScalpDecision(False, True, "reaction_trigger_stale", 0.0, measured)

  chase = float(getattr(act, "maximum_chase_pips", 100.0) or 100.0)
  if opportunity.direction == "BUY":
    distance = (executable - opportunity.zone_high) / pip_size
  else:
    distance = (opportunity.zone_low - executable) / pip_size
  measured["chase_pips"] = distance
  measured["maximum_chase_pips"] = chase
  if distance > chase:
    return ScalpDecision(False, True, "scalp_missed_chase", 0.0, measured)

  inside = opportunity.zone_low <= executable <= opportunity.zone_high
  measured["quote_inside"] = inside
  if not inside and distance <= 0:
    # Still approaching the zone — wait for a touch / reclaim.
    return ScalpDecision(False, False, "quote_outside_zone", 0.0, measured)
  if not inside and distance > 0:
    # Price already ran through the zone in trade direction — momentum
    # chase within maximum_chase_pips (owner: scalp must chase with momentum).
    measured["chase_entry"] = True
  else:
    measured["chase_entry"] = False

  # Location — enforce inside HFS
  loc_ctx = build_entry_location_context(
    execution_price=executable,
    direction=opportunity.direction,
    ask=float(quote_ask),
    bid=float(quote_bid),
    m15_range_low=context.dealing_range_low,
    m15_range_high=context.dealing_range_high,
    m5_range_low=context.active_range_low,
    m5_range_high=context.active_range_high,
    zone_low=opportunity.zone_low,
    zone_high=opportunity.zone_high,
  )
  evidence = None
  if opportunity.archetype == ARCHETYPE_BREAKOUT_RETEST:
    evidence = (opportunity.measured or {}).get("breakout_evidence")
  location = evaluate_entry_location(
    strategy=_strategy_name(opportunity),
    direction=opportunity.direction,
    context=loc_ctx,
    cfg=_enforce_location_cfg(hfs),
    breakout_evidence=evidence,
  )
  measured["location_reason"] = location.reason_code
  measured["location_position"] = loc_ctx.effective_range_position_raw
  if not location.allowed:
    return ScalpDecision(
      False, True, location.reason_code or "entry_location_blocked", 0.0, measured,
    )

  # Cost-aware net target. Minimum net pips is the owner room gate for scalp;
  # intentional ~1:1 room-synced books (target=stop=30) cannot clear a 1.10
  # net-RR floor after spread/slip — that path killed live HFS Impulse
  # Pullback BUY (2026-08-06 09:08 UTC, reason=scalp_net_rr_insufficient).
  gross = float(opportunity.expected_target_pips)
  net = gross - spread_pips - float(expected_slippage_pips)
  measured["net_target_pips"] = net
  min_net = float(getattr(getattr(hfs, "target", None), "minimum_net_target_pips", 15.0) or 15.0)
  if net < min_net:
    return ScalpDecision(False, True, "scalp_net_target_insufficient", 0.0, measured)
  stop = float(opportunity.expected_stop_pips)
  net_rr = net / stop if stop > 0 else 0.0
  measured["net_reward_risk"] = net_rr
  min_rr = float(getattr(getattr(hfs, "policy", None), "minimum_reward_risk", 1.10) or 1.10)
  measured["minimum_reward_risk"] = min_rr
  if net_rr < min_rr:
    # Preference telemetry only once min-net room is already satisfied.
    measured["net_rr_below_policy"] = True
    measured["preference_telemetry"] = True
    measured["preference_reason_code"] = "scalp_net_rr_below_policy"

  return ScalpDecision(
    True,
    False,
    "scalp_activation_allowed",
    score=float(opportunity.score),
    measured=measured,
  )
