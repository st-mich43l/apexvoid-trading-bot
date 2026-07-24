"""Replay tests for the post-23-Jul worker veto regression (fix/demo-eval-
worker-veto-regression). Written against current master FIRST - several of
these fail before the production fix lands, proving the regression rather
than just asserting the desired end state.

Baseline compared: 4be59b123604af38df08b56bc37dea02d1d1d59c (PR #87, 23 Jul).
Current master at investigation time: 7ba44e581e17d14566730b7ee9ddd6897e1d39ca.

Production evidence (apexvoid VPS, checked live before writing this file):
  auto_trade:gate_reject:XAU:entry_inside_opposing_zone = 440
  auto_trade:gate_reject:XAU:opposing_barrier = 51
  auto_trade:gate_reject:XAU:overlapping_zone_conflict = 22
  auto_trade:gate_reject:XAU:counter_bias_target_barrier = 39
  auto_trade:gate_reject:XAU:strategy_entry_moved = 12
Live logs showed "Mapped Zone Reaction"/"Range Edge Scalp" repeatedly
rejected with "entry X inside opposing round/reaction Y-Z" at prices that
are each strategy's own structural source, not a genuinely separate barrier.
"""

from dataclasses import replace
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade import worker
from app.autotrade.execution_policy import (
  StructuralBarrier,
  StructuralSourceIdentity,
  classify_barrier_relationship,
  max_entry_drift_pips,
)
from app.autotrade.multi_match import (
  deserialize_matches,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
)
from app.analysis.types import Level, Zone
from app.persistence import redis_state


# ---------------------------------------------------------------------------
# Root cause 1: a structural source vetoes the strategy trading it
# ---------------------------------------------------------------------------

def test_opposing_barrier_reason_excludes_a_key_level_that_is_the_candidates_own_source():
  """Key Level / Mapped Zone Reaction: the candidate's own key-level band is
  passed straight through into `levels` (htf_levels are symbol-wide, not
  filtered per-candidate) - before the fix, a strategy trading INTO its own
  level is indistinguishable from a strategy blocked BY an unrelated one.
  """
  own_level = [Level(price=4055.20, kind="round", touches=3, band=3.34)]
  entry = 4055.20  # dead center of the strategy's own thesis band

  reason = worker._opposing_barrier_reason(
    "BUY", entry, 1.2, [], own_level, 0.5,
    exclude_low=4051.86, exclude_high=4058.54,
  )

  assert reason is None


def test_opposing_barrier_reason_excludes_sells_own_supply_source():
  own_supply = [Zone(4047.24, 4050.56, "supply", touches=2)]
  entry = 4049.0  # inside the SELL's own supply thesis

  reason = worker._opposing_barrier_reason(
    "SELL", entry, 1.2, own_supply, [], 0.5,
    exclude_low=4047.24, exclude_high=4050.56,
  )

  assert reason is None


def test_opposing_barrier_reason_excludes_buys_own_demand_source():
  own_demand = [Zone(4040.0, 4043.0, "demand", touches=2)]
  entry = 4041.5

  reason = worker._opposing_barrier_reason(
    "BUY", entry, 1.2, own_demand, [], 0.5,
    exclude_low=4040.0, exclude_high=4043.0,
  )

  assert reason is None


def test_opposing_barrier_reason_still_vetoes_a_genuinely_separate_barrier():
  """Exclusion must not blanket-disable the guard: an unrelated opposing
  zone that does not overlap the candidate's own source still fires.
  """
  unrelated_supply = [Zone(4116.0, 4127.0, "supply", touches=8)]

  reason = worker._opposing_barrier_reason(
    "BUY", 4116.25, 1.2, unrelated_supply, [], 0.5,
    exclude_low=4000.0, exclude_high=4001.0,
  )

  assert reason is not None
  assert "inside opposing" in reason


