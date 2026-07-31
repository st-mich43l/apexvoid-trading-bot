"""Durable retained-zone state, isolated from execution setup lifecycle.

ZoneWatch owns long-lived structural zones and retest episodes.  A watched zone
is analysis state only: it must not create a StrategyMatch, setup lifecycle,
ready-stream event, or Telegram card until the zone is executable now.

All writes use revision-based Redis Lua compare-and-swap.  This prevents M1 and
M5 scanner tasks from losing touch counts, resurrecting invalid zones, or
regressing a zone episode through last-write-wins races.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence, TypeVar


DISCOVERED = "discovered"
WATCHING_RETEST = "watching_retest"
EVALUATING = "evaluating"
PUBLISHED_LOCKED = "published_locked"
CONSUMED = "consumed"
INVALIDATED = "invalidated"
EXPIRED = "expired"
# Backward-compat alias: pre-v3 code/tests used EXHAUSTED for terminal
# structural exhaustion. Mission names that state EXPIRED.
EXHAUSTED = EXPIRED

ZONE_WATCH_STATES = (
  DISCOVERED,
  WATCHING_RETEST,
  EVALUATING,
  PUBLISHED_LOCKED,
  CONSUMED,
  INVALIDATED,
  EXPIRED,
)
TERMINAL_ZONE_WATCH_STATES = frozenset({INVALIDATED, EXPIRED, CONSUMED})
# Duplicate-prevention lock while a TradePlan exists for the current episode.
# Not user-facing; not actively watchable for a new handoff.
LOCKED_ZONE_WATCH_STATES = frozenset({PUBLISHED_LOCKED})

_TRANSITIONS: dict[str, frozenset[str]] = {
  DISCOVERED: frozenset({WATCHING_RETEST, EVALUATING, INVALIDATED, EXPIRED}),
  WATCHING_RETEST: frozenset({
    EVALUATING, PUBLISHED_LOCKED, INVALIDATED, EXPIRED,
  }),
  EVALUATING: frozenset({
    WATCHING_RETEST, PUBLISHED_LOCKED, INVALIDATED, EXPIRED,
  }),
  PUBLISHED_LOCKED: frozenset({
    CONSUMED, WATCHING_RETEST, INVALIDATED, EXPIRED,
  }),
  CONSUMED: frozenset(),
  INVALIDATED: frozenset(),
  EXPIRED: frozenset(),
}

GRADE_A = "A"
GRADE_B = "B"
GRADE_C = "C"
ACTIVE_WATCHLIST_GRADES = frozenset({GRADE_A, GRADE_B})

ZONE_WATCH_VERSION = 3
ZONE_WATCH_RETENTION_SECONDS = 7 * 24 * 3600
# Touch count may downgrade confidence (A→B) but must never terminally
# consume or expire a structurally valid zone. Kept as a soft telemetry
# threshold only for callers that still want "many retests" signals.
_DOWNGRADE_AFTER_TOUCHES = 2
_MAX_CAS_RETRIES = 12

_CAS_SAVE_LUA = """
local raw = redis.call('GET', KEYS[1])
local expected = tonumber(ARGV[1])
if expected < 0 then
  if raw then return 0 end
