"""Strategy-specific mathematical hard gates (shadow-comparable).

These evaluators do **not** publish trades. They encode the Phase 5 models:

1. Liquidity Sweep Reversal
2. Impulse Pullback Continuation
3. Range Edge Mean Reversion
4. Breakout Retest Continuation (observe-only)

Live HFS discovery remains in ``strategies.py`` until shadow/paper promote.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.scalping.math_features import (
  ScalpFeatureVector,
  build_feature_vector,
  room_sufficient,
)


@dataclass(frozen=True)
class MathGateResult:
  strategy: str
  allowed: bool
  hard_block: bool
  reason_code: str
  score_inputs: dict[str, float] = field(default_factory=dict)
  features: dict[str, Any] = field(default_factory=dict)
  measured: dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


def _block(strategy: str, reason: str, features: ScalpFeatureVector, **extra: Any) -> MathGateResult:
  return MathGateResult(
    strategy=strategy,
    allowed=False,
    hard_block=True,
    reason_code=reason,
    features=features.to_dict(),
    measured=dict(extra),
  )


def _pass(
  strategy: str,
  reason: str,
  features: ScalpFeatureVector,
  score_inputs: dict[str, float],
  **extra: Any,
) -> MathGateResult:
  return MathGateResult(
    strategy=strategy,
    allowed=True,
    hard_block=False,
    reason_code=reason,
    score_inputs=score_inputs,
    features=features.to_dict(),
    measured=dict(extra),
  )


def evaluate_liquidity_sweep_reversal(
  *,
  direction: str,
  price: float,
  liquidity_level: float,
  bar_low: float,
  bar_high: float,
  bar_close: float,
  bar_open: float,
  atr: float,
  range_low: float,
  range_high: float,
  barrier: float | None,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
  target_min_price: float,
  max_location_buy: float = 0.40,
  min_location_sell: float = 0.60,
  reclaim: bool = True,
  utc_hour: int | None = None,
) -> MathGateResult:
  """BUY: Low < L then Close > L, location < 0.4, room_net >= target_min.

  SELL mirrored.
  """
  direction = direction.upper()
  features = build_feature_vector(
    price=price,
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    level=liquidity_level,
    direction=direction,
    barrier=barrier,
    spread=spread,
    slippage=slippage,
    buffer=buffer,
    open_=bar_open,
    high=bar_high,
    low=bar_low,
    close=bar_close,
    reclaim=reclaim,
    utc_hour=utc_hour,
  )

  if direction == "BUY":
    swept = bar_low < liquidity_level
    reclaimed = bar_close > liquidity_level
    loc = features.location_buy
    loc_ok = loc is not None and features.range_position is not None and features.range_position < max_location_buy
  else:
    swept = bar_high > liquidity_level
    reclaimed = bar_close < liquidity_level
    loc = features.location_sell
    loc_ok = loc is not None and features.range_position is not None and features.range_position > min_location_sell

  if not swept:
    return _block("liquidity_sweep_reversal", "no_liquidity_sweep", features)
  if reclaim and not reclaimed:
    return _block("liquidity_sweep_reversal", "no_reclaim_close", features)
  if not loc_ok:
    return _block(
      "liquidity_sweep_reversal",
      "location_outside_edge",
      features,
      range_position=features.range_position,
    )
  if not room_sufficient(features.room_net_price, target_min_price):
    return _block(
      "liquidity_sweep_reversal",
      "room_net_below_target_min",
      features,
      room_net=features.room_net_price,
      target_min=target_min_price,
    )

  return _pass(
    "liquidity_sweep_reversal",
    "sweep_reclaim_location_room_ok",
    features,
    {
      "location": float(loc or 0.0),
      "trigger": float(features.trigger_quality or 0.0),
      "momentum": 0.5,
      "structure": 0.8,
      "room": min(1.0, float(features.room_net_atr or 0.0) / 1.0) if features.room_net_atr else 0.0,
      "cost": min(1.0, float(features.execution_cost_atr or 0.0)),
      "exhaustion": float(features.exhaustion or 0.0),
    },
    liquidity_level=liquidity_level,
  )


def evaluate_impulse_pullback_continuation(
  *,
  direction: str,
  price: float,
  atr: float,
  impulse_origin: float,
  impulse_extreme: float,
  range_low: float | None = None,
  range_high: float | None = None,
  barrier: float | None = None,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
  target_min_price: float,
  min_impulse_atr: float = 1.0,
  retracement_min: float = 0.25,
  retracement_max: float = 0.65,
  continuation_trigger: bool = True,
  utc_hour: int | None = None,
) -> MathGateResult:
  """Require Impulse > k·ATR and retracement in (0.25, 0.65), then continuation."""
  direction = direction.upper()
  features = build_feature_vector(
    price=price,
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    close=price,
    impulse_origin=impulse_origin,
    impulse_extreme=impulse_extreme,
    direction=direction,
    barrier=barrier,
    spread=spread,
    slippage=slippage,
    buffer=buffer,
    utc_hour=utc_hour,
  )

  impulse = features.impulse_atr
  if impulse is None or impulse < min_impulse_atr:
    return _block(
      "impulse_pullback_continuation",
      "impulse_below_minimum_atr",
      features,
      impulse_atr=impulse,
      min_impulse_atr=min_impulse_atr,
    )

  retr = features.retracement
  if retr is None or not (retracement_min < retr < retracement_max):
    return _block(
      "impulse_pullback_continuation",
      "retracement_outside_band",
      features,
      retracement=retr,
      band=(retracement_min, retracement_max),
    )

  # Reject chase at the extreme (r ≈ 0).
  if retr <= 0.05:
    return _block("impulse_pullback_continuation", "chasing_extreme", features, retracement=retr)

  if not continuation_trigger:
    return _block("impulse_pullback_continuation", "waiting_continuation_trigger", features)

  if not room_sufficient(features.room_net_price, target_min_price):
    return _block(
      "impulse_pullback_continuation",
      "room_net_below_target_min",
      features,
      room_net=features.room_net_price,
    )

  loc = features.location_buy if direction == "BUY" else features.location_sell
  return _pass(
    "impulse_pullback_continuation",
    "impulse_pullback_continuation_ok",
    features,
    {
      "location": float(loc or 0.5),
      "trigger": 0.75 if continuation_trigger else 0.2,
      "momentum": min(1.0, float(impulse) / 2.0),
      "structure": 0.75,
      "room": min(1.0, float(features.room_net_atr or 0.0)),
      "cost": min(1.0, float(features.execution_cost_atr or 0.0)),
      "exhaustion": float(features.exhaustion or 0.0),
    },
    retracement=retr,
    impulse_atr=impulse,
  )


def evaluate_range_edge_mean_reversion(
  *,
  direction: str,
  price: float,
  atr: float,
  range_low: float,
  range_high: float,
  barrier: float | None,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
  target_min_price: float,
  buy_max_position: float = 0.25,
  sell_min_position: float = 0.75,
  deadzone_low: float = 0.40,
  deadzone_high: float = 0.60,
  min_range_quality_atr: float = 0.75,
  utc_hour: int | None = None,
) -> MathGateResult:
  """BUY only p < 0.25; SELL only p > 0.75; never 0.4 < p < 0.6."""
  direction = direction.upper()
  features = build_feature_vector(
    price=price,
    atr=atr,
    range_low=range_low,
    range_high=range_high,
    zone_low=range_low,
    zone_high=range_high,
    direction=direction,
    barrier=barrier,
    spread=spread,
    slippage=slippage,
    buffer=buffer,
    utc_hour=utc_hour,
  )

  rq = features.zone_width_atr
  if rq is None or rq < min_range_quality_atr:
    return _block(
      "range_edge_mean_reversion",
      "range_quality_below_minimum",
      features,
      range_quality_atr=rq,
    )

  p = features.range_position
  if p is None:
    return _block("range_edge_mean_reversion", "missing_range_position", features)

  if deadzone_low < p < deadzone_high:
    return _block(
      "range_edge_mean_reversion",
      "equilibrium_dead_zone",
      features,
      range_position=p,
    )

  if direction == "BUY" and p >= buy_max_position:
    return _block(
      "range_edge_mean_reversion",
      "buy_not_at_discount_edge",
      features,
      range_position=p,
    )
  if direction == "SELL" and p <= sell_min_position:
    return _block(
      "range_edge_mean_reversion",
      "sell_not_at_premium_edge",
      features,
      range_position=p,
    )

  if not room_sufficient(features.room_net_price, target_min_price):
    return _block(
      "range_edge_mean_reversion",
      "room_net_below_target_min",
      features,
      room_net=features.room_net_price,
    )

  loc = features.location_buy if direction == "BUY" else features.location_sell
  return _pass(
    "range_edge_mean_reversion",
    "range_edge_ok",
    features,
    {
      "location": float(loc or 0.0),
      "trigger": 0.7,
      "momentum": 0.45,
      "structure": min(1.0, float(rq) / 2.0),
      "room": min(1.0, float(features.room_net_atr or 0.0)),
      "cost": min(1.0, float(features.execution_cost_atr or 0.0)),
      "exhaustion": float(features.exhaustion or 0.0),
    },
    range_position=p,
    range_quality_atr=rq,
  )


def evaluate_breakout_retest_continuation(
  *,
  direction: str,
  price: float,
  atr: float,
  box_low: float,
  box_high: float,
  level: float,
  barrier: float | None = None,
  spread: float = 0.0,
  slippage: float = 0.0,
  buffer: float = 0.0,
  target_min_price: float,
  break_displacement: float | None = None,
  min_break_atr: float = 0.25,
  retest_rejection: bool = True,
  accepted_break: bool = True,
  failed_break: bool = False,
  utc_hour: int | None = None,
) -> MathGateResult:
  """Observe-only: displacement, room beyond break, retest quality, failed-break veto."""
  direction = direction.upper()
  features = build_feature_vector(
    price=price,
    atr=atr,
    range_low=box_low,
    range_high=box_high,
    level=level,
    zone_low=box_low,
    zone_high=box_high,
    direction=direction,
    barrier=barrier,
    spread=spread,
    slippage=slippage,
    buffer=buffer,
    utc_hour=utc_hour,
  )

  if failed_break:
    return _block(
      "breakout_retest_continuation",
      "failed_break_veto",
      features,
      box_low=box_low,
      box_high=box_high,
    )
  if not accepted_break:
    return _block(
      "breakout_retest_continuation",
      "break_not_accepted",
      features,
    )
  if not retest_rejection:
    return _block(
      "breakout_retest_continuation",
      "retest_rejection_missing",
      features,
    )

  width = float(box_high) - float(box_low)
  if atr <= 0 or width <= 0:
    return _block(
      "breakout_retest_continuation",
      "invalid_compression_box",
      features,
    )

  disp = break_displacement
  if disp is None:
    disp = (price - level) if direction == "BUY" else (level - price)
  disp_atr = float(disp) / atr if atr > 0 else 0.0
  if disp_atr < float(min_break_atr):
    return _block(
      "breakout_retest_continuation",
      "displacement_below_minimum",
      features,
      displacement_atr=disp_atr,
      min_break_atr=min_break_atr,
    )

  if not room_sufficient(features.room_net_price, target_min_price):
    return _block(
      "breakout_retest_continuation",
      "room_net_below_target_min",
      features,
      room_net=features.room_net_price,
    )

  compression_atr = width / atr
  return _pass(
    "breakout_retest_continuation",
    "breakout_retest_ok",
    features,
    {
      "location": float(
        (features.location_buy if direction == "BUY" else features.location_sell) or 0.5
      ),
      "trigger": min(1.0, 0.55 + 0.2 * disp_atr),
      "momentum": min(1.0, disp_atr),
      "structure": min(1.0, max(0.0, 1.2 - compression_atr)),
      "room": min(1.0, float(features.room_net_atr or 0.0)),
      "cost": min(1.0, float(features.execution_cost_atr or 0.0)),
      "exhaustion": float(features.exhaustion or 0.0),
    },
    displacement_atr=disp_atr,
    compression_atr=compression_atr,
    retest_rejection=True,
  )