def test_opposing_barrier_reason_without_exclusion_args_is_unchanged():
  """Byte-identical default behaviour for every existing caller that does
  not yet pass exclude_low/exclude_high.
  """
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  reason = worker._opposing_barrier_reason("BUY", 4116.25, 1.2, supply, [], 0.5)
  assert reason is not None
  assert "inside opposing" in reason


@pytest.mark.parametrize(
  ("direction", "entry", "target", "zone", "source_side"),
  [
    (
      "BUY", 4116.25, 4130.0,
      Zone(4116.0, 4127.0, "supply", touches=8),
      "demand",
    ),
    (
      "SELL", 4116.25, 4100.0,
      Zone(4105.0, 4117.0, "demand", touches=8),
      "supply",
    ),
  ],
)
def test_unrelated_opposing_structure_warns_in_observe_and_blocks_in_strict(
  direction,
  entry,
  target,
  zone,
  source_side,
):
  source = StructuralSourceIdentity(
    strategy="Mapped Zone Reaction",
    strategy_family="mapped_zone_reaction",
    structural_source="market_map_zone",
    zone_id=f"{source_side}:4000:4002",
    level_id=None,
    key_level=4001.0,
    low=4000.0,
    high=4002.0,
  )

  observed = worker._opposing_barrier_decision(
    direction, entry, target, 1.2, [zone], [], 0.5,
    source=source,
    guard_mode="observe",
  )
  strict = worker._opposing_barrier_decision(
    direction, entry, target, 1.2, [zone], [], 0.5,
    source=source,
    guard_mode="strict",
  )

  assert observed.outcome == "allow_with_warning"
  assert not observed.hard_block
  assert strict.outcome == "block"
  assert strict.hard_block


# ---------------------------------------------------------------------------
# Root cause 5: entry drift formula has no floor
# ---------------------------------------------------------------------------

def test_max_entry_drift_pips_has_a_configured_floor_even_with_tight_atr_and_room():
  """XAU M1 ATR can be small enough that atr_pips and room_cap both
  collapse the effective limit to 3-5 pips - well inside normal tick
  latency. The floor must not let the effective limit fall below it.
  """
  limit, measured = max_entry_drift_pips(
    strategy="Mapped Zone Reaction",
    atr=0.3,  # tiny ATR -> atr_pips collapses toward zero without a floor
    pip_size=0.1,
    remaining_target_room_pips=60.0,  # realistic target ladder, not consumed
    cfg=_Cfg(
      auto_trade_max_entry_distance_pips=10.0,
      auto_trade_map_min_entry_drift_pips=10.0,
      auto_trade_map_max_entry_drift_atr=1.0,
      auto_trade_map_hard_entry_drift_pips=20.0,
    ),
  )

  assert limit >= 10.0, f"drift floor not enforced, got {limit} ({measured})"


class _Cfg:
  def __init__(self, **kwargs):
    for key, value in kwargs.items():
      setattr(self, key, value)


# ---------------------------------------------------------------------------
# Root cause 2: cooldown marker has no close-reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zone_cooldown_ignores_unconfirmed_reason_by_default():
  """A marker with no confirmed stop_loss reason (the only kind the current
  broker integration can produce) must not block a same-direction re-entry
  once the reason/confidence-aware check lands.
  """
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    (
      '{"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000,'
      ' "reason": "reconciliation_unknown", "confidence": "unconfirmed"}'
    ),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )

  assert reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("reason_code", "confidence"),
  [
    ("manual_close", "confirmed"),
    ("external_close", "unconfirmed"),
    ("reconciliation_unknown", "unconfirmed"),
    ("take_profit", "confirmed"),
  ],
)
async def test_only_confirmed_stop_loss_can_enforce_cooldown(
  monkeypatch,
  reason_code,
  confidence,
):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", True)
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({
      "entry_price": 4116.25,
      "stop_price": 4111.54,
      "closed_at": 1000,
      "reason": reason_code,
      "confidence": confidence,
    }),
  )

  assert await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  ) is None


