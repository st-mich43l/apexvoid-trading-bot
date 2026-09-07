import inspect
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade import worker
from app.core import instrument_geometry
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
    full_tp_pips=50,
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


async def _seed_scanner_range_for_match(match: StrategyMatch, now: int) -> None:
  from app.autotrade.range_context import (
    RangeBarrier,
    RangeContext,
    persist_scanner_range_observation,
  )

  low = RangeBarrier(
    float(match.range_low),
    float(match.range_low) - 0.1,
    float(match.range_low) + 0.1,
    touches=3,
  )
  high = RangeBarrier(
    float(match.range_high),
    float(match.range_high) - 0.1,
    float(match.range_high) + 0.1,
    touches=3,
  )
  width = float(match.range_high) - float(match.range_low)
  context = RangeContext(
    version=1,
    range_id=str(match.range_id),
    symbol="XAU",
    state="confirmed",
    source="scanner",
    execution_timeframe="M5",
    context_timeframes=("M5",),
    lower=float(match.range_low),
    upper=float(match.range_high),
    equilibrium=(float(match.range_low) + float(match.range_high)) / 2,
    width_price=width,
    width_pips=width / 0.1,
    width_atr=width / 2.0,
    lower_barrier=low,
    upper_barrier=high,
    supports=(low,),
    resistances=(high,),
    quality=5.0,
    generated_at=now,
    expires_at=now + 660,
  )
  await persist_scanner_range_observation(
    redis_state.get_client(),
    symbol="XAU",
    context=context,
  )


@pytest.mark.asyncio
async def test_worker_publishes_one_durable_auto_only_candidate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_candidate_ttl": 3600})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
  assert len(entries) == 1, await client.get(
    "auto_trade:last_route_outcome:XAU"
  )
  payload = json.loads(entries[0][1]["payload"])
  assert payload["candidate_id"] == first
  assert payload["setup"] == "Range Box Scalp"
  assert payload["mode"] == "auto_box_scalp"
  assert payload["timeframe"] == "M1"
  assert payload["direction"] == "BUY"
  assert payload["entry_zone"] == {"low": 4016.5, "high": 4017.1}
  assert payload["spot_ts"] == now
  assert payload["version"] == 5
  assert payload["range_id"] == "xau-8034-8050"
  assert payload["range_low"] == 4016.8
  assert payload["range_high"] == 4025.1
  assert payload["full_take_profit_pips"] == 50
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
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
  monkeypatch.setattr(
    worker,
    "classify_regime",
    lambda *args, **kwargs: RegimeInfo(
      "chop", None, 0, 1.0, False, None, ("isolated range test",),
    ),
  )
  forming = AsyncMock()
  monkeypatch.setattr(scanner, "_handle_event", forming)

  result = await worker._handle_event(
    "XAU:M1:1784552400",
    source=source,
    client=client,
  )

  assert result is None
  forming.assert_not_awaited()
  assert await client.xlen("auto_trade:test") == 0
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"
  assert status["gate_source"] == "idle_no_match"


