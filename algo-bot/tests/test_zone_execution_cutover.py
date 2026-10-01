from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.autotrade.strategy_match import StrategyMatch
from app.autotrade import worker
from app.analysis.ohlc_source import RedisOHLCSource
from app.autotrade import zone_watch as zw


pytestmark = [pytest.mark.no_database, pytest.mark.real_redis]


@pytest.fixture(autouse=True)
def _freeze_technique_killzone_hour(monkeypatch):
  """Cutover stays under technique.enforce; freeze UTC hour to NY open."""
  from app.autotrade import killzone as kz

  real = kz.evaluate_killzone_gate
  real_win = kz.evaluate_reaction_publish_window

  def _gated(*, ts=None, hour=None, cfg=None, require=True):
    return real(ts=None, hour=14, cfg=cfg, require=require)

  def _window(*, ts=None, hour=None, cfg=None, require=True):
    return real_win(ts=None, hour=14, cfg=cfg, require=require)

  monkeypatch.setattr(kz, "evaluate_killzone_gate", _gated)
  monkeypatch.setattr(kz, "evaluate_reaction_publish_window", _window)


@pytest_asyncio.fixture
async def client():
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.skip("REAL_REDIS_URL is required for ZoneWatch cutover tests")
  redis = Redis.from_url(url, decode_responses=True)
  await redis.ping()
  await redis.flushdb()
  try:
    yield redis
  finally:
    await redis.flushdb()
    await redis.aclose()


def _match(*, strategy: str = "Key Level") -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id="setup-1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy=strategy,
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
    family="key_level" if strategy != "Range Edge Scalp" else "range",
    structural_source="key_level",
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
    structural_zone_low=4113.0,
    structural_zone_high=4116.0,
    touch_bar_ts="1785390000",
    confirmation_bar_ts="1785390300",
    reaction_type="rejection_choch",
  )


def _result(
  *,
  low: float = 4113.0,
  high: float = 4116.0,
  setup="Key Level",
  structural_source: str = "key_level",
):
  return SimpleNamespace(
    setup=setup,
    direction="SELL",
    entry_zone=SimpleNamespace(low=low, high=high),
    structural_low=low,
    structural_high=high,
    structural_timeframe="M15",
    structural_source=structural_source,
    structural_id="zone-1",
    confluence_zone_id="zone-1",
    confluence_tags=("key_level", "supply"),
    confluence=3,
    source_score=12.0,
    confirmation_type="rejection_choch",
    confirmation="rejection_choch",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-1"),
  )


@pytest.mark.asyncio
async def test_zone_presence_counts_one_touch_per_visit(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    now=100,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  first, entered = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=200, htf_evidence=True,
  )
  second, entered_again = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=260, htf_evidence=True,
  )
  assert entered is True
  assert entered_again is False
  assert first.touch_count == second.touch_count == 1
  assert first.episode_id == second.episode_id


@pytest.mark.asyncio
async def test_rediscovery_refreshes_metadata_without_resetting_touch(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=100,
  )
  touched = await zw.record_zone_touch(client, record.zone_id, now=150)
  refreshed, created = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4112.9,
    high=4116.1,
    source_timeframe="M15",
    structural_sources=("key_level", "supply_demand"),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    score=14.0,
    now=300,
  )
  assert created is False
  assert refreshed.touch_count == touched.touch_count == 1
  assert refreshed.last_confirmed_at == 300
  assert refreshed.low == pytest.approx(4112.9)
  assert refreshed.score == pytest.approx(14.0)


async def _seed_price_and_bars(client, *, spot=4360.0, base=4358.0):
  now = int(time.time())
  await client.set(
    "price:XAU:spot",
    f'{{"bid": {spot}, "ask": {spot + 0.5}, "ts": {now}}}',
  )
  for index in range(30):
    close = base + (index % 3)
    payload = (
      f'{{"t": {index}, "o": {close}, "h": {close + 1}, '
      f'"l": {close - 1}, "c": {close}, "v": 1}}'
    )
    await client.zadd("bars:XAU:M5", {payload: index})
  return now


async def _discover(client, zone_id, low, high, direction="BUY"):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id=zone_id,
    symbol="XAU",
    direction=direction,
    low=low,
    high=high,
    source_timeframe="M5",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=100,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  return record


  # If match.range_* had been used, position would be nonsense near 0.5 of a 4pt box.


def _eligibility_with_stop_error(
  *,
  planned_stop_error: str,
  allowed: bool = True,
):
from app.autotrade.execution_eligibility import (
    ANALYSIS_ONLY,
    EXECUTION_ELIGIBILITY_VERSION,
    STATIC_ELIGIBLE,
    ExecutionEligibility,
  )

  return ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=allowed,
    state=STATIC_ELIGIBLE if allowed else ANALYSIS_ONLY,
    reason_code="ok" if allowed else planned_stop_error,
    message="",
    hard_block=not allowed,
    direction="SELL",
    entry_low=4113.0,
    entry_high=4116.0,
    planned_entry_price=4114.5,
    measured={"planned_stop_error": planned_stop_error},
  )


def _iso(epoch: int) -> str:
  from datetime import datetime, timezone

  return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


async def _retire_setup(client, setup_id: str, state: str):
  from app.autotrade import setup_lifecycle as sl

  await sl.create_setup(client, setup_id=setup_id, thesis_id="t", symbol="XAU")
  await sl.transition_setup(client, setup_id, state, reason_code="test")
  return await sl.load_setup(client, setup_id)