# ---------------------------------------------------------------------------
# Root cause 3: overlap veto deletes both directions unconditionally
# ---------------------------------------------------------------------------

def _frame_with_bullish_reclaim() -> pd.DataFrame:
  index = pd.date_range("2026-07-23", periods=6, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open":  [4118.0, 4117.5, 4116.5, 4116.2, 4117.0, 4119.5],
    "high":  [4118.3, 4117.8, 4116.8, 4117.5, 4119.8, 4120.2],
    "low":   [4117.6, 4116.3, 4115.9, 4116.0, 4116.9, 4119.3],
    "close": [4117.8, 4116.5, 4116.1, 4117.3, 4119.6, 4120.0],
  }, index=index)


def test_overlap_resolves_to_buy_thesis_on_bullish_m1_reclaim():
  """A bullish reclaim through the demand zone's own reaction memory must
  let the BUY thesis survive an otherwise-symmetric BUY/SELL overlap,
  instead of the current unconditional double-reject.
  """
  from app.analysis.market_map import MapEntry, MarketMap
  from app.autotrade.worker import _resolve_overlap_thesis

  demand = MapEntry(
    side="buy", lo=4112.0, hi=4122.0, label_lo=4112, label_hi=4122,
    tier="major", tags=[], score=6.0,
  )
  supply = MapEntry(
    side="sell", lo=4116.0, hi=4127.0, label_lo=4116, label_hi=4127,
    tier="major", tags=[], score=5.0,
  )
  market_map = MarketMap(
    entries=[demand, supply], price=4118.0, eq=None, box_low=None,
    box_high=None, bias="up", bias_tf="M30",
  )

  outcome = _resolve_overlap_thesis(
    "BUY", 4118.0, market_map, _frame_with_bullish_reclaim(), atr=1.0, cfg=None,
  )

  assert outcome.outcome in ("allow", "allow_with_warning")
  assert outcome.hard_block is False


def _frame_with_bearish_rejection() -> pd.DataFrame:
  index = pd.date_range("2026-07-23", periods=6, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open":  [4119.0, 4120.0, 4121.0, 4121.5, 4120.0, 4118.0],
    "high":  [4119.3, 4120.4, 4121.8, 4122.0, 4120.2, 4118.2],
    "low":   [4118.7, 4119.8, 4120.8, 4119.5, 4117.8, 4116.8],
    "close": [4119.1, 4120.2, 4121.4, 4119.7, 4118.0, 4117.0],
  }, index=index)


def _overlap_map():
  from app.analysis.market_map import MapEntry, MarketMap

  return MarketMap(
    entries=[
      MapEntry(
        side="buy", lo=4112.0, hi=4122.0, label_lo=4112, label_hi=4122,
        tier="major", tags=[], score=6.0,
      ),
      MapEntry(
        side="sell", lo=4116.0, hi=4127.0, label_lo=4116, label_hi=4127,
        tier="major", tags=[], score=6.0,
      ),
    ],
    price=4118.0,
    eq=None,
    box_low=None,
    box_high=None,
    bias="range",
    bias_tf="M30",
  )


def test_overlap_resolves_to_sell_thesis_on_bearish_m1_rejection():
  outcome = worker._resolve_overlap_thesis(
    "SELL",
    4118.0,
    _overlap_map(),
    _frame_with_bearish_rejection(),
    atr=1.0,
    cfg=_Cfg(auto_trade_structural_guard_mode="observe"),
  )

  assert outcome.outcome in ("allow", "allow_with_warning")
  assert not outcome.hard_block


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_ambiguous_overlap_waits_without_deleting_either_thesis(direction):
  outcome = worker._resolve_overlap_thesis(
    direction,
    4118.0,
    _overlap_map(),
    None,
    atr=1.0,
    cfg=_Cfg(auto_trade_structural_guard_mode="observe"),
  )

  assert outcome.outcome == "wait"
  assert outcome.reason_code == "ambiguous_waiting_confirmation"
  assert not outcome.hard_block