else
  if not raw then return -1 end
  local current = cjson.decode(raw)
  local revision = tonumber(current['revision'] or 0)
  if revision ~= expected then return 0 end
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
return 1
"""


def zone_watch_key(zone_id: str) -> str:
  return f"analysis:zone_watch:{zone_id}"


class ZoneWatchError(ValueError):
  """Illegal zone operation or repeated compare-and-swap contention."""


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
  revision: int = 0
  inside: bool = False
  episode_id: str | None = None
  zone_entered_at: int | None = None
  zone_exited_at: int | None = None
  last_evaluated_m1_ts: int | None = None
  last_plan_id: str | None = None
  last_rearm_reason: str | None = None

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
      "revision": self.revision,
      "inside": self.inside,
      "episode_id": self.episode_id,
      "zone_entered_at": self.zone_entered_at,
      "zone_exited_at": self.zone_exited_at,
      "last_evaluated_m1_ts": self.last_evaluated_m1_ts,
      "last_plan_id": self.last_plan_id,
      "last_rearm_reason": self.last_rearm_reason,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ZoneWatch":
    now = int(time.time())
    raw_state = str(data.get("state") or DISCOVERED)
    # Migrate pre-v3 "exhausted" payloads onto the mission EXPIRED name.
    if raw_state == "exhausted":
      raw_state = EXPIRED
    return cls(
      version=int(data.get("version", ZONE_WATCH_VERSION)),
      zone_id=str(data["zone_id"]),
      symbol=str(data["symbol"]).upper(),
      direction=str(data["direction"]).upper(),
      low=float(data["low"]),
      high=float(data["high"]),
      width=float(data.get("width", float(data["high"]) - float(data["low"]))),
      source_timeframe=str(data.get("source_timeframe") or "").upper(),
      structural_sources=tuple(str(item) for item in data.get("structural_sources") or ()),
      confluence_tags=tuple(str(item) for item in data.get("confluence_tags") or ()),
      grade=str(data.get("grade") or GRADE_C).upper(),
      score=float(data.get("score") or 0.0),
      freshness=int(data.get("freshness") or 0),
      touch_count=int(data.get("touch_count") or 0),
      discovered_at=int(data.get("discovered_at", now)),
      last_confirmed_at=int(data.get("last_confirmed_at", now)),
      last_touch_at=_optional_int(data.get("last_touch_at")),
      invalidation_price=_optional_float(data.get("invalidation_price")),
      state=raw_state,
      market_map_id=str(data.get("market_map_id") or ""),
      structure_signature=str(data.get("structure_signature") or ""),
      updated_at=int(data.get("updated_at", now)),
      revision=int(data.get("revision") or 0),
      inside=bool(data.get("inside", False)),
      episode_id=(None if data.get("episode_id") is None else str(data["episode_id"])),
      zone_entered_at=_optional_int(data.get("zone_entered_at")),
      zone_exited_at=_optional_int(data.get("zone_exited_at")),
      last_evaluated_m1_ts=_optional_int(data.get("last_evaluated_m1_ts")),
      last_plan_id=(
        None if data.get("last_plan_id") is None else str(data["last_plan_id"])
      ),
      last_rearm_reason=(
        None
        if data.get("last_rearm_reason") is None
        else str(data["last_rearm_reason"])
      ),
    )


def _optional_int(value: Any) -> int | None:
  return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
  return None if value is None else float(value)


def _payload(record: ZoneWatch) -> str:
  return json.dumps(record.to_dict(), separators=(",", ":"), sort_keys=True)


async def load_zone_watch(client: Any, zone_id: str) -> ZoneWatch | None:
  raw = await client.get(zone_watch_key(zone_id))
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    return ZoneWatch.from_dict(json.loads(text))
  except (TypeError, ValueError, json.JSONDecodeError, KeyError):
    return None


async def _cas_save(
  client: Any,
  record: ZoneWatch,
  *,
  expected_revision: int,
) -> bool:
  try:
    result = await client.eval(
      _CAS_SAVE_LUA,
      1,
      zone_watch_key(record.zone_id),
      expected_revision,
      _payload(record),
      ZONE_WATCH_RETENTION_SECONDS,
    )
    return int(result) == 1
  except Exception:
    if not getattr(client, "_apexvoid_allow_non_atomic_test_fallback", False):
      raise
    # fakeredis has no Lua/EVAL - best-effort compare-and-set for tests only.
    current = await load_zone_watch(client, record.zone_id)
    if expected_revision < 0:
      if current is not None:
        return False
    else:
      if current is None or current.revision != expected_revision:
        return False
    await client.set(
      zone_watch_key(record.zone_id),
      _payload(record),
      ex=ZONE_WATCH_RETENTION_SECONDS,
    )
    return True


T = TypeVar("T")
Mutator = Callable[[ZoneWatch], tuple[ZoneWatch, T]]


async def _mutate(
  client: Any,
  zone_id: str,
  mutator: Mutator[T],
) -> tuple[ZoneWatch, T]:
  for _attempt in range(_MAX_CAS_RETRIES):
    current = await load_zone_watch(client, zone_id)
    if current is None:
      raise ZoneWatchError(f"unknown zone_id {zone_id!r}")
    updated, result = mutator(current)
    if updated == current:
      return current, result
    updated = replace(
      updated,
      version=ZONE_WATCH_VERSION,
      revision=current.revision + 1,
    )
    if await _cas_save(client, updated, expected_revision=current.revision):
      return updated, result
  raise ZoneWatchError(f"zone watch CAS contention exceeded for {zone_id!r}")


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
  """Create or refresh a stable retained zone without resetting its episode."""
  ts = int(now if now is not None else time.time())
  low_value = float(low)
  high_value = float(high)
  if not low_value < high_value:
    raise ZoneWatchError(f"invalid zone bounds {low_value}..{high_value}")
  key = zone_watch_key(zone_id)

  for _attempt in range(_MAX_CAS_RETRIES):
    existing = await load_zone_watch(client, zone_id)
    if existing is None:
      created = ZoneWatch(
        version=ZONE_WATCH_VERSION,
        zone_id=zone_id,
        symbol=symbol.upper(),
        direction=direction.upper(),
        low=low_value,
        high=high_value,
        width=high_value - low_value,
        source_timeframe=source_timeframe.upper(),
        structural_sources=tuple(sorted(set(structural_sources))),
        confluence_tags=tuple(sorted(set(confluence_tags))),
        grade=grade.upper(),
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
      if await _cas_save(client, created, expected_revision=-1):
        return created, True
      continue

    # Refresh discovery metadata and TTL while preserving state, touch count,
    # grade decay, and the current retest episode.  Terminal zones never
    # resurrect merely because a detector sees the old structure again.
    refreshed = replace(
      existing,
      low=low_value,
      high=high_value,
      width=high_value - low_value,
      source_timeframe=source_timeframe.upper() or existing.source_timeframe,
      structural_sources=tuple(sorted(set(structural_sources))),
      confluence_tags=tuple(sorted(set(confluence_tags))),
      score=float(score),
      last_confirmed_at=ts,
      market_map_id=market_map_id or existing.market_map_id,
      structure_signature=structure_signature or existing.structure_signature,
      updated_at=ts,
      version=ZONE_WATCH_VERSION,
      revision=existing.revision + 1,
    )
    if await _cas_save(client, refreshed, expected_revision=existing.revision):
      return refreshed, False
  raise ZoneWatchError(f"zone discovery CAS contention exceeded for {key!r}")


async def transition_zone_watch(
  client: Any,
  zone_id: str,
  new_state: str,
  *,
  reason_code: str = "",
  **field_updates: Any,
) -> tuple[ZoneWatch, bool]:
  if new_state not in ZONE_WATCH_STATES:
    raise ZoneWatchError(f"unknown zone watch state: {new_state!r}")

  def apply(record: ZoneWatch) -> tuple[ZoneWatch, bool]:
    if record.state == new_state:
      return record, False
    if new_state not in _TRANSITIONS.get(record.state, frozenset()):
      suffix = f" ({reason_code})" if reason_code else ""
      raise ZoneWatchError(
        f"illegal zone watch transition {record.state!r} -> {new_state!r} "
        f"for {zone_id!r}{suffix}"
      )
    return replace(
      record,
      state=new_state,
      updated_at=int(time.time()),
      **field_updates,
    ), True

  return await _mutate(client, zone_id, apply)


def grade_for_touch_count(
  touch_count: int,
  current_grade: str,
  *,
  htf_evidence: bool = False,
) -> tuple[str, bool]:
  """Confidence downgrade only — never a terminal exhaustion signal.

  ``htf_evidence`` is retained for call-site compatibility; touch count alone
  must not kill a structurally valid zone (mission §7). The second return
  value is always False.
  """
  del htf_evidence  # retained for API compatibility
  if touch_count >= _DOWNGRADE_AFTER_TOUCHES and current_grade == GRADE_A:
    return GRADE_B, False
  return current_grade, False


def _episode_id(zone_id: str, entered_at: int, touch_count: int) -> str:
  raw = f"{zone_id}|{int(entered_at)}|{int(touch_count)}"
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def record_zone_presence(
  client: Any,
  zone_id: str,
  *,
  inside: bool,
  now: int | None = None,
  htf_evidence: bool = False,
  decisive_break: bool = False,
) -> tuple[ZoneWatch, bool]:
  """Record outside/inside transitions; one visit increments one touch only.

  decisive_break marks an exit (inside -> outside) where price closed
  beyond the zone's far/invalidating edge rather than bouncing back out
  the near edge it approached from - that structurally invalidates the
  zone. A valid bounce never terminals a zone on touch count alone.
  """
  ts = int(now if now is not None else time.time())

  def apply(record: ZoneWatch) -> tuple[ZoneWatch, bool]:
    if record.state in TERMINAL_ZONE_WATCH_STATES:
      return record, False
    if record.state in LOCKED_ZONE_WATCH_STATES:
      # Handoff already published; presence updates wait for executor outcome.
      return record, False
    if inside:
      if record.inside:
        state = EVALUATING if record.state == WATCHING_RETEST else record.state
        return replace(record, state=state, updated_at=ts), False
      count = record.touch_count + 1
      next_grade, _never_exhaust = grade_for_touch_count(
        count,
        record.grade,
        htf_evidence=htf_evidence,
      )
      return replace(
        record,
        state=EVALUATING,
        grade=next_grade,
        touch_count=count,
        last_touch_at=ts,
        inside=True,
        episode_id=_episode_id(zone_id, ts, count),
        zone_entered_at=ts,
        zone_exited_at=None,
        last_evaluated_m1_ts=None,
        updated_at=ts,
      ), True
    if not record.inside:
      state = WATCHING_RETEST if record.state == DISCOVERED else record.state
      return replace(record, state=state, updated_at=ts), False
    # Closed-bar structural break → INVALIDATED. Live wick alone must not
    # reach here as decisive_break (callers own that evidence gate).
    if decisive_break and not htf_evidence:
      return replace(
        record,
        state=INVALIDATED,
        inside=False,
        zone_exited_at=ts,
        invalidation_price=record.invalidation_price,
        updated_at=ts,
      ), True
    return replace(
      record,
      state=WATCHING_RETEST,
      inside=False,
      zone_exited_at=ts,
      updated_at=ts,
    ), True

  return await _mutate(client, zone_id, apply)


async def record_zone_touch(
  client: Any,
  zone_id: str,
  *,
  now: int | None = None,
  htf_evidence: bool = False,
) -> ZoneWatch:
  """Compatibility API for an explicitly deduplicated retest touch.

  Touch count alone never terminals the zone — only grade may downgrade.
  """
  ts = int(now if now is not None else time.time())

  def apply(record: ZoneWatch) -> tuple[ZoneWatch, None]:
    if record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES:
      return record, None
    count = record.touch_count + 1
    grade, _never_exhaust = grade_for_touch_count(
      count,
      record.grade,
      htf_evidence=htf_evidence,
    )
    return replace(
      record,
      touch_count=count,
      grade=grade,
      last_touch_at=ts,
      updated_at=ts,
    ), None

  updated, _ = await _mutate(client, zone_id, apply)
  return updated


async def lock_zone_watch_published(
  client: Any,
  zone_id: str,
  *,
  plan_id: str,
  reason_code: str = "execution_handoff_created",
) -> ZoneWatch:
  """Successful TradePlan handoff → PUBLISHED_LOCKED (duplicate prevention)."""
  updated, _ = await transition_zone_watch(
    client,
    zone_id,
    PUBLISHED_LOCKED,
    reason_code=reason_code,
    last_plan_id=str(plan_id),
    last_rearm_reason=None,
  )
  return updated


async def consume_zone_watch(
  client: Any,
  zone_id: str,
  *,
  reason_code: str = "broker_fill",
  plan_id: str | None = None,
) -> ZoneWatch:
  """First confirmed broker fill consumes the thesis for this episode."""
  fields: dict[str, Any] = {}
  if plan_id is not None:
    fields["last_plan_id"] = str(plan_id)
  updated, _ = await transition_zone_watch(
    client,
    zone_id,
    CONSUMED,
    reason_code=reason_code,
    **fields,
  )
  return updated


async def rearm_zone_watch(
  client: Any,
  zone_id: str,
  *,
  reason_code: str,
  new_episode: bool = False,
  now: int | None = None,
) -> ZoneWatch:
  """PUBLISHED_LOCKED → WATCHING_RETEST when structure is still valid.

  Used for broker reject / plan expiry / no-fill / cancel-before-fill.
  Retains last_plan_id for audit. Optionally starts a new episode when
  price has exited and re-entered.
  """
  if not reason_code:
    raise ZoneWatchError("rearm_zone_watch requires a non-empty reason_code")
  ts = int(now if now is not None else time.time())

  def apply(record: ZoneWatch) -> tuple[ZoneWatch, bool]:
    if record.state == WATCHING_RETEST and record.last_rearm_reason == reason_code:
      return record, False
    if record.state != PUBLISHED_LOCKED:
      raise ZoneWatchError(
        f"illegal zone watch rearm from {record.state!r} for {zone_id!r} "
        f"({reason_code})"
      )
    episode = record.episode_id
    touch = record.touch_count
    entered = record.zone_entered_at
    if new_episode:
      touch = record.touch_count  # preserve count; episode identity rotates
      entered = ts
      episode = _episode_id(zone_id, ts, touch)
    return replace(
      record,
      state=WATCHING_RETEST,
      inside=False if new_episode else record.inside,
      episode_id=episode,
      zone_entered_at=entered,
      last_rearm_reason=reason_code,
      updated_at=ts,
    ), True

  updated, _ = await _mutate(client, zone_id, apply)
  return updated


async def apply_zone_watch_plan_outcome(
  client: Any,
  zone_id: str,
  *,
  outcome: str,
  reason_code: str = "",
  plan_id: str | None = None,
) -> ZoneWatch | None:
  """Map executor/broker outcomes onto ZoneWatch lock/consume/rearm.

  ``outcome`` is one of: fill | reject | expired | cancelled | no_fill.
  Returns None when the zone is missing or the outcome does not apply.
  """
  record = await load_zone_watch(client, zone_id)
  if record is None:
    return None
  normalized = str(outcome or "").strip().lower()
  if normalized in {"fill", "filled", "order_filled", "opened"}:
    if record.state != PUBLISHED_LOCKED:
      return record
    return await consume_zone_watch(
      client,
      zone_id,
      reason_code=reason_code or "broker_fill",
      plan_id=plan_id,
    )
  if normalized in {
    "reject",
    "rejected",
    "plan_rejected",
    "expired",
    "cancelled",
    "no_fill",
    "no-fill",
  }:
    if record.state != PUBLISHED_LOCKED:
      return record
    return await rearm_zone_watch(
      client,
      zone_id,
      reason_code=reason_code or f"plan_outcome_{normalized}",
      new_episode=False,
    )
  return record


async def mark_m1_evaluated(
  client: Any,
  zone_id: str,
  bar_ts: int,
) -> ZoneWatch:
  ts = int(bar_ts)

  def apply(record: ZoneWatch) -> tuple[ZoneWatch, None]:
    if (
      record.last_evaluated_m1_ts is not None
      and record.last_evaluated_m1_ts >= ts
    ):
      return record, None
    return replace(
      record,
      last_evaluated_m1_ts=ts,
      updated_at=int(time.time()),
    ), None

  updated, _ = await _mutate(client, zone_id, apply)
  return updated


async def list_active_zone_watches(
  client: Any,
  *,
  symbol: str | None = None,
) -> list[ZoneWatch]:
  records: list[ZoneWatch] = []
  async for raw_key in client.scan_iter(match="analysis:zone_watch:*"):
    key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
    zone_id = key.rsplit(":", 1)[-1]
    record = await load_zone_watch(client, zone_id)
    if record is None or not is_actively_watchable(record):
      continue
    if symbol is not None and record.symbol != symbol.upper():
      continue
    records.append(record)
  return sorted(records, key=lambda item: (-item.score, item.low, item.zone_id))


def is_actively_watchable(record: ZoneWatch) -> bool:
  return (
    record.grade in ACTIVE_WATCHLIST_GRADES
    and record.state not in TERMINAL_ZONE_WATCH_STATES
    and record.state not in LOCKED_ZONE_WATCH_STATES
  )
