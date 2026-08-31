"""Persisted quote-in-zone execution handoff for TradePlan V8 setups.

The scanner owns setup confirmation. This module owns only the execution
timing that follows it: side-aware quote/zone evidence, retest state, and
family-specific M1 timing evidence. Distance is observation only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from typing import Any

from app.autotrade.strategy_taxonomy import is_m1_scalp_match, is_m1_scalp_strategy


IMMEDIATE_CONFIRMATION = "immediate_confirmation"
WAITING_RETEST = "waiting_retest"
IN_ZONE_WAITING_M1 = "in_zone_waiting_m1"
TRIGGER_READY = "trigger_ready"
TRIGGER_PRICE_LEFT_ZONE = "trigger_price_left_zone"
PUBLISHED = "published"
EXPIRED = "expired"
INVALIDATED = "invalidated"

EXECUTION_CONFIRMATION_PHASES = frozenset({
  IMMEDIATE_CONFIRMATION,
  WAITING_RETEST,
  IN_ZONE_WAITING_M1,
  TRIGGER_READY,
  TRIGGER_PRICE_LEFT_ZONE,
  PUBLISHED,
  EXPIRED,
  INVALIDATED,
})

M5_AUTHORITATIVE = "m5_authoritative"
M1_RETEST = "m1_retest"

_REACTION_STRATEGIES = frozenset({
  "Key Level Reaction",
  "Session Level Reaction",
  "Trendline Reaction",
  "Mapped Zone Reaction",
  "Liquidity Sweep",
  "Snap-Back",
  # Fade Scalp's family (range_reversion) is shared with Range Edge Scalp,
  # whose hard M1 requirement is intentional (no separate M5 reaction
  # exists for that setup) - registered here individually, by strategy
  # name, so it gets the M1-optional treatment without touching Range Edge
  # Scalp's family-level classification.
  "Fade Scalp",
})
_CONTINUATION_STRATEGIES = frozenset({
  "Momentum Ride",
  "Breakout Continuation",
})
_M1_SCALP_TRIGGERS = frozenset({
  "sweep_reclaim",
  "impulse_pullback",
  "breakout_retest",
  "range_sweep",
})
# Product Reaction taxonomy is only Key/Session/Trendline (see
# strategy_taxonomy.REACTION_STRATEGIES). Confirmation mechanics below are
# M5-authoritative / M1-optional — not product "Reaction" naming.
_M5_AUTHORITATIVE_REACTION_FAMILIES = frozenset({
  "key_level",
  "session_level",
  "trendline",
  "mapped_zone_reaction",
  "liquidity_reversal",
  "trend_pullback",
})
# Zone setups (family supply_demand) share the same confirmation timing
# contract but must not live under a Reaction-named set.
_ZONE_CONFIRMATION_FAMILIES = frozenset({
  "supply_demand",
})
_M5_AUTHORITATIVE_FAMILIES = (
  _M5_AUTHORITATIVE_REACTION_FAMILIES | _ZONE_CONFIRMATION_FAMILIES
)
_AUTHORITATIVE_REACTIONS = frozenset({
  "rejection_choch",
  "sweep_reclaim",
  "strong_reclaim",
  "wick_rejection",
  # Mapped-zone matches created before first-class detector confirmation
  # names used these two equivalent values.
  "rejection",
  "reclaim",
  # evaluate_structural_reaction's lowest-priority pattern (checked only
  # after every stricter one fails to match) - omitted here originally,
  # which meant any live reaction detector whose confirmation happened to
  # resolve as an engulfing candle got silently auto-invalidated as
  # "confirmation_metadata_missing" despite being a real, shared-path
  # confirmation.
  "engulfing",
  # Configured M1 trigger patterns (analysis.triggers.m1.patterns). Live
  # 2026-08-06 rejected body_close as confirmation_metadata_missing and
  # blocked algo TradePlan build even when touch/confirmation/zone ids
  # were present.
  "body_close",
  "strong_close",
  "pin_bar",
  "hammer",
})


def _is_m1_scalp_confirmation_match(match: Any) -> bool:
  return is_m1_scalp_match(match)


@dataclass(frozen=True)
class ExecutionConfirmation:
  source: str
  pattern: str | None
  bar_ts: int
  wick_extreme: float | None
  zone_episode_id: str
  message: str


@dataclass(frozen=True)
class ConfirmationPolicy:
  m5_authoritative: bool
  m1_required_on_retest: bool
  allow_same_cycle_publish: bool
  require_quote_inside_zone: bool
  reaction_family: bool
  zone_family: bool
  metadata_valid: bool
  reason_code: str

  @property
  def m5_authoritative_contract(self) -> bool:
    """True for Reaction or Zone confirmation timing (not product taxonomy)."""
    return self.reaction_family or self.zone_family


@dataclass(frozen=True)
class ExecutableZoneEvidence:
  executable_quote: float | None
  quote_side: str
  inside: bool
  distance_to_zone: float | None
  distance_pips: float | None
  tolerance_price: float


@dataclass(frozen=True)
class ExecutionConfirmationState:
  setup_id: str
  phase: str
  episode_id: str | None
  zone_entered_at: int | None
  zone_exited_at: int | None
  last_inside_at: int | None
  last_evaluated_m1_ts: int | None
  trigger_bar_ts: int | None
  trigger_pattern: str | None
  trigger_source: str | None
  trigger_consumed: bool
  updated_at: int

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: Any) -> "ExecutionConfirmationState | None":
    if raw is None:
      return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
      data = json.loads(text)
      state = cls(
        setup_id=str(data["setup_id"]),
        phase=str(data["phase"]),
        episode_id=(
          None if data.get("episode_id") is None else str(data["episode_id"])
        ),
        zone_entered_at=_optional_int(data.get("zone_entered_at")),
        zone_exited_at=_optional_int(data.get("zone_exited_at")),
        last_inside_at=_optional_int(data.get("last_inside_at")),
        last_evaluated_m1_ts=_optional_int(
          data.get("last_evaluated_m1_ts"),
        ),
        trigger_bar_ts=_optional_int(data.get("trigger_bar_ts")),
        trigger_pattern=(
          None
          if data.get("trigger_pattern") is None
          else str(data["trigger_pattern"])
        ),
        trigger_source=(
          None
          if data.get("trigger_source") is None
          else str(data["trigger_source"])
        ),
        trigger_consumed=bool(data.get("trigger_consumed", False)),
        updated_at=int(data["updated_at"]),
      )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
      return None
    if state.phase not in EXECUTION_CONFIRMATION_PHASES:
      return None
    return state


def _optional_int(value: Any) -> int | None:
  return None if value is None else int(value)


def execution_confirmation_key(setup_id: str) -> str:
  return f"auto_trade:execution_confirmation:{setup_id}"


async def load_execution_confirmation(
  client: Any,
  setup_id: str,
) -> ExecutionConfirmationState | None:
  return ExecutionConfirmationState.from_json(
    await client.get(execution_confirmation_key(setup_id)),
  )


async def save_execution_confirmation(
  client: Any,
  state: ExecutionConfirmationState,
  *,
  expires_at: int | None,
) -> None:
  now = int(time.time())
  ttl = max(
    86400,
    0 if expires_at is None else int(expires_at) - now,
  )
  await client.set(
    execution_confirmation_key(state.setup_id),
    state.to_json(),
    ex=ttl,
  )


def parse_bar_timestamp(value: Any) -> int | None:
  """Normalise pandas/ISO/numeric timestamps to epoch seconds."""
  if value is None:
    return None
  timestamp_method = getattr(value, "timestamp", None)
  if callable(timestamp_method):
    try:
      result = float(timestamp_method())
      return int(result) if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
      pass
  try:
    numeric = float(value)
    if math.isfinite(numeric):
      absolute = abs(numeric)
      if absolute >= 1e18:
        numeric /= 1e9
      elif absolute >= 1e15:
        numeric /= 1e6
      elif absolute >= 1e12:
        numeric /= 1e3
      return int(numeric)
  except (TypeError, ValueError, OverflowError):
    pass
  try:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
      parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())
  except (TypeError, ValueError, OverflowError):
    return None


def _m5_reaction_type(match: Any) -> str:
  return str(
    getattr(match, "m5_reaction_type", None)
    or getattr(match, "reaction_type", None)
    or ""
  ).casefold()


def _m5_confirmation_bar_ts(match: Any) -> Any:
  return (
    getattr(match, "m5_confirmation_bar_ts", None)
    or getattr(match, "confirmation_bar_ts", None)
  )


def confirmation_policy_for(match: Any) -> ConfirmationPolicy:
  strategy = str(getattr(match, "strategy", "") or "")
  family = str(getattr(match, "family", "") or "").casefold()
  if _is_m1_scalp_confirmation_match(match):
    # M1 scalp activation already ran closed-bar M1 gates before publish.
    # Without a scalp confirmation policy the worker classified family=scalp
    # as non_reaction_m1_required and the TradePlan never formed.
    reaction_type = _m5_reaction_type(match)
    entry_low = _finite_float(getattr(match, "entry_low", None))
    entry_high = _finite_float(getattr(match, "entry_high", None))
    metadata_valid = bool(
      (
        reaction_type in _M1_SCALP_TRIGGERS
        or reaction_type in _AUTHORITATIVE_REACTIONS
      )
      and parse_bar_timestamp(getattr(match, "touch_bar_ts", None)) is not None
      and parse_bar_timestamp(_m5_confirmation_bar_ts(match)) is not None
      and str(getattr(match, "structural_zone_id", "") or "").strip()
      and entry_low is not None
      and entry_high is not None
      and entry_low < entry_high
    )
    return ConfirmationPolicy(
      m5_authoritative=metadata_valid,
      m1_required_on_retest=False,
      allow_same_cycle_publish=metadata_valid,
      require_quote_inside_zone=True,
      reaction_family=False,
      zone_family=False,
      metadata_valid=metadata_valid,
      reason_code="m1_scalp_authoritative" if metadata_valid else "confirmation_metadata_missing",
    )
  if strategy in _CONTINUATION_STRATEGIES or family == "momentum_continuation":
    # Impulse/continuation is confirmed by the detector itself (strong body
    # break). Do not force the reversal-shaped zone-edge M1 gate.
    return ConfirmationPolicy(
      m5_authoritative=True,
      m1_required_on_retest=False,
      allow_same_cycle_publish=True,
      require_quote_inside_zone=False,
      reaction_family=False,
      zone_family=False,
      metadata_valid=True,
      reason_code="momentum_continuation",
    )
  zone_family = family in _ZONE_CONFIRMATION_FAMILIES
  reaction_family = (
    not zone_family
    and (
      strategy in _REACTION_STRATEGIES
      or family in _M5_AUTHORITATIVE_REACTION_FAMILIES
    )
  )
  m5_authoritative_family = zone_family or reaction_family or (
    strategy in _REACTION_STRATEGIES or family in _M5_AUTHORITATIVE_FAMILIES
  )
  if not m5_authoritative_family:
    return ConfirmationPolicy(
      m5_authoritative=False,
      m1_required_on_retest=False,
      allow_same_cycle_publish=False,
      require_quote_inside_zone=False,
      reaction_family=False,
      zone_family=False,
      metadata_valid=True,
      reason_code="non_reaction_m1_required",
    )

  reaction_type = _m5_reaction_type(match)
  entry_low = _finite_float(getattr(match, "entry_low", None))
  entry_high = _finite_float(getattr(match, "entry_high", None))
  metadata_valid = bool(
    reaction_type in _AUTHORITATIVE_REACTIONS
    and parse_bar_timestamp(getattr(match, "touch_bar_ts", None)) is not None
    and parse_bar_timestamp(_m5_confirmation_bar_ts(match)) is not None
    and str(getattr(match, "structural_zone_id", "") or "").strip()
    and entry_low is not None
    and entry_high is not None
    and entry_low < entry_high
  )
  return ConfirmationPolicy(
    m5_authoritative=metadata_valid,
    m1_required_on_retest=False,
    allow_same_cycle_publish=metadata_valid,
    require_quote_inside_zone=True,
    reaction_family=reaction_family,
    zone_family=zone_family,
    metadata_valid=metadata_valid,
    reason_code=(
      "m5_authoritative"
      if metadata_valid else "confirmation_metadata_missing"
    ),
  )


def _finite_float(value: Any) -> float | None:
  try:
    result = float(value)
  except (TypeError, ValueError):
    return None
  return result if math.isfinite(result) else None


def executable_quote_in_zone(
  direction: str,
  bid: Any,
  ask: Any,
  zone_low: float,
  zone_high: float,
  tolerance: float,
  *,
  pip_size: float,
) -> ExecutableZoneEvidence:
  """Evaluate raw entry bounds against ask for BUY and bid for SELL."""
  side = str(direction).upper()
  quote_side = "ask" if side == "BUY" else "bid"
  quote = _finite_float(ask if side == "BUY" else bid)
  low = float(zone_low)
  high = float(zone_high)
  tolerance_price = max(0.0, float(tolerance))
  if (
    side not in {"BUY", "SELL"}
    or quote is None
    or not all(math.isfinite(value) for value in (low, high, tolerance_price))
    or low >= high
  ):
    return ExecutableZoneEvidence(
      executable_quote=quote,
      quote_side=quote_side,
      inside=False,
      distance_to_zone=None,
      distance_pips=None,
      tolerance_price=tolerance_price,
    )
  distance = (
    low - quote
    if quote < low else quote - high
    if quote > high else 0.0
  )
  pip = float(pip_size)
  return ExecutableZoneEvidence(
    executable_quote=quote,
    quote_side=quote_side,
    inside=low - tolerance_price <= quote <= high + tolerance_price,
    distance_to_zone=distance,
    distance_pips=(
      distance / pip
      if pip > 0 and math.isfinite(pip) else None
    ),
    tolerance_price=tolerance_price,
  )


@dataclass(frozen=True)
class ScalpZoneAccess:
  """Inside-zone or trade-direction chase within ``maximum_chase_pips``."""

  evidence: ExecutableZoneEvidence
  status: str  # inside | chase | chase_missed | approach_wait | invalid
  chase_pips: float | None = None
  maximum_chase_pips: float = 0.0

  @property
  def executable(self) -> bool:
    return self.status in {"inside", "chase"}


ZONE_ACCESS_MOMENTUM_CHASE = "momentum_chase"
ZONE_ACCESS_RETEST_ONLY = "retest_only"


def scalp_maximum_chase_pips(cfg: Any | None = None) -> float:
  """Shared scalp chase budget (Range Edge / M1 scalping). Default 100."""
  if cfg is None:
    try:
      from app.core.config import runtime_config
      cfg = runtime_config
    except Exception:
      return 100.0
  scalping_cfg = getattr(getattr(cfg, "strategies", None), "scalping", None)
  act = getattr(scalping_cfg, "activation", None)
  try:
    value = float(getattr(act, "maximum_chase_pips", 100.0) or 100.0)
  except (TypeError, ValueError):
    return 100.0
  return max(0.0, value)


def scalp_maximum_chase_stop_fraction(cfg: Any | None = None) -> float:
  """Fraction of planned stop pips allowed as chase (default 0.15)."""
  if cfg is None:
    try:
      from app.core.config import runtime_config
      cfg = runtime_config
    except Exception:
      return 0.15
  scalping_cfg = getattr(getattr(cfg, "strategies", None), "scalping", None)
  act = getattr(scalping_cfg, "activation", None)
  try:
    value = float(getattr(act, "maximum_chase_stop_fraction", 0.15) or 0.15)
  except (TypeError, ValueError):
    return 0.15
  return max(0.0, value)


def scalp_effective_chase_pips(
  cfg: Any | None = None,
  *,
  stop_pips: float | None,
) -> float:
  """Effective chase cap: min(flat maximum_chase_pips, stop × fraction)."""
  flat = scalp_maximum_chase_pips(cfg)
  try:
    stop = None if stop_pips is None else float(stop_pips)
  except (TypeError, ValueError):
    stop = None
  if stop is None or not math.isfinite(stop) or stop <= 0:
    return flat
  frac = scalp_maximum_chase_stop_fraction(cfg)
  return min(flat, stop * frac)


def scalp_zone_access(
  direction: str,
  bid: Any,
  ask: Any,
  zone_low: float,
  zone_high: float,
  tolerance: float,
  *,
  pip_size: float,
  maximum_chase_pips: float | None = None,
  zone_access_mode: str = ZONE_ACCESS_MOMENTUM_CHASE,
) -> ScalpZoneAccess:
  """Allow scalp activation inside the zone or chasing momentum past it.

  ``momentum_chase`` (default): inside the zone, or past the far edge within
  the chase budget (Range Sweep / Range Edge / Impulse continuation).

  ``retest_only`` (Breakout Retest): executable only while quote sits inside
  the retest band — approach from either side waits; no break-without-retest
  chase (see docs/scalping/OWN_BREAKOUT_TECHNIQUE.md).
  """
  evidence = executable_quote_in_zone(
    direction, bid, ask, zone_low, zone_high, tolerance, pip_size=pip_size,
  )
  chase_cap = (
    float(maximum_chase_pips)
    if maximum_chase_pips is not None
    else scalp_maximum_chase_pips()
  )
  if evidence.inside:
    return ScalpZoneAccess(evidence, "inside", 0.0, chase_cap)
  quote = evidence.executable_quote
  pip = float(pip_size)
  if quote is None or pip <= 0 or not math.isfinite(pip):
    return ScalpZoneAccess(evidence, "invalid", None, chase_cap)
  side = str(direction).upper()
  if zone_access_mode == ZONE_ACCESS_RETEST_ONLY:
    low = float(min(zone_low, zone_high))
    high = float(max(zone_low, zone_high))
    offset_pips = (
      (low - quote) / pip
      if quote < low
      else (quote - high) / pip
      if quote > high
      else 0.0
    )
    return ScalpZoneAccess(evidence, "approach_wait", offset_pips, chase_cap)
  # Trade-direction past edge: BUY above high, SELL below low.
  if side == "BUY":
    past = quote - float(zone_high)
  elif side == "SELL":
    past = float(zone_low) - quote
  else:
    return ScalpZoneAccess(evidence, "invalid", None, chase_cap)
  past_pips = past / pip
  if past_pips > chase_cap + 1e-9:
    return ScalpZoneAccess(evidence, "chase_missed", past_pips, chase_cap)
  if past_pips > 0:
    return ScalpZoneAccess(evidence, "chase", past_pips, chase_cap)
  return ScalpZoneAccess(evidence, "approach_wait", past_pips, chase_cap)


def deterministic_episode_id(
  setup_id: str,
  direction: str,
  entry_low: float,
  entry_high: float,
  zone_entered_at: int,
) -> str:
  raw = "|".join((
    str(setup_id),
    str(direction).upper(),
    f"{float(entry_low):.8f}",
    f"{float(entry_high):.8f}",
    str(int(zone_entered_at)),
  ))
  return hashlib.sha256(raw.encode()).hexdigest()


def new_state(
  setup_id: str,
  phase: str,
  *,
  now: int,
  episode_id: str | None = None,
  zone_entered_at: int | None = None,
  zone_exited_at: int | None = None,
  last_inside_at: int | None = None,
  last_evaluated_m1_ts: int | None = None,
  trigger_bar_ts: int | None = None,
  trigger_pattern: str | None = None,
  trigger_source: str | None = None,
  trigger_consumed: bool = False,
) -> ExecutionConfirmationState:
  if phase not in EXECUTION_CONFIRMATION_PHASES:
    raise ValueError(f"unknown execution confirmation phase: {phase!r}")
  return ExecutionConfirmationState(
    setup_id=setup_id,
    phase=phase,
    episode_id=episode_id,
    zone_entered_at=zone_entered_at,
    zone_exited_at=zone_exited_at,
    last_inside_at=last_inside_at,
    last_evaluated_m1_ts=last_evaluated_m1_ts,
    trigger_bar_ts=trigger_bar_ts,
    trigger_pattern=trigger_pattern,
    trigger_source=trigger_source,
    trigger_consumed=trigger_consumed,
    updated_at=now,
  )
