import inspect
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade import worker
from app.persistence import redis_state
from app.analysis import scanner
from app.autotrade.gate import AutoScalpBox, AutoScalpDecision, AutoScalpRail
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
  strategy_match_key,
)
from app.autotrade.scale_context import AutoScaleContext
from app.autotrade.trend import RegimeInfo, TrendDecision
from app.analysis.types import Zone


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=20, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [4016.8] * 20,
    "high": [4017.4] * 20,
    "low": [4016.2] * 20,
    "close": [4017.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)


def _decision() -> AutoScalpDecision:
  support = AutoScalpRail(
    "support",
    4016.5,
    4017.1,
    4016.8,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-low", "M15 range-low"),
  )
  resistance = AutoScalpRail(
    "resistance",
    4024.8,
    4025.4,
    4025.1,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-high", "M15 range-high"),
  )
  box = AutoScalpBox("xau-8034-8050", support, resistance, 77.0)
  return AutoScalpDecision(
    "candidate",
    direction="BUY",
    trigger="range_rejection",
    rail=support,
    target=resistance,
    target_room_pips=76.0,
    full_tp_pips=70,
    box=box,
    confluence=3,
    reasons=("M1 range rejection", "support rail"),
    rail_count=4,
  )


def _scale_context(now: int) -> AutoScaleContext:
  return AutoScaleContext(
    bar_ts=now - 60,
    atr=1.2,
    structure_swing=4014.8,
    displacement_direction="up",
    displacement_age_bars=1,
    bos_direction="up",
    bos_ts=now - 60,
    opposing_level_distance_atr=2.5,
  )


def _strategy_match(now: int) -> StrategyMatch:
  return StrategyMatch(
    STRATEGY_MATCH_VERSION,
    strategy_match_id(
      "XAU", "M5", str(now), "Liquidity Sweep", "BUY", 4016.5, 4017.4,
    ),
    "XAU",
    "M5",
    str(now),
    now,
    now + 420,
    "Liquidity Sweep",
    "with_trend",
    "BUY",
    4016.8,
    4016.5,
    4017.4,
    4017.0,
    3,
    ("sell-side liquidity swept", "bullish reclaim"),
    1.2,
    4014.8,
    (30, 60, 90),
  )


def _range_strategy_match(now: int) -> StrategyMatch:
  return replace(
    _strategy_match(now),
    match_id=strategy_match_id(
      "XAU", "M5", str(now), "Range Edge Scalp", "BUY", 4016.5, 4017.4,
    ),
    strategy="Range Edge Scalp",
    strategy_mode="range_scalp",
    reasons=("two-sided local range", "lower-edge rejection"),
    targets_pips=(70,),
    range_id="xau-strategy-range-4016.80-4025.10",
    range_low=4016.8,
    range_high=4025.1,
    full_take_profit_pips=70,
  )


@pytest.mark.asyncio
async def test_worker_publishes_one_durable_auto_only_candidate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_stream_maxlen", 100)
  monkeypatch.setattr(worker.settings, "auto_trade_candidate_ttl", 3600)
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  spot = worker.AutoTradeSpot(4017.2, now, True)

  first = await worker._publish_candidate(
    client, "XAU", "1784552400", spot, _decision(), _scale_context(now)
  )
  second = await worker._publish_candidate(
    client, "XAU", "1784552400", spot, _decision(), _scale_context(now)
  )

  assert first is not None
  assert second is None
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["candidate_id"] == first
  assert payload["setup"] == "Range Box Scalp"
  assert payload["mode"] == "auto_box_scalp"
  assert payload["timeframe"] == "M1"
  assert payload["direction"] == "BUY"
  assert payload["entry_zone"] == {"low": 4016.5, "high": 4017.1}
  assert payload["spot_ts"] == now
  assert payload["version"] == 3
  assert payload["range_id"] == "xau-8034-8050"
  assert payload["range_low"] == 4016.8
  assert payload["range_high"] == 4025.1
  assert payload["full_take_profit_pips"] == 70
  assert payload["structure_swing"] == 4014.8
  assert payload["displacement_age_bars"] == 1
  assert payload["bos_direction"] == "up"
  assert await client.exists(worker._box_edge_key(
    "XAU",
    "xau-8034-8050",
    "BUY",
  ))


