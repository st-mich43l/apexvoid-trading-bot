"""ZoneWatch: the retained-zone domain, separate from the execution domain.

refactor/p0-direct-zone-signal-execution (mission section 2). A retained zone
is not an executable setup and must never be placed into an execution queue -
this module intentionally has no StrategyMatch/TradePlan concept at all. It
formalises "a strong structural zone worth watching for a retest" as its own
small, durable record, reusing `confluence_zone.confluence_zone_id` for
identity (stable across coordinate jitter, a new scanner bar, a new
confirmation timestamp, and tag-order differences) rather than inventing a
parallel identity scheme.

Storage: `analysis:zone_watch:{zone_id}` (mirrors setup_lifecycle.py's
`analysis:setup:{setup_id}` pattern: atomic compare-and-swap transitions via
the same style of Lua script, so two concurrent scanner cycles racing the
same zone can never both believe their stale read is still current).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time
from typing import Any, Mapping, Sequence


DISCOVERED = "discovered"
WATCHING_RETEST = "watching_retest"
EVALUATING = "evaluating"
INVALIDATED = "invalidated"
EXHAUSTED = "exhausted"

ZONE_WATCH_STATES = (
  DISCOVERED,
  WATCHING_RETEST,
  EVALUATING,
  INVALIDATED,
  EXHAUSTED,
)
TERMINAL_ZONE_WATCH_STATES = frozenset({INVALIDATED, EXHAUSTED})

_TRANSITIONS: dict[str, frozenset[str]] = {
  DISCOVERED: frozenset({WATCHING_RETEST, INVALIDATED, EXHAUSTED}),
  WATCHING_RETEST: frozenset({EVALUATING, INVALIDATED, EXHAUSTED}),
  EVALUATING: frozenset({WATCHING_RETEST, INVALIDATED, EXHAUSTED}),
  INVALIDATED: frozenset(),
  EXHAUSTED: frozenset(),
}

GRADE_A = "A"
GRADE_B = "B"
GRADE_C = "C"
ACTIVE_WATCHLIST_GRADES = frozenset({GRADE_A, GRADE_B})

ZONE_WATCH_VERSION = 1
ZONE_WATCH_RETENTION_SECONDS = 7 * 24 * 3600

# Section 14: zone quality degrades with repeated touches unless
# higher-timeframe evidence justifies keeping the higher grade.
_EXHAUST_AFTER_TOUCHES = 3
_DOWNGRADE_AFTER_TOUCHES = 2


def zone_watch_key(zone_id: str) -> str:
  return f"analysis:zone_watch:{zone_id}"


class ZoneWatchError(ValueError):
  """An illegal transition or an operation on an unknown zone_id."""


@dataclass(frozen=True)
class ZoneWatch:
  version: int
  zone_id: str
  symbol: str
  direction: str
  low: float
  high: float
  width: float
  source_timeframe: str
  structural_sources: tuple[str, ...]
  confluence_tags: tuple[str, ...]
  grade: str
  score: float
  freshness: int
  touch_count: int
  discovered_at: int
  last_confirmed_at: int
  last_touch_at: int | None
  invalidation_price: float | None
  state: str
  market_map_id: str
  structure_signature: str
  updated_at: int

  def to_dict(self) -> dict[str, Any]:
    return {
      "version": self.version,
      "zone_id": self.zone_id,
      "symbol": self.symbol,
      "direction": self.direction,
      "low": self.low,
      "high": self.high,
      "width": self.width,
      "source_timeframe": self.source_timeframe,
      "structural_sources": list(self.structural_sources),
      "confluence_tags": list(self.confluence_tags),
      "grade": self.grade,
      "score": self.score,
      "freshness": self.freshness,
      "touch_count": self.touch_count,
      "discovered_at": self.discovered_at,
      "last_confirmed_at": self.last_confirmed_at,
      "last_touch_at": self.last_touch_at,
      "invalidation_price": self.invalidation_price,
      "state": self.state,
      "market_map_id": self.market_map_id,
      "structure_signature": self.structure_signature,
      "updated_at": self.updated_at,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ZoneWatch":
    return cls(
      version=int(data.get("version", ZONE_WATCH_VERSION)),
      zone_id=str(data["zone_id"]),
      symbol=str(data["symbol"]).upper(),
      direction=str(data["direction"]).upper(),
      low=float(data["low"]),
      high=float(data["high"]),
      width=float(data["width"]),
      source_timeframe=str(data.get("source_timeframe") or ""),
      structural_sources=tuple(data.get("structural_sources") or ()),
      confluence_tags=tuple(data.get("confluence_tags") or ()),
      grade=str(data.get("grade") or GRADE_C),
      score=float(data.get("score") or 0.0),
      freshness=int(data.get("freshness") or 0),
      touch_count=int(data.get("touch_count") or 0),
      discovered_at=int(data.get("discovered_at", time.time())),
      last_confirmed_at=int(data.get("last_confirmed_at", time.time())),
      last_touch_at=(
        None if data.get("last_touch_at") is None
        else int(data["last_touch_at"])
      ),
      invalidation_price=(
        None if data.get("invalidation_price") is None
        else float(data["invalidation_price"])
      ),
      state=str(data["state"]),
      market_map_id=str(data.get("market_map_id") or ""),
      structure_signature=str(data.get("structure_signature") or ""),
      updated_at=int(data.get("updated_at", time.time())),
    )


def _ttl_for(record: ZoneWatch) -> int:
  if record.state in TERMINAL_ZONE_WATCH_STATES:
    return ZONE_WATCH_RETENTION_SECONDS
  return ZONE_WATCH_RETENTION_SECONDS


async def load_zone_watch(client: Any, zone_id: str) -> ZoneWatch | None:
  raw = await client.get(zone_watch_key(zone_id))
  if raw is None:
    return None
  data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
  return ZoneWatch.from_dict(data)


async def _save(client: Any, record: ZoneWatch) -> None:
  await client.set(
    zone_watch_key(record.zone_id),
    json.dumps(record.to_dict(), separators=(",", ":")),
    ex=_ttl_for(record),
  )


async def discover_zone_watch(
  client: Any,
  *,
  zone_id: str,
  symbol: str,
  direction: str,
  low: float,
  high: float,
  source_timeframe: str,
  structural_sources: Sequence[str],
  confluence_tags: Sequence[str],
  grade: str,
  score: float = 0.0,
  market_map_id: str = "",
  structure_signature: str = "",
  now: int | None = None,
) -> tuple[ZoneWatch, bool]:
  """Retain a newly-discovered strong zone, or return the existing record.

  Idempotent: the scanner runs every closed bar and will call this
  repeatedly for the same structural region (same zone_id via
  confluence_zone_id) - a repeat call never resets touch_count, grade decay,
  or state, it just returns the existing record unchanged.
  """
  existing = await load_zone_watch(client, zone_id)
  if existing is not None:
    return existing, False
  ts = int(now if now is not None else time.time())
  record = ZoneWatch(
    version=ZONE_WATCH_VERSION,
    zone_id=zone_id,
    symbol=symbol.upper(),
    direction=direction.upper(),
    low=float(low),
    high=float(high),
    width=max(0.0, float(high) - float(low)),
    source_timeframe=source_timeframe,
    structural_sources=tuple(structural_sources),
    confluence_tags=tuple(confluence_tags),
    grade=grade,
    score=float(score),
    freshness=0,
    touch_count=0,
    discovered_at=ts,
    last_confirmed_at=ts,
    last_touch_at=None,
    invalidation_price=None,
    state=DISCOVERED,
    market_map_id=market_map_id,
    structure_signature=structure_signature,
    updated_at=ts,
  )
  await _save(client, record)
  return record, True


async def transition_zone_watch(
  client: Any,
  zone_id: str,
  new_state: str,
  *,
  reason_code: str = "",
  **field_updates: Any,
) -> tuple[ZoneWatch, bool]:
  """Move a zone watch to ``new_state``. Returns (record, changed).

  Not a Lua-guarded CAS like setup_lifecycle.transition_setup - a ZoneWatch
  is a discovery/retention record, not an execution boundary, so a plain
  read-modify-write is an acceptable risk here (the execution-domain's own
  CAS guards are what actually protect exactly-once publication).
  """
  if new_state not in ZONE_WATCH_STATES:
    raise ZoneWatchError(f"unknown zone watch state: {new_state!r}")
  record = await load_zone_watch(client, zone_id)
  if record is None:
    raise ZoneWatchError(f"transition_zone_watch: unknown zone_id {zone_id!r}")
  if record.state == new_state:
    return record, False
  allowed = _TRANSITIONS.get(record.state, frozenset())
  if new_state not in allowed:
    raise ZoneWatchError(
      f"illegal zone watch transition {record.state!r} -> {new_state!r} "
      f"for {zone_id!r}" + (f" ({reason_code})" if reason_code else "")
    )
  updated = replace(
    record,
    state=new_state,
    updated_at=int(time.time()),
    **field_updates,
  )
  await _save(client, updated)
  return updated, True


def grade_for_touch_count(
  touch_count: int,
  current_grade: str,
  *,
  htf_evidence: bool = False,
) -> tuple[str, bool]:
  """Section 14 touch-based degradation. Returns (grade, exhausted).

  touch 0: fresh (grade unchanged)
  touch 1: valid (grade unchanged)
  touch 2: reduced grade (A -> B, B/C unchanged - already at/below B)
  touch 3+: exhausted, unless htf_evidence justifies keeping the grade
  """
  if touch_count >= _EXHAUST_AFTER_TOUCHES and not htf_evidence:
    return current_grade, True
  if touch_count >= _DOWNGRADE_AFTER_TOUCHES and current_grade == GRADE_A:
    return GRADE_B, False
  return current_grade, False


async def record_zone_touch(
  client: Any,
  zone_id: str,
  *,
  now: int | None = None,
  htf_evidence: bool = False,
) -> ZoneWatch:
  """Record one retest touch, applying section 14's grade-decay contract.

  A zone whose touch count crosses the exhaustion threshold (without
  qualifying HTF evidence) transitions straight to EXHAUSTED - it is
  dropped from the active watchlist, not silently kept at a stale grade.
  """
  record = await load_zone_watch(client, zone_id)
  if record is None:
    raise ZoneWatchError(f"record_zone_touch: unknown zone_id {zone_id!r}")
  ts = int(now if now is not None else time.time())
  new_touch_count = record.touch_count + 1
  new_grade, exhausted = grade_for_touch_count(
    new_touch_count, record.grade, htf_evidence=htf_evidence,
  )
  if exhausted and record.state not in TERMINAL_ZONE_WATCH_STATES:
    return (await transition_zone_watch(
      client, zone_id, EXHAUSTED,
      reason_code="retest_exhausted",
      touch_count=new_touch_count,
      last_touch_at=ts,
      grade=new_grade,
    ))[0]
  updated = replace(
    record,
    touch_count=new_touch_count,
    grade=new_grade,
    last_touch_at=ts,
    updated_at=ts,
  )
  await _save(client, updated)
  return updated


def is_actively_watchable(record: ZoneWatch) -> bool:
  """Only A/B grade, non-terminal zones belong on the active watchlist."""
  return (
    record.grade in ACTIVE_WATCHLIST_GRADES
    and record.state not in TERMINAL_ZONE_WATCH_STATES
  )
