"""Range Edge enters only at the price printed on its card.

Production 2026-10-08 (XAU): card "Entry Zone 4,125 - 4,127" for a SELL zone
4124.79-4126.81, yet the order went in at 4124.19 - the quote 6 pips past the
zone's low edge, taken by the scalp momentum chase - and was stopped out in
eight minutes. A quote outside the zone is a missed entry, not a market chase.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from app.autotrade import worker
from app.autotrade.execution_confirmation import (
  WAITING_RETEST,
  load_execution_confirmation,
)
from app.autotrade.execution_route import ROUTE_MARKET, resolve_execution_route_plan
from app.autotrade.strategy_taxonomy import (
  is_retest_only_scalp_strategy,
)
from app.persistence import redis_state
from tests.test_publish_trade_plan_v8 import _confirm_setup, _match

pytestmark = pytest.mark.no_database

ZONE_LOW, ZONE_HIGH = 4124.79, 4126.81


@pytest.fixture(autouse=True)
def _no_news_by_default(monkeypatch):
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))


def test_only_the_zone_owning_scalps_are_retest_only():
  assert is_retest_only_scalp_strategy("Range Edge Scalp")
  assert is_retest_only_scalp_strategy("Breakout Retest Scalp")
  assert not is_retest_only_scalp_strategy("Range Sweep Scalp")
  assert not is_retest_only_scalp_strategy("Impulse Pullback Scalp")
  assert not is_retest_only_scalp_strategy("Key Level")


def test_range_edge_route_waits_for_the_zone_instead_of_booking_the_quote():
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4124.19,
    zone_low=ZONE_LOW,
    zone_high=ZONE_HIGH,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Range Edge Scalp",
  )
  assert plan.route == ROUTE_MARKET
  assert plan.entry_geometry == "below"
  assert plan.immediate_market is False
  assert "card zone" in plan.routing_reason


def _range_edge_match():
  return _match(
    match_id="match-range-edge-card",
    thesis_id="thesis-range-edge-card",
    strategy="Range Edge Scalp",
    strategy_mode="range_scalp",
    direction="SELL",
    family="range_reversion",
    structural_source="scalp",
    structural_kind="range_edge",
    key_level=ZONE_HIGH,
    entry_low=ZONE_LOW,
    entry_high=ZONE_HIGH,
    current_price=4124.19,
    structure_swing=4127.83,
    targets_pips=(50,),
    full_take_profit_pips=50,
    htf_bias="down",
  )


@pytest.mark.asyncio
async def test_range_edge_quote_below_the_zone_is_parked_not_chased():
  client = redis_state.get_client()
  match = _range_edge_match()
  await _confirm_setup(client, match)
  below = worker.AutoTradeSpot(
    price=4124.19, ts=int(time.time()), fresh=True, bid=4124.19, ask=4124.39,
  )
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", below, match)
  assert plan_id is None
  conf = await load_execution_confirmation(client, match.match_id)
  assert conf is not None
  assert conf.phase == WAITING_RETEST