@pytest.mark.asyncio
async def test_worker_handles_m1_without_calling_scanner(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_auto_scalp_gate",
    lambda frames, **kwargs: _decision(),
  )
  monkeypatch.setattr(
    worker,
    "build_auto_scale_context",
    lambda *args, **kwargs: _scale_context(now),
  )
  forming = AsyncMock()
  monkeypatch.setattr(scanner, "_handle_event", forming)

  result = await worker._handle_event(
    "XAU:M1:1784552400",
    source=source,
    client=client,
  )

  assert result == _decision()
  forming.assert_not_awaited()
  assert await client.xlen("auto_trade:test") == 1
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "candidate"
  assert status["rail"]["role"] == "support"
  assert status["rail"]["timeframes"] == ["M5", "M15"]
  assert status["box"]["id"] == "xau-8034-8050"
  assert status["full_tp_pips"] == 70


@pytest.mark.asyncio
async def test_worker_routes_scanner_strategy_without_regime_confirmation(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _strategy_match(now)
  await client.set(strategy_match_key("XAU"), match.to_json(), ex=420)
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_strategy_bridge_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_stream_maxlen", 100)
  monkeypatch.setattr(worker.settings, "auto_trade_candidate_ttl", 3600)
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_auto_scalp_gate",
    lambda *args, **kwargs: AutoScalpDecision("waiting_for_box"),
  )
  monkeypatch.setattr(
    worker,
    "build_auto_scale_context",
    lambda *args, **kwargs: _scale_context(now),
  )
  monkeypatch.setattr(
    worker,
    "classify_regime",
    lambda *args, **kwargs: RegimeInfo(
      "trend", "up", 3, 1.2, True, None, ("private label disagrees",),
    ),
  )
  trend_publish = AsyncMock()
  monkeypatch.setattr(worker, "_publish_trend_candidate", trend_publish)

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  assert result.state == "waiting_for_box"
  trend_publish.assert_not_awaited()
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  candidate = json.loads(entries[0][1]["payload"])
  assert candidate["version"] == 4
  assert candidate["mode"] == "auto_strategy_match"
  assert candidate["setup"] == "Liquidity Sweep"
  assert candidate["signal_source"] == "scanner_strategy_match"
  assert candidate["candidate_id"] == match.match_id
  assert candidate["source_event_ts"] == match.event_ts
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "candidate"
  assert status["gate_source"] == "scanner_strategy_match"
  assert status["strategy_match"]["id"] == match.match_id
  assert status["strategy_match"]["strategy"] == "Liquidity Sweep"
  assert status["direction"] == "BUY"
  assert status["selected_strategy"] == "Liquidity Sweep"
  assert status["selected_timeframe"] == "M5"
  assert status["selection_state"] == "published"


