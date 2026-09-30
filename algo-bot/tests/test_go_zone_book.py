"""go_zone_book.py: adapts the barrier book the Go analysis-engine publishes and
distinguishes "Go published nothing" from "Go published genuinely no opposing
structure" for the worker's execution-time opposing-barrier check.

Go owns the barrier rules (execution-width gate, M5/M15/H1 scope, merge,
cross-side reconciliation - analysis-engine/internal/barrier, parity-tested
there against the Python reference). Python only adapts the published list."""

from __future__ import annotations

import json

import pytest

from app.autotrade.go_zone_book import (
  go_zone_book_key,
  go_zone_opposing_entries,
  opposing_entries_for_go_match,
  parse_zone_book,
)
from app.autotrade.structural_target_room import ZoneOpposingEntry


def _barrier(side="sell", low=2020.0, high=2030.0, tier="zone", score=0.95, touches=1):
  return {
    "side": side, "low": low, "high": high, "tier": tier, "score": score,
    "touches": touches, "source_timeframes": ["M15", "H1"],
  }


def _doc(*barriers, entries=(), symbol="XAU", generated_at=1790000000):
  return json.dumps({
    "symbol": symbol,
    "generated_at": generated_at,
    "entries": list(entries),
    "barriers": list(barriers),
  })


def test_go_zone_book_key_matches_the_go_publisher_exactly():
  assert go_zone_book_key("xau") == "analysis:zone_book:XAU"


def test_parse_zone_book_adapts_the_published_barriers_as_is():
  parsed = parse_zone_book(_doc(
    _barrier(side="buy", low=1990.0, high=1995.0, score=0.6, touches=3),
    _barrier(side="sell"),
  ))
  assert parsed == (
    ZoneOpposingEntry(side="buy", lo=1990.0, hi=1995.0, tier="zone", score=0.6, touches=3, mitigated=False),
    ZoneOpposingEntry(side="sell", lo=2020.0, hi=2030.0, tier="zone", score=0.95, touches=1, mitigated=False),
  )


def test_parse_zone_book_never_rederives_barriers_from_the_raw_entries():
  # A live, in-width zone is present in `entries`, but Go published no barrier
  # for it: Go's list is the only source of truth.
  raw_entry = {
    "timeframe": "M15", "kind": "supply", "low": 2020.0, "high": 2025.0, "atr": 4.0,
    "strength": 0.8, "touch_count": 2, "state": "fresh",
  }
  assert parse_zone_book(_doc(entries=[raw_entry])) == ()


@pytest.mark.parametrize("payload", [
  {"symbol": "XAU", "generated_at": 1, "entries": []},
  {"symbol": "XAU", "generated_at": 1, "entries": [], "barriers": None},
])
def test_parse_zone_book_rejects_a_book_without_a_published_barrier_list(payload):
  with pytest.raises(ValueError):
    parse_zone_book(json.dumps(payload))


def test_parse_zone_book_ignores_barriers_with_an_unknown_side_or_inverted_band():
  parsed = parse_zone_book(_doc(
    _barrier(side="sideways"),
    _barrier(low=2030.0, high=2020.0),
    _barrier(side="buy", low=1990.0, high=1995.0),
  ))
  assert [(e.side, e.lo, e.hi) for e in parsed] == [("buy", 1990.0, 1995.0)]


def test_parse_zone_book_raises_on_malformed_json_the_caller_must_handle():
  with pytest.raises(Exception):
    parse_zone_book("not json")


class _FakeClient:
  def __init__(self, value):
    self._value = value

  async def get(self, key):
    return self._value


@pytest.mark.asyncio
async def test_go_zone_opposing_entries_returns_none_when_the_key_is_missing():
  assert await go_zone_opposing_entries(_FakeClient(None), "XAU") is None


@pytest.mark.asyncio
async def test_go_zone_opposing_entries_returns_none_on_malformed_payload_not_a_crash():
  assert await go_zone_opposing_entries(_FakeClient("{not json"), "XAU") is None


@pytest.mark.asyncio
async def test_go_zone_opposing_entries_returns_none_for_a_book_without_barriers():
  legacy = json.dumps({"symbol": "XAU", "generated_at": 1, "entries": []})
  assert await go_zone_opposing_entries(_FakeClient(legacy), "XAU") is None


@pytest.mark.asyncio
async def test_go_zone_opposing_entries_returns_empty_tuple_when_go_published_genuinely_nothing_opposing():
  result = await go_zone_opposing_entries(_FakeClient(_doc()), "XAU")
  assert result == ()  # distinct from None: Go DID publish, there is nothing


@pytest.mark.asyncio
async def test_go_zone_opposing_entries_survives_a_redis_error():
  class _Raises:
    async def get(self, key):
      raise ConnectionError("boom")
  assert await go_zone_opposing_entries(_Raises(), "XAU") is None


@pytest.mark.asyncio
async def test_opposing_entries_for_go_match_returns_go_s_barriers():
  result = await opposing_entries_for_go_match(_FakeClient(_doc(_barrier())), "XAU")
  assert result == (ZoneOpposingEntry(side="sell", lo=2020.0, hi=2030.0, tier="zone", score=0.95, touches=1, mitigated=False),)


@pytest.mark.asyncio
async def test_opposing_entries_for_go_match_continues_with_no_barriers_when_go_has_not_published():
  # No Python re-derivation: an unavailable Go book yields no barriers.
  assert await opposing_entries_for_go_match(_FakeClient(None), "XAU") == ()
