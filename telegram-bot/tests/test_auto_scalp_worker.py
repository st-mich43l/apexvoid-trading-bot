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
from app.autotrade.map_strategy import ActionableMapEntry
from app.autotrade.trend import RegimeInfo, TrendDecision
from app.analysis.types import Level, Zone
from app.analysis.market_map import MapEntry, MarketMap


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
    sweep_low=4015.9,
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
  assert payload["sweep_low"] == 4015.9
  assert payload["sweep_high"] is None
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
async def test_range_edge_match_blocked_outside_chop_regime(monkeypatch):
  """Range Edge Scalp ("Range Box Scalp" label) is a mean-reversion play on
  an actual consolidation, same as the private box gate - it must not fire
  once regime has moved past chop (22 Jul incident: this exact path filled
  a BUY straight into a sharp post-rally pullback, stopped in under a
  minute).
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _range_strategy_match(now)
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  trend_regime = RegimeInfo("trend", "up", 5, 1.3, True, None, ("forced trend",))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4017.2, now, True),
    match,
    regime=trend_regime,
  )

  assert candidate_id is None
  assert await client.xlen("auto_trade:test") == 0
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:range_edge_not_chop", "count",
  )
  assert reject_count is not None and int(reject_count) >= 1


@pytest.mark.asyncio
async def test_non_range_edge_strategy_match_ignores_regime(monkeypatch):
  """Box Breakout / Liquidity Sweep / Mapped Zone Reaction matches are
  trend/breakout-appropriate by design and must NOT be gated by chop -
  only Range Edge Scalp's mean-reversion premise requires it.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _strategy_match(now)  # Liquidity Sweep, not is_range_edge
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  trend_regime = RegimeInfo("trend", "up", 5, 1.3, True, None, ("forced trend",))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4017.2, now, True),
    match,
    regime=trend_regime,
  )

  assert candidate_id == match.match_id
  assert await client.xlen("auto_trade:test") == 1


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
async def test_box_scalp_does_not_fire_outside_chop_regime(
  monkeypatch,
):
  """Box-scalp is a mean-reversion play on an actual consolidation, so it
  must lose selection once regime has moved past chop even when its own
  confluence would otherwise "win" the comparison against trend (22 Jul
  incident: a box-labeled BUY filled straight into a sharp post-rally
  pullback and was stopped in well under a minute). The trend candidate
  must be the one selected here instead - not neither.
  """
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
  assert payload["mode"] == "auto_trend_pullback"
  assert payload["setup"] == "Trend Pullback"
  assert payload["regime"] == "trend"
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["regime"] == "trend"
  assert status["box_state"] == "candidate"
  assert status["trend_state"] == "candidate"
  assert status["trend_mode"] == "pullback"
  assert status["direction"] == "BUY"
  assert status["selected_strategy"] == "Trend Pullback"
  assert status["selection_state"] == "published"


@pytest.mark.asyncio
async def test_box_scalp_fires_in_chop_even_when_trend_also_candidate(
  monkeypatch,
):
  """Regression guard for the fix above: chop regime must still let
  box-scalp win the confluence comparison exactly as before when trend is
  ALSO (spuriously) a candidate - the new regime gate must not accidentally
  suppress box-scalp during genuine chop.
  """
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
  chop_regime = RegimeInfo("chop", None, 0, 0.5, False, None, ("forced chop",))
  monkeypatch.setattr(
    worker, "classify_regime", lambda frames, decision, cfg: chop_regime,
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
  assert payload["regime"] == "chop"


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


# --- opposing-barrier veto (22 Jul incident: strategy_match BUY filled 20
# pips below a published round-number supply level with no check at all) ---


def test_opposing_barrier_reason_buy_vetoed_by_nearby_supply_zone():
  supply = [Zone(4017.5, 4018.0, "supply", touches=2)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, supply, [], 0.5,
  )
  assert reason is not None
  assert "supply" in reason


def test_opposing_barrier_reason_buy_ignores_supply_outside_buffer():
  far_supply = [Zone(4020.0, 4020.5, "supply", touches=2)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, far_supply, [], 0.5,
  )
  assert reason is None


def test_opposing_barrier_reason_ignores_zone_behind_entry():
  # A supply zone below current price is behind a BUY, not ahead of it.
  behind = [Zone(4010.0, 4011.0, "supply", touches=0)]
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, behind, [], 0.5,
  ) is None