@pytest.mark.asyncio
async def test_worker_routes_scanner_strategy_without_regime_confirmation(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _strategy_match(now)
  await client.set(strategy_match_key("XAU"), match.to_json(), ex=420)
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_candidate_ttl": 3600})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
  # The scanner-routed autonomous path publishes only TradePlan V8 now (see
  # docs/adr-trade-plan-v8-cutover.md "Legacy autonomous removal") - no V6
  # candidate is ever written to the candidate stream, regime confirmation
  # or not. This match has no thesis_id/confirmed setup_lifecycle record
  # (out of scope for this test), so TradePlan also does not publish here;
  # test_publish_trade_plan_v8.py covers the TradePlan-publishes-given-a-confirmed-
  # setup case directly.
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 0, await client.get(
    "auto_trade:last_route_outcome:XAU"
  )
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "strategy_match_waiting"
  assert status["gate_source"] == "scanner_strategy_match"
  assert status["strategy_match"]["id"] == match.match_id
  assert status["strategy_match"]["strategy"] == "Liquidity Sweep"
  assert status["direction"] == "BUY"


@pytest.mark.asyncio
async def test_worker_publishes_range_match_as_strategy_and_disarms_edge(
  monkeypatch,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _range_strategy_match(now)
  await _seed_scanner_range_for_match(match, now)
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_candidate_ttl": 3600})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
  assert payload["version"] == 5
  assert payload["timeframe"] == "M5"
  assert payload["mode"] == "auto_strategy_match"
  assert payload["setup"] == "Range Edge Scalp"
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": "strict",})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
@pytest.mark.parametrize(
  ("guard_mode", "publishes"),
  [("observe", False), ("strict", False)],
)
async def test_private_range_regime_guard_is_profile_aware(
  monkeypatch,
  guard_mode,
  publishes,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": guard_mode,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_eq_exclusion_fraction": 0.0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_edge_proximity_atr": 999.0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_htf_veto_enabled": False})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_opposing_barrier_veto_enabled": False,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_overlap_veto_enabled": False})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_cooldown_enabled": False})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  candidate_id = await worker._publish_candidate(
    client,
    "XAU",
    "1784552400",
    worker.AutoTradeSpot(4016.8, now, True),
    _decision(),
    _scale_context(now),
    regime=RegimeInfo(
      "trend", "up", 5, 1.3, True, None, ("forced trend",),
    ),
  )

  assert (candidate_id is not None) is publishes
  assert await client.xlen("auto_trade:test") == int(publishes)
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:range_edge_not_chop", "count",
  )
  assert bool(reject_count) is (not publishes)


@pytest.mark.asyncio
async def test_non_range_edge_strategy_match_ignores_regime(monkeypatch):
  """Box Breakout / Liquidity Sweep / Mapped Zone Reaction matches are
  trend/breakout-appropriate by design and must NOT be gated by chop -
  only Range Edge Scalp's mean-reversion premise requires it.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _strategy_match(now)  # Liquidity Sweep, not is_range_edge
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_box_retire_seconds": 3600,})
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  assert await worker._handle_event(
    "XAU:M5:1784552400",
    client=client,
  ) is None

  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  assert await scanner._handle_event(
    "XAU:M1:1784552400",
    client=client,
  ) == []
  assert await client.xlen("auto_trade:candidates") == 0


@pytest.mark.asyncio
async def test_candidate_fails_closed_on_news_missing_or_stale_spot(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_trend_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
    target_prices=(4025.5,),
    targets_pips=(90,),
    confluence=2,
    reasons=("forced",),
  )
  monkeypatch.setattr(worker, "evaluate_trend_gate", lambda *a, **k: trend_decision)

  result = await worker._handle_event(
    "XAU:M1:1784552400", source=source, client=client,
  )

  assert result is None
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 0
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"
  assert status["arbitration"]["reason_code"] == "no_intent"


@pytest.mark.asyncio
async def test_box_scalp_fires_in_chop_even_when_trend_also_candidate(
  monkeypatch,
):
  """Regression guard for the fix above: chop regime classification and
  box_eligibility telemetry must still work exactly as before when trend is
  ALSO (spuriously) a candidate - the new regime gate must not accidentally
  break box eligibility reporting during genuine chop. The private M1 range
  gate itself no longer enters arbitration at all (retired as an autonomous
  setup source, P2), so - unlike before - it can no longer win or appear in
  ordered_intent_ids; this test now guards that box_eligibility keeps
  reporting eligible=True as pure diagnostic telemetry even though nothing
  built from it ever reaches arbitration.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_trend_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
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
    target_prices=(4025.5,),
    targets_pips=(90,),
    confluence=2,
    reasons=("forced",),
  )
  monkeypatch.setattr(worker, "evaluate_trend_gate", lambda *a, **k: trend_decision)

  result = await worker._handle_event(
    "XAU:M1:1784552400", source=source, client=client,
  )

  assert result is None
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 0
  status = json.loads(await client.get("auto_trade:last_gate:XAU"))
  assert status["state"] == "idle_no_match"
  assert not any(
    intent_id.startswith("range:")
    for intent_id in status["arbitration"]["ordered_intent_ids"]
  )


