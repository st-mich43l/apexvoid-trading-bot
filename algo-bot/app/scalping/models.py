"""Typed contracts for the M1 scalping engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from app.autotrade.strategy_names import (
  BREAKOUT_RETEST_SCALP,
  IMPULSE_PULLBACK_SCALP,
  RANGE_SWEEP_SCALP,
)


CONTEXT_VERSION = 1
OPPORTUNITY_VERSION = 1
LIVE_OUTCOME_VERSION = 2

ARCHETYPE_RANGE_SWEEP = "range_sweep"
ARCHETYPE_IMPULSE_PULLBACK = "impulse_pullback"
ARCHETYPE_BREAKOUT_RETEST = "breakout_retest"

# Canonical display names for M1 scalping archetypes.
STRATEGY_DISPLAY = {
  ARCHETYPE_RANGE_SWEEP: RANGE_SWEEP_SCALP,
  ARCHETYPE_IMPULSE_PULLBACK: IMPULSE_PULLBACK_SCALP,
  ARCHETYPE_BREAKOUT_RETEST: BREAKOUT_RETEST_SCALP,
}


DISCOVERED = "discovered"
ARMED = "armed"
TOUCHED = "touched"
TRIGGERED = "triggered"
RETEST_WAIT = "retest_wait"
EXECUTABLE = "executable"
PUBLISHED = "published"
INVALIDATED = "invalidated"
EXPIRED = "expired"
MISSED = "missed"
CANCELLED = "cancelled"
COMPLETED = "completed"

ACTIVE_STATES = frozenset({
  DISCOVERED, ARMED, TOUCHED, TRIGGERED, RETEST_WAIT, EXECUTABLE, PUBLISHED,
})
TERMINAL_STATES = frozenset({
  INVALIDATED, EXPIRED, MISSED, CANCELLED, COMPLETED,
})


def deterministic_id(*parts: object) -> str:
  """Stable identity from structural parts (no wall-clock)."""
  material = "|".join("" if part is None else str(part) for part in parts)
  return hashlib.sha1(material.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ScalpContextSnapshot:
  version: int
  context_id: str
  symbol: str
  created_at: int

  h1_bar_ts: int | None
  m15_bar_ts: int | None
  m5_bar_ts: int

  htf_bias: str
  m5_structure: str
  regime: str

  dealing_range_low: float | None
  dealing_range_high: float | None
  dealing_range_position: float | None

  active_range_low: float | None
  active_range_high: float | None
  active_range_eq: float | None

  nearest_support_low: float | None
  nearest_support_high: float | None
  nearest_resistance_low: float | None
  nearest_resistance_high: float | None

  buy_corridor_room_pips: float | None
  sell_corridor_room_pips: float | None

  session: str
  permitted_archetypes: tuple[str, ...]

  atr: float = 0.0
  measured: dict[str, Any] = field(default_factory=dict)

  def to_json(self) -> str:
    payload = asdict(self)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ScalpContextSnapshot:
    data = json.loads(raw)
    return cls(
      version=int(data.get("version", CONTEXT_VERSION)),
      context_id=str(data["context_id"]),
      symbol=str(data["symbol"]).upper(),
      created_at=int(data["created_at"]),
      h1_bar_ts=_opt_int(data.get("h1_bar_ts")),
      m15_bar_ts=_opt_int(data.get("m15_bar_ts")),
      m5_bar_ts=int(data["m5_bar_ts"]),
      htf_bias=str(data.get("htf_bias") or "unknown"),
      m5_structure=str(data.get("m5_structure") or "unknown"),
      regime=str(data.get("regime") or "unknown"),
      dealing_range_low=_opt_float(data.get("dealing_range_low")),
      dealing_range_high=_opt_float(data.get("dealing_range_high")),
      dealing_range_position=_opt_float(data.get("dealing_range_position")),
      active_range_low=_opt_float(data.get("active_range_low")),
      active_range_high=_opt_float(data.get("active_range_high")),
      active_range_eq=_opt_float(data.get("active_range_eq")),
      nearest_support_low=_opt_float(data.get("nearest_support_low")),
      nearest_support_high=_opt_float(data.get("nearest_support_high")),
      nearest_resistance_low=_opt_float(data.get("nearest_resistance_low")),
      nearest_resistance_high=_opt_float(data.get("nearest_resistance_high")),
      buy_corridor_room_pips=_opt_float(data.get("buy_corridor_room_pips")),
      sell_corridor_room_pips=_opt_float(data.get("sell_corridor_room_pips")),
      session=str(data.get("session") or "unknown"),
      permitted_archetypes=tuple(data.get("permitted_archetypes") or ()),
      atr=float(data.get("atr") or 0.0),
      measured=dict(data.get("measured") or {}),
    )


@dataclass(frozen=True)
class ScalpOpportunity:
  version: int
  opportunity_id: str
  context_id: str
  symbol: str
  archetype: str
  direction: str

  discovered_at: int
  source_bar_ts: int

  zone_low: float
  zone_high: float
  key_level: float

  trigger_type: str
  trigger_bar_ts: int
  trigger_price: float

  invalidation_price: float
  expected_target_price: float
  expected_target_pips: float
  expected_stop_pips: float
  expected_reward_risk: float

  location_position: float | None
  score: float
  reasons: tuple[str, ...]

  expires_at: int
  episode_id: str = ""
  source_identity: str = ""
  measured: dict[str, Any] = field(default_factory=dict)

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ScalpOpportunity:
    data = json.loads(raw)
    return cls(
      version=int(data.get("version", OPPORTUNITY_VERSION)),
      opportunity_id=str(data["opportunity_id"]),
      context_id=str(data["context_id"]),
      symbol=str(data["symbol"]).upper(),
      archetype=str(data["archetype"]),
      direction=str(data["direction"]).upper(),
      discovered_at=int(data["discovered_at"]),
      source_bar_ts=int(data["source_bar_ts"]),
      zone_low=float(data["zone_low"]),
      zone_high=float(data["zone_high"]),
      key_level=float(data["key_level"]),
      trigger_type=str(data["trigger_type"]),
      trigger_bar_ts=int(data["trigger_bar_ts"]),
      trigger_price=float(data["trigger_price"]),
      invalidation_price=float(data["invalidation_price"]),
      expected_target_price=float(data["expected_target_price"]),
      expected_target_pips=float(data["expected_target_pips"]),
      expected_stop_pips=float(data["expected_stop_pips"]),
      expected_reward_risk=float(data["expected_reward_risk"]),
      location_position=_opt_float(data.get("location_position")),
      score=float(data.get("score") or 0.0),
      reasons=tuple(data.get("reasons") or ()),
      expires_at=int(data["expires_at"]),
      episode_id=str(data.get("episode_id") or ""),
      source_identity=str(data.get("source_identity") or ""),
      measured=dict(data.get("measured") or {}),
    )


@dataclass(frozen=True)
class ScalpDecision:
  allowed: bool
  hard_block: bool
  reason_code: str
  score: float
  measured: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScalpScore:
  total: float
  location: float
  trigger: float
  structure: float
  room: float
  freshness: float
  cost: float
  penalties: tuple[str, ...] = ()


@dataclass(frozen=True)
class MicroSwing:
  kind: str
  price: float
  bar_ts: int
  index: int


@dataclass(frozen=True)
class MicroStructure:
  structure: str
  swings: tuple[MicroSwing, ...]
  last_break_direction: str | None
  last_break_price: float | None
  last_break_ts: int | None
  equal_highs: tuple[float, ...]
  equal_lows: tuple[float, ...]
  measured: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScalpSignal:
  signal_id: str
  opportunity_id: str
  mode: str
  decision: ScalpDecision
  opportunity: ScalpOpportunity
  created_at: int
  measured: dict[str, Any] = field(default_factory=dict)

  def to_json(self) -> str:
    return json.dumps({
      "signal_id": self.signal_id,
      "opportunity_id": self.opportunity_id,
      "mode": self.mode,
      "decision": asdict(self.decision),
      "opportunity": asdict(self.opportunity),
      "created_at": self.created_at,
      "measured": self.measured,
    }, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class PaperOutcome:
  outcome: str
  bars_held: int
  mfe_pips: float
  mae_pips: float
  net_pips: float
  exit_price: float | None
  exit_reason: str
  measured: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScalpLiveOutcome:
  """Finalised live-scalp measurement. No trading behaviour — instrumentation."""

  opportunity_id: str
  episode_id: str
  symbol: str
  archetype: str
  direction: str
  session: str
  htf_bias: str
  regime: str

  entry_price: float
  invalidation_price: float
  stop_pips: float
  planned_target_pips: float
  planned_rr: float

  exit_path: str
  realized_r: float
  realized_pips: float
  legs_filled: int

  mfe_pips: float
  mae_pips: float
  bars_held: int
  opened_at: int
  closed_at: int
  version: int = LIVE_OUTCOME_VERSION
  expected_stop_pips: float | None = None
  realized_risk_pips: float | None = None
  planned_vs_realized_stop_ratio: float | None = None
  risk_denominator_source: str = "planned"
  measured: dict[str, Any] = field(default_factory=dict)

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ScalpLiveOutcome:
    data = json.loads(raw)
    expected = data.get("expected_stop_pips")
    realized_risk = data.get("realized_risk_pips")
    ratio = data.get("planned_vs_realized_stop_ratio")
    return cls(
      opportunity_id=str(data["opportunity_id"]),
      episode_id=str(data.get("episode_id") or ""),
      symbol=str(data["symbol"]).upper(),
      archetype=str(data.get("archetype") or ""),
      direction=str(data.get("direction") or "").upper(),
      session=str(data.get("session") or ""),
      htf_bias=str(data.get("htf_bias") or ""),
      regime=str(data.get("regime") or ""),
      entry_price=float(data["entry_price"]),
      invalidation_price=float(data["invalidation_price"]),
      stop_pips=float(data["stop_pips"]),
      planned_target_pips=float(data.get("planned_target_pips") or 0.0),
      planned_rr=float(data.get("planned_rr") or 0.0),
      exit_path=str(data.get("exit_path") or "unknown"),
      realized_r=float(data.get("realized_r") or 0.0),
      realized_pips=float(data.get("realized_pips") or 0.0),
      legs_filled=int(data.get("legs_filled") or 0),
      mfe_pips=float(data.get("mfe_pips") or 0.0),
      mae_pips=float(data.get("mae_pips") or 0.0),
      bars_held=int(data.get("bars_held") or 0),
      opened_at=int(data.get("opened_at") or 0),
      closed_at=int(data.get("closed_at") or 0),
      version=int(data.get("version") or 1),
      expected_stop_pips=None if expected is None else float(expected),
      realized_risk_pips=None if realized_risk is None else float(realized_risk),
      planned_vs_realized_stop_ratio=None if ratio is None else float(ratio),
      risk_denominator_source=str(data.get("risk_denominator_source") or "planned"),
      measured=dict(data.get("measured") or {}),
    )


@dataclass(frozen=True)
class ScalpLifecycleRecord:
  opportunity_id: str
  episode_id: str
  state: str
  context_id: str
  updated_at: int
  reason_code: str = ""
  measured: dict[str, Any] = field(default_factory=dict)

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ScalpLifecycleRecord:
    data = json.loads(raw)
    return cls(
      opportunity_id=str(data["opportunity_id"]),
      episode_id=str(data.get("episode_id") or ""),
      state=str(data["state"]),
      context_id=str(data.get("context_id") or ""),
      updated_at=int(data["updated_at"]),
      reason_code=str(data.get("reason_code") or ""),
      measured=dict(data.get("measured") or {}),
    )


def _opt_int(value: Any) -> int | None:
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _opt_float(value: Any) -> float | None:
  if value is None:
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None
