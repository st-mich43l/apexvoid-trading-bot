"""go_zone_book.py: reads the barrier book the Go analysis-engine publishes and keeps three facts apart:
a book with barriers, a book that genuinely holds none, and a book that is not there to be trusted.

Go owns the barrier rules (execution-width gate, M5/M15/H1 scope, merge, cross-side reconciliation -
analysis-engine/internal/barrier). Python only validates and adapts the published list. The failure-injection
cases below are the safety contract: nothing that is missing, stale, malformed or untrustworthy may read as
"no opposing structure"."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.autotrade.go_zone_book import (
  ZONE_BOOK_MAX_AGE_SECONDS,
  ZONE_BOOK_MAX_FUTURE_SECONDS,
  ZoneBookState,
  go_zone_book_key,
  parse_zone_book,
  read_go_zone_book,
)
from app.autotrade.structural_target_room import ZoneOpposingEntry

pytestmark = pytest.mark.no_database

NOW = 1_790_000_000


def _barrier(side="sell", low=2020.0, high=2030.0, tier="zone", score=0.95, touches=1):
  return {
    "side": side, "low": low, "high": high, "tier": tier, "score": score,
    "touches": touches, "source_timeframes": ["M15", "H1"],
  }


def _doc(*barriers, entries=(), symbol="XAU", generated_at=NOW, **extra):
  doc = {"symbol": symbol, "generated_at": generated_at, "entries": list(entries), "barriers": list(barriers)}
  doc.update(extra)
  return json.dumps(doc)


def _read(raw, **kwargs):
  return parse_zone_book(raw, symbol="XAU", now=kwargs.pop("now", NOW), **kwargs)


def test_go_zone_book_key_matches_the_go_publisher_exactly():
  assert go_zone_book_key("xau") == "analysis:zone_book:XAU"


# --- the three states -------------------------------------------------------

def test_a_fresh_book_with_barriers_is_available_with_barriers_and_adapted_as_published():
  read = _read(_doc(_barrier(), _barrier(side="buy", low=1990.0, high=1995.0, tier="major", score=0.4, touches=0)))
  assert read.state is ZoneBookState.AVAILABLE_WITH_BARRIERS and read.reason is None and read.available
  assert read.entries == (
    ZoneOpposingEntry(side="sell", lo=2020.0, hi=2030.0, tier="zone", score=0.95, touches=1, mitigated=False),
    ZoneOpposingEntry(side="buy", lo=1990.0, hi=1995.0, tier="major", score=0.4, touches=0, mitigated=False),
  )
  assert read.generated_at == NOW and read.age_seconds == 0


def test_a_fresh_book_that_explicitly_lists_no_barriers_is_available_empty():
  read = _read(_doc())
  assert read.state is ZoneBookState.AVAILABLE_EMPTY and read.entries == () and read.available


def test_the_raw_entries_are_never_turned_into_barriers():
  raw_entry = {"timeframe": "M15", "kind": "supply", "low": 2020.0, "high": 2030.0, "atr": 4.0, "strength": 0.8, "touch_count": 2, "state": "fresh"}
  read = _read(_doc(entries=[raw_entry]))
  assert read.state is ZoneBookState.AVAILABLE_EMPTY and read.entries == ()


# --- every way a book can be unavailable ---------------------------------------

@pytest.mark.parametrize("raw,reason", [
  ("{not json", "malformed_json"),
  ("", "malformed_json"),
  (b"\xff\xfe\x00", "malformed_json"),
  ("[]", "not_an_object"),
  ("null", "not_an_object"),
  ("7", "not_an_object"),
  (json.dumps({"symbol": "XAU", "generated_at": NOW, "entries": []}), "barriers_not_computed"),
  (json.dumps({"symbol": "XAU", "generated_at": NOW, "entries": [], "barriers": None}), "barriers_not_computed"),
  (json.dumps({"symbol": "XAU", "generated_at": NOW, "barriers": {}}), "barriers_not_a_list"),
  (json.dumps({"symbol": "XAU", "generated_at": NOW, "barriers": "none"}), "barriers_not_a_list"),
  (json.dumps({"symbol": "XAU", "barriers": []}), "missing_generated_at"),
  (json.dumps({"symbol": "XAU", "generated_at": 0, "barriers": []}), "missing_generated_at"),
  (json.dumps({"symbol": "XAU", "generated_at": "1790000000", "barriers": []}), "missing_generated_at"),
  (json.dumps({"symbol": "XAU", "generated_at": True, "barriers": []}), "missing_generated_at"),
  (json.dumps({"symbol": "XAU", "generated_at": 1.5, "barriers": []}), "missing_generated_at"),
  (_doc(symbol="EURUSD"), "symbol_mismatch"),
  (_doc(schema_version=2), "unsupported_schema"),
  (_doc(schema_version="1"), "unsupported_schema"),
  (_doc(schema_version=True), "unsupported_schema"),
  (_doc(schema_version=0), "unsupported_schema"),
])
def test_an_untrustworthy_document_is_unavailable_with_a_stable_reason(raw, reason):
  read = _read(raw)
  assert read.state is ZoneBookState.UNAVAILABLE and not read.available
  assert read.reason == reason
  assert read.entries == (), "an unavailable book must carry no barriers at all"


def test_the_original_layout_without_a_schema_version_is_still_read():
  assert _read(_doc(_barrier())).state is ZoneBookState.AVAILABLE_WITH_BARRIERS
  assert _read(_doc(_barrier(), schema_version=1)).state is ZoneBookState.AVAILABLE_WITH_BARRIERS


@pytest.mark.parametrize("age,state", [
  (0, ZoneBookState.AVAILABLE_EMPTY),
  (ZONE_BOOK_MAX_AGE_SECONDS, ZoneBookState.AVAILABLE_EMPTY),
  (ZONE_BOOK_MAX_AGE_SECONDS + 1, ZoneBookState.UNAVAILABLE),
  (3 * 3600, ZoneBookState.UNAVAILABLE),
  (-ZONE_BOOK_MAX_FUTURE_SECONDS, ZoneBookState.AVAILABLE_EMPTY),
  (-ZONE_BOOK_MAX_FUTURE_SECONDS - 1, ZoneBookState.UNAVAILABLE),
])
def test_freshness_is_checked_against_the_engine_timestamp_not_the_redis_ttl(age, state):
  read = _read(_doc(generated_at=NOW - age))
  assert read.state is state
  if state is ZoneBookState.UNAVAILABLE:
    assert read.reason == ("stale" if age > 0 else "generated_in_the_future")
    assert read.generated_at == NOW - age and read.age_seconds == age


def test_a_stale_book_is_unavailable_even_when_it_still_lists_barriers_and_even_when_it_lists_none():
  assert _read(_doc(_barrier(), generated_at=NOW - 7200)).reason == "stale"
  assert _read(_doc(generated_at=NOW - 7200)).reason == "stale"


@pytest.mark.parametrize("bad,reason", [
  ({"side": "sideways"}, "side"),
  ({"side": None}, "side"),
  ({"side": "SELL"}, "side"),
  ({"low": 2030.0, "high": 2020.0}, "band"),
  ({"low": "abc"}, "band"),
  ({"low": None}, "band"),
  ({"high": None}, "band"),
  ({"low": True}, "band"),
  ({"low": 0.0, "high": 5.0}, "band"),
  ({"low": -4.0, "high": 5.0}, "band"),
  ({"tier": "weird"}, "tier"),
  ({"tier": "MAJOR"}, "tier"),
  ({"tier": 3}, "tier"),
  ({"score": "high"}, "score_or_touches"),
  ({"touches": -1}, "score_or_touches"),
  ({"touches": 1.5}, "score_or_touches"),
  ({"touches": True}, "score_or_touches"),
])
def test_one_barrier_the_reader_cannot_validate_makes_the_whole_book_unavailable(bad, reason):
  broken = dict(_barrier(), **bad)
  read = _read(_doc(_barrier(side="buy", low=1990.0, high=1995.0), broken))
  assert read.state is ZoneBookState.UNAVAILABLE
  assert read.reason == f"invalid_barrier:1:{reason}"
  assert read.entries == (), "a partly valid book is not a smaller book: dropping a barrier widens the room"


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_prices_are_rejected(literal):
  raw = '{"symbol": "XAU", "generated_at": %d, "barriers": [{"side": "sell", "low": %s, "high": 2030.0}]}' % (NOW, literal)
  read = _read(raw)
  assert read.state is ZoneBookState.UNAVAILABLE and read.reason == "invalid_barrier:0:band"


def test_a_barrier_that_is_not_an_object_is_rejected():
  read = _read(json.dumps({"symbol": "XAU", "generated_at": NOW, "barriers": [["sell", 1, 2]]}))
  assert read.reason == "invalid_barrier:0:not_an_object"


def test_a_missing_tier_defaults_to_zone_but_an_unknown_one_does_not():
  item = _barrier()
  del item["tier"]
  assert _read(_doc(item)).entries[0].tier == "zone"
  item["tier"] = None
  assert _read(_doc(item)).entries[0].tier == "zone"


def test_a_zero_width_band_is_a_valid_level():
  read = _read(_doc(_barrier(low=2020.0, high=2020.0, tier="level")))
  assert read.state is ZoneBookState.AVAILABLE_WITH_BARRIERS


# --- the Redis boundary ----------------------------------------------------

class _Client:
  def __init__(self, *values):
    self._values = list(values)
    self.reads = 0

  async def get(self, key):
    self.reads += 1
    value = self._values[min(self.reads - 1, len(self._values) - 1)]
    if isinstance(value, BaseException):
      raise value
    return value


@pytest.mark.asyncio
async def test_a_missing_or_expired_key_is_unavailable_not_empty():
  for value in (None, "", b""):
    read = await read_go_zone_book(_Client(value), "XAU", now=NOW)
    assert read.state is ZoneBookState.UNAVAILABLE and read.reason == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.TimeoutError(), ConnectionError("down"), OSError("broken pipe"), RuntimeError("boom")])
async def test_a_redis_failure_is_unavailable_never_a_crash_and_never_empty(error):
  read = await read_go_zone_book(_Client(error), "XAU", now=NOW)
  assert read.state is ZoneBookState.UNAVAILABLE and read.reason == "read_error" and read.entries == ()


@pytest.mark.asyncio
async def test_a_published_book_is_read_through_the_client():
  read = await read_go_zone_book(_Client(_doc(_barrier())), "XAU", now=NOW)
  assert read.state is ZoneBookState.AVAILABLE_WITH_BARRIERS
  empty = await read_go_zone_book(_Client(_doc().encode()), "XAU", now=NOW)
  assert empty.state is ZoneBookState.AVAILABLE_EMPTY


@pytest.mark.asyncio
async def test_a_stale_publication_is_not_resurrected_and_a_newer_one_replaces_it():
  """Restart / concurrent publishers: the reader judges the document it gets by its own timestamp. A stale book (an engine that
  stopped, or an old instance overwriting during a rollout) is unavailable until a fresh publish replaces it."""
  client = _Client(_doc(_barrier(), generated_at=NOW - 3600), _doc(_barrier(), generated_at=NOW - 5))
  first = await read_go_zone_book(client, "XAU", now=NOW)
  second = await read_go_zone_book(client, "XAU", now=NOW)
  assert first.state is ZoneBookState.UNAVAILABLE and first.reason == "stale"
  assert second.state is ZoneBookState.AVAILABLE_WITH_BARRIERS and second.age_seconds == 5


@pytest.mark.asyncio
async def test_a_book_written_before_the_engine_restarted_is_unavailable_once_it_ages_out():
  """After a restart the engine is silent until it finishes bootstrap; the old document sits in Redis (20 minute TTL) and ages."""
  doc = _doc(_barrier(), generated_at=NOW)
  assert (await read_go_zone_book(_Client(doc), "XAU", now=NOW + 120)).available
  assert (await read_go_zone_book(_Client(doc), "XAU", now=NOW + ZONE_BOOK_MAX_AGE_SECONDS + 1)).state is ZoneBookState.UNAVAILABLE


def test_telemetry_names_the_state_and_the_reason():
  read = _read(_doc(generated_at=NOW - 3600))
  telemetry = read.telemetry()
  assert telemetry["zone_book_state"] == "unavailable" and telemetry["zone_book_reason"] == "stale"
  assert telemetry["zone_book_age_seconds"] == 3600.0 and telemetry["zone_book_barriers"] == 0
  assert _read(_doc(_barrier())).telemetry()["zone_book_barriers"] == 1
