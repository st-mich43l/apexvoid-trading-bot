"""Range retirement + Market Map reaction memory hotfix tests."""

from __future__ import annotations
from app.core.config import runtime_config

from datetime import datetime, timezone
from types import SimpleNamespace

from tests.configuration.canonical_fixtures import (
  install_runtime_overrides,
  map_strategy_cfg,
  market_map_cfg,
)

import pandas as pd
import pytest

from app.analysis.market_map import MapEntry, MarketMap, render_market_map
from app.autotrade import worker
from app.autotrade.gate import AutoScalpBox, AutoScalpDecision, AutoScalpRail
from app.autotrade.range_context import RangeBarrier, RangeContext
from app.autotrade.range_lifecycle import (
  breakout_retest_key,
  disarmed_side_payload,
  persist_breakout_retest_watch,
  range_retired_key,
  retest_zone_for_break,
  retire_range_context,
)
from app.persistence import redis_state


def _rail(level: float, role: str = "support") -> AutoScalpRail:
  return AutoScalpRail(
    role=role,
    low=level - 0.5,
    high=level + 0.5,
    level=level,
    touches=3,
    score=5.0,
    timeframes=("M1",),
    sources=("test",),
  )


def _box(low: float = 4045.82, high: float = 4056.14) -> AutoScalpBox:
  return AutoScalpBox(
    box_id="box-test",
    lower=_rail(low, "support"),
    upper=_rail(high, "resistance"),
    width_pips=high - low,
  )


def _context(
  low: float = 4045.82,
  high: float = 4056.14,
  *,
  state: str = "confirmed",
) -> RangeContext:
  lower = RangeBarrier(level=low, low=low - 0.4, high=low + 0.4, touches=3)
  upper = RangeBarrier(level=high, low=high - 0.4, high=high + 0.4, touches=3)
  return RangeContext(
    version=1,
    range_id="range-test",
    symbol="XAU",
    state=state,
    source="private",
    execution_timeframe="M1",
    context_timeframes=("M1", "M5"),
    lower=low,
    upper=high,
    equilibrium=(low + high) / 2,
    width_price=high - low,
    width_pips=high - low,
    width_atr=2.0,
    lower_barrier=lower,
    upper_barrier=upper,
    supports=(lower,),
    resistances=(upper,),
    generated_at=1_000,
    expires_at=10_000,
  )


def _cfg(**overrides) -> SimpleNamespace:
  values = {
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_map_track_distance_atr": 8.0,
    "auto_trade_map_execute_distance_atr": 1.5,
    "auto_trade_map_execute_tolerance_pips": 0.0,
    "auto_trade_map_execute_tolerance_atr": 0.0,
    "auto_trade_map_zone_min_width_atr": 0.0,
    "auto_trade_map_zone_min_width_abs": 0.3,
    "auto_trade_map_reaction_lookback_bars": 5,
    "auto_trade_max_entry_distance_pips": 10,
    "auto_trade_map_max_entry_drift_atr": 0.40,
    "auto_trade_allow_counter_bias": False,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_targets_pips": "30,60,90",
    "atr_length": 14,
    "proximal_band_atr": 0.5,
    "pip_size": 0.1,
  }
  values.update(overrides)
  return map_strategy_cfg(**values)


def _m1_frame(rows: list[dict]) -> pd.DataFrame:
  index = pd.date_range(
    "2026-07-24 10:00",
    periods=len(rows),
    freq="1min",
    tz="UTC",
  )
  return pd.DataFrame(rows, index=index)


def _demand() -> MapEntry:
  return MapEntry(
    "buy",
    4053.23,
    4056.09,
    4053,
    4056,
    "zone",
    ["demand", "flip", "breakout-retest"],
    12.0,
  )


def _map(*entries: MapEntry, price: float = 4055.0) -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=price,
    eq=4051.0,
    box_low=4045.0,
    box_high=4056.0,
    bias="up",
    bias_tf="M30",
    map_id="map-test-id",
    generated_at=1_700_000_000,
    source_timeframe="M5",
    actionable_entries=list(entries),
  )