def _match(
  *,
  direction: str = "BUY",
  strategy: str = "Mapped Zone Reaction",
  entry_low: float = 4050.0,
  entry_high: float = 4052.0,
  key_level: float = 4051.0,
  target_price: float | None = None,
  tags: tuple[str, ...] = (),
  event_ts: str = "1784900000",
) -> StrategyMatch:
  now = int(datetime.now(timezone.utc).timestamp())
  match_id = strategy_match_id(
    "XAU",
    "M1",
    event_ts,
    strategy,
    direction,
    entry_low,
    entry_high,
  )
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol="XAU",
    source_tf="M1",
    event_ts=event_ts,
    issued_at=now,
    expires_at=now + 420,
    strategy=strategy,
    strategy_mode="mapped_zone_reaction",
    direction=direction,
    key_level=key_level,
    entry_low=entry_low,
    entry_high=entry_high,
    current_price=key_level,
    confluence=3,
    reasons=("deterministic replay",),
    atr=1.0,
    structure_swing=entry_low - 2 if direction == "BUY" else entry_high + 2,
    targets_pips=(30, 60, 90),
    tags=tags,
    target_price=target_price,
    tier="A",
    risk_multiplier=1.0,
    family="mapped_zone_reaction",
    structural_source="market_map_zone",
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("direction", "zones", "levels"),
  [
    ("BUY", [], [Level(4051.0, "reaction", 3, 1.0)]),
    ("SELL", [], [Level(4051.0, "reaction", 3, 1.0)]),
    ("BUY", [Zone(4050.0, 4052.0, "demand", touches=2)], []),
    ("SELL", [Zone(4050.0, 4052.0, "supply", touches=2)], []),
  ],
)
async def test_candidate_publishes_inside_its_own_structural_source(
  monkeypatch,
  direction,
  zones,
  levels,
):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _match(direction=direction, event_ts=str(now))
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:replay")
  monkeypatch.setattr(worker.settings, "auto_trade_structural_guard_mode", "observe")
  monkeypatch.setattr(worker.settings, "auto_trade_opposing_barrier_veto_enabled", False)
  monkeypatch.setattr(worker.settings, "auto_trade_overlap_veto_enabled", False)
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", False)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4051.0, now, True),
    match,
    consume_redis_match=False,
    htf_zones=zones,
    htf_levels=levels,
  )

  assert candidate_id == match.match_id
  assert await client.xlen("auto_trade:replay") == 1
  guard = json.loads(await client.get("auto_trade:last_guard:XAU"))
  assert guard["reason"] in {
    "primary_source_excluded_from_barrier",
    "no_overlap",
  }


@pytest.mark.asyncio
async def test_demo_eval_ignores_even_confirmed_zone_cooldown(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(
    worker.settings, "auto_trade_zone_cooldown_enabled", False,
  )
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({
      "reason": "stop_loss",
      "confidence": "confirmed",
      "entry_price": 4051.0,
      "stop_price": 4048.0,
      "closed_at": 1784900000,
    }),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4051.2, 1.0, 1.0,
  )

  assert reason is None


@pytest.mark.parametrize(
  ("direction", "barrier", "entry", "target"),
  [
    ("BUY", Zone(4058.0, 4060.0, "supply"), 4051.0, 4064.0),
    ("SELL", Zone(4042.0, 4044.0, "demand"), 4051.0, 4038.0),
  ],
)
def test_counter_bias_target_adapts_around_nearest_barrier(
  direction,
  barrier,
  entry,
  target,
):
  match = _match(
    direction=direction,
    target_price=target,
    tags=("counter_bias",),
  )

  adapted, outcome = worker._adapt_counter_bias_target(
    match,
    entry,
    [barrier],
    [],
    0.1,
  )

  assert outcome.outcome == "adjust_target"
  assert not outcome.hard_block
  assert outcome.reason_code == "target_capped_by_structure"
  assert adapted.target_price != target
  assert outcome.measured["barrier_price"] in {barrier.low, barrier.high}


