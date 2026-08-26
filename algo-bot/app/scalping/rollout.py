"""Shadow / paper / controlled-live rollout helpers for math scalper.

Modes already exist on HFS config (`off|shadow|paper|live`). These helpers
evaluate the mathematical strategy layer without replacing live discovery
until promotion criteria pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from app.scalping.math_features import unified_scalp_score
from app.scalping.math_strategies import (
  MathGateResult,
  evaluate_impulse_pullback_continuation,
  evaluate_liquidity_sweep_reversal,
  evaluate_range_edge_mean_reversion,
)
from app.scalping.models import ARCHETYPE_RANGE_SWEEP, ScalpOpportunity
from app.scalping.replay import evaluate_paper_outcome


RolloutMode = Literal["off", "shadow", "paper", "live"]


@dataclass(frozen=True)
class ControlledLivePolicy:
  """PR I defaults: one strategy, reduced risk, kill-switch ready."""

  strategy: str = "liquidity_sweep_reversal"
  risk_fraction: float = 0.05
  maximum_session_trades: int = 6
  maximum_daily_trades: int = 12
  enabled: bool = False
  kill_switch: bool = False
  session_allowlist: tuple[str, ...] = (
    "asia",
    "london",
    "london_ny_overlap",
    "ny_open",
  )


@dataclass(frozen=True)
class ShadowEvaluation:
  mode: RolloutMode
  results: tuple[MathGateResult, ...]
  ranked: tuple[dict[str, Any], ...] = ()
  would_execute: bool = False
  measured: dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return {
      "mode": self.mode,
      "results": [r.to_dict() for r in self.results],
      "ranked": list(self.ranked),
      "would_execute": self.would_execute,
      "measured": dict(self.measured),
    }


def evaluate_math_shadow(
  *,
  mode: RolloutMode,
  direction: str,
  price: float,
  atr: float,
  range_low: float,
  range_high: float,
  liquidity_level: float | None = None,
  barrier: float | None = None,
  bar_open: float | None = None,
  bar_high: float | None = None,
  bar_low: float | None = None,
  bar_close: float | None = None,
  impulse_origin: float | None = None,
  impulse_extreme: float | None = None,
  continuation_trigger: bool = False,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
  target_min_price: float,
  utc_hour: int | None = None,
  policy: ControlledLivePolicy | None = None,
) -> ShadowEvaluation:
  """Run all three math strategies; rank survivors; never send broker orders."""
  results: list[MathGateResult] = []

  if (
    liquidity_level is not None
    and None not in (bar_open, bar_high, bar_low, bar_close)
  ):
    results.append(
      evaluate_liquidity_sweep_reversal(
        direction=direction,
        price=price,
        liquidity_level=float(liquidity_level),
        bar_low=float(bar_low),
        bar_high=float(bar_high),
        bar_close=float(bar_close),
        bar_open=float(bar_open),
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
    )

  if impulse_origin is not None and impulse_extreme is not None:
    results.append(
      evaluate_impulse_pullback_continuation(
        direction=direction,
        price=price,
        atr=atr,
        impulse_origin=float(impulse_origin),
        impulse_extreme=float(impulse_extreme),
        range_low=range_low,
        range_high=range_high,
        barrier=barrier,
        spread=spread,
        slippage=slippage,
        buffer=buffer,
        target_min_price=target_min_price,
        continuation_trigger=continuation_trigger,
        utc_hour=utc_hour,
      )
    )

  results.append(
    evaluate_range_edge_mean_reversion(
      direction=direction,
      price=price,
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
  )

  survivors = [r for r in results if r.allowed and not r.hard_block]
  ranked: list[dict[str, Any]] = []
  for item in survivors:
    score = unified_scalp_score(
      location=float(item.score_inputs.get("location", 0.0)),
      trigger=float(item.score_inputs.get("trigger", 0.0)),
      momentum=float(item.score_inputs.get("momentum", 0.0)),
      structure=float(item.score_inputs.get("structure", 0.0)),
      room=float(item.score_inputs.get("room", 0.0)),
      cost=float(item.score_inputs.get("cost", 0.0)),
      exhaustion=float(item.score_inputs.get("exhaustion", 0.0)),
    )
    ranked.append({"strategy": item.strategy, "score": score, "reason": item.reason_code})
  ranked.sort(key=lambda row: row["score"], reverse=True)

  live_policy = policy or ControlledLivePolicy()
  would = False
  if mode == "shadow":
    would = False
  elif mode == "paper":
    would = bool(ranked)
  elif mode == "live":
    if live_policy.kill_switch or not live_policy.enabled:
      would = False
    elif ranked and ranked[0]["strategy"] == live_policy.strategy:
      would = True

  measured: dict[str, Any] = {
    "policy": asdict(live_policy),
    "survivor_count": len(survivors),
  }
  # MAD gates are not stamped on HFS shadow — MAD does not drive scalping.

  return ShadowEvaluation(
    mode=mode,
    results=tuple(results),
    ranked=tuple(ranked),
    would_execute=would,
    measured=measured,
  )


def annotate_range_sweep_math_gate(
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
  utc_hour: int | None = None,
  slippage: float = 0.0,
  buffer: float = 0.0,
) -> ScalpOpportunity:
  """Stamp Liquidity Sweep math gates onto HFS range_sweep (shadow-comparable).

  Does not change allow/block for live publish — recorded under
  ``measured.math_liquidity_sweep`` for density / disagreement review.
  """
  if opportunity.archetype != ARCHETYPE_RANGE_SWEEP:
    return opportunity
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
  measured = dict(opportunity.measured)
  measured["math_liquidity_sweep"] = gate.to_dict()
  if gate.score_inputs:
    measured["math_score_inputs"] = dict(gate.score_inputs)
  return replace(opportunity, measured=measured)


# Re-export paper outcome helper for rollout callers.
paper_evaluate = evaluate_paper_outcome
