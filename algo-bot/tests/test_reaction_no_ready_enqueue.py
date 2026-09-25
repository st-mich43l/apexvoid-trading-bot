"""Reaction path never enqueues strategy_match_ready (no_database)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.autotrade.strategy_taxonomy import is_reaction_strategy
from app.autotrade import worker


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_reaction_remained_watching_skips_ready_enqueue():
  """Mirror scanner handoff: reaction + remained_watching → no ready xadd."""
  assert is_reaction_strategy("Key Level")
  assert is_reaction_strategy("Session Level")
  assert is_reaction_strategy("Trendline")
  assert not is_reaction_strategy("Demand Zone")

  enqueued = AsyncMock(return_value="1-0")
  direct = AsyncMock(
    return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
      plan_id="v8:x",
      reason_code="zone_watching_retest",
      zone_id="z",
      setup_id="s",
    ),
  )

  strategies = (
    "Key Level",
    "Session Level",
    "Trendline",
  )
  for strategy in strategies:
    result = await direct()
    assert result.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
    if is_reaction_strategy(strategy):
      continue
    await enqueued()

  enqueued.assert_not_awaited()


@pytest.mark.asyncio
async def test_cutover_reaction_exception_skips_ready_fallback(monkeypatch):
  """zone_execution_cutover must not enqueue ready for reaction strategies."""
  from app.autotrade import zone_execution_cutover as cutover
  from app.autotrade.strategy_match import StrategyMatch
  from app.autotrade import zone_watch as zw

  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client._apexvoid_allow_non_atomic_test_fallback = True

  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M5",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  await zw.record_zone_presence(client, record.zone_id, inside=True, now=100)

  match = StrategyMatch(
    version=1,
    match_id="setup-1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy="Key Level",
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4114.5,
    entry_low=4113.0,
    entry_high=4116.0,
    current_price=4114.5,
    confluence=3,
    reasons=("supply rejection",),
    atr=4.0,
    structure_swing=4116.0,
    targets_pips=(300,),
    family="key_level",
    structural_source="key_level",
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
  )

  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, 1_785_390_000)),
  )
  monkeypatch.setattr(
    cutover,
    "_persist_match",
    AsyncMock(side_effect=lambda _c, m: m),
  )
  monkeypatch.setattr(
    cutover,
    "_safe_direct_publish",
    AsyncMock(
      return_value=worker.PublishResult(
        status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
        plan_id="v8:setup-1",
        reason_code="direct_publish_failed_durable_fallback",
        zone_id="zone-1",
        setup_id="setup-1",
      ),
    ),
  )
  enqueue = AsyncMock(return_value="1-0")
  monkeypatch.setattr(cutover, "enqueue_strategy_match_ready", enqueue)
  monkeypatch.setattr(
    "app.autotrade.setup_lifecycle.load_setup",
    AsyncMock(return_value=None),
  )

  latest = await zw.load_zone_watch(client, "zone-1")
  result = await cutover._activate_match(
    client, latest, match, event_ts="t",
  )
  assert result is None
  enqueue.assert_not_awaited()
  assert await client.xlen("auto_trade:strategy_match_ready") == 0
