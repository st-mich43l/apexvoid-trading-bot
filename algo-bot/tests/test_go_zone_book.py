"""go_zone_book.py: parses the Go analysis-engine's published zone book and
distinguishes "Go published nothing" from "Go published genuinely no
opposing structure" for the worker's execution-time opposing-barrier check."""

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


def _doc(*entries, symbol="XAU", generated_at=1790000000):
  return json.dumps({"symbol": symbol, "generated_at": generated_at, "entries": list(entries)})


def _entry(
  kind="supply", state="fresh", low=2020.0, high=2025.0,
  strength=0.8, touch_count=2, timeframe="M15", atr=4.0,
):
  return {
    "timeframe": timeframe,
    "kind": kind,
    "low": low,
    "high": high,
    "atr": atr,
    "strength": strength,
    "touch_count": touch_count,
    "state": state,
  }


def test_go_zone_book_key_matches_the_go_publisher_exactly():
  assert go_zone_book_key("xau") == "analysis:zone_book:XAU"


def test_parse_zone_book_converts_supply_and_demand_to_sell_buy_sides():
  parsed = parse_zone_book(_doc(_entry(kind="supply"), _entry(kind="demand", low=1990.0, high=1995.0)))
  assert parsed == (
    ZoneOpposingEntry(side="buy", lo=1990.0, hi=1995.0, tier="zone", score=0.8, touches=2, mitigated=False),
    ZoneOpposingEntry(side="sell", lo=2020.0, hi=2025.0, tier="zone", score=0.8, touches=2, mitigated=False),
  )


def test_parse_zone_book_drops_invalidated_and_mitigated_zones():
  parsed = parse_zone_book(_doc(
    _entry(state="invalidated"), _entry(state="mitigated"), _entry(state="fresh", low=2030.0, high=2035.0),
  ))
  assert len(parsed) == 1 and parsed[0].lo == 2030.0


def test_parse_zone_book_keeps_partially_mitigated_as_still_live():
  parsed = parse_zone_book(_doc(_entry(state="partially_mitigated")))
  assert len(parsed) == 1 and parsed[0].mitigated is False


def test_parse_zone_book_ignores_a_kind_it_does_not_recognize():
  parsed = parse_zone_book(_doc(_entry(kind="order_block")))
  assert parsed == ()


def test_parse_zone_book_drops_m1_and_oversized_fx_zones():
  parsed = parse_zone_book(
    _doc(
      _entry(timeframe="M1", low=1.1000, high=1.1005, atr=0.0004),
      _entry(timeframe="M5", low=1.1010, high=1.1040, atr=0.0010),
      _entry(timeframe="M15", low=1.1050, high=1.1065, atr=0.0010),
    ),
    pip_size=0.0001,
    max_width_atr=2.0,
    max_width_pips=100.0,
  )
  assert [(entry.lo, entry.hi) for entry in parsed] == [(1.105, 1.1065)]


def test_parse_zone_book_merges_same_side_and_reconciles_cross_side_noise():
  parsed = parse_zone_book(_doc(
    _entry(kind="supply", low=1.1000, high=1.1100, strength=0.9, atr=0.01),
    _entry(kind="supply", low=1.1080, high=1.1120, strength=0.7, atr=0.01),
    _entry(kind="demand", low=1.1050, high=1.1150, strength=0.4, atr=0.01),
    _entry(kind="demand", low=0.9000, high=0.9100, strength=0.8, atr=0.01),
    _entry(kind="supply", low=1.2000, high=1.2100, strength=0.8, atr=0.01),
  ))
  bands = [(entry.side, entry.lo, entry.hi) for entry in parsed]
  assert ("sell", 1.1, 1.112) in bands
  assert ("buy", 1.105, 1.115) not in bands
  assert len(parsed) == 3


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
async def test_opposing_entries_for_go_match_prefers_go_when_published():
  go_entry = ZoneOpposingEntry(side="sell", lo=1.0, hi=2.0)
  fallback = (ZoneOpposingEntry(side="buy", lo=3.0, hi=4.0),)
  result = await opposing_entries_for_go_match(
    _FakeClient(_doc(_entry())), "XAU", python_fallback=fallback,
  )
  assert result != fallback and result[0].side == "sell"


@pytest.mark.asyncio
async def test_opposing_entries_for_go_match_falls_back_to_python_when_go_has_not_published():
  fallback = (ZoneOpposingEntry(side="buy", lo=3.0, hi=4.0),)
  result = await opposing_entries_for_go_match(_FakeClient(None), "XAU", python_fallback=fallback)
  assert result == fallback


@pytest.mark.asyncio
async def test_opposing_entries_for_go_match_uses_go_s_genuinely_empty_result_not_the_fallback():
  fallback = (ZoneOpposingEntry(side="buy", lo=3.0, hi=4.0),)
  result = await opposing_entries_for_go_match(_FakeClient(_doc()), "XAU", python_fallback=fallback)
  assert result == ()  # Go published "nothing opposing" - that is the real answer, not the stale fallback