def test_counter_bias_barrier_with_no_minimum_room_is_terminal():
  match = _match(
    direction="BUY",
    target_price=4064.0,
    tags=("counter_bias",),
  )
  _, outcome = worker._adapt_counter_bias_target(
    match,
    4051.0,
    [Zone(4051.5, 4053.0, "supply")],
    [],
    0.1,
  )

  assert outcome.hard_block
  assert outcome.reason_code == "target_room_insufficient"
  assert outcome.measured["available_room_pips"] < 15


@pytest.mark.parametrize(
  ("strategy", "distance", "expected_floor"),
  [
    ("Mapped Zone Reaction", 8.0, 10.0),
    ("Trend Pullback", 12.0, 15.0),
  ],
)
def test_normal_m1_reaction_latency_fits_strategy_drift_floor(
  strategy,
  distance,
  expected_floor,
):
  limit, _ = max_entry_drift_pips(
    strategy=strategy,
    atr=0.3,
    pip_size=0.1,
    remaining_target_room_pips=60,
    cfg=_Cfg(
      auto_trade_max_entry_distance_pips=10.0,
      auto_trade_map_min_entry_drift_pips=10.0,
      auto_trade_trend_min_entry_drift_pips=15.0,
      auto_trade_map_max_entry_drift_atr=1.0,
      auto_trade_trend_max_entry_drift_atr=1.5,
      auto_trade_map_hard_entry_drift_pips=20.0,
      auto_trade_trend_hard_entry_drift_pips=30.0,
    ),
  )

  assert limit >= expected_floor
  assert distance <= limit


def test_consumed_target_room_collapses_drift_to_zero():
  limit, measured = max_entry_drift_pips(
    strategy="Mapped Zone Reaction",
    atr=1.0,
    pip_size=0.1,
    remaining_target_room_pips=0,
    cfg=_Cfg(
      auto_trade_max_entry_distance_pips=10.0,
      auto_trade_map_min_entry_drift_pips=10.0,
      auto_trade_map_max_entry_drift_atr=1.0,
      auto_trade_map_hard_entry_drift_pips=20.0,
    ),
  )

  assert limit == 0
  assert measured["room_cap_pips"] == 0


@pytest.mark.asyncio
async def test_consuming_one_match_preserves_unrelated_sibling():
  client = redis_state.get_client()
  first = _match(event_ts="1784900001")
  second = _match(
    direction="SELL",
    entry_low=4060.0,
    entry_high=4062.0,
    key_level=4061.0,
    event_ts="1784900002",
  )
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([first, second]),
  )

  await worker._consume_strategy_match(client, "XAU", first)

  remaining = deserialize_matches(
    await client.get(strategy_matches_key("XAU"))
  )
  assert [item.match_id for item in remaining] == [second.match_id]


@pytest.mark.asyncio
async def test_news_wait_preserves_active_match(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _match(event_ts=str(now))
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([match]),
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_multi_match_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_structural_guard_mode", "observe")
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", False)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value={
    "title": "US high impact",
  }))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4051.0, now, True),
    match,
    consume_redis_match=False,
  )

  assert candidate_id is None
  remaining = deserialize_matches(
    await client.get(strategy_matches_key("XAU"))
  )
  assert [item.match_id for item in remaining] == [match.match_id]


