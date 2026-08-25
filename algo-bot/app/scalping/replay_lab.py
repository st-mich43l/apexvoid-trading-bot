"""Deterministic scalp replay laboratory (PR B).

M1-style events → math features/gates → paper fills with declared
spread/slippage. Calibration uses chronological 60/20/20 splits; holdout
must not be used for threshold tuning.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from app.scalping.math_features import unified_scalp_score
from app.scalping.math_strategies import (
  MathGateResult,
  evaluate_impulse_pullback_continuation,
  evaluate_liquidity_sweep_reversal,
  evaluate_range_edge_mean_reversion,
)
from app.scalping.models import (
  OPPORTUNITY_VERSION,
  ScalpOpportunity,
  deterministic_id,
)
from app.scalping.replay import (
  aggregate_report,
  calibration_report,
  evaluate_paper_outcome,
)


@dataclass(frozen=True)
class LabEvent:
  """One research event: gate inputs + forward bars for paper fill."""

  timestamp: int
  direction: str
  price: float
  atr: float
  range_low: float
  range_high: float
  strategy: str
  bar_open: float
  bar_high: float
  bar_low: float
  bar_close: float
  bars_after: list[dict[str, Any]]
  liquidity_level: float | None = None
  barrier: float | None = None
  impulse_origin: float | None = None
  impulse_extreme: float | None = None
  continuation_trigger: bool = True
  spread: float = 0.0
  slippage: float = 0.0
  buffer: float = 0.0
  target_min_price: float = 1.0
  stop_price: float | None = None
  target_price: float | None = None
  pip_size: float = 0.1
  session: str = "unknown"
  symbol: str = "XAU"
  # Sweep overrides (optional)
  max_location_buy: float = 0.40
  min_location_sell: float = 0.60
  min_impulse_atr: float = 1.0
  retracement_min: float = 0.25
  retracement_max: float = 0.65
  buy_max_position: float = 0.25
  sell_min_position: float = 0.75
  min_range_quality_atr: float = 0.75
  utc_hour: int | None = None
  measured: dict[str, Any] = field(default_factory=dict)

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> LabEvent:
    bar = data.get("bar") or {}
    return cls(
      timestamp=int(data["timestamp"]),
      direction=str(data["direction"]).upper(),
      price=float(data["price"]),
      atr=float(data["atr"]),
      range_low=float(data["range_low"]),
      range_high=float(data["range_high"]),
      strategy=str(data.get("strategy") or "liquidity_sweep_reversal"),
      bar_open=float(bar.get("open", data.get("bar_open"))),
      bar_high=float(bar.get("high", data.get("bar_high"))),
      bar_low=float(bar.get("low", data.get("bar_low"))),
      bar_close=float(bar.get("close", data.get("bar_close"))),
      bars_after=list(data.get("bars_after") or []),
      liquidity_level=_opt_float(data.get("liquidity_level")),
      barrier=_opt_float(data.get("barrier")),
      impulse_origin=_opt_float(data.get("impulse_origin")),
      impulse_extreme=_opt_float(data.get("impulse_extreme")),
      continuation_trigger=bool(data.get("continuation_trigger", True)),
      spread=float(data.get("spread") or 0.0),
      slippage=float(data.get("slippage") or 0.0),
      buffer=float(data.get("buffer") or 0.0),
      target_min_price=float(data.get("target_min_price") or 1.0),
      stop_price=_opt_float(data.get("stop_price")),
      target_price=_opt_float(data.get("target_price")),
      pip_size=float(data.get("pip_size") or 0.1),
      session=str(data.get("session") or "unknown"),
      symbol=str(data.get("symbol") or "XAU").upper(),
      max_location_buy=float(data.get("max_location_buy") or 0.40),
      min_location_sell=float(data.get("min_location_sell") or 0.60),
      min_impulse_atr=float(data.get("min_impulse_atr") or 1.0),
      retracement_min=float(data.get("retracement_min") or 0.25),
      retracement_max=float(data.get("retracement_max") or 0.65),
      buy_max_position=float(data.get("buy_max_position") or 0.25),
      sell_min_position=float(data.get("sell_min_position") or 0.75),
      min_range_quality_atr=float(data.get("min_range_quality_atr") or 0.75),
      utc_hour=_opt_int(data.get("utc_hour")),
      measured=dict(data.get("measured") or {}),
    )


def evaluate_lab_gate(event: LabEvent) -> MathGateResult:
  strategy = event.strategy.strip().lower()
  if strategy in {"liquidity_sweep_reversal", "range_sweep", "hfs_range_sweep"}:
    if event.liquidity_level is None:
      raise ValueError("liquidity_sweep_reversal requires liquidity_level")
    return evaluate_liquidity_sweep_reversal(
      direction=event.direction,
      price=event.price,
      liquidity_level=float(event.liquidity_level),
      bar_low=event.bar_low,
      bar_high=event.bar_high,
      bar_close=event.bar_close,
      bar_open=event.bar_open,
      atr=event.atr,
      range_low=event.range_low,
      range_high=event.range_high,
      barrier=event.barrier,
      spread=event.spread,
      slippage=event.slippage,
      buffer=event.buffer,
      target_min_price=event.target_min_price,
      max_location_buy=event.max_location_buy,
      min_location_sell=event.min_location_sell,
      utc_hour=event.utc_hour,
    )
  if strategy in {"impulse_pullback_continuation", "impulse_pullback"}:
    if event.impulse_origin is None or event.impulse_extreme is None:
      raise ValueError("impulse_pullback requires impulse_origin and impulse_extreme")
    return evaluate_impulse_pullback_continuation(
      direction=event.direction,
      price=event.price,
      atr=event.atr,
      impulse_origin=float(event.impulse_origin),
      impulse_extreme=float(event.impulse_extreme),
      range_low=event.range_low,
      range_high=event.range_high,
      barrier=event.barrier,
      spread=event.spread,
      slippage=event.slippage,
      buffer=event.buffer,
      target_min_price=event.target_min_price,
      min_impulse_atr=event.min_impulse_atr,
      retracement_min=event.retracement_min,
      retracement_max=event.retracement_max,
      continuation_trigger=event.continuation_trigger,
      utc_hour=event.utc_hour,
    )
  if strategy in {"range_edge_mean_reversion", "range_edge"}:
    return evaluate_range_edge_mean_reversion(
      direction=event.direction,
      price=event.price,
      atr=event.atr,
      range_low=event.range_low,
      range_high=event.range_high,
      barrier=event.barrier,
      spread=event.spread,
      slippage=event.slippage,
      buffer=event.buffer,
      target_min_price=event.target_min_price,
      buy_max_position=event.buy_max_position,
      sell_min_position=event.sell_min_position,
      min_range_quality_atr=event.min_range_quality_atr,
      utc_hour=event.utc_hour,
    )
  raise ValueError(f"unknown lab strategy {event.strategy!r}")


def _synthetic_opportunity(event: LabEvent, gate: MathGateResult) -> ScalpOpportunity:
  direction = event.direction.upper()
  pip = event.pip_size if event.pip_size > 0 else 0.1
  stop = event.stop_price
  target = event.target_price
  if stop is None:
    stop_dist = max(event.atr * 0.5, event.target_min_price)
    stop = event.price - stop_dist if direction == "BUY" else event.price + stop_dist
  if target is None:
    room = gate.features.get("room_net_price")
    if room is not None and float(room) > 0:
      tgt_dist = float(room)
    else:
      tgt_dist = max(event.target_min_price, event.atr * 0.75)
    target = event.price + tgt_dist if direction == "BUY" else event.price - tgt_dist
  stop_pips = abs(event.price - float(stop)) / pip
  target_pips = abs(float(target) - event.price) / pip
  rr = target_pips / stop_pips if stop_pips > 0 else 0.0
  opp_id = deterministic_id(
    "lab", event.strategy, event.timestamp, direction, event.price,
  )
  return ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id=opp_id,
    context_id=f"lab:{event.timestamp}",
    symbol=event.symbol,
    archetype=event.strategy,
    direction=direction,
    discovered_at=event.timestamp,
    source_bar_ts=event.timestamp,
    zone_low=event.range_low,
    zone_high=event.range_high,
    key_level=float(event.liquidity_level or event.price),
    trigger_type="lab_math_gate",
    trigger_bar_ts=event.timestamp,
    trigger_price=event.price,
    invalidation_price=float(stop),
    expected_target_price=float(target),
    expected_target_pips=float(target_pips),
    expected_stop_pips=float(stop_pips),
    expected_reward_risk=float(rr),
    location_position=gate.features.get("range_position"),
    score=unified_scalp_score(**{
      k: float(gate.score_inputs.get(k, 0.0))
      for k in ("location", "trigger", "momentum", "structure", "room", "cost", "exhaustion")
    }) if gate.score_inputs else 0.0,
    reasons=(gate.reason_code,),
    expires_at=event.timestamp + 3600,
    measured={
      "math_score_inputs": dict(gate.score_inputs),
      "math_features": dict(gate.features),
      "lab": True,
    },
  )


def replay_lab_event(event: LabEvent) -> dict[str, Any]:
  gate = evaluate_lab_gate(event)
  row: dict[str, Any] = {
    "timestamp": event.timestamp,
    "session": event.session,
    "archetype": event.strategy,
    "direction": event.direction,
    "symbol": event.symbol,
    "gate_allowed": gate.allowed,
    "gate_hard_block": gate.hard_block,
    "gate_reason": gate.reason_code,
    "features": gate.features,
    "score_inputs": gate.score_inputs,
  }
  if not gate.allowed:
    row.update({
      "outcome": "blocked",
      "decision": "blocked",
      "block_reason": gate.reason_code,
      "net_r": 0.0,
      "mfe_pips": 0.0,
      "mae_pips": 0.0,
      "bars_held": 0,
    })
    return row

  opp = _synthetic_opportunity(event, gate)
  bars = pd.DataFrame(event.bars_after)
  outcome = evaluate_paper_outcome(
    opp,
    bars,
    pip_size=event.pip_size,
    spread_pips=(event.spread / event.pip_size) if event.pip_size > 0 else 0.0,
    slippage_pips=(event.slippage / event.pip_size) if event.pip_size > 0 else 0.0,
  )
  stop_pips = float(opp.expected_stop_pips) or 1.0
  row.update({
    "decision": "allowed",
    "outcome": outcome.outcome,
    "mfe_pips": outcome.mfe_pips,
    "mae_pips": outcome.mae_pips,
    "bars_held": outcome.bars_held,
    "net_r": outcome.net_pips / stop_pips,
    "entry": opp.trigger_price,
    "stop": opp.invalidation_price,
    "target": opp.expected_target_price,
    "score": opp.score,
    "block_reason": None,
  })
  return row


def load_lab_events(path: Path) -> list[LabEvent]:
  events: list[LabEvent] = []
  for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    events.append(LabEvent.from_dict(json.loads(line)))
  return events


def replay_lab_fixture(path: Path) -> dict[str, Any]:
  events = load_lab_events(path)
  rows = [replay_lab_event(event) for event in events]
  traded = [r for r in rows if r.get("outcome") != "blocked"]
  return {
    "events": rows,
    "aggregate_all": aggregate_report(rows),
    "aggregate_traded": aggregate_report(traded),
    "calibration_traded": calibration_report(traded),
    "blocked_count": sum(1 for r in rows if r.get("outcome") == "blocked"),
    "allowed_count": len(traded),
  }


_SWEEPABLE = frozenset({
  "max_location_buy",
  "min_location_sell",
  "min_impulse_atr",
  "retracement_min",
  "retracement_max",
  "buy_max_position",
  "sell_min_position",
  "min_range_quality_atr",
  "target_min_price",
  "buffer",
  "spread",
  "slippage",
})


def parameter_sweep(
  base_events: Sequence[LabEvent],
  *,
  param: str,
  values: Sequence[float],
) -> list[dict[str, Any]]:
  """Sweep one parameter on development events; returns expectancy per value.

  Never pass holdout events here.
  """
  if param not in _SWEEPABLE:
    raise ValueError(f"unsweepable param {param!r}; allowed={sorted(_SWEEPABLE)}")
  results: list[dict[str, Any]] = []
  for value in values:
    rows = [
      replay_lab_event(replace(event, **{param: float(value)}))
      for event in base_events
    ]
    traded = [r for r in rows if r.get("outcome") != "blocked"]
    agg = aggregate_report(traded)
    results.append({
      "param": param,
      "value": float(value),
      "allowed": len(traded),
      "blocked": len(rows) - len(traded),
      "expectancy_r": agg.get("expectancy_r", 0.0),
      "profit_factor": agg.get("profit_factor", 0.0),
      "count": agg.get("count", 0),
    })
  return results


def default_sweep_grids() -> dict[str, list[float]]:
  return {
    "retracement_min": [0.20, 0.25, 0.30, 0.35],
    "retracement_max": [0.55, 0.60, 0.65, 0.70, 0.75],
    "buy_max_position": [0.15, 0.20, 0.25, 0.30],
    "sell_min_position": [0.70, 0.75, 0.80, 0.85],
    "min_impulse_atr": [0.5, 0.75, 1.0, 1.25, 1.5],
    "max_location_buy": [0.30, 0.35, 0.40, 0.45],
  }


def _opt_float(value: Any) -> float | None:
  if value is None:
    return None
  return float(value)


def _opt_int(value: Any) -> int | None:
  if value is None:
    return None
  return int(value)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="XAU scalp math replay laboratory")
  parser.add_argument("--fixture", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--sweep-param", default=None)
  parser.add_argument(
    "--sweep-values",
    default=None,
    help="Comma-separated floats (development events only; never holdout)",
  )
  args = parser.parse_args(argv)
  events = load_lab_events(args.fixture)
  report: dict[str, Any] = replay_lab_fixture(args.fixture)
  if args.sweep_param and args.sweep_values:
    from app.scalping.replay import split_dataset

    indexed = [{"timestamp": e.timestamp, "idx": i} for i, e in enumerate(events)]
    splits = split_dataset(indexed)
    dev_events = [events[int(row["idx"])] for row in splits["development"]]
    values = [float(x.strip()) for x in str(args.sweep_values).split(",") if x.strip()]
    report["sweep"] = {
      "param": args.sweep_param,
      "split": "development",
      "n_development": len(dev_events),
      "results": parameter_sweep(dev_events, param=args.sweep_param, values=values),
      "note": "holdout unused for sweep",
    }
  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
