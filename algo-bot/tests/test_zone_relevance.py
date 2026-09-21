"""Market relevance classification: structural validity is orthogonal to
"does this zone matter to CURRENT price right now" (owner 2026-09-17).

Pure functions, no Redis - ZoneWatch records are constructed directly.
"""

from __future__ import annotations

import pytest

from app.autotrade import zone_relevance as zr
from app.autotrade.zone_watch import GRADE_A, WATCHING_RETEST, ZoneWatch


def _zone(**overrides) -> ZoneWatch:
  base = dict(
    version=3,
    zone_id="zone-1",
    symbol="XAU",
    direction="BUY",
    low=4295.0,
    high=4300.0,
    width=5.0,
    source_timeframe="M5",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=GRADE_A,
    score=2.0,
    freshness=0,
    touch_count=0,
    discovered_at=1_000_000,
    last_confirmed_at=1_000_000,
    last_touch_at=None,
    invalidation_price=None,
    state=WATCHING_RETEST,
    market_map_id="mm-1",
    structure_signature="sig-1",
    updated_at=1_000_000,
  )
  base.update(overrides)
  return ZoneWatch(**base)


# --- classify_zone_relevance -------------------------------------------


def test_distance_zero_when_price_inside_zone_is_immediate():
  zone = _zone(low=4295.0, high=4300.0)
  relevance = zr.classify_zone_relevance(zone, mid_price=4297.5, atr=2.0)
  assert relevance.relevance == zr.IMMEDIATE
  assert relevance.distance_price == 0.0
  assert relevance.distance_atr == 0.0
  assert relevance.relative_position == zr.OVERLAPPING_MARKET


def test_band_edges_are_inclusive_at_each_threshold():
  # nearest edge is `high`=4300; atr=2.0 -> distance_atr = distance/2.0
  zone = _zone(low=4295.0, high=4300.0)
  # exactly at immediate_atr (0.25 * 2.0 = 0.5 price)
  assert zr.classify_zone_relevance(zone, 4300.5, atr=2.0).relevance == zr.IMMEDIATE
  # just past immediate, inside nearby (1.25 * 2.0 = 2.5 price)
  assert zr.classify_zone_relevance(zone, 4301.0, atr=2.0).relevance == zr.NEARBY
  assert zr.classify_zone_relevance(zone, 4302.5, atr=2.0).relevance == zr.NEARBY
  # just past nearby, inside remote (3.0 * 2.0 = 6.0 price)
  assert zr.classify_zone_relevance(zone, 4303.0, atr=2.0).relevance == zr.REMOTE
  assert zr.classify_zone_relevance(zone, 4306.0, atr=2.0).relevance == zr.REMOTE
  # past remote -> dormant
  assert zr.classify_zone_relevance(zone, 4306.5, atr=2.0).relevance == zr.DORMANT


def test_old_valid_zone_five_atr_away_is_dormant():
  # Production regression case: a structurally valid zone, far from a
  # market that has since moved on, must never read as a current setup.
  zone = _zone(low=4280.0, high=4285.0, discovered_at=0, last_touch_at=0)
  relevance = zr.classify_zone_relevance(zone, mid_price=4360.2, atr=6.0)
  assert relevance.relevance == zr.DORMANT
  assert relevance.distance_atr > 3.0


def test_fresh_zone_point_three_atr_away_is_nearby():
  # Spec Test 2: fresh valid zone ~0.3 ATR away -> visible in the active
  # watchlist (immediate or nearby), never dormant.
  zone = _zone(low=4350.2, high=4354.8)
  relevance = zr.classify_zone_relevance(zone, mid_price=4356.6, atr=6.0)
  assert relevance.distance_atr == pytest.approx(0.3)
  assert relevance.relevance == zr.NEARBY


def test_missing_atr_falls_back_to_inside_or_dormant():
  zone = _zone(low=4295.0, high=4300.0)
  assert zr.classify_zone_relevance(zone, 4297.0, atr=None).relevance == zr.IMMEDIATE
  assert zr.classify_zone_relevance(zone, 4400.0, atr=None).relevance == zr.DORMANT
  assert zr.classify_zone_relevance(zone, 4400.0, atr=0.0).relevance == zr.DORMANT


