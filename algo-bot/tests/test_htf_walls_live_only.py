"""The worker consumes Go's live barrier book; it never rebuilds HTF walls.

The old test exercised the retired Python M15 reconstruction helper.  The
production contract is now the Redis document published by Analysis Engine,
so this test protects the replacement boundary instead.
"""

from __future__ import annotations

import json

import pytest

from app.autotrade.go_zone_book import (
  go_zone_opposing_entries,
  opposing_entries_for_go_match,
)
from app.autotrade.structural_target_room import ZoneOpposingEntry

pytestmark = pytest.mark.no_database


def _book(*barriers: dict[str, object]) -> str:
  return json.dumps({
    "symbol": "XAU",
    "generated_at": 1790000000,
    "entries": [{"low": 4000.0, "high": 4002.0, "side": "supply"}],
    "barriers": list(barriers),
  })


class _Client:
  def __init__(self, value: str | None):
    self.value = value

  async def get(self, key: str):
    return self.value


@pytest.mark.asyncio
async def test_go_barrier_book_is_the_only_htf_wall_source():
  result = await opposing_entries_for_go_match(
    _Client(_book({"side": "sell", "low": 4131.0, "high": 4133.0})),
    "XAU",
  )
  assert result == (
    ZoneOpposingEntry(
      side="sell",
      lo=4131.0,
      hi=4133.0,
      tier="zone",
      score=0.0,
      touches=0,
      mitigated=False,
    ),
  )


@pytest.mark.asyncio
async def test_missing_go_barrier_book_does_not_trigger_python_reconstruction():
  assert await go_zone_opposing_entries(_Client(None), "XAU") is None
  assert await opposing_entries_for_go_match(_Client(None), "XAU") == ()


@pytest.mark.asyncio
async def test_go_empty_barrier_book_is_distinct_from_missing_book():
  empty = json.dumps({"symbol": "XAU", "generated_at": 1, "barriers": []})
  assert await go_zone_opposing_entries(_Client(empty), "XAU") == ()
