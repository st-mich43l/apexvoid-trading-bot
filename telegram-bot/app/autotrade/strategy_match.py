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
  raw = (
    f"v{STRATEGY_MATCH_VERSION}|{symbol.upper()}|{source_tf.upper()}|"
    f"{event_ts}|{strategy}|{direction.upper()}|"
    f"{entry_low:.2f}|{entry_high:.2f}"
  )
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def strategy_range_id(symbol: str, lower: float, upper: float) -> str:
  return f"{symbol.lower()}-strategy-range-{lower:.2f}-{upper:.2f}"


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
  valid_range = (
    all(value is None for value in range_values)
    and match.range_id is None
    and match.full_take_profit_pips is None
  ) or (
    all(value is not None and math.isfinite(value) for value in range_values)
    and match.range_id is not None
    and match.range_low < match.range_high
    and match.full_take_profit_pips > 0
  )
  if match.reaction_id:
    identity_ok = match.match_id == match.reaction_id
  else:
    identity_ok = match.match_id == strategy_match_id(
      match.symbol,
      match.source_tf,
      match.event_ts,
      match.strategy,
      match.direction,
      match.entry_low,
      match.entry_high,
    )
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
    and match.tier in {"A", "B", "C"}
    and math.isfinite(match.risk_multiplier)
    and match.risk_multiplier >= 0
    and valid_range
    and identity_ok
  )