def test_barrier_relationship_distinguishes_source_support_and_opposition():
  source = StructuralSourceIdentity(
    strategy="Demand Zone Reaction",
    strategy_family="mapped_zone_reaction",
    structural_source="market_map_zone",
    zone_id="demand:4050:4052",
    level_id=None,
    key_level=4051.0,
    low=4050.0,
    high=4052.0,
  )
  primary = StructuralBarrier(
    "demand:4050:4052", "zone", "demand", 4050.0, 4052.0,
  )
  support = StructuralBarrier(
    "support:4048:4049", "zone", "support", 4048.0, 4049.0,
  )
  resistance = StructuralBarrier(
    "supply:4058:4060", "zone", "supply", 4058.0, 4060.0,
  )

  assert classify_barrier_relationship(
    strategy=source.strategy,
    direction="BUY",
    entry_reference=4051.0,
    target_reference=4064.0,
    source_identity=source,
    barrier=primary,
  ) == "primary_source"
  assert classify_barrier_relationship(
    strategy=source.strategy,
    direction="BUY",
    entry_reference=4048.5,
    target_reference=4064.0,
    source_identity=source,
    barrier=support,
  ) == "supportive"
  assert classify_barrier_relationship(
    strategy=source.strategy,
    direction="BUY",
    entry_reference=4051.0,
    target_reference=4064.0,
    source_identity=source,
    barrier=resistance,
  ) == "opposing_ahead"


@pytest.mark.asyncio
async def test_temporary_entry_drift_wait_preserves_match(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  match = _match(event_ts=str(now))
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([match]),
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_multi_match_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_structural_guard_mode", "observe")
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", False)
  monkeypatch.setattr(worker.settings, "auto_trade_max_entry_distance_pips", 10.0)
  monkeypatch.setattr(worker.settings, "auto_trade_map_min_entry_drift_pips", 10.0)
  monkeypatch.setattr(worker.settings, "auto_trade_map_max_entry_drift_atr", 0.4)
  monkeypatch.setattr(worker.settings, "auto_trade_map_hard_entry_drift_pips", 20.0)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4053.2, now, True),
    match,
    consume_redis_match=False,
  )

  assert candidate_id is None
  remaining = deserialize_matches(
    await client.get(strategy_matches_key("XAU"))
  )
  assert [item.match_id for item in remaining] == [match.match_id]
  guard = json.loads(await client.get("auto_trade:last_guard:XAU"))
  assert guard["outcome"] == "wait"
  assert guard["reason"] == "strategy_entry_moved"


@pytest.mark.asyncio
async def test_crossed_invalidation_is_terminal_for_only_that_match(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  invalid = _match(event_ts=str(now))
  sibling = _match(
    direction="SELL",
    entry_low=4060.0,
    entry_high=4062.0,
    key_level=4061.0,
    event_ts=str(now + 1),
  )
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([invalid, sibling]),
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_multi_match_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_structural_guard_mode", "observe")
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", False)

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4047.9, now, True),
    invalid,
    consume_redis_match=False,
  )

  assert candidate_id is None
  remaining = deserialize_matches(
    await client.get(strategy_matches_key("XAU"))
  )
  assert [item.match_id for item in remaining] == [sibling.match_id]


@pytest.mark.asyncio
async def test_one_overlap_wait_does_not_silence_publishable_sibling(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  waiting = _match(
    entry_low=4117.0,
    entry_high=4119.0,
    key_level=4118.0,
    event_ts=str(now),
  )
  publishable = _match(
    entry_low=4200.0,
    entry_high=4202.0,
    key_level=4201.0,
    event_ts=str(now + 1),
  )
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([waiting, publishable]),
  )
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_multi_match_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_stream", "auto_trade:replay")
  monkeypatch.setattr(worker.settings, "auto_trade_structural_guard_mode", "observe")
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", False)
  monkeypatch.setattr(worker.settings, "auto_trade_overlap_veto_enabled", False)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  waiting_result = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4118.0, now, True),
    waiting,
    consume_redis_match=False,
    market_map=_overlap_map(),
  )
  published_result = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(4201.0, now, True),
    publishable,
    consume_redis_match=False,
    market_map=_overlap_map(),
  )

  assert waiting_result is None
  assert published_result == publishable.match_id
  assert await client.xlen("auto_trade:replay") == 1
  remaining = deserialize_matches(
    await client.get(strategy_matches_key("XAU"))
  )
  assert [item.match_id for item in remaining] == [waiting.match_id]
