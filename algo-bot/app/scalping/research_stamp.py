"""Observe-only research stamps for the own scalp mechanism.

Attaches ``scalp_features`` and ``math_counterfactual`` to discovered
opportunities. Never flips allow/block — live authority stays in strategies
+ activation + publish.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.scalping.math_features import build_feature_vector
from app.scalping.math_strategies import (
  evaluate_impulse_pullback_continuation,
  evaluate_liquidity_sweep_reversal,
  evaluate_range_edge_mean_reversion,
)
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_MOMENTUM_CHASE,
  ARCHETYPE_RANGE_SWEEP,
  ScalpOpportunity,
)


def _barrier_for(
  opportunity: ScalpOpportunity,
  *,
  nearest_resistance_low: float | None,
  nearest_support_high: float | None,
) -> float | None:
  if opportunity.direction.upper() == "BUY":
    return nearest_resistance_low
  return nearest_support_high


def _base_features(
  opportunity: ScalpOpportunity,
  *,
  atr: float,
  range_low: float,
  range_high: float,
  barrier: float | None,
  bar_open: float,
  bar_high: float,
  bar_low: float,
  bar_close: float,
  spread: float,
  session: str,
  utc_hour: int | None,
  slippage: float = 0.0,
  buffer: float = 0.0,
) -> dict[str, Any]:
  measured = opportunity.measured or {}
  features = build_feature_vector(
    price=float(opportunity.trigger_price),
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    level=float(opportunity.key_level),
    zone_low=float(opportunity.zone_low),
    zone_high=float(opportunity.zone_high),
    close=bar_close,
    impulse_origin=_opt_float(measured.get("impulse_origin")),
    impulse_extreme=_opt_float(measured.get("impulse_extreme")),
    direction=opportunity.direction,
    barrier=barrier,
    spread=spread,
    slippage=slippage,
    buffer=buffer,
    open_=bar_open,
    high=bar_high,
    low=bar_low,
    reclaim=opportunity.archetype == ARCHETYPE_RANGE_SWEEP,
    utc_hour=utc_hour,
    session=session or None,
  )
  return features.to_dict()


def _opt_float(value: Any) -> float | None:
  if value is None:
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _math_counterfactual(
  opportunity: ScalpOpportunity,
  *,
  atr: float,
  range_low: float,
  range_high: float,
  barrier: float | None,
  bar_open: float,
  bar_high: float,
  bar_low: float,
  bar_close: float,
  spread: float,
  target_min_price: float,
  utc_hour: int | None,
  slippage: float = 0.0,
  buffer: float = 0.0,
) -> dict[str, Any]:
  """Return a counterfactual math dict — never used to block live."""
  archetype = opportunity.archetype
  measured = opportunity.measured or {}

  if archetype == ARCHETYPE_RANGE_SWEEP:
    gate = evaluate_liquidity_sweep_reversal(
      direction=opportunity.direction,
      price=float(opportunity.trigger_price),
      liquidity_level=float(opportunity.key_level),
      bar_low=bar_low,
      bar_high=bar_high,
      bar_close=bar_close,
      bar_open=bar_open,
      atr=atr,
      range_low=range_low,
      range_high=range_high,
      barrier=barrier,
      spread=spread,
      slippage=slippage,
      buffer=buffer,
      target_min_price=target_min_price,
      utc_hour=utc_hour,
    )
    payload = gate.to_dict()
    payload["math_model"] = "liquidity_sweep_reversal"
    return payload

  if archetype == ARCHETYPE_IMPULSE_PULLBACK:
    origin = _opt_float(measured.get("impulse_origin"))
    extreme = _opt_float(measured.get("impulse_extreme"))
    if origin is None or extreme is None:
      return {
        "math_model": "impulse_pullback_continuation",
        "allowed": None,
        "hard_block": False,
        "reason_code": "insufficient_inputs",
        "score_inputs": {},
        "features": {},
        "measured": {},
      }
    gate = evaluate_impulse_pullback_continuation(
      direction=opportunity.direction,
      price=float(opportunity.trigger_price),
      atr=atr,
      impulse_origin=origin,
      impulse_extreme=extreme,
      range_low=range_low,
      range_high=range_high,
      barrier=barrier,
      spread=spread,
      slippage=slippage,
      buffer=buffer,
      target_min_price=target_min_price,
      continuation_trigger=True,
      utc_hour=utc_hour,
    )
    payload = gate.to_dict()
    payload["math_model"] = "impulse_pullback_continuation"
    return payload

  if archetype in {ARCHETYPE_BREAKOUT_RETEST, ARCHETYPE_MOMENTUM_CHASE}:
    return {
      "math_model": None,
      "allowed": None,
      "hard_block": False,
      "reason_code": "no_math_model_yet",
      "score_inputs": {},
      "features": {},
      "measured": {"archetype": archetype},
    }

  # Research alias only — does not publish technique Range Edge from scalp.
  gate = evaluate_range_edge_mean_reversion(
    direction=opportunity.direction,
    price=float(opportunity.trigger_price),
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    barrier=barrier,
    spread=spread,
    slippage=slippage,
    buffer=buffer,
    target_min_price=target_min_price,
    utc_hour=utc_hour,
  )
  payload = gate.to_dict()
  payload["math_model"] = "range_edge_mean_reversion"
  payload["research_alias_only"] = True
  return payload


def annotate_opportunity_research(
  opportunity: ScalpOpportunity,
  *,
  atr: float,
  range_low: float,
  range_high: float,
  nearest_resistance_low: float | None,
  nearest_support_high: float | None,
  bar_open: float,
  bar_high: float,
  bar_low: float,
  bar_close: float,
  spread: float,
  target_min_price: float,
  session: str = "",
  utc_hour: int | None = None,
  slippage: float = 0.0,
  buffer: float = 0.0,
) -> ScalpOpportunity:
  """Stamp scalp_features + math_counterfactual; preserve live decision fields."""
  barrier = _barrier_for(
    opportunity,
    nearest_resistance_low=nearest_resistance_low,
    nearest_support_high=nearest_support_high,
  )
  features = _base_features(
    opportunity,
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    barrier=barrier,
    bar_open=bar_open,
    bar_high=bar_high,
    bar_low=bar_low,
    bar_close=bar_close,
    spread=spread,
    session=session,
    utc_hour=utc_hour,
    slippage=slippage,
    buffer=buffer,
  )
  counterfactual = _math_counterfactual(
    opportunity,
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    barrier=barrier,
    bar_open=bar_open,
    bar_high=bar_high,
    bar_low=bar_low,
    bar_close=bar_close,
    spread=spread,
    target_min_price=target_min_price,
    utc_hour=utc_hour,
    slippage=slippage,
    buffer=buffer,
  )
  measured = dict(opportunity.measured or {})
  measured["scalp_features"] = features
  measured["math_counterfactual"] = counterfactual
  # Keep legacy Sweep stamp key for existing digs / ranking score_inputs path.
  if opportunity.archetype == ARCHETYPE_RANGE_SWEEP:
    measured["math_liquidity_sweep"] = {
      k: v for k, v in counterfactual.items() if k != "math_model"
    }
    score_inputs = counterfactual.get("score_inputs")
    if isinstance(score_inputs, dict) and score_inputs:
      measured["math_score_inputs"] = dict(score_inputs)
  elif (
    opportunity.archetype == ARCHETYPE_IMPULSE_PULLBACK
    and counterfactual.get("allowed") is True
  ):
    score_inputs = counterfactual.get("score_inputs")
    if isinstance(score_inputs, dict) and score_inputs:
      measured["math_score_inputs"] = dict(score_inputs)

  math_would = counterfactual.get("allowed")
  measured["math_would_allow"] = math_would
  measured["math_agree"] = (
    None if math_would is None else bool(math_would) is True
  )
  return replace(opportunity, measured=measured)


def annotate_opportunities_research(
  opportunities: list[ScalpOpportunity],
  **kwargs: Any,
) -> list[ScalpOpportunity]:
  return [annotate_opportunity_research(opp, **kwargs) for opp in opportunities]


def research_agree_rows(
  opportunities: list[ScalpOpportunity],
  *,
  session: str = "",
  bar_ts: int | None = None,
) -> list[dict[str, Any]]:
  """Compact rows for cycle math_shadow / performance join."""
  rows: list[dict[str, Any]] = []
  for opp in opportunities:
    measured = opp.measured or {}
    cf = measured.get("math_counterfactual") or {}
    rows.append({
      "opportunity_id": opp.opportunity_id,
      "archetype": opp.archetype,
      "direction": opp.direction,
      "session": session,
      "bar_ts": bar_ts,
      "live_discovered": True,
      "math_would_allow": measured.get("math_would_allow"),
      "math_agree": measured.get("math_agree"),
      "math_model": cf.get("math_model"),
      "math_reason": cf.get("reason_code"),
    })
  return rows