@pytest.mark.asyncio
async def test_trend_candidate_carries_scale_context_for_scale_in_add_evaluation(
  monkeypatch,
):
  """Regression guard for the dead-plumbing bug found alongside pullback
  add: before this, no regime="trend" candidate ever carried displacement/
  BOS/counter-BOS/extreme/rejection context, so ScaleInTriggerPlanner in
  ctrader-engine could never actually accept a scale-in add in production
  (momentum's own conditions had nothing to evaluate against). This proves
  _publish_trend_candidate now attaches the same scale-context fields the
  box-scalp path already carried.

  Calls _publish_trend_candidate directly rather than through
  worker._handle_event's autonomous wiring - the private trend detector has
  no autonomous publish call site anymore (see
  docs/adr-trade-plan-v8-cutover.md "Legacy autonomous removal"), but the
  function itself is unchanged and still directly unit-tested, same as
  _publish_strategy_match/_publish_candidate elsewhere in this file.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_trend_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  trend_context = AutoScaleContext(
    bar_ts=now - 60,
    atr=1.2,
    structure_swing=4014.8,
    displacement_direction="up",
    displacement_age_bars=1,
    bos_direction="up",
    bos_ts=now - 60,
    opposing_level_distance_atr=None,
    counter_bos_ts=now - 300,
    extreme_price=4020.0,
    extreme_ts=now - 30,
    rejection_confirmed=True,
  )
  monkeypatch.setattr(
    worker, "build_auto_scale_context", lambda *a, **k: trend_context,
  )
  trend_regime = RegimeInfo("trend", "up", 2, 1.3, True, None, ("forced trend",))
  trend_decision = TrendDecision(
    "candidate",
    direction="BUY",
    mode="pullback",
    entry_zone=(4016.0, 4016.5),
    key_level=4016.2,
    atr=1.2,
    structure_swing=4010.0,
    target_prices=(4025.5,),
    targets_pips=(90,),
    confluence=2,
    reasons=("forced",),
  )

  candidate_id = await worker._publish_trend_candidate(
    client,
    "XAU",
    "1784552400",
    worker.AutoTradeSpot(4017.2, now, True),
    trend_regime,
    trend_decision,
    frames={"M1": _frame()},
  )

  assert candidate_id is not None
  entries = await client.xrange("auto_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["mode"] == "auto_trend_pullback"
  assert payload["regime"] == "trend"
  assert payload["displacement_direction"] == "up"
  assert payload["displacement_age_bars"] == 1
  assert payload["bos_direction"] == "up"
  assert payload["counter_bos_ts"] == now - 300
  assert payload["extreme_price"] == 4020.0
  assert payload["extreme_ts"] == now - 30
  assert payload["rejection_confirmed"] is True
  assert "add_zone_side" in payload


def test_worker_source_has_no_direct_scanner_market_map_or_telegram_import():
  """Worker must not pull scanner/detectors/market_map/Telegram client.

  Catches static imports, function-body imports, importlib.import_module,
  and __import__ string references to the forbidden modules.
  """
  import ast
  from pathlib import Path

  forbidden = frozenset({
    "app.analysis.scanner",
    "app.analysis.detectors",
    "app.analysis.market_map",
    "app.bot.client",
  })
  source_path = Path(inspect.getsourcefile(worker) or worker.__file__)
  source = source_path.read_text(encoding="utf-8")
  tree = ast.parse(source, filename=str(source_path))
  found: set[str] = set()

  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        name = alias.name
        if name in forbidden or any(
          name.startswith(f"{mod}.") for mod in forbidden
        ):
          found.add(name)
    elif isinstance(node, ast.ImportFrom):
      module = node.module or ""
      if module in forbidden or any(
        module.startswith(f"{mod}.") for mod in forbidden
      ):
        found.add(module)
      # from app.bot import client
      if module == "app.bot" and any(
        alias.name == "client" for alias in node.names
      ):
        found.add("app.bot.client")
      if module == "app.analysis" and any(
        alias.name in {"scanner", "detectors", "market_map"}
        for alias in node.names
      ):
        found.add(f"app.analysis.{next(a.name for a in node.names if a.name in {'scanner', 'detectors', 'market_map'})}")
    elif isinstance(node, ast.Call):
      func = node.func
      is_import_module = (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and (
          (isinstance(func.value, ast.Name) and func.value.id == "importlib")
          or (
            isinstance(func.value, ast.Attribute)
            and func.value.attr == "importlib"
          )
        )
      )
      is_builtin_import = (
        isinstance(func, ast.Name) and func.id == "__import__"
      )
      if (is_import_module or is_builtin_import) and node.args:
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
          name = arg0.value
          if name in forbidden or any(
            name.startswith(f"{mod}.") for mod in forbidden
          ):
            found.add(name)

  # String-literal import targets (e.g. importlib.import_module("app.bot.client"))
  # already covered via AST; also fail closed on explicit "from app… import"
  # substrings that somehow evade parse (encoding tricks).
  for needle in (
    "from app.analysis.scanner",
    "from app.analysis.detectors",
    "from app.analysis.market_map",
    "from app.bot.client",
    "importlib.import_module(\"app.bot.client\")",
    "importlib.import_module('app.bot.client')",
    "__import__(\"app.bot.client\")",
    "__import__('app.bot.client')",
  ):
    if needle in source:
      found.add(needle)

  assert not found, f"worker forbidden layer imports: {sorted(found)}"


def test_trend_bias_is_metadata_for_counter_direction_candidate():
  regime = RegimeInfo(
    "trend",
    "up",
    3,
    1.3,
    False,
    None,
    ("htf_bias=down", "relationship_to_bias=counter_bias"),
  )

  assert worker._trend_bias_metadata(regime, "BUY") == (
    "bearish",
    "counter_bias",
  )


def test_trend_groups_are_scoped_to_the_structural_zone():
  first = TrendDecision(
    "candidate",
    direction="BUY",
    mode="pullback",
    entry_zone=(4010.0, 4011.0),
    key_level=4010.5,
  )
  second = replace(
    first,
    entry_zone=(4016.0, 4017.0),
    key_level=4016.5,
  )

  assert worker._trend_group_id("XAU", first) != worker._trend_group_id(
    "XAU",
    second,
  )


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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": "strict",})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_eq_exclusion_fraction": 0.15})
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


@pytest.mark.no_database
def test_nearest_directional_zone_picks_supply_for_sell_demand_for_buy():
  supply = Zone(4131.0, 4133.0, "supply", touches=0)
  demand = Zone(4100.0, 4102.0, "demand", touches=0)
  zones = [supply, demand]

  assert worker._nearest_directional_zone("SELL", 4127.18, zones) is supply
  assert worker._nearest_directional_zone("BUY", 4105.0, zones) is demand


@pytest.mark.no_database
def test_nearest_directional_zone_skips_entry_structure_same_wall():
  """SELL inside supply must not treat that supply as stop-side opposing."""
  entry_supply = Zone(4125.0, 4130.0, "supply", touches=1)
  higher_supply = Zone(4140.0, 4142.0, "supply", touches=0)
  zones = [entry_supply, higher_supply]

  assert worker._nearest_directional_zone(
    "SELL", 4127.5, zones,
  ) is higher_supply
  assert worker._nearest_directional_zone(
    "SELL",
    4127.5,
    zones,
    candidate_entry_low=4125.0,
    candidate_entry_high=4130.0,
    atr=4.0,
    pip_size=0.1,
  ) is higher_supply
  # Only the entry wall present → no opposing attachment.
  assert worker._nearest_directional_zone(
    "SELL",
    4127.5,
    [entry_supply],
    candidate_entry_low=4125.0,
    candidate_entry_high=4130.0,
    atr=4.0,
    pip_size=0.1,
  ) is None


@pytest.mark.asyncio
async def test_htf_veto_is_preference_telemetry_not_a_hard_block(
  monkeypatch,
):
  # "htf_veto" is a PREFERENCE_TELEMETRY_REASONS condition
  # (execution_policy.py) - classify_guard_severity checks that set before
  # guard_mode, so it always returns ALLOW_WITH_WARNING/hard_block=False
  # regardless of auto_trade_htf_veto_enabled or guard_mode="strict". This
  # now matches every other soft structural signal (opposing_barrier,
  # zone_cooldown, ...): warn, don't silently drop a confirmed setup.
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": "strict",})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  # Push the rail/entry far enough from EQ and from each other that A1's
  # guards don't also fire - isolate the HTF veto under test.
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_eq_exclusion_fraction": 0.0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_edge_proximity_atr": 999.0})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # direction="BUY", rail (support) level=4016.8
  spot = worker.AutoTradeSpot(4016.8, now, True)
  # Fresh demand zone below price the BUY hasn't reached yet -> untested-ahead.
  untested_demand = [Zone(4010.0, 4014.0, "demand", touches=0)]

  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_htf_veto_enabled": True})
  published_enabled = await worker._publish_candidate(
    client, "XAU", "1", spot, decision, _scale_context(now),
    htf_zones=untested_demand,
  )
  assert published_enabled is not None

  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_htf_veto_enabled": False})
  published_disabled = await worker._publish_candidate(
    client, "XAU", "2", spot, decision, _scale_context(now),
    htf_zones=untested_demand,
  )
  assert published_disabled is not None


# --- opposing-barrier veto (22 Jul incident: strategy_match BUY filled 20
# pips below a published round-number supply level with no check at all) ---


def test_opposing_barrier_reason_ahead_of_nearby_supply_zone_is_telemetry_only():
  # "opposing_barrier" is a PREFERENCE_TELEMETRY_REASONS condition
  # (execution_policy.py) - _opposing_barrier_reason's wrapper only
  # surfaces a non-None reason for classify_guard_severity's hard_block
  # path, which this "ahead, not inside" relationship never reaches. Only
  # entry literally INSIDE an opposing barrier still hard-blocks via the
  # hard_geometry=True path (see
  # test_opposing_barrier_reason_vetoes_buy_inside_opposing_supply below).
  supply = [Zone(4017.5, 4018.0, "supply", touches=2)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, supply, [], 0.5,
  )
  assert reason is None


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


def test_opposing_barrier_reason_round_number_level_is_telemetry_either_direction():
  # A round-number level isn't sided like a Zone: it can cap a BUY from below
  # or a SELL from above, unlike supply/demand - but same as the Zone case,
  # "ahead of, not inside" is preference telemetry, not a hard veto.
  round_level = [Level(price=4020.0, kind="round", touches=3, band=0.3)]
  buy_reason = worker._opposing_barrier_reason(
    "BUY", 4019.5, 1.2, [], round_level, 0.5,
  )
  sell_reason = worker._opposing_barrier_reason(
    "SELL", 4020.5, 1.2, [], round_level, 0.5,
  )
  assert buy_reason is None
  assert sell_reason is None


def test_opposing_barrier_reason_respects_disabled_atr_or_buffer():
  supply = [Zone(4017.5, 4018.0, "supply", touches=2)]
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, None, supply, [], 0.5,
  ) is None
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, supply, [], 0.0,
  ) is None


@pytest.mark.asyncio
async def test_opposing_barrier_ahead_of_strategy_match_round_number_still_publishes(
  monkeypatch,
):
  """Originally reproduced the 22 Jul incident (a Box Breakout-style
  strategy_match BUY filled straight into an untested round-number supply
  level with no check at all). The check now exists and runs
  (_publish_strategy_match calls _opposing_barrier_reason either way), but
  "opposing_barrier" is PREFERENCE_TELEMETRY_REASONS
  (execution_policy.py) - an ahead-of-entry barrier is recorded and
  warned on, not silently dropped as a hard veto, matching every other
  soft structural signal. auto_trade_opposing_barrier_veto_enabled no
  longer changes the outcome for this condition either way, so there is
  nothing left to contrast a second publish attempt against.
  """
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _strategy_match(now)  # BUY, entry 4016.5-4017.4
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": "strict",})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_opposing_barrier_veto_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_opposing_barrier_atr": 0.5})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  spot = worker.AutoTradeSpot(4017.2, now, True)
  round_level = [Level(price=4017.5, kind="round", touches=4, band=0.1)]

  published = await worker._publish_strategy_match(
    client, "XAU", spot, match, htf_levels=round_level,
  )
  assert published is not None


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
async def test_counter_bias_target_barrier_adapts_before_eq(monkeypatch):
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
    entry_low=4072.0,
    entry_high=4074.2,
    current_price=4072.88,
    structure_swing=4070.5,
    targets_pips=(30, 60, 90, 111),
    tags=("counter_bias",),
    target_price=4084.0,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  barrier = Zone(4080.0, 4082.0, "supply", touches=0)

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4072.88, now, True),
    match,
    consume_redis_match=False,
    match_source="market_map_strategy",
    htf_zones=[barrier],
  )

  assert candidate_id is not None, await client.get(
    "auto_trade:last_route_outcome:XAU"
  )
  entries = await client.xrange("auto_trade:test")
  payload = json.loads(entries[0][1]["payload"])
  assert payload["target_price"] < barrier.low
  assert payload["target_adjustment"]["selected_target_pips"] == 60


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
    entry_low=4072.0,
    entry_high=4074.2,
    current_price=4072.88,
    structure_swing=4070.5,
    targets_pips=(30, 60, 90, 111),
    tags=("counter_bias",),
    target_price=4084.0,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4072.88, now, True),
    match,
    consume_redis_match=False,
    match_source="market_map_strategy",
  )

  assert candidate_id == match.match_id, await client.get(
    "auto_trade:last_route_outcome:XAU"
  )
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


def test_opposing_barrier_side_unclear_zone_containment_is_telemetry_only():
  # Bug since classify_barrier_relationship's introduction (13414b7): both
  # branches of "overlapping_ambiguous" if barrier.side == "neutral" or not
  # opposing else "overlapping_ambiguous" returned the identical literal,
  # so a zone whose side couldn't be cleanly classified as opposing this
  # direction was hard-blocked exactly like a confirmed, unambiguous one
  # (test_opposing_barrier_reason_vetoes_buy_inside_opposing_supply, still
  # unchanged below). Live 2026-08-13: this was the dominant blocker in
  # production (entry_inside_opposing_zone, ~58% of all v8 plan rejections
  # over 12h) on zones logged as "supply_demand" -- a side that matches
  # neither {supply,resistance} nor {demand,support} for any direction.
  neutral = [Zone(4116.0, 4127.0, "neutral", touches=8)]
  source = worker._structural_source_identity(
    strategy="legacy", family="", structural_source="legacy",
    low=4200.0, high=4200.0, key_level=None,
  )
  decision = worker._opposing_barrier_decision(
    "BUY", 4116.25, None, 1.2, neutral, [], 0.5,
    source=source, guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert decision.hard_block is False
  assert decision.reason_code == "entry_inside_ambiguous_zone"
  assert decision.measured["relationship"] == "overlapping_neutral"

  reason = worker._opposing_barrier_reason(
    "BUY", 4116.25, 1.2, neutral, [], 0.5,
  )
  assert reason is None  # hard_block-only wrapper: nothing to veto on


def test_opposing_barrier_ahead_distance_math_unchanged_when_not_contained():
  # Regression guard: an entry genuinely ahead of (not inside) the barrier
  # still uses the pre-existing ATR/buffer tolerance logic to DETECT the
  # barrier - that math is unchanged. Only the severity changed
  # ("opposing_barrier" moved to PREFERENCE_TELEMETRY_REASONS, so
  # _opposing_barrier_reason's hard_block-only wrapper now returns None
  # here - see test_opposing_barrier_reason_ahead_of_nearby_supply_zone_is_telemetry_only).
  # Go one level down to _opposing_barrier_decision to prove the distance
  # detection itself still fires exactly as before.
  # distance = 4116.0 - 4115.5 = 0.5, within buffer_atr(0.5) * atr(1.2) = 0.6.
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  source = worker._structural_source_identity(
    strategy="legacy", family="", structural_source="legacy",
    low=4115.5, high=4115.5, key_level=None,
  )
  decision = worker._opposing_barrier_decision(
    "BUY", 4115.5, None, 1.2, supply, [], 0.5,
    source=source, guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert decision.reason_code == "opposing_barrier"
  assert decision.measured["relationship"] == "opposing_ahead"
  assert decision.measured["distance"] == pytest.approx(0.5)
  assert decision.hard_block is False

  # And still respects the buffer: too far away, no barrier detected at all.
  far = worker._opposing_barrier_decision(
    "BUY", 4110.0, None, 1.2, supply, [], 0.5,
    source=source, guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert far.reason_code == "no_opposing_barrier"


def test_opposing_barrier_reason_containment_is_boundary_inclusive():
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  low_edge = worker._opposing_barrier_reason("BUY", 4116.0, 1.2, supply, [], 0.5)
  high_edge = worker._opposing_barrier_reason("BUY", 4127.0, 1.2, supply, [], 0.5)
  assert low_edge is not None and "inside opposing" in low_edge
  assert high_edge is not None and "inside opposing" in high_edge


def test_opposing_barrier_condition_containment_has_its_own_counter(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_opposing_barrier_atr": 0.5})
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  reason = worker._opposing_barrier_reason("BUY", 4116.25, 1.2, supply, [], 0.5)

  return_value = worker._opposing_barrier_condition(reason)

  assert return_value == "entry_inside_opposing_zone"
  assert return_value != "opposing_barrier"


def test_instrument_currencies_splits_six_letter_fx_pair():
  assert worker._instrument_currencies("GBPJPY") == ("GBP", "JPY")
  assert worker._instrument_currencies("eurusd") == ("EUR", "USD")
  assert worker._instrument_currencies("XAU") is None
  assert worker._instrument_currencies("XA1JPY") is None


@pytest.mark.asyncio
async def test_event_cluster_guard_noop_when_disabled(monkeypatch):
  install_runtime_overrides(
    monkeypatch,
    overrides={"actionability.gates.event_cluster_guard_enabled": False},
  )
  blow_up = AsyncMock(side_effect=AssertionError("must not query when disabled"))
  monkeypatch.setattr(worker, "nearest_currency_event", blow_up)

  hit = await worker._event_cluster_guard("GBPJPY", 1_780_000_000)

  assert hit is None
  blow_up.assert_not_called()


@pytest.mark.asyncio
async def test_event_cluster_guard_fires_when_both_currencies_have_events(
  monkeypatch,
):
  # 2026 dig: a BoE print and a BoJ statement in the same 48h window
  # compound GBPJPY volatility rather than adding it.
  install_runtime_overrides(
    monkeypatch,
    overrides={
      "actionability.gates.event_cluster_guard_enabled": True,
      "actionability.gates.event_cluster_span_hours": 48,
      "actionability.gates.event_cluster_guard_minutes": 180,
    },
  )
  now = 1_780_000_000
  gbp_event = {"ts_utc": now + 3600, "currency": "GBP", "title": "BoE CPI"}
  jpy_event = {"ts_utc": now - 1800, "currency": "JPY", "title": "BoJ Policy"}

  async def fake_nearest(currency, start, end, anchor):
    return gbp_event if currency == "GBP" else jpy_event

  monkeypatch.setattr(worker, "nearest_currency_event", fake_nearest)

  hit = await worker._event_cluster_guard("GBPJPY", now)

  assert hit == jpy_event  # nearer to `now` than the GBP event


@pytest.mark.asyncio
async def test_event_cluster_guard_noop_when_only_one_currency_has_an_event(
  monkeypatch,
):
  install_runtime_overrides(
    monkeypatch,
    overrides={"actionability.gates.event_cluster_guard_enabled": True},
  )
  now = 1_780_000_000

  async def fake_nearest(currency, start, end, anchor):
    return {"ts_utc": now, "currency": "GBP"} if currency == "GBP" else None

  monkeypatch.setattr(worker, "nearest_currency_event", fake_nearest)

  hit = await worker._event_cluster_guard("GBPJPY", now)

  assert hit is None


@pytest.mark.asyncio
async def test_news_guard_hit_falls_back_to_single_event_window(monkeypatch):
  install_runtime_overrides(
    monkeypatch,
    overrides={"actionability.gates.event_cluster_guard_enabled": False},
  )
  single_event = {"ts_utc": 1_780_000_000, "currency": "USD"}
  monkeypatch.setattr(
    worker, "event_in_window", AsyncMock(return_value=single_event),
  )

  hit = await worker._news_guard_hit("EURUSD", 1_780_000_000)

  assert hit == single_event


def test_defended_level_guard_blocks_buy_within_buffer(monkeypatch):
  # Intervention sells USDJPY near 160 — only BUY is hard-blocked in-band.
  monkeypatch.setattr(
    instrument_geometry, "defended_levels", lambda symbol: (160.0,),
  )
  monkeypatch.setattr(
    instrument_geometry, "defended_level_buffer_price", lambda symbol: 0.30,
  )

  decision = worker._defended_level_guard(
    "USDJPY",
    159.85,
    direction="BUY",
    guard_mode=worker.GUARD_MODE_OBSERVE,
  )

  assert decision.hard_block is True
  assert decision.reason_code == "entry_near_defended_level"
  # Unconditional even in observe mode — hard_geometry=True.
  decision_strict = worker._defended_level_guard(
    "USDJPY",
    159.85,
    direction="BUY",
    guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert decision_strict.hard_block is True


def test_defended_level_guard_allows_sell_within_buffer(monkeypatch):
  # Prod 2026-08-25: SELLs at ~159.4 were wrongly sterilized by a symmetric
  # 100-pip band. SELLs near 160 are intervention-aligned.
  monkeypatch.setattr(
    instrument_geometry, "defended_levels", lambda symbol: (160.0,),
  )
  monkeypatch.setattr(
    instrument_geometry, "defended_level_buffer_price", lambda symbol: 0.30,
  )

  decision = worker._defended_level_guard(
    "USDJPY",
    159.85,
    direction="SELL",
    guard_mode=worker.GUARD_MODE_OBSERVE,
  )

  assert decision.hard_block is False
  assert decision.reason_code == "defended_level_sell_aligned"


def test_defended_level_guard_allows_buy_outside_buffer(monkeypatch):
  monkeypatch.setattr(
    instrument_geometry, "defended_levels", lambda symbol: (160.0,),
  )
  monkeypatch.setattr(
    instrument_geometry, "defended_level_buffer_price", lambda symbol: 0.30,
  )

  decision = worker._defended_level_guard(
    "USDJPY",
    159.40,
    direction="BUY",
    guard_mode=worker.GUARD_MODE_OBSERVE,
  )

  assert decision.hard_block is False
  assert decision.reason_code == "no_defended_level_nearby"


def test_defended_level_guard_noop_when_unconfigured(monkeypatch):
  monkeypatch.setattr(instrument_geometry, "defended_levels", lambda symbol: ())
  monkeypatch.setattr(
    instrument_geometry, "defended_level_buffer_price", lambda symbol: 0.0,
  )

  decision = worker._defended_level_guard(
    "EURUSD",
    1.16,
    direction="BUY",
    guard_mode=worker.GUARD_MODE_OBSERVE,
  )

  assert decision.hard_block is False
  assert decision.reason_code == "no_defended_level_configured"


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
async def test_zone_cooldown_reason_ignores_legacy_ambiguous_marker():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )

  assert reason is None


@pytest.mark.asyncio
async def test_zone_cooldown_reason_vetoes_confirmed_stop_loss(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_cooldown_enabled": True,})
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({
      "entry_price": 4116.25,
      "stop_price": 4111.54,
      "closed_at": 1000,
      "reason": "stop_loss",
      "confidence": "confirmed",
    }),
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
async def test_publish_candidate_during_active_cooldown_is_telemetry_only(monkeypatch):
  # "zone_cooldown" is a PREFERENCE_TELEMETRY_REASONS condition
  # (execution_policy.py) - classify_guard_severity never hard-blocks it
  # regardless of guard_mode, so an active cooldown is recorded and warned
  # on rather than silently dropping a confirmed setup. _record_gate_reject
  # only fires on the hard_block branch, so the reject counter stays unset
  # too.
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": "strict",})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_cooldown_enabled": True,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_cooldown_atr": 1.0})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # BUY, rail level=4016.8
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({
      "entry_price": 4017.0,
      "stop_price": 4014.8,
      "closed_at": now,
      "reason": "stop_loss",
      "confidence": "confirmed",
    }),
  )
  spot = worker.AutoTradeSpot(4017.2, now, True)

  result = await worker._publish_candidate(
    client, "XAU", "1", spot, decision, _scale_context(now),
  )

  assert result is not None


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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  decision = _decision()  # BUY, rail level=4016.8, EQ far from spot
  spot = worker.AutoTradeSpot(4016.8, now, True)
  market_map = _market_map([
    _map_entry("sell", 4016.0, 4018.0),
    _map_entry("buy", 4015.0, 4017.5),
  ])

  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_overlap_veto_enabled": False})
  passed = await worker._publish_candidate(
    client, "XAU", "1", spot, decision, _scale_context(now),
    market_map=market_map,
  )
  assert passed is not None

  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_overlap_veto_enabled": True})
  warned = await worker._publish_candidate(
    client, "XAU", "2", spot, decision, _scale_context(now),
    market_map=market_map,
  )
  assert warned is not None
  reject_count = await client.hget(
    "auto_trade:gate_reject:XAU:overlapping_zone_conflict", "count",
  )
  assert reject_count is None
  advisory = await client.hget(
    "auto_trade:guard_evaluation:XAU:overlap:allow_with_warning", "count",
  )
  assert advisory is not None and int(advisory) >= 1