def test_opposing_barrier_reason_round_number_level_blocks_either_direction():
  # A round-number level isn't sided like a Zone: it can cap a BUY from below
  # or a SELL from above, unlike supply/demand.
  round_level = [Level(price=4020.0, kind="round", touches=3, band=0.3)]
  buy_reason = worker._opposing_barrier_reason(
    "BUY", 4019.5, 1.2, [], round_level, 0.5,
  )
  sell_reason = worker._opposing_barrier_reason(
    "SELL", 4020.5, 1.2, [], round_level, 0.5,
  )
  assert buy_reason is not None and "round" in buy_reason
  assert sell_reason is not None and "round" in sell_reason


def test_opposing_barrier_reason_respects_disabled_atr_or_buffer():
  supply = [Zone(4017.5, 4018.0, "supply", touches=2)]
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, None, supply, [], 0.5,
  ) is None
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, supply, [], 0.0,
  ) is None


@pytest.mark.asyncio
async def test_opposing_barrier_blocks_strategy_match_into_round_number(
  monkeypatch,
):
  """Reproduces the 22 Jul incident: a Box Breakout-style strategy_match BUY
  filled straight into an untested round-number supply level. Before this
  fix, _publish_strategy_match had no opposing-barrier check at all.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _strategy_match(now)  # BUY, entry 4016.5-4017.4
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker.settings, "auto_trade_opposing_barrier_veto_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_opposing_barrier_atr", 0.5)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  spot = worker.AutoTradeSpot(4017.2, now, True)
  round_level = [Level(price=4017.5, kind="round", touches=4, band=0.1)]

  vetoed = await worker._publish_strategy_match(
    client, "XAU", spot, match, htf_levels=round_level,
  )
  assert vetoed is None
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:opposing_barrier", "count",
  )
  assert reject_count is not None and int(reject_count) >= 1

  monkeypatch.setattr(
    worker.settings, "auto_trade_opposing_barrier_veto_enabled", False,
  )
  passed = await worker._publish_strategy_match(
    client, "XAU", spot, match, htf_levels=round_level,
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


@pytest.mark.asyncio
async def test_market_map_actionable_snapshot_and_degenerate_counter_have_ttl():
  client = redis_state.get_client()
  decision = worker.MarketMapStrategyDecision(
    "waiting_for_touch",
    ("no mapped SELL zone within reach",),
    entries_seen=3,
    actionable_entries=(
      ActionableMapEntry(
        "sell", 4087.0, 4095.0, "zone", 8.0, False, 14.12,
      ),
    ),
    filter_counts=(
      ("side", 1),
      ("actionable", 0),
      ("degenerate_width", 1),
      ("distance", 1),
    ),
  )

  await worker._record_market_map_strategy_telemetry(
    client,
    "XAU",
    decision,
  )

  payload = json.loads(
    await client.get("auto_trade:map_strategy:actionable:XAU")
  )
  assert payload == [{
    "contains_price": False,
    "hi": 4095.0,
    "lo": 4087.0,
    "score": 8.0,
    "side": "sell",
    "tier": "zone",
  }]
  ttl = await client.ttl("auto_trade:map_strategy:actionable:XAU")
  assert 0 < ttl <= 3600
  assert int(await client.get(
    "auto_trade:map_zone_rejected:XAU:degenerate_width"
  )) == 1


@pytest.mark.asyncio
async def test_counter_bias_target_barrier_rejects_before_eq(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  base = _strategy_match(now)
  match = replace(
    base,
    match_id=strategy_match_id(
      "XAU", "M1", str(now), "Mapped Zone Reaction", "BUY", 4066.0, 4074.5,
    ),
    source_tf="M1",
    strategy="Mapped Zone Reaction",
    strategy_mode="mapped_zone_reaction",
    direction="BUY",
    entry_low=4066.0,
    entry_high=4074.5,
    current_price=4072.88,
    structure_swing=4066.0,
    targets_pips=(30, 60, 90, 111),
    tags=("counter_bias",),
    target_price=4084.0,
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  barrier = Zone(4078.0, 4080.0, "supply", touches=0)

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4072.88, now, True),
    match,
    consume_redis_match=False,
    match_source="market_map_strategy",
    htf_zones=[barrier],
  )

  assert candidate_id is None
  assert await client.xlen("auto_trade:test") == 0
  assert int(await client.hget(
    "auto_trade:gate_reject:XAU:counter_bias_target_barrier",
    "count",
  )) == 1


@pytest.mark.asyncio
async def test_counter_bias_tag_reaches_candidate_setup_and_stats_label(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  base = _strategy_match(now)
  match = replace(
    base,
    match_id=strategy_match_id(
      "XAU", "M1", str(now), "Mapped Zone Reaction", "BUY", 4066.0, 4074.5,
    ),
    source_tf="M1",
    strategy="Mapped Zone Reaction",
    strategy_mode="mapped_zone_reaction",
    direction="BUY",
    entry_low=4066.0,
    entry_high=4074.5,
    current_price=4072.88,
    structure_swing=4066.0,
    targets_pips=(30, 60, 90, 111),
    tags=("counter_bias",),
    target_price=4084.0,
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4072.88, now, True),
    match,
    consume_redis_match=False,
    match_source="market_map_strategy",
  )

  assert candidate_id == match.match_id
  entries = await client.xrange("auto_trade:test")
  payload = json.loads(entries[0][1]["payload"])
  assert payload["setup"] == "Mapped Zone Reaction · counter_bias"
  assert payload["strategy_tags"] == ["counter_bias"]
  assert payload["target_price"] == 4084.0


# --- Fix 1: opposing-barrier containment gap --------------------------------

def _map_entry(side: str, lo: float, hi: float, *, score: float = 5.0) -> MapEntry:
  return MapEntry(
    side=side, lo=lo, hi=hi, label_lo=int(lo), label_hi=int(hi),
    tier="major", tags=[], score=score,
  )


def _market_map(entries: list[MapEntry], *, price: float = 4118.0) -> MarketMap:
  return MarketMap(
    entries=entries, price=price, eq=None, box_low=None, box_high=None,
    bias="up", bias_tf="M30",
  )


def test_opposing_barrier_reason_vetoes_buy_inside_opposing_supply():
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4116.25, 1.2, supply, [], 0.5,
  )
  assert reason is not None
  assert "inside opposing" in reason
  assert worker._opposing_barrier_condition(reason) == "entry_inside_opposing_zone"


def test_opposing_barrier_reason_vetoes_sell_inside_opposing_demand():
  demand = [Zone(4112.0, 4122.0, "demand", touches=5)]
  reason = worker._opposing_barrier_reason(
    "SELL", 4117.0, 1.2, demand, [], 0.5,
  )
  assert reason is not None
  assert "inside opposing" in reason
  assert worker._opposing_barrier_condition(reason) == "entry_inside_opposing_zone"


def test_opposing_barrier_reason_ahead_logic_unchanged_when_not_contained():
  # Regression guard: an entry genuinely ahead of (not inside) the barrier
  # still uses the pre-existing ATR/buffer tolerance logic, unchanged.
  # distance = 4116.0 - 4115.5 = 0.5, within buffer_atr(0.5) * atr(1.2) = 0.6.
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4115.5, 1.2, supply, [], 0.5,
  )
  assert reason is not None
  assert reason.startswith("Opposing barrier ahead:")
  assert worker._opposing_barrier_condition(reason) == "opposing_barrier"
  # And still respects the buffer: too far away, no veto at all.
  assert worker._opposing_barrier_reason(
    "BUY", 4110.0, 1.2, supply, [], 0.5,
  ) is None


def test_opposing_barrier_reason_containment_is_boundary_inclusive():
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  low_edge = worker._opposing_barrier_reason("BUY", 4116.0, 1.2, supply, [], 0.5)
  high_edge = worker._opposing_barrier_reason("BUY", 4127.0, 1.2, supply, [], 0.5)
  assert low_edge is not None and "inside opposing" in low_edge
  assert high_edge is not None and "inside opposing" in high_edge


def test_opposing_barrier_condition_containment_has_its_own_counter(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_opposing_barrier_atr", 0.5)
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  reason = worker._opposing_barrier_reason("BUY", 4116.25, 1.2, supply, [], 0.5)

  return_value = worker._opposing_barrier_condition(reason)

  assert return_value == "entry_inside_opposing_zone"
  assert return_value != "opposing_barrier"


@pytest.mark.asyncio
async def test_incident_replay_buy_at_4116_25_is_vetoed_by_two_guards(monkeypatch):
  """Replays the 23 Jul 2026 incident numbers directly: a SELL resistance
  band tested 8x at 4,116-4,127, and a Market Map that simultaneously
  publishes BUY 4,112-4,122 and SELL 4,116-4,127 (overlapping 4,116-4,122).
  Both the containment veto and the overlap veto must independently fire.
  """
  entry_reference = 4116.25
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  market_map = _market_map([
    _map_entry("sell", 4116.0, 4127.0),
    _map_entry("buy", 4112.0, 4122.0),
  ])

  barrier_reason = worker._opposing_barrier_reason(
    "BUY", entry_reference, 1.2, supply, [], 0.5,
  )
  overlap_reason = worker._overlapping_zone_conflict_reason(
    entry_reference, market_map,
  )

  assert barrier_reason is not None
  assert worker._opposing_barrier_condition(barrier_reason) == (
    "entry_inside_opposing_zone"
  )
  assert overlap_reason is not None
  assert "demand" in overlap_reason and "supply" in overlap_reason


# --- Fix 3: post-stop-out cooldown ------------------------------------------

@pytest.mark.asyncio
async def test_zone_cooldown_reason_vetoes_same_direction_near_stopped_out_entry():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )

  assert reason is not None
  assert "zone cooldown" in reason


@pytest.mark.asyncio
async def test_zone_cooldown_reason_allows_opposite_direction():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "SELL", 4116.90, 2.0, 1.0,
  )

  assert reason is None


@pytest.mark.asyncio
async def test_zone_cooldown_reason_none_when_marker_absent_or_expired():
  client = redis_state.get_client()
  # Never written / already expired (Redis TTL naturally removes the key) -
  # both look identical from the read side: GET returns None.
  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )
  assert reason is None


@pytest.mark.asyncio
async def test_zone_cooldown_reason_none_outside_atr_band():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4200.0, 2.0, 1.0,
  )

  assert reason is None


@pytest.mark.asyncio
async def test_publish_candidate_is_vetoed_during_active_cooldown(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_atr", 1.0)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # BUY, rail level=4016.8
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4017.0, "stop_price": 4014.8, "closed_at": now}),
  )
  spot = worker.AutoTradeSpot(4017.2, now, True)

  result = await worker._publish_candidate(
    client, "XAU", "1", spot, decision, _scale_context(now),
  )

  assert result is None
  reject_count = await client.hget("auto_trade:gate_reject:XAU:zone_cooldown", "count")
  assert reject_count is not None and int(reject_count) >= 1


# --- Fix 4: overlapping opposing-zone veto ----------------------------------

def test_overlapping_zone_conflict_reason_vetoes_entry_inside_both():
  market_map = _market_map([
    _map_entry("sell", 4116.0, 4127.0),
    _map_entry("buy", 4112.0, 4122.0),
  ])

  reason = worker._overlapping_zone_conflict_reason(4118.0, market_map)

  assert reason is not None
  assert "demand" in reason and "supply" in reason


def test_overlapping_zone_conflict_reason_allows_entry_in_demand_only():
  market_map = _market_map([
    _map_entry("sell", 4116.0, 4127.0),
    _map_entry("buy", 4112.0, 4122.0),
  ])

  reason = worker._overlapping_zone_conflict_reason(4113.0, market_map)

  assert reason is None


def test_has_overlapping_zones_detects_map_self_contradiction():
  overlapping = _market_map([
    _map_entry("sell", 4116.0, 4127.0),
    _map_entry("buy", 4112.0, 4122.0),
  ])
  disjoint = _market_map([
    _map_entry("sell", 4130.0, 4140.0),
    _map_entry("buy", 4100.0, 4110.0),
  ])

  assert worker._has_overlapping_zones(overlapping) is True
  assert worker._has_overlapping_zones(disjoint) is False
  assert worker._has_overlapping_zones(None) is False


@pytest.mark.asyncio
async def test_publish_candidate_overlap_veto_disabled_still_increments_counter(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:test")
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # BUY, rail level=4016.8, EQ far from spot
  spot = worker.AutoTradeSpot(4016.8, now, True)
  market_map = _market_map([
    _map_entry("sell", 4016.0, 4018.0),
    _map_entry("buy", 4015.0, 4017.5),
  ])

  monkeypatch.setattr(worker.settings, "auto_trade_overlap_veto_enabled", False)
  passed = await worker._publish_candidate(
    client, "XAU", "1", spot, decision, _scale_context(now),
    market_map=market_map,
  )
  assert passed is not None

  monkeypatch.setattr(worker.settings, "auto_trade_overlap_veto_enabled", True)
  vetoed = await worker._publish_candidate(
    client, "XAU", "2", spot, decision, _scale_context(now),
    market_map=market_map,
  )
  assert vetoed is None
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:overlapping_zone_conflict", "count",
  )
  assert reject_count is not None and int(reject_count) >= 1
