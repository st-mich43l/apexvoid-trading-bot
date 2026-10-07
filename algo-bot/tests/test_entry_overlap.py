"""Cross-strategy same-direction entry-zone overlap guard (Go-origin plans)."""

from __future__ import annotations

import asyncio
import os

import fakeredis
import pytest
from redis.asyncio import Redis

from app.autotrade.entry_overlap import (
  PAD_ATR,
  WINDOW_SECONDS,
  EntryReservation,
  ReservationUnavailable,
  find_overlap,
  release_entry_zone,
  reserve_entry_zone,
)

pytestmark = pytest.mark.no_database

T0 = 1_790_760_000


def _res(setup_id="a", direction="BUY", low=100.0, high=101.0, at=T0):
  return EntryReservation(
    setup_id=setup_id, strategy="range_sweep", direction=direction,
    low=low, high=high, reserved_at=at,
  )


def _find(existing, *, direction="BUY", low=100.5, high=101.5, atr=1.0, now=T0 + 60, setup_id="b"):
  return find_overlap(
    existing, setup_id=setup_id, direction=direction,
    low=low, high=high, atr=atr, now=now,
  )


def test_intersecting_same_direction_zone_is_blocked():
  assert _find([_res()]) is not None


def test_zone_within_the_atr_pad_is_blocked_and_beyond_it_is_not():
  existing = [_res(low=100.0, high=101.0)]
  assert _find(existing, low=102.0, high=103.0, atr=1.0) is not None
  assert _find(existing, low=102.0 + PAD_ATR * 0.01 + 0.0001, high=103.0, atr=1.0) is None
  assert _find(existing, low=105.0, high=106.0, atr=1.0) is None


def test_opposite_direction_is_left_to_active_exposure():
  assert _find([_res(direction="SELL")], direction="BUY") is None


def test_reservation_expires_after_the_window():
  existing = [_res(at=T0)]
  assert _find(existing, now=T0 + WINDOW_SECONDS) is not None
  assert _find(existing, now=T0 + WINDOW_SECONDS + 1) is None


def test_a_setup_never_blocks_itself():
  assert _find([_res(setup_id="b")], setup_id="b") is None


def test_zero_atr_falls_back_to_strict_intersection():
  existing = [_res(low=100.0, high=101.0)]
  assert _find(existing, low=101.5, high=102.0, atr=0.0) is None
  assert _find(existing, low=100.9, high=102.0, atr=0.0) is not None


def _run(coro):
  return asyncio.run(coro)


def _client(*, scripting_fallback=True):
  """fakeredis has no Lua engine here; the explicit flag selects the test path."""
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client._apexvoid_allow_non_atomic_test_fallback = scripting_fallback
  return client


def test_reserve_then_second_strategy_on_the_same_band_is_blocked():
  async def scenario():
    client = _client()
    first = await reserve_entry_zone(
      client, symbol="XAU", setup_id="s1", strategy="order_block",
      direction="BUY", low=4179.59, high=4183.34, atr=3.5, now=T0,
    )
    second = await reserve_entry_zone(
      client, symbol="XAU", setup_id="s2", strategy="key_level",
      direction="BUY", low=4183.26, high=4186.73, atr=3.47, now=T0 + 900,
    )
    return first, second

  first, second = _run(scenario())
  assert first is None
  assert second is not None and second.strategy == "order_block"


def test_blocked_candidate_does_not_reserve_its_own_zone():
  async def scenario():
    client = _client()
    await reserve_entry_zone(
      client, symbol="XAU", setup_id="s1", strategy="a",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0,
    )
    await reserve_entry_zone(
      client, symbol="XAU", setup_id="s2", strategy="b",
      direction="BUY", low=100.5, high=101.5, atr=0.5, now=T0 + 10,
    )
    await release_entry_zone(client, symbol="XAU", setup_id="s1")
    return await reserve_entry_zone(
      client, symbol="XAU", setup_id="s3", strategy="c",
      direction="BUY", low=100.5, high=101.5, atr=0.5, now=T0 + 20,
    )

  assert _run(scenario()) is None


def test_release_frees_the_zone_for_a_failed_plan():
  async def scenario():
    client = _client()
    await reserve_entry_zone(
      client, symbol="XAU", setup_id="s1", strategy="a",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0,
    )
    await release_entry_zone(client, symbol="XAU", setup_id="s1")
    return await reserve_entry_zone(
      client, symbol="XAU", setup_id="s2", strategy="b",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0 + 5,
    )

  assert _run(scenario()) is None


def test_symbols_do_not_share_reservations():
  async def scenario():
    client = _client()
    await reserve_entry_zone(
      client, symbol="XAU", setup_id="s1", strategy="a",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0,
    )
    return await reserve_entry_zone(
      client, symbol="GBPUSD", setup_id="s2", strategy="b",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0 + 5,
    )

  assert _run(scenario()) is None


