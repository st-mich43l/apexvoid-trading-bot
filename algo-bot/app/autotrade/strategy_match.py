"""Typed scanner strategy matches consumed by ApexVoid Algo.

The scanner owns the complete price-action decision.  Once a detector emits a
``DetectionResult`` the strategy is matched; this contract transports that
decision without asking the Algo worker to confirm it again or route it by a
market-regime label.  The remaining checks are execution safety only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math

from app.analysis.confluence_zone import confluence_setup_id
from app.core.symbols import digits_for
from app.runtime.price_identity import price_token
from app.analysis.execution_eligibility import ExecutionEligibility
from app.analysis.structural_reaction_support import structural_thesis_id


STRATEGY_MATCH_VERSION = 1
STRATEGY_MATCH_KEY_PREFIX = "auto_trade:strategy_match"


@dataclass(frozen=True)
class StrategyMatch:
  version: int
  match_id: str
  symbol: str
  source_tf: str
  event_ts: str
  issued_at: int
  expires_at: int
  strategy: str
  strategy_mode: str
  direction: str
  key_level: float
  entry_low: float
  entry_high: float
  current_price: float
  confluence: int
  reasons: tuple[str, ...]
  atr: float
  structure_swing: float
  targets_pips: tuple[int, ...]
  range_id: str | None = None
  range_low: float | None = None
  range_high: float | None = None
  full_take_profit_pips: int | None = None
  tags: tuple[str, ...] = ()
  target_price: float | None = None
  tier: str = "A"
  risk_multiplier: float = 1.0
  family: str = ""
  range_state: str | None = None
  routing_hint: str | None = None
  structural_source: str = ""
  zone_id: str | None = None
  # Scanner-side merged confluence identity. When present, setup/card/order
  # all claim this exact zone rather than independently re-resolving it.
  confluence_zone_id: str | None = None
  level_id: str | None = None
  # Stable Mapped Zone Reaction identity (additive; absent on older matches).
  reaction_id: str | None = None
  thesis_id: str | None = None
  structural_zone_id: str | None = None
  # Raw Market Map zone bounds before proximal/spot execution expansion.
  structural_zone_low: float | None = None
  structural_zone_high: float | None = None
  touch_bar_ts: str | None = None
  confirmation_bar_ts: str | None = None
  reaction_type: str | None = None
  # Explicit execution target semantics. ``targets_pips`` are always
  # fill-relative; ``absolute_target_price`` is a structural cap/target.
  target_model: str = "fill_relative"
  target_reference_price: str = "broker_fill"
  absolute_target_price: float | None = None
  # Real analysis context for TradePlan V7 (app/autotrade/trade_plan_builder.py)
  # - populated from the DetectionResult/DetectionContext that produced this
  # match so the builder never has to derive bias/kind/timeframe from
  # direction (BUY => demand, BUY => bias up are exactly the forbidden
  # shortcuts per docs/adr-trade-plan-v7-boundary.md). Additive, defaulted
  # fields so older cached matches still round-trip.
  structural_kind: str | None = None
  structural_timeframe: str | None = None
  htf_bias: str = ""
  regime_kind: str = ""
  execution_eligibility: ExecutionEligibility | None = None
  # Additive activation / location provenance (older Redis payloads omit these).
  entry_location_source: str | None = None
  entry_location_position: float | None = None
  entry_location_reason: str | None = None
  entry_activation_trigger: str | None = None
  entry_activation_trigger_ts: str | None = None
  # Soft math diagnostics for Telegram Math line (additive / optional).
  math_fib_ratio: float | None = None
  math_velocity: float | None = None
  math_acceleration: float | None = None
  math_pd: float | None = None

  @property
  def is_range_edge(self) -> bool:
    # full_take_profit_pips is selected upstream (see
    # app.autotrade.range_targets.select_range_target) against the
    # configured AUTO_TRADE_RANGE_TARGETS_PIPS ladder, not a fixed {50,70}
    # pair - this contract only needs to know a target was actually chosen.
    return (
      self.strategy in {"Range Edge Scalp", "One-Sided Range Reaction"}
      and self.strategy_mode in {"range_scalp", "one_sided_range"}
      and self.range_id is not None
      and self.range_low is not None
      and self.range_high is not None
      and self.full_take_profit_pips is not None
      and self.full_take_profit_pips > 0
    )

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: object) -> StrategyMatch | None:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
      payload = json.loads(text)
      result = cls(
        version=int(payload["version"]),
        match_id=str(payload["match_id"]),
        symbol=str(payload["symbol"]).upper(),
        source_tf=str(payload["source_tf"]).upper(),
        event_ts=str(payload["event_ts"]),
        issued_at=int(payload["issued_at"]),
        expires_at=int(payload["expires_at"]),
        strategy=str(payload["strategy"]),
        strategy_mode=str(payload["strategy_mode"]),
        direction=str(payload["direction"]).upper(),
        key_level=float(payload["key_level"]),
        entry_low=float(payload["entry_low"]),
        entry_high=float(payload["entry_high"]),
        current_price=float(payload["current_price"]),
        confluence=int(payload["confluence"]),
        reasons=tuple(str(item) for item in payload.get("reasons", [])),
        atr=float(payload["atr"]),
        structure_swing=float(payload["structure_swing"]),
        targets_pips=tuple(int(item) for item in payload["targets_pips"]),
        range_id=(
          None if payload.get("range_id") is None else str(payload["range_id"])
        ),
        range_low=(
          None if payload.get("range_low") is None
          else float(payload["range_low"])
        ),
        range_high=(
          None if payload.get("range_high") is None
          else float(payload["range_high"])
        ),
        full_take_profit_pips=(
          None if payload.get("full_take_profit_pips") is None
          else int(payload["full_take_profit_pips"])
        ),
        tags=tuple(str(item) for item in payload.get("tags", [])),
        target_price=(
          None if payload.get("target_price") is None
          else float(payload["target_price"])
        ),
        tier=str(payload.get("tier") or "A").upper(),
        risk_multiplier=float(payload.get("risk_multiplier") or 1.0),
        family=str(payload.get("family") or ""),
        range_state=(
          None if payload.get("range_state") is None
          else str(payload["range_state"])
        ),
        routing_hint=(
          None if payload.get("routing_hint") is None
          else str(payload["routing_hint"])
        ),
        structural_source=str(
          payload.get("structural_source") or payload.get("strategy") or ""
        ),
        zone_id=(
          None if payload.get("zone_id") is None
          else str(payload["zone_id"])
        ),
        confluence_zone_id=(
          None if payload.get("confluence_zone_id") is None
          else str(payload["confluence_zone_id"])
        ),
        level_id=(
          None if payload.get("level_id") is None
          else str(payload["level_id"])
        ),
        reaction_id=(
          None if payload.get("reaction_id") is None
          else str(payload["reaction_id"])
        ),
        thesis_id=(
          None if payload.get("thesis_id") is None
          else str(payload["thesis_id"])
        ),
        structural_zone_id=(
          None if payload.get("structural_zone_id") is None
          else str(payload["structural_zone_id"])
        ),
        structural_zone_low=(
          None if payload.get("structural_zone_low") is None
          else float(payload["structural_zone_low"])
        ),
        structural_zone_high=(
          None if payload.get("structural_zone_high") is None
          else float(payload["structural_zone_high"])
        ),
        touch_bar_ts=(
          None if payload.get("touch_bar_ts") is None
          else str(payload["touch_bar_ts"])
        ),
        confirmation_bar_ts=(
          None if payload.get("confirmation_bar_ts") is None
          else str(payload["confirmation_bar_ts"])
        ),
        reaction_type=(
          None if payload.get("reaction_type") is None
          else str(payload["reaction_type"])
        ),
        target_model=str(
          payload.get("target_model")
          or (
            "hybrid"
            if payload.get("target_price") is not None
            and payload.get("targets_pips")
            else "absolute"
            if payload.get("target_price") is not None
            else "fill_relative"
          )
        ).lower(),
        target_reference_price=str(
          payload.get("target_reference_price")
          or (
            "planned_entry"
            if payload.get("target_price") is not None
            else "broker_fill"
          )
        ).lower(),
        absolute_target_price=(
          float(payload["absolute_target_price"])
          if payload.get("absolute_target_price") is not None
          else float(payload["target_price"])
          if payload.get("target_price") is not None
          else None
        ),
        structural_kind=(
          None if payload.get("structural_kind") is None
          else str(payload["structural_kind"])
        ),
        structural_timeframe=(
          None if payload.get("structural_timeframe") is None
          else str(payload["structural_timeframe"])
        ),
        htf_bias=str(payload.get("htf_bias") or ""),
        regime_kind=str(payload.get("regime_kind") or ""),
        execution_eligibility=ExecutionEligibility.from_dict(
          payload.get("execution_eligibility"),
        ),
        entry_location_source=(
          None if payload.get("entry_location_source") is None
          else str(payload["entry_location_source"])
        ),
        entry_location_position=(
          None if payload.get("entry_location_position") is None
          else float(payload["entry_location_position"])
        ),
        entry_location_reason=(
          None if payload.get("entry_location_reason") is None
          else str(payload["entry_location_reason"])
        ),
        entry_activation_trigger=(
          None if payload.get("entry_activation_trigger") is None
          else str(payload["entry_activation_trigger"])
        ),
        entry_activation_trigger_ts=(
          None if payload.get("entry_activation_trigger_ts") is None
          else str(payload["entry_activation_trigger_ts"])
        ),
        math_fib_ratio=(
          None if payload.get("math_fib_ratio") is None
          else float(payload["math_fib_ratio"])
        ),
        math_velocity=(
          None if payload.get("math_velocity") is None
          else float(payload["math_velocity"])
        ),
        math_acceleration=(
          None if payload.get("math_acceleration") is None
          else float(payload["math_acceleration"])
        ),
        math_pd=(
          None if payload.get("math_pd") is None
          else float(payload["math_pd"])
        ),
      )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
      return None
    return result if _valid_match(result) else None


def strategy_match_key(symbol: str) -> str:
  return f"{STRATEGY_MATCH_KEY_PREFIX}:{symbol.upper()}"


def strategy_match_id(
  symbol: str,
  source_tf: str,
  event_ts: str,
  strategy: str,
  direction: str,
  entry_low: float,
  entry_high: float,
) -> str:
  """Stable per-detector-event identity for restart-safe idempotency."""
  digits = digits_for(symbol)
  raw = (
    f"v{STRATEGY_MATCH_VERSION}|{symbol.upper()}|{source_tf.upper()}|"
    f"{event_ts}|{strategy}|{direction.upper()}|"
    f"{price_token(entry_low, digits=digits)}|"
    f"{price_token(entry_high, digits=digits)}"
  )
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def strategy_range_id(symbol: str, lower: float, upper: float) -> str:
  digits = digits_for(symbol)
  return (
    f"{symbol.lower()}-strategy-range-"
    f"{price_token(lower, digits=digits)}-"
    f"{price_token(upper, digits=digits)}"
  )


def _identity_ok(match: StrategyMatch) -> bool:
  if match.reaction_id:
    return match.match_id == match.reaction_id
  if match.confluence_zone_id:
    return match.match_id == confluence_setup_id(
      match.confluence_zone_id,
      match.direction,
    )
  structural_id = match.structural_zone_id or match.zone_id or match.level_id
  if structural_id and match.structural_source:
    expected = structural_thesis_id(
      symbol=match.symbol,
      strategy=match.strategy,
      direction=match.direction,
      structural_source=match.structural_source,
      structural_id=structural_id,
      touch_bar_ts=str(match.touch_bar_ts or ""),
      confirmation_bar_ts=str(match.confirmation_bar_ts or ""),
    )
    if match.match_id == expected:
      return True
  return match.match_id == strategy_match_id(
    match.symbol,
    match.source_tf,
    match.event_ts,
    match.strategy,
    match.direction,
    match.entry_low,
    match.entry_high,
  )


def _valid_match(match: StrategyMatch) -> bool:
  numeric = (
    match.key_level,
    match.entry_low,
    match.entry_high,
    match.current_price,
    match.atr,
    match.structure_swing,
  )
  range_values = (match.range_low, match.range_high)
  hfs_fitted = (
    match.full_take_profit_pips is not None
    and match.full_take_profit_pips > 0
    and (
      match.family in {"hfs", "scalp"}
      or match.strategy_mode in {"hfs_scalp", "scalp_m1"}
      or str(match.strategy).startswith("HFS ")
      or str(match.strategy) in {
        "Range Sweep Scalp",
        "Impulse Pullback Scalp",
        "Breakout Retest Scalp",
        "Momentum Chase Scalp",
      }
    )
  )
  valid_range = (
    all(value is None for value in range_values)
    and match.range_id is None
    and (match.full_take_profit_pips is None or hfs_fitted)
  ) or (
    all(value is not None and math.isfinite(value) for value in range_values)
    and match.range_id is not None
    and match.range_low < match.range_high
    and match.full_take_profit_pips is not None
    and match.full_take_profit_pips > 0
  )
  identity_ok = _identity_ok(match)
  return (
    match.version == STRATEGY_MATCH_VERSION
    and bool(match.match_id)
    and bool(match.symbol)
    and bool(match.source_tf)
    and bool(match.strategy)
    and match.direction in {"BUY", "SELL"}
    and match.issued_at <= match.expires_at
    and match.entry_low <= match.entry_high
    and match.confluence >= 1
    and all(math.isfinite(value) for value in numeric)
    and match.atr > 0
    and bool(match.targets_pips)
    and all(value > 0 for value in match.targets_pips)
    and tuple(sorted(set(match.targets_pips))) == match.targets_pips
    and (
      match.target_price is None
      or math.isfinite(match.target_price)
    )
    and match.target_model in {"absolute", "fill_relative", "hybrid"}
    and match.target_reference_price in {
      "detection", "planned_entry", "broker_fill", "structural_level",
    }
    and (
      match.absolute_target_price is None
      or math.isfinite(match.absolute_target_price)
    )
    and match.tier in {"A", "B", "C"}
    and math.isfinite(match.risk_multiplier)
    and match.risk_multiplier >= 0
    and valid_range
    and identity_ok
  )