def test_relative_position_below_overlapping_above():
  zone = _zone(low=4295.0, high=4300.0)
  assert zr.classify_zone_relevance(zone, 4400.0, atr=2.0).relative_position == zr.ABOVE_MARKET
  assert zr.classify_zone_relevance(zone, 4200.0, atr=2.0).relative_position == zr.BELOW_MARKET
  assert zr.classify_zone_relevance(zone, 4297.0, atr=2.0).relative_position == zr.OVERLAPPING_MARKET


# --- partition_by_relevance ---------------------------------------------


def test_partition_splits_active_from_memory_nearest_first():
  nearer = _zone(zone_id="nearer", low=4356.0, high=4357.0)
  near = _zone(zone_id="near", low=4350.0, high=4354.0)
  far = _zone(zone_id="far", low=4280.0, high=4285.0, score=3.0)
  remote = _zone(zone_id="remote", low=4340.0, high=4344.0)
  active, memory = zr.partition_by_relevance(
    [near, remote, far, nearer], mid_price=4357.0, atr=6.0,
  )
  active_ids = [record.zone_id for record, _ in active]
  memory_ids = [record.zone_id for record, _ in memory]
  assert "far" in memory_ids
  assert "far" not in active_ids
  # nearest-first within the active bucket
  assert active_ids == ["nearer", "near"]


def test_partition_active_watchlist_empty_when_everything_is_far():
  far_a = _zone(zone_id="a", low=4280.0, high=4285.0)
  far_b = _zone(zone_id="b", low=4295.0, high=4300.0, direction="SELL")
  active, memory = zr.partition_by_relevance([far_a, far_b], mid_price=4360.0, atr=5.0)
  assert active == []
  assert len(memory) == 2


# --- coverage_state -------------------------------------------------------


def test_coverage_gap_when_no_active_setups():
  assert zr.coverage_state([]) == zr.GAP


def test_coverage_good_with_two_directions_nearby():
  buy = _zone(zone_id="buy", direction="BUY", low=4355.0, high=4356.0)
  sell = _zone(zone_id="sell", direction="SELL", low=4358.0, high=4359.0)
  buy_relevance = zr.classify_zone_relevance(buy, 4357.0, atr=6.0)
  sell_relevance = zr.classify_zone_relevance(sell, 4357.0, atr=6.0)
  assert zr.coverage_state([(buy, buy_relevance), (sell, sell_relevance)]) == zr.GOOD


def test_coverage_thin_with_a_single_active_setup():
  buy = _zone(zone_id="buy", direction="BUY", low=4356.5, high=4357.0)
  relevance = zr.classify_zone_relevance(buy, 4357.0, atr=6.0)
  assert zr.coverage_state([(buy, relevance)]) == zr.THIN


# --- age helpers ------------------------------------------------------------


def test_age_since_discovery_and_last_touch():
  zone = _zone(discovered_at=1_000, last_touch_at=1_500)
  assert zr.age_since_discovery_seconds(zone, now=1_100) == 100
  assert zr.age_since_last_touch_seconds(zone, now=1_600) == 100


def test_age_since_last_touch_is_none_when_never_touched():
  zone = _zone(last_touch_at=None)
  assert zr.age_since_last_touch_seconds(zone, now=2_000) is None


def test_is_dead_zone_only_beyond_twice_the_dormant_band():
  zone = _zone(low=4280.0, high=4285.0)
  # remote band defaults to 3 ATR: dormant past 3, dead past 6.
  four_atr = zr.classify_zone_relevance(zone, 4285.0 + 4 * 2.0, atr=2.0)
  eight_atr = zr.classify_zone_relevance(zone, 4285.0 + 8 * 2.0, atr=2.0)
  assert four_atr.relevance == zr.DORMANT and not zr.is_dead_zone(four_atr)
  assert zr.is_dead_zone(eight_atr)
  no_atr = zr.classify_zone_relevance(zone, 4400.0, atr=None)
  assert not zr.is_dead_zone(no_atr)