@pytest.mark.asyncio
async def test_broken_range_disarms_both_rails(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_box_retire_seconds": 3600})
  context = _context(state="confirmed")
  await worker._persist_range_side_states(
    client,
    symbol="XAU",
    context=context,
    decision=AutoScalpDecision("waiting_for_touch", box=_box()),
  )
  retired = retire_range_context(context, direction="BUY", now=2_000)
  await worker._persist_range_side_states(
    client,
    symbol="XAU",
    context=retired,
    decision=AutoScalpDecision("box_broken", box=_box()),
  )
  buy = await _side(client, retired.range_id, "BUY")
  sell = await _side(client, retired.range_id, "SELL")
  assert buy["state"] == "DISARMED"
  assert sell["state"] == "DISARMED"
  assert retired.state == "retired"


async def _side(client, range_id: str, direction: str) -> dict:
  import json
  raw = await client.get(
    f"auto_trade:range_side:XAU:{range_id}:{direction}"
  )
  return json.loads(raw)


@pytest.mark.asyncio
async def test_broken_range_id_is_retired():
  client = redis_state.get_client()
  from app.autotrade.range_lifecycle import mark_range_retired
  context = _context()
  await mark_range_retired(
    client, symbol="XAU", range_id=context.range_id, ttl=3600,
  )
  assert await client.exists(range_retired_key("XAU", context.range_id))


@pytest.mark.asyncio
async def test_bullish_breakout_creates_buy_retest_watcher():
  client = redis_state.get_client()
  payload = await persist_breakout_retest_watch(
    client,
    symbol="XAU",
    range_id="range-test",
    direction="BUY",
    lower=4045.82,
    upper=4056.14,
    ttl=3600,
  )
  assert payload["direction"] == "BUY"
  assert payload["state"] == "waiting"
  assert payload["broken_edge"] == 4056.14
  assert payload["zone_high"] == 4056.14
  stored = await client.get(breakout_retest_key("XAU"))
  assert stored is not None


@pytest.mark.asyncio
async def test_bearish_breakout_creates_sell_retest_watcher():
  client = redis_state.get_client()
  payload = await persist_breakout_retest_watch(
    client,
    symbol="XAU",
    range_id="range-test",
    direction="SELL",
    lower=4045.82,
    upper=4056.14,
    ttl=3600,
  )
  assert payload["direction"] == "SELL"
  assert payload["broken_edge"] == 4045.82
  low, high = retest_zone_for_break(
    direction="SELL", lower=4045.82, upper=4056.14,
  )
  assert low == payload["zone_low"]
  assert high == payload["zone_high"]


def test_box_broken_never_coexists_with_armed_rails():
  context = retire_range_context(_context(), direction="BUY", now=2_000)
  payload = disarmed_side_payload(context=context, direction="BUY")
  assert context.state == "retired"
  assert payload["state"] == "DISARMED"
  assert payload["state"] != "ARMED"


def test_actionable_map_entry_appears_in_rendered_market_map():
  hidden = _demand()
  visible = MapEntry(
    "buy",
    4040.0,
    4041.0,
    4040,
    4041,
    "zone",
    ["demand"],
    7.0,
  )
  market_map = MarketMap(
    entries=[visible],
    price=4055.0,
    eq=4050.0,
    box_low=4040.0,
    box_high=4060.0,
    bias="up",
    bias_tf="M30",
    map_id="abc123",
    generated_at=1,
    source_timeframe="M5",
    actionable_entries=[hidden, visible],
  )
  text = render_market_map(
    market_map,
    "XAU",
    datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc),
    market_map_cfg(seq_reset_tz="UTC"),
  )
  # "ACTIONABLE NOW" was misleading - these bands are only listed here
  # because the per-side display cap dropped them, not because price is
  # near them. See render_market_map's comment on this section.
  assert "ALSO QUALIFIES" in text
  assert "4,053" in text
  assert "map_id abc123" in text


def test_evaluator_and_renderer_use_same_map_id():
  demand = _demand()
  market_map = _map(demand)
  rendered = _map(demand)
  assert market_map.map_id == rendered.map_id


@pytest.mark.asyncio
async def test_auto_status_reports_breakout_retest_instead_of_no_detection(
  monkeypatch,
):
  from app.autotrade import delivery

  client = redis_state.get_client()
  await persist_breakout_retest_watch(
    client,
    symbol="XAU",
    range_id="range-test",
    direction="BUY",
    lower=4045.82,
    upper=4056.14,
    ttl=3600,
  )
  await client.set(
    "auto_trade:last_match_build:XAU",
    '{"stage":"match_build_rejected","reason":"no_detection_result"}',
  )
  retired = retire_range_context(_context(), direction="BUY", now=2_000)
  await client.set(
    "auto_trade:range_context:XAU",
    retired.to_json(),
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  text = await delivery.auto_trade_status_text()
  assert "breakout-retest" in text
  assert "no_detection_result" not in text
