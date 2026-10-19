"""Reads the opposing-barrier book the Go analysis-engine publishes (analysis-
engine/internal/transport/redis/zonebook.go, ``barriers``) for the worker's
execution-time opposing-barrier/target-room recheck (structural_target_room.py)
on a Go-origin match.

Go owns technical structure. It computes the barrier set - live zones only,
M5/M15/H1 scope, the execution-width gate, same-side merge and cross-side
reconciliation (analysis-engine/internal/barrier) - and this module only
adapts that published list into the ``ZoneOpposingEntry`` shape the shared
target-room check reads. Algo Bot never re-derives structure for a Go-origin
match: there is no Python fallback and no raw-zone normalization here.

The book has exactly three states, and a caller can never confuse them:

* ``AVAILABLE_WITH_BARRIERS`` - a valid, fresh book that lists one or more
  opposing barriers.
* ``AVAILABLE_EMPTY`` - a valid, fresh book that explicitly publishes
  ``barriers: []``: Go computed the set and found no eligible opposing structure.
* ``UNAVAILABLE`` - the book is missing, unreadable, stale, malformed, from the
  future, from an unsupported schema, for another symbol, not computed
  (``barriers`` absent or null) or contains any barrier this reader cannot
  trust. "Go has not told us" is not "there is nothing in the way": a caller
  must hold a new autonomous admission, never treat it as an empty market.

Parsing is strict. A barrier that cannot be validated (unknown side or tier,
non-finite or inverted band, non-numeric field) makes the WHOLE book
unavailable instead of being dropped, because dropping a barrier would silently
widen the room in front of an entry.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.autotrade.structural_target_room import ZoneOpposingEntry

log = logging.getLogger(__name__)

# Must match analysis-engine/internal/transport/redis/zonebook.go::ZoneBookKey
# exactly - a naming drift here silently makes this module read nothing.
_KEY_PREFIX = "analysis:zone_book"

# The engine republishes the book on every closed bar and gives the key a 20 minute TTL. Two M5 bars plus slack: a book older than
# this was published by an engine that has since stopped, restarted or fallen behind, and a plan must not be judged against it.
ZONE_BOOK_MAX_AGE_SECONDS = 600
# Clock skew tolerated between the engine host and this one.
ZONE_BOOK_MAX_FUTURE_SECONDS = 60
# The schema versions this reader understands. A document without ``schema_version`` is the original (version 1) layout.
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
_SIDES = frozenset({"buy", "sell"})
_TIERS = frozenset({"level", "zone", "major"})


class ZoneBookState(str, Enum):
  AVAILABLE_WITH_BARRIERS = "available_with_barriers"
  AVAILABLE_EMPTY = "available_empty"
  UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ZoneBookRead:
  """The outcome of reading one symbol's book: its state, the validated
  barriers (empty unless AVAILABLE_WITH_BARRIERS) and, when unavailable, a
  stable machine-readable ``reason``."""

  state: ZoneBookState
  entries: tuple[ZoneOpposingEntry, ...] = ()
  reason: str | None = None
  generated_at: int | None = None
  age_seconds: float | None = None

  @property
  def available(self) -> bool:
    return self.state is not ZoneBookState.UNAVAILABLE

  def telemetry(self) -> dict[str, Any]:
    return {
      "zone_book_state": self.state.value,
      "zone_book_reason": self.reason,
      "zone_book_generated_at": self.generated_at,
      "zone_book_age_seconds": None if self.age_seconds is None else round(self.age_seconds, 1),
      "zone_book_barriers": len(self.entries),
    }


def go_zone_book_key(symbol: str) -> str:
  return f"{_KEY_PREFIX}:{symbol.upper()}"


def _unavailable(reason: str, *, generated_at: int | None = None, age: float | None = None) -> ZoneBookRead:
  return ZoneBookRead(ZoneBookState.UNAVAILABLE, (), reason, generated_at, age)


def _finite_number(value: Any) -> float | None:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  number = float(value)
  return number if math.isfinite(number) else None


_MEMBER_NUMBERS = ("low", "high", "strength")
_MEMBER_INTEGERS = ("touches", "origin_time", "created_at", "last_touched_at")
_MEMBER_TEXT = ("id", "timeframe", "state")


def _parse_members(raw: Any) -> tuple[dict[str, Any], ...]:
  """The original zones behind a barrier, kept as published.

  Provenance is diagnostic: a member that cannot be validated is dropped on its own and NEVER makes the book unavailable or
  alters the barrier, because no decision reads it. Nothing is invented for a field the engine did not publish.
  """
  if not isinstance(raw, list):
    return ()
  members: list[dict[str, Any]] = []
  for item in raw:
    if not isinstance(item, dict):
      continue
    member: dict[str, Any] = {}
    for key in _MEMBER_TEXT:
      value = item.get(key)
      if isinstance(value, str) and value:
        member[key] = value
    for key in _MEMBER_NUMBERS:
      number = _finite_number(item.get(key))
      if number is not None:
        member[key] = number
    for key in _MEMBER_INTEGERS:
      value = item.get(key)
      if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        member[key] = value
    if "low" in member and "high" in member and member["high"] >= member["low"]:
      members.append(member)
  return tuple(members)


def parse_zone_book(raw: str | bytes, *, symbol: str, now: float | None = None) -> ZoneBookRead:
  """Pure, strict parse of one published document. Never raises: every defect
  is an UNAVAILABLE read with a reason."""
  if isinstance(raw, bytes):
    try:
      raw = raw.decode()
    except UnicodeDecodeError:
      return _unavailable("malformed_json")
  try:
    doc = json.loads(raw)
  except (TypeError, ValueError):
    return _unavailable("malformed_json")
  if not isinstance(doc, dict):
    return _unavailable("not_an_object")
  version = doc.get("schema_version", 1)
  if isinstance(version, bool) or not isinstance(version, int) or version not in SUPPORTED_SCHEMA_VERSIONS:
    return _unavailable("unsupported_schema")
  published_symbol = doc.get("symbol")
  if published_symbol is not None and str(published_symbol).upper() != symbol.upper():
    return _unavailable("symbol_mismatch")
  generated = doc.get("generated_at")
  if isinstance(generated, bool) or not isinstance(generated, int) or generated <= 0:
    return _unavailable("missing_generated_at")
  clock = time.time() if now is None else float(now)
  age = clock - generated
  if age < -ZONE_BOOK_MAX_FUTURE_SECONDS:
    return _unavailable("generated_in_the_future", generated_at=generated, age=age)
  if age > ZONE_BOOK_MAX_AGE_SECONDS:
    return _unavailable("stale", generated_at=generated, age=age)
  if "barriers" not in doc or doc["barriers"] is None:
    # An older publisher, or a symbol Go has no barrier policy for: "not computed" (zonebook.go ZoneBookDTO).
    return _unavailable("barriers_not_computed", generated_at=generated, age=age)
  published = doc["barriers"]
  if not isinstance(published, list):
    return _unavailable("barriers_not_a_list", generated_at=generated, age=age)
  entries: list[ZoneOpposingEntry] = []
  for index, item in enumerate(published):
    if not isinstance(item, dict):
      return _unavailable(f"invalid_barrier:{index}:not_an_object", generated_at=generated, age=age)
    side = item.get("side")
    if side not in _SIDES:
      return _unavailable(f"invalid_barrier:{index}:side", generated_at=generated, age=age)
    low, high = _finite_number(item.get("low")), _finite_number(item.get("high"))
    if low is None or high is None or high < low or low <= 0:
      return _unavailable(f"invalid_barrier:{index}:band", generated_at=generated, age=age)
    tier = item.get("tier", "zone")
    if tier is None:
      tier = "zone"
    if tier not in _TIERS:
      return _unavailable(f"invalid_barrier:{index}:tier", generated_at=generated, age=age)
    score = _finite_number(item.get("score", 0.0))
    touches = item.get("touches", 0)
    if score is None or isinstance(touches, bool) or not isinstance(touches, int) or touches < 0:
      return _unavailable(f"invalid_barrier:{index}:score_or_touches", generated_at=generated, age=age)
    entries.append(ZoneOpposingEntry(
      side=side, lo=low, hi=high, tier=tier, score=score, touches=touches,
      mitigated=False,  # Go only publishes live standing barriers
      members=_parse_members(item.get("members")),
    ))
  state = ZoneBookState.AVAILABLE_WITH_BARRIERS if entries else ZoneBookState.AVAILABLE_EMPTY
  return ZoneBookRead(state, tuple(entries), None, generated, age)


async def read_go_zone_book(client: Any, symbol: str, *, now: float | None = None) -> ZoneBookRead:
  """The Go-published opposing-structure book for symbol, classified.

  A Redis failure, a missing or expired key and every defect `parse_zone_book`
  detects come back UNAVAILABLE with a reason. Nothing here substitutes a
  Python-derived structure and nothing manufactures an empty book.
  """
  try:
    raw = await client.get(go_zone_book_key(symbol))
  except Exception as exc:  # noqa: BLE001 - a Redis outage must not crash the plan build, and must not read as "no barriers"
    log.warning("go zone book read failed symbol=%s error=%s", symbol, exc)
    return _unavailable("read_error")
  if not raw:
    return _unavailable("missing")
  read = parse_zone_book(raw, symbol=symbol, now=now)
  if not read.available:
    log.warning("go zone book unavailable symbol=%s reason=%s age=%s", symbol, read.reason, read.age_seconds)
  return read
