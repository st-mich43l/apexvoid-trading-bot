"""The worker consumes Go's live barrier book; it never rebuilds HTF walls.

The old test exercised the retired Python M15 reconstruction helper.  The
production contract is now the Redis document published by Analysis Engine,
so this test protects the replacement boundary instead.
"""

from __future__ import annotations

import json
import time

import pytest

from app.autotrade.go_zone_book import ZoneBookState, read_go_zone_book
from app.autotrade.structural_target_room import ZoneOpposingEntry

pytestmark = pytest.mark.no_database


def _book(*barriers: dict[str, object]) -> str:
  return json.dumps({
    "symbol": "XAU",
    "generated_at": int(time.time()),
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
  read = await read_go_zone_book(_Client(_book({"side": "sell", "low": 4131.0, "high": 4133.0})), "XAU")
  assert read.state is ZoneBookState.AVAILABLE_WITH_BARRIERS
  assert read.entries == (
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
async def test_missing_go_barrier_book_does_not_trigger_python_reconstruction_and_is_not_an_empty_book():
  read = await read_go_zone_book(_Client(None), "XAU")
  assert read.state is ZoneBookState.UNAVAILABLE and read.reason == "missing"
  assert read.entries == ()


@pytest.mark.asyncio
async def test_go_empty_barrier_book_is_distinct_from_missing_book():
  empty = json.dumps({"symbol": "XAU", "generated_at": int(time.time()), "barriers": []})
  read = await read_go_zone_book(_Client(empty), "XAU")
  assert read.state is ZoneBookState.AVAILABLE_EMPTY and read.available
