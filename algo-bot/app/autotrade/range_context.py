"""Versioned range contract shared by scanner, worker, status and execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from app.runtime.price_identity import price_token


RANGE_CONTEXT_VERSION = 1
ACTIVE_RANGE_STATES = {
  "provisional",
  "confirmed",
  "post_impulse",
  "breakout_pending",
}
# Two completed bars plus a short grace for producer freshness.
SCANNER_SOURCE_MAX_AGE_SECONDS = (2 * 5 * 60) + 60
PRIVATE_SOURCE_MAX_AGE_SECONDS = (2 * 60) + 30
SCANNER_SNAPSHOT_TTL_SECONDS = SCANNER_SOURCE_MAX_AGE_SECONDS
WORKER_SNAPSHOT_TTL_SECONDS = PRIVATE_SOURCE_MAX_AGE_SECONDS
_STATE_MAP = {
  "provisional_range": "provisional",
  "confirmed_range": "confirmed",
  "post_impulse_range": "post_impulse",
  "broken_range": "broken",
  "no_range": "no_range",
}


@dataclass(frozen=True)
class RangeBarrier:
  level: float
  low: float
  high: float
  touches: int = 0
  wick_rejections: int = 0
  accepted_closes: int = 0
  fallback: bool = False
  sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class RangeContext:
  version: int
  range_id: str
  symbol: str
  state: str
  source: str
  execution_timeframe: str
  context_timeframes: tuple[str, ...]
  lower: float
  upper: float
  equilibrium: float
  width_price: float
  width_pips: float
  width_atr: float
  lower_barrier: RangeBarrier
  upper_barrier: RangeBarrier
  supports: tuple[RangeBarrier, ...] = ()
  resistances: tuple[RangeBarrier, ...] = ()
  inside_close_count: int = 0
  outside_close_count: int = 0
  touch_count_lower: int = 0
  touch_count_upper: int = 0
  wick_rejections_lower: int = 0
  wick_rejections_upper: int = 0
  accepted_closes_lower: int = 0
  accepted_closes_upper: int = 0
  last_touch_lower_ts: int | None = None
  last_touch_upper_ts: int | None = None
  contraction_score: float = 0.0
  post_impulse: bool = False
  breakout_state: str | None = None
  invalidation_reason: str | None = None
  quality: float = 0.0
  generated_at: int = 0
  expires_at: int = 0
  # Stable ownership for one auction episode. ``generated_at`` is producer
  # freshness and advances on every observation; it must not define identity.
  episode_started_at: int = 0
  episode_last_seen_at: int = 0

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: object) -> RangeContext | None:
    if raw is None:
      return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
      payload = json.loads(text)
      lower_barrier = RangeBarrier(
        **_barrier_payload(payload["lower_barrier"])
      )
      upper_barrier = RangeBarrier(
        **_barrier_payload(payload["upper_barrier"])
      )
      result = cls(
        **{
          **payload,
          "context_timeframes": tuple(payload.get("context_timeframes", [])),
          "lower_barrier": lower_barrier,
          "upper_barrier": upper_barrier,
          "supports": tuple(
            RangeBarrier(**_barrier_payload(item))
            for item in payload.get("supports", [])
          ),
          "resistances": tuple(
            RangeBarrier(**_barrier_payload(item))
            for item in payload.get("resistances", [])
          ),
        }
      )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
      return None
    return result if result.valid else None

  @property
  def valid(self) -> bool:
    return (
      self.version == RANGE_CONTEXT_VERSION
      and self.state in {
        "no_range",
        "provisional",
        "confirmed",
        "post_impulse",
        "breakout_pending",
        "broken",
        "retired",
      }
      and math.isfinite(self.lower)
      and math.isfinite(self.upper)
      and self.lower > 0
      and self.upper > self.lower
      and self.expires_at >= self.generated_at
    )


def range_context_key(symbol: str) -> str:
  return f"auto_trade:range_context:{symbol.upper()}"


def range_context_source_key(symbol: str, source: str) -> str:
  return f"auto_trade:range_context:{source}:{symbol.upper()}"


def range_context_compare_key(symbol: str) -> str:
  return f"auto_trade:range_context_compare:{symbol.upper()}"


def source_max_age_seconds(source: str) -> int:
  if source == "scanner":
    return SCANNER_SOURCE_MAX_AGE_SECONDS
  return PRIVATE_SOURCE_MAX_AGE_SECONDS


def is_range_context_current(
  context: RangeContext | None,
  *,
  now: int,
  max_age_seconds: int,
) -> bool:
  if context is None or not context.valid:
    return False
  if context.generated_at > now:
    return False
  if context.expires_at < now:
    return False
  return (now - context.generated_at) <= max(1, int(max_age_seconds))


def _source_observation(
  context: RangeContext | None,
  *,
  now: int,
  max_age_seconds: int,
) -> dict[str, Any]:
  if context is None:
    return {"state": "absent"}
  age = max(0, now - int(context.generated_at))
  if is_range_context_current(
    context,
    now=now,
    max_age_seconds=max_age_seconds,
  ):
    return {
      "state": "current",
      "age_seconds": age,
      "range_id": context.range_id,
      "range_state": context.state,
      "lower": context.lower,
      "upper": context.upper,
      "quality": context.quality,
    }
  return {
    "state": "stale",
    "age_seconds": age,
    "range_id": context.range_id,
    "range_state": context.state,
    "lower": context.lower,
    "upper": context.upper,
    "quality": context.quality,
  }


@dataclass(frozen=True)
class RangeExecutionEligibility:
  symbol: str
  range_id: str | None
  source: str | None
  has_current_private_box: bool
  has_current_resolved_range: bool
  resolved_state: str | None
  regime: str | None
  box_decision_state: str
  eligible: bool
  reason_code: str
  checked_at: int


def evaluate_range_box_eligibility(
  *,
  symbol: str,
  decision: Any,
  private_context: RangeContext | None,
  resolved: RangeContext | None,
  regime_state: str | None,
  now: int,
  range_enabled: bool,
) -> RangeExecutionEligibility:
  box = getattr(decision, "box", None)
  decision_state = str(getattr(decision, "state", "") or "")
  private_current = is_range_context_current(
    private_context,
    now=now,
    max_age_seconds=PRIVATE_SOURCE_MAX_AGE_SECONDS,
  )
  resolved_current = is_range_context_current(
    resolved,
    now=now,
    max_age_seconds=max(
      SCANNER_SOURCE_MAX_AGE_SECONDS,
      PRIVATE_SOURCE_MAX_AGE_SECONDS,
    ),
  )
  base = RangeExecutionEligibility(
    symbol=symbol.upper(),
    range_id=None if resolved is None else resolved.range_id,
    source=None if resolved is None else resolved.source,
    has_current_private_box=private_current and box is not None,
    has_current_resolved_range=(
      resolved_current
      and resolved is not None
      and resolved.state in ACTIVE_RANGE_STATES
    ),
    resolved_state=None if resolved is None else resolved.state,
    regime=regime_state,
    box_decision_state=decision_state,
    eligible=False,
    reason_code="no_private_box",
    checked_at=now,
  )
  if not range_enabled:
    return replace(base, reason_code="range_disabled")
  if box is None or decision_state == "waiting_for_box":
    return replace(base, reason_code="no_private_box")
  if not private_current:
    return replace(base, reason_code="private_range_stale")
  if resolved is None or not resolved_current:
    return replace(base, reason_code="no_resolved_range")
  if resolved.state not in ACTIVE_RANGE_STATES:
    return replace(
      base,
      reason_code=(
        "range_retired"
        if resolved.state in {"broken", "retired"}
        else "range_not_active"
      ),
    )
  if private_context is None or not _compatible(private_context, resolved):
    return replace(base, reason_code="range_geometry_mismatch")
  if regime_state != "chop":
    return replace(base, reason_code="range_regime_not_chop")
  if decision_state == "waiting_rejection":
    return replace(base, reason_code="waiting_for_rejection")
  if decision_state != "candidate":
    if decision_state in {"waiting_for_touch", "waiting_touch"}:
      return replace(base, reason_code="waiting_for_touch")
    return replace(base, reason_code=decision_state or "waiting_for_touch")
  return replace(base, eligible=True, reason_code="candidate_ready")


def range_geometry_matches_match(
  context: RangeContext,
  *,
  range_id: str | None,
  range_low: float | None,
  range_high: float | None,
) -> bool:
  if range_low is None or range_high is None:
    return bool(range_id and context.range_id == range_id)
  return _geometry_compatible(
    context.lower,
    context.upper,
    float(range_low),
    float(range_high),
    atr=_context_atr(context),
  )


def continue_range_episode(
  previous: RangeContext | None,
  current: RangeContext | None,
  *,
  require_same_source: bool = True,
) -> RangeContext | None:
  """Carry identity only while the same active auction geometry survives."""
  if current is None:
    return None
  if (
    previous is None
    or previous.symbol != current.symbol
    or (require_same_source and previous.source != current.source)
    or previous.state not in ACTIVE_RANGE_STATES
    or current.state not in ACTIVE_RANGE_STATES
    or not _compatible(previous, current)
  ):
    return current
  return replace(
    current,
    range_id=previous.range_id,
    episode_started_at=(
      previous.episode_started_at or previous.generated_at
    ),
    episode_last_seen_at=current.generated_at,
  )


def scanner_range_context(
  *,
  symbol: str,
  timeframe: str,
  structure: Any,
  atr: float,
  pip_size: float,
  generated_at: int,
  ttl: int,
) -> RangeContext | None:
  scalp_range = getattr(structure, "scalp_range", None)
  if scalp_range is None or atr <= 0 or pip_size <= 0:
    return None
  barriers = list(getattr(structure, "scalp_barriers", []) or [])
  lower = _range_barrier(scalp_range.lower)
  upper = _range_barrier(scalp_range.upper)
  supports = tuple(
    _range_barrier(item)
    for item in barriers
    if getattr(item, "side", "") == "support"
  )
  resistances = tuple(
    _range_barrier(item)
    for item in barriers
    if getattr(item, "side", "") == "resistance"
  )
  state = _STATE_MAP.get(
    str(getattr(scalp_range, "state", "confirmed_range")),
    "confirmed",
  )
  return _build_context(
    symbol=symbol,
    state=state,
    source="scanner",
    execution_timeframe=timeframe,
    context_timeframes=(timeframe,),
    lower=lower,
    upper=upper,
    atr=atr,
    pip_size=pip_size,
    supports=supports,
    resistances=resistances,
    inside_close_count=int(getattr(scalp_range, "inside_closes", 0)),
    post_impulse=bool(getattr(scalp_range, "post_impulse", False)),
    quality=float(getattr(scalp_range, "quality", 0.0)),
    generated_at=generated_at,
    ttl=ttl,
  )
def private_range_context(
  *,
  symbol: str,
  decision: Any,
  atr: float,
  pip_size: float,
  generated_at: int,
  ttl: int,
) -> RangeContext | None:
  box = getattr(decision, "box", None)
  if box is None or atr <= 0 or pip_size <= 0:
    return None
  lower = _range_barrier(box.lower)
  upper = _range_barrier(box.upper)
  context = _build_context(
    symbol=symbol,
    state=(
      "broken" if getattr(decision, "state", "") == "box_broken"
      else "confirmed"
    ),
    source="private",
    execution_timeframe="M1",
    context_timeframes=("M1", "M5", "M15"),
    lower=lower,
    upper=upper,
    atr=atr,
    pip_size=pip_size,
    supports=(lower,),
    resistances=(upper,),
    inside_close_count=round(float(getattr(box, "inside_ratio", 0.0)) * 60),
    post_impulse=False,
    quality=(
      float(box.lower.score)
      + float(box.upper.score)
      + float(getattr(box, "inside_ratio", 0.0))
    ),
    generated_at=generated_at,
    ttl=ttl,
  )
  # The native detector already creates an immutable id for the first
  # observed box. Use it as the episode id, then let
  # ``continue_range_episode`` carry it across small rail changes.
  return replace(
    context,
    range_id=str(box.box_id),
    episode_started_at=int(
      getattr(box, "formation_start_ts", 0) or generated_at
    ),
    episode_last_seen_at=int(
      getattr(box, "formation_end_ts", 0) or generated_at
    ),
  )


def resolve_range_context(
  scanner: RangeContext | None,
  private: RangeContext | None,
  *,
  now: int,
) -> tuple[RangeContext | None, dict[str, Any]]:
  scanner_obs = _source_observation(
    scanner,
    now=now,
    max_age_seconds=SCANNER_SOURCE_MAX_AGE_SECONDS,
  )
  private_obs = _source_observation(
    private,
    now=now,
    max_age_seconds=PRIVATE_SOURCE_MAX_AGE_SECONDS,
  )
  current_scanner = (
    scanner if scanner_obs.get("state") == "current" else None
  )
  current_private = (
    private if private_obs.get("state") == "current" else None
  )
  base_compare = {
    "scanner": scanner_obs,
    "private": private_obs,
  }
  stale_excluded = (
    scanner_obs.get("state") == "stale"
    or private_obs.get("state") == "stale"
  )
  sources = [
    item for item in (current_scanner, current_private)
    if item is not None
  ]
  if not sources:
    return None, {
      **base_compare,
      "state": "no_range",
      "resolution": "none",
      "disagreement": False,
      "stale_source_excluded": stale_excluded,
    }
  if len(sources) == 1:
    selected = sources[0]
    return selected, {
      **base_compare,
      "state": selected.state,
      "resolution": selected.source,
      "disagreement": False,
      "stale_source_excluded": stale_excluded,
    }
  scanner_ctx, private_ctx = sources
  broken = [
    item for item in sources
    if item.state in {"broken", "retired"}
    and item.breakout_state == "accepted"
  ]
  if broken:
    selected = max(
      broken,
      key=lambda item: (item.generated_at, item.quality),
    )
    return selected, {
      **base_compare,
      "state": selected.state,
      "resolution": "accepted_structural_breakout",
      "disagreement": any(
        item.state in ACTIVE_RANGE_STATES for item in sources
      ),
      "reason": selected.invalidation_reason
      or "accepted_structural_breakout",
      "stale_source_excluded": stale_excluded,
    }
  compatible = _compatible(scanner_ctx, private_ctx)
  if compatible:
    lower = _merge_barrier(
      scanner_ctx.lower_barrier,
      private_ctx.lower_barrier,
    )
    upper = _merge_barrier(
      scanner_ctx.upper_barrier,
      private_ctx.upper_barrier,
    )
    priority = max(
      (scanner_ctx, private_ctx),
      key=lambda item: (
        _state_rank(item.state),
        item.quality,
        1 if item.source == "scanner" else 0,
      ),
    )
    atr = max(
      scanner_ctx.width_price / max(scanner_ctx.width_atr, 1e-9),
      private_ctx.width_price / max(private_ctx.width_atr, 1e-9),
    )
    pip_size = max(
      scanner_ctx.width_price / max(scanner_ctx.width_pips, 1e-9),
      private_ctx.width_price / max(private_ctx.width_pips, 1e-9),
    )
    merged = _build_context(
      symbol=priority.symbol,
      state=priority.state,
      source="merged",
      execution_timeframe="M1",
      context_timeframes=tuple(dict.fromkeys([
        *scanner_ctx.context_timeframes,
        *private_ctx.context_timeframes,
      ])),
      lower=lower,
      upper=upper,
      atr=atr,
      pip_size=pip_size,
      supports=tuple({
        item.level: item
        for item in [*scanner_ctx.supports, *private_ctx.supports]
      }.values()),
      resistances=tuple({
        item.level: item
        for item in [*scanner_ctx.resistances, *private_ctx.resistances]
      }.values()),
      inside_close_count=max(
        scanner_ctx.inside_close_count,
        private_ctx.inside_close_count,
      ),
      post_impulse=scanner_ctx.post_impulse or private_ctx.post_impulse,
      quality=scanner_ctx.quality + private_ctx.quality,
      generated_at=max(scanner_ctx.generated_at, private_ctx.generated_at),
      ttl=max(scanner_ctx.expires_at, private_ctx.expires_at) - now,
      episode_seed=priority.range_id,
    )
    merged = replace(
      merged,
      range_id=priority.range_id,
      episode_started_at=min(
        value for value in (
          scanner_ctx.episode_started_at or scanner_ctx.generated_at,
          private_ctx.episode_started_at or private_ctx.generated_at,
        )
        if value > 0
      ),
      episode_last_seen_at=max(
        scanner_ctx.generated_at,
        private_ctx.generated_at,
      ),
    )
    return merged, {
      **base_compare,
      "state": merged.state,
      "resolution": "merged",
      "disagreement": False,
      "stale_source_excluded": stale_excluded,
    }
  selected = max(
    sources,
    key=lambda item: (
      _state_rank(item.state),
      item.quality,
      1 if item.source == "scanner" else 0,
    ),
  )
  return selected, {
    **base_compare,
    "state": selected.state,
    "resolution": selected.source,
    "disagreement": True,
    "reason": "materially_incompatible_geometry",
    "stale_source_excluded": stale_excluded,
  }


async def persist_scanner_range_observation(
  client: Any,
  *,
  symbol: str,
  context: RangeContext | None,
) -> None:
  key = range_context_source_key(symbol, "scanner")
  if context is None:
    await client.delete(key)
    return
  await client.set(
    key,
    context.to_json(),
    ex=max(60, context.expires_at - context.generated_at),
  )


async def persist_private_range_observation(
  client: Any,
  *,
  symbol: str,
  context: RangeContext | None,
) -> None:
  key = range_context_source_key(symbol, "private")
  if context is None:
    await client.delete(key)
    return
  await client.set(
    key,
    context.to_json(),
    ex=max(60, context.expires_at - context.generated_at),
  )


async def persist_resolved_range(
  client: Any,
  *,
  symbol: str,
  resolved: RangeContext | None,
  comparison: dict[str, Any],
) -> None:
  ttl = 900
  if resolved is not None:
    ttl = max(60, resolved.expires_at - resolved.generated_at)
    await client.set(
      range_context_key(symbol),
      resolved.to_json(),
      ex=ttl,
    )
  else:
    await client.delete(range_context_key(symbol))
  await client.set(
    range_context_compare_key(symbol),
    json.dumps(comparison, separators=(",", ":"), sort_keys=True),
    ex=ttl,
  )


async def persist_range_resolution(
  client: Any,
  *,
  symbol: str,
  scanner: RangeContext | None,
  private: RangeContext | None,
  resolved: RangeContext | None,
  comparison: dict[str, Any],
) -> None:
  """Deprecated compatibility wrapper.

  Source keys are producer-owned. Callers must persist scanner/private
  observations through the dedicated helpers; this only writes resolved
  and comparison keys.
  """
  del scanner, private
  await persist_resolved_range(
    client,
    symbol=symbol,
    resolved=resolved,
    comparison=comparison,
  )

def _build_context(
  *,
  symbol: str,
  state: str,
  source: str,
  execution_timeframe: str,
  context_timeframes: tuple[str, ...],
  lower: RangeBarrier,
  upper: RangeBarrier,
  atr: float,
  pip_size: float,
  supports: tuple[RangeBarrier, ...],
  resistances: tuple[RangeBarrier, ...],
  inside_close_count: int,
  post_impulse: bool,
  quality: float,
  generated_at: int,
  ttl: int,
  episode_seed: str | None = None,
) -> RangeContext:
  width = upper.level - lower.level
  raw_id = (
    f"v{RANGE_CONTEXT_VERSION}|{symbol.upper()}|"
    f"{source}|{execution_timeframe.upper()}|"
    f"{episode_seed or generated_at}|"
    f"{price_token(lower.level, pip_size=pip_size)}|"
    f"{price_token(upper.level, pip_size=pip_size)}"
  )
  range_id = hashlib.sha256(raw_id.encode("ascii")).hexdigest()[:24]
  return RangeContext(
    version=RANGE_CONTEXT_VERSION,
    range_id=range_id,
    symbol=symbol.upper(),
    state=state,
    source=source,
    execution_timeframe=execution_timeframe,
    context_timeframes=context_timeframes,
    lower=lower.level,
    upper=upper.level,
    equilibrium=(lower.level + upper.level) / 2,
    width_price=width,
    width_pips=width / pip_size,
    width_atr=width / atr,
    lower_barrier=lower,
    upper_barrier=upper,
    supports=supports,
    resistances=resistances,
    inside_close_count=inside_close_count,
    touch_count_lower=lower.touches,
    touch_count_upper=upper.touches,
    wick_rejections_lower=lower.wick_rejections,
    wick_rejections_upper=upper.wick_rejections,
    accepted_closes_lower=lower.accepted_closes,
    accepted_closes_upper=upper.accepted_closes,
    contraction_score=max(0.0, quality / max(width / atr, 1e-9)),
    post_impulse=post_impulse,
    breakout_state="accepted" if state == "broken" else None,
    invalidation_reason="accepted_structural_breakout"
    if state == "broken" else None,
    quality=quality,
    generated_at=generated_at,
    expires_at=generated_at + max(60, ttl),
    episode_started_at=generated_at,
    episode_last_seen_at=generated_at,
  )


def _range_barrier(value: Any) -> RangeBarrier:
  return RangeBarrier(
    level=float(value.level),
    low=float(getattr(value, "low", value.level)),
    high=float(getattr(value, "high", value.level)),
    touches=int(getattr(value, "touches", 0)),
    wick_rejections=int(getattr(value, "wick_rejections", 0)),
    accepted_closes=int(getattr(value, "accepted_closes", 0)),
    fallback=bool(getattr(value, "fallback", False)),
    sources=tuple(str(item) for item in getattr(value, "sources", ())),
  )


def _barrier_payload(payload: dict[str, Any]) -> dict[str, Any]:
  return {
    **payload,
    "sources": tuple(str(item) for item in payload.get("sources", [])),
  }


def _merge_barrier(left: RangeBarrier, right: RangeBarrier) -> RangeBarrier:
  total = max(1, left.touches + right.touches)
  level = (
    left.level * max(1, left.touches)
    + right.level * max(1, right.touches)
  ) / max(2, max(1, left.touches) + max(1, right.touches))
  half_width = max(
    abs(left.high - left.low),
    abs(right.high - right.low),
    0.02,
  ) / 2
  return RangeBarrier(
    level=level,
    low=level - half_width,
    high=level + half_width,
    touches=total,
    wick_rejections=left.wick_rejections + right.wick_rejections,
    accepted_closes=max(left.accepted_closes, right.accepted_closes),
    fallback=left.fallback and right.fallback,
    sources=tuple(dict.fromkeys([*left.sources, *right.sources])),
  )


def _compatible(left: RangeContext, right: RangeContext) -> bool:
  atr_values = [
    value for value in (_context_atr(left), _context_atr(right))
    if value > 0
  ]
  return _geometry_compatible(
    left.lower,
    left.upper,
    right.lower,
    right.upper,
    atr=min(atr_values) if atr_values else 0.0,
  )


def _context_atr(context: RangeContext) -> float:
  if context.width_atr <= 0:
    return 0.0
  value = context.width_price / context.width_atr
  return value if math.isfinite(value) and value > 0 else 0.0


def _geometry_compatible(
  left_low: float,
  left_high: float,
  right_low: float,
  right_high: float,
  *,
  atr: float,
) -> bool:
  left_width = left_high - left_low
  right_width = right_high - right_low
  if min(left_width, right_width) <= 0:
    return False
  overlap = min(left_high, right_high) - max(left_low, right_low)
  overlap_ratio = max(0.0, overlap) / min(left_width, right_width)
  width = min(left_width, right_width)
  atr_tolerance = atr * 0.35 if atr > 0 else width * 0.10
  edge_tolerance = min(width * 0.15, max(0.10, atr_tolerance))
  return (
    overlap_ratio >= 0.80
    and abs(left_low - right_low) <= edge_tolerance
    and abs(left_high - right_high) <= edge_tolerance
  )


def _state_rank(state: str) -> int:
  return {
    "confirmed": 5,
    "post_impulse": 4,
    "provisional": 3,
    "breakout_pending": 2,
    "broken": 1,
    "retired": 0,
    "no_range": 0,
  }.get(state, 0)


def _summary(context: RangeContext) -> dict[str, Any]:
  return {
    "range_id": context.range_id,
    "state": context.state,
    "source": context.source,
    "lower": context.lower,
    "upper": context.upper,
    "quality": context.quality,
  }
