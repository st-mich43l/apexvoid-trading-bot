from __future__ import annotations

import asyncio

import pytest

from app.bot import telegram_actor as actor


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_actor_runs_higher_priority_before_price_jobs():
  await actor.stop_telegram_actor()
  actor.start_telegram_actor()
  order: list[str] = []
  started = asyncio.Event()

  async def blocker():
    started.set()
    await asyncio.sleep(0.05)
    order.append("lifecycle")
    return "ok"

  async def price():
    order.append("price")
    return None

  async def fill():
    order.append("fill")
    return "fill"

  try:
    first = asyncio.create_task(
      actor.submit(blocker, priority=actor.PRIORITY_LIFECYCLE)
    )
    await started.wait()
    price_task = asyncio.create_task(
      # Lowest priority: below PRIORITY_CARD, runs after PRIORITY_LIFECYCLE.
      actor.submit(price, priority=actor.PRIORITY_CARD + 1, droppable=True)
    )
    fill_task = asyncio.create_task(
      actor.submit(fill, priority=actor.PRIORITY_LIFECYCLE)
    )
    await asyncio.gather(first, price_task, fill_task)
    assert order[0] == "lifecycle"
    assert order.index("fill") < order.index("price")
  finally:
    await actor.stop_telegram_actor()


@pytest.mark.asyncio
async def test_droppable_jobs_skip_while_flood_paused():
  await actor.stop_telegram_actor()
  actor.start_telegram_actor()
  ran = []

  async def price():
    ran.append("price")
    return "nope"

  try:
    actor.note_flood(30)
    result = await actor.submit(
      price, priority=actor.PRIORITY_CARD + 1, droppable=True,
    )
    assert result is None
    assert ran == []
  finally:
    await actor.stop_telegram_actor()


@pytest.mark.asyncio
async def test_inline_submit_without_actor():
  await actor.stop_telegram_actor()

  async def ping():
    return 7

  assert await actor.submit(ping, priority=actor.PRIORITY_CARD) == 7


def test_quote_inside_cached_spot_zone():
  from app.autotrade.zone_execution_cutover import (
    _cache_spot_zone_bands,
    quote_inside_cached_spot_zone,
  )

  class Rec:
    def __init__(self, low, high):
      self.low = low
      self.high = high

  _cache_spot_zone_bands("XAU", [Rec(4330.0, 4335.0)])
  assert quote_inside_cached_spot_zone("XAU", 4332.0) is True
  assert quote_inside_cached_spot_zone("XAU", 4320.0) is False
