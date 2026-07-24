"""Versioned range contract shared by scanner, worker, status and execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any


RANGE_CONTEXT_VERSION = 1
ACTIVE_RANGE_STATES = {
  "provisional",
  "confirmed",
  "post_impulse",
  "breakout_pending",
}
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
  return _build_context(
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


def resolve_range_context(
  scanner: RangeContext | None,
  private: RangeContext | None,
  *,
  now: int,
) -> tuple[RangeContext | None, dict[str, Any]]:
  sources = [
    item for item in (scanner, private)
    if item is not None and item.expires_at >= now
  ]
  if not sources:
    return None, {
      "state": "no_range",
      "resolution": "none",
      "disagreement": False,
    }
  if len(sources) == 1:
    selected = sources[0]
    return selected, {
      "state": selected.state,
      "resolution": selected.source,
      "disagreement": False,
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
      "state": selected.state,
      "resolution": "accepted_structural_breakout",
      "disagreement": any(
        item.state in ACTIVE_RANGE_STATES for item in sources
      ),
      "reason": selected.invalidation_reason
      or "accepted_structural_breakout",
      "scanner": _summary(scanner_ctx),
      "private": _summary(private_ctx),
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
    )
    return merged, {
      "state": merged.state,
      "resolution": "merged",
      "disagreement": False,
      "scanner": _summary(scanner_ctx),
      "private": _summary(private_ctx),
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
    "state": selected.state,
    "resolution": selected.source,
    "disagreement": True,
    "reason": "materially_incompatible_geometry",
    "scanner": _summary(scanner_ctx),
    "private": _summary(private_ctx),
  }


async def persist_range_resolution(
  client: Any,
  *,
  symbol: str,
  scanner: RangeContext | None,
  private: RangeContext | None,
  resolved: RangeContext | None,
  comparison: dict[str, Any],
) -> None:
  pipe = client.pipeline()
  ttl = 900
  if scanner is not None:
    pipe.set(
      range_context_source_key(symbol, "scanner"),
      scanner.to_json(),
      ex=max(60, scanner.expires_at - scanner.generated_at),
    )
  if private is not None:
    pipe.set(
      range_context_source_key(symbol, "private"),
      private.to_json(),
      ex=max(60, private.expires_at - private.generated_at),
    )
  if resolved is not None:
    ttl = max(60, resolved.expires_at - resolved.generated_at)
    pipe.set(range_context_key(symbol), resolved.to_json(), ex=ttl)
  else:
    pipe.delete(range_context_key(symbol))
  pipe.set(
    range_context_compare_key(symbol),
    json.dumps(comparison, separators=(",", ":"), sort_keys=True),
    ex=ttl,
  )
  await pipe.execute()


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
) -> RangeContext:
  width = upper.level - lower.level
  raw_id = (
    f"v{RANGE_CONTEXT_VERSION}|{symbol.upper()}|"
    f"{lower.level:.2f}|{upper.level:.2f}"
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
  width = min(left.width_price, right.width_price)
  if width <= 0:
    return False
  tolerance = max(0.5, width * 0.25)
  return (
    abs(left.lower - right.lower) <= tolerance
    and abs(left.upper - right.upper) <= tolerance
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