@pytest.mark.asyncio
async def test_worker_routes_m1_market_map_reaction_as_its_own_strategy(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = replace(
    _strategy_match(now),
    match_id=strategy_match_id(
      "XAU",
      "M1",
      str(now),
      "Mapped Zone Reaction",
      "SELL",
      4016.5,
      4017.4,
    ),
    source_tf="M1",
    event_ts=str(now),
    strategy="Mapped Zone Reaction",
    strategy_mode="mapped_zone_reaction",
    direction="SELL",
    reasons=("M30 bias down", "M1 touch + rejection"),
    structure_swing=4017.4,
  )
  map_decision = worker.MarketMapStrategyDecision(
    "candidate",
    match.reasons,
    match,
    (4016.5, 4017.4),
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_auto_scalp_gate",
    lambda *args, **kwargs: AutoScalpDecision("waiting_for_box"),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_market_map_strategy",
    lambda *args, **kwargs: map_decision,
  )

  await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  candidate = json.loads(entries[0][1]["payload"])
  assert candidate["setup"] == "Mapped Zone Reaction"
  assert candidate["signal_source"] == "market_map_strategy"
  assert candidate["timeframe"] == "M1"
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["gate_source"] == "market_map_strategy"
  assert status["market_map_state"] == "candidate"
  assert status["selected_strategy"] == "Mapped Zone Reaction"
  assert status["selection_state"] == "published"


@pytest.mark.asyncio
async def test_worker_publishes_range_match_as_strategy_and_disarms_edge(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _range_strategy_match(now)
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_stream_maxlen", 100)
  monkeypatch.setattr(worker.settings, "auto_trade_candidate_ttl", 3600)
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4017.2, now, True),
    match,
  )

  assert candidate_id == match.match_id
  entries = await client.xrange("auto_trade:test")
  payload = json.loads(entries[0][1]["payload"])
  assert payload["version"] == 3
  assert payload["timeframe"] == "M5"
  assert payload["mode"] == "auto_box_scalp"
  assert payload["source_strategy"] == "Range Edge Scalp"
  assert payload["full_take_profit_pips"] == 70
  edge = worker._box_edge_key("XAU", match.range_id, "BUY")
  assert await client.exists(edge)


