"""Versioned range contract shared by scanner, worker, status and execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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


def _barrier_payload(payload: dict[str, Any]) -> dict[str, Any]:
  return {
    **payload,
    "sources": tuple(str(item) for item in payload.get("sources", [])),
  }