def test_corrupt_stored_state_fails_open():
  async def scenario():
    client = _client()
    await client.set("autotrade:entry_overlap:XAU", "not json")
    return await reserve_entry_zone(
      client, symbol="XAU", setup_id="s1", strategy="a",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0,
    )

  assert _run(scenario()) is None


# Real XAU admissions from production, 2026-09-30, in fill order. Zones and
# ATR are the analysis-engine envelope values; results are net pips.
_PRODUCTION_XAU = (
  (1790758268, "fvg", "BUY", 4195.90, 4197.44, 4.23, -31),
  (1790758627, "impulse_pullback", "BUY", 4191.83, 4193.71, 2.40, -42),
  (1790761626, "impulse_pullback", "BUY", 4187.74, 4188.20, 1.01, 14),
  (1790763616, "range_sweep", "BUY", 4184.82, 4185.01, 1.35, 21),
  (1790764534, "key_level", "BUY", 4183.2556, 4186.7277, 3.47, -49),
  (1790765410, "range_sweep", "BUY", 4183.76, 4183.94, 1.39, -26),
  (1790765588, "order_block", "BUY", 4179.59, 4183.34, 3.58, -39),
  (1790766427, "range_sweep", "BUY", 4181.52, 4182.71, 1.52, -24),
  (1790767267, "fvg", "BUY", 4176.51, 4177.63, 3.17, 118),
)


def test_production_2026_09_30_xau_pileup_is_cut_and_the_winners_survive():
  async def scenario():
    client = _client()
    admitted: list[tuple[str, int]] = []
    blocked: list[tuple[str, int]] = []
    for index, (ts, strategy, direction, low, high, atr, pips) in enumerate(_PRODUCTION_XAU):
      blocker = await reserve_entry_zone(
        client, symbol="XAU", setup_id=f"s{index}", strategy=strategy,
        direction=direction, low=low, high=high, atr=atr, now=ts,
      )
      (blocked if blocker else admitted).append((strategy, pips))
    return admitted, blocked

  admitted, blocked = _run(scenario())
  assert sum(pips for _, pips in blocked) < 0
  assert ("fvg", 118) in admitted
  assert ("key_level", -49) in [(s, p) for s, p in blocked]
  assert ("order_block", -39) in [(s, p) for s, p in blocked]


def test_reservation_fails_closed_when_it_cannot_run_atomically():
  async def scenario():
    client = _client(scripting_fallback=False)
    return await reserve_entry_zone(
      client, symbol="XAU", setup_id="s1", strategy="a",
      direction="BUY", low=100.0, high=101.0, atr=0.5, now=T0,
    )

  with pytest.raises(ReservationUnavailable):
    _run(scenario())


@pytest.mark.real_redis
@pytest.mark.asyncio
async def test_real_redis_concurrent_reservations_have_exactly_one_winner():
  """Two workers must never both conclude they own a corridor."""
  configured = os.getenv("REAL_REDIS_URL")
  if not configured:
    pytest.fail("REAL_REDIS_URL is required")
  client = Redis.from_url(f"{configured.rsplit('/', 1)[0]}/12", decode_responses=True)
  await client.flushdb()
  try:
    results = await asyncio.gather(*(
      reserve_entry_zone(
        client, symbol="XAU", setup_id=f"s{i}", strategy=f"strategy_{i}",
        direction="BUY", low=4183.0 + i * 0.1, high=4186.0, atr=3.0, now=T0,
      )
      for i in range(40)
    ))
    winners = [i for i, blocker in enumerate(results) if blocker is None]
    assert len(winners) == 1
    blockers = {blocker.setup_id for blocker in results if blocker is not None}
    assert blockers == {f"s{winners[0]}"}

    # A different symbol, the opposite direction and a far corridor stay free.
    assert await reserve_entry_zone(
      client, symbol="GBPUSD", setup_id="g", strategy="a",
      direction="BUY", low=4183.0, high=4186.0, atr=3.0, now=T0,
    ) is None
    assert await reserve_entry_zone(
      client, symbol="XAU", setup_id="sell", strategy="a",
      direction="SELL", low=4183.0, high=4186.0, atr=3.0, now=T0,
    ) is None
    assert await reserve_entry_zone(
      client, symbol="XAU", setup_id="far", strategy="a",
      direction="BUY", low=4300.0, high=4302.0, atr=3.0, now=T0,
    ) is None
    assert 0 < await client.ttl("autotrade:entry_overlap:XAU") <= WINDOW_SECONDS + 60
  finally:
    await client.flushdb()
    await client.aclose()