@pytest.mark.asyncio
async def test_broken_box_is_retired_and_cannot_publish_again(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(
    worker.settings,
    "auto_trade_box_retire_seconds",
    3600,
  )
  candidate = _decision()
  broken = AutoScalpDecision(
    "box_broken",
    box=candidate.box,
    reasons=("accepted outside",),
  )

  result = await worker._apply_box_retirement(
    client,
    "XAU",
    broken,
  )
  retired = await worker._apply_box_retirement(
    client,
    "XAU",
    candidate,
  )

  assert result.state == "box_broken"
  assert retired.state == "box_retired"
  assert "already retired" in retired.reasons[-1]


@pytest.mark.asyncio
async def test_used_edge_rearms_only_after_midpoint_close():
  client = redis_state.get_client()
  decision = _decision()
  key = worker._box_edge_key(
    "XAU",
    decision.box.box_id,
    "BUY",
  )
  await client.set(key, "1")

  blocked = await worker._apply_box_retirement(
    client,
    "XAU",
    decision,
    price=4017.0,
  )
  rearmed = await worker._apply_box_retirement(
    client,
    "XAU",
    decision,
    price=4022.0,
  )

  assert blocked.state == "edge_disarmed"
  assert rearmed.state == "candidate"
  assert not await client.exists(key)


@pytest.mark.asyncio
async def test_scanner_range_edge_rearms_after_spot_crosses_midpoint():
  client = redis_state.get_client()
  key = worker._box_edge_key("XAU", "xau-strategy-range", "BUY")
  await client.set(key, json.dumps({
    "source": "scanner_strategy_match",
    "direction": "BUY",
    "midpoint": 4020.0,
  }))

  await worker._rearm_scanner_range_edges(
    client, "XAU", worker.AutoTradeSpot(4019.9, 1, True),
  )
  assert await client.exists(key)

  await worker._rearm_scanner_range_edges(
    client, "XAU", worker.AutoTradeSpot(4020.0, 2, True),
  )
  assert not await client.exists(key)


@pytest.mark.asyncio
async def test_worker_ignores_forming_timeframe_and_scanner_still_ignores_m1(
  monkeypatch,
):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  assert await worker._handle_event(
    "XAU:M5:1784552400",
    client=client,
  ) is None

  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  assert await scanner._handle_event(
    "XAU:M1:1784552400",
    client=client,
  ) == []
  assert await client.xlen("auto_trade:candidates") == 0


@pytest.mark.asyncio
async def test_candidate_fails_closed_on_news_missing_or_stale_spot(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(
    worker,
    "event_in_window",
    AsyncMock(return_value={"title": "US CPI"}),
  )
  decision = _decision()

  assert await worker._publish_candidate(
    client,
    "XAU",
    "1",
    worker.AutoTradeSpot(4016.4, 1, True),
    decision,
  ) is None
  assert await worker._publish_candidate(
    client, "XAU", "2", None, decision
  ) is None
  assert await worker._publish_candidate(
    client,
    "XAU",
    "3",
    worker.AutoTradeSpot(4016.4, 1, False),
    decision,
  ) is None
  assert await client.xlen("auto_trade:test") == 0


@pytest.mark.asyncio
async def test_non_candidate_decision_is_never_published(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  spot = worker.AutoTradeSpot(4100.0, 1, True)

  assert await worker._publish_candidate(
    client, "XAU", "1", spot, AutoScalpDecision("waiting_for_touch")
  ) is None
  assert await client.xlen("auto_trade:test") == 0


@pytest.mark.asyncio
async def test_private_strategy_match_uses_confluence_not_regime_label(
  monkeypatch,
):
  """A regime label cannot veto the stronger matched strategy."""
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_trend_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker, "evaluate_auto_scalp_gate", lambda frames, **kwargs: _decision(),
  )
  monkeypatch.setattr(
    worker, "build_auto_scale_context", lambda *a, **k: _scale_context(now),
  )
  trend_regime = RegimeInfo("trend", "up", 5, 1.3, True, None, ("forced trend",))
  monkeypatch.setattr(
    worker, "classify_regime", lambda frames, decision, cfg: trend_regime,
  )
  trend_decision = TrendDecision(
    "candidate",
    direction="BUY",
    mode="pullback",
    entry_zone=(4016.0, 4016.5),
    key_level=4016.2,
    atr=1.2,
    structure_swing=4010.0,
    target_prices=(4020.0,),
    targets_pips=(38,),
    confluence=2,
    reasons=("forced",),
  )
  monkeypatch.setattr(worker, "evaluate_trend_gate", lambda *a, **k: trend_decision)

  result = await worker._handle_event(
    "XAU:M1:1784552400", source=source, client=client,
  )

  assert result == _decision()
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["mode"] == "auto_box_scalp"
  assert payload["setup"] == "Range Box Scalp"
  assert payload["regime"] == "trend"
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["regime"] == "trend"
  assert status["state"] == "candidate"
  assert status["box_state"] == "candidate"
  assert status["trend_state"] == "candidate"
  assert status["trend_mode"] == "pullback"
  assert status["direction"] == "BUY"
  assert status["selected_strategy"] == "Range Box Scalp"
  assert status["selection_state"] == "published"


def test_worker_source_has_no_direct_scanner_market_map_or_telegram_import():
  source = inspect.getsource(worker)
  forbidden = (
    "from app.analysis.scanner",
    "from app.analysis.detectors",
    "from app.analysis.market_map",
    "from app.bot.client",
  )
  assert all(item not in source for item in forbidden)


# --- A1: entry-location guard -----------------------------------------------

def test_eq_exclusion_rejects_entry_near_box_midpoint_spec_example():
  support = AutoScalpRail(
    "support", 4116.9, 4117.1, 4117.0, 3, 8.0, ("M1",), ("m1",),
  )
  resistance = AutoScalpRail(
    "resistance", 4141.9, 4142.1, 4142.0, 3, 8.0, ("M1",), ("m1",),
  )
  box = AutoScalpBox("xau-test", support, resistance, 250.0)

  rejected = worker._eq_exclusion_reason(box, 4127.18, 0.15)
  accepted = worker._eq_exclusion_reason(box, 4121.0, 0.15)

  assert rejected is not None
  assert "EQ" in rejected
  assert accepted is None


def test_edge_proximity_rejects_entry_two_atr_from_rail():
  rail = AutoScalpRail(
    "support", 4016.5, 4017.1, 4016.8, 3, 8.0, ("M5",), ("m5",),
  )

  rejected = worker._edge_proximity_reason(rail, 4016.8 + 2 * 1.2, 1.2, 0.5)
  accepted = worker._edge_proximity_reason(rail, 4016.8 + 0.2 * 1.2, 1.2, 0.5)

  assert rejected is not None
  assert accepted is None


@pytest.mark.asyncio
async def test_eq_exclusion_blocks_publish_and_is_not_applied_to_trend(
  monkeypatch,
):
  """EQ exclusion applies only to the box-scalp ("auto_box_scalp") family:
  a breakout/trend candidate legitimately transits the mid-range.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker.settings, "auto_trade_eq_exclusion_fraction", 0.15)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # box: support level=4016.8, resistance level=4025.1
  eq = (decision.box.lower.level + decision.box.upper.level) / 2  # 4020.95
  spot = worker.AutoTradeSpot(eq, now, True)

  result = await worker._publish_candidate(
    client, "XAU", "1784552400", spot, decision, _scale_context(now),
  )

  assert result is None
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:eq_exclusion", "count",
  )
  assert reject_count is not None and int(reject_count) >= 1
  # EQ exclusion is never even evaluated on the trend/breakout publish path -
  # structural guarantee, independent of any specific fixture's numbers.
  trend_source = inspect.getsource(worker._publish_trend_candidate)
  assert "_eq_exclusion_reason" not in trend_source


# --- A3: HTF supply/demand veto ---------------------------------------------

def test_htf_veto_rejects_sell_below_untested_supply_and_allows_at_supply():
  zone = Zone(4131.0, 4133.0, "supply", touches=0)

  below = worker._htf_veto_reason("SELL", 4127.18, zone)
  at_supply = worker._htf_veto_reason("SELL", 4132.0, zone)

  assert below is not None
  assert at_supply is None


def test_htf_veto_ignores_already_tested_zones():
  tested_zone = Zone(4131.0, 4133.0, "supply", touches=1)
  assert worker._htf_veto_reason("SELL", 4127.18, tested_zone) is None


def test_nearest_directional_zone_picks_supply_for_sell_demand_for_buy():
  supply = Zone(4131.0, 4133.0, "supply", touches=0)
  demand = Zone(4100.0, 4102.0, "demand", touches=0)
  zones = [supply, demand]

  assert worker._nearest_directional_zone("SELL", 4127.18, zones) is supply
  assert worker._nearest_directional_zone("BUY", 4105.0, zones) is demand


@pytest.mark.asyncio
async def test_htf_veto_blocks_publish_when_enabled_and_passes_when_disabled(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  # Push the rail/entry far enough from EQ and from each other that A1's
  # guards don't also fire - isolate the HTF veto under test.
  monkeypatch.setattr(worker.settings, "auto_trade_eq_exclusion_fraction", 0.0)
  monkeypatch.setattr(worker.settings, "auto_trade_edge_proximity_atr", 999.0)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # direction="BUY", rail (support) level=4016.8
  spot = worker.AutoTradeSpot(4016.8, now, True)
  # Fresh demand zone below price the BUY hasn't reached yet -> untested-ahead.
  untested_demand = [Zone(4010.0, 4014.0, "demand", touches=0)]

  monkeypatch.setattr(worker.settings, "auto_trade_htf_veto_enabled", True)
  vetoed = await worker._publish_candidate(
    client, "XAU", "1", spot, decision, _scale_context(now),
    htf_zones=untested_demand,
  )
  assert vetoed is None
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:htf_veto", "count",
  )
  assert reject_count is not None and int(reject_count) >= 1

  monkeypatch.setattr(worker.settings, "auto_trade_htf_veto_enabled", False)
  passed = await worker._publish_candidate(
    client, "XAU", "2", spot, decision, _scale_context(now),
    htf_zones=untested_demand,
  )
  assert passed is not None


# --- A5: rejection counters --------------------------------------------------

@pytest.mark.asyncio
async def test_record_gate_reject_increments_condition_counter():
  client = redis_state.get_client()
  await worker._record_gate_reject(client, "XAU", "waiting_for_box")
  await worker._record_gate_reject(client, "XAU", "waiting_for_box")

  count = await client.hget(
    "auto_trade:gate_reject:XAU:waiting_for_box", "count",
  )
  assert int(count) == 2
