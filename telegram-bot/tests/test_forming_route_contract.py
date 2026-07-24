import json
import time

import pytest

from app.analysis.types import Zone
from app.autotrade import worker
from app.autotrade.multi_match import (
  deserialize_matches,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.strategy_match import StrategyMatch, strategy_match_id
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _supply_match() -> StrategyMatch:
  now = int(time.time())
  event_ts = str(now)
  return StrategyMatch(
    version=1,
    match_id=strategy_match_id(
      "XAU", "M5", event_ts, "Supply Zone Reaction", "SELL",
      4062.49, 4066.18,
    ),
    symbol="XAU",
    source_tf="M5",
    event_ts=event_ts,
    issued_at=now,
    expires_at=now + 420,
    strategy="Supply Zone Reaction",
    strategy_mode="counter_bias",
    direction="SELL",
    key_level=4064.0,
    entry_low=4062.49,
    entry_high=4066.18,
    current_price=4063.03,
    confluence=3,
    reasons=("sweep_reclaim",),
    atr=4.0,
    structure_swing=4066.18,
    targets_pips=(20, 30, 40),
    tags=("counter_bias",),
    target_price=4059.03,
    family="supply_demand",
    structural_source="supply_demand",
    structural_zone_id="supply:M5:4062.49:4066.18",
    structural_zone_low=4062.49,
    structural_zone_high=4066.18,
    touch_bar_ts=str(now - 60),
    confirmation_bar_ts=str(now),
    reaction_type="sweep_reclaim",
  )


def _enable_supply(monkeypatch):
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_dry_run", False)
  monkeypatch.setattr(worker.settings, "auto_trade_strategy_match_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_supply_reaction_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_mapped_zone_enabled", False)
  monkeypatch.setattr(worker.settings, "auto_trade_structural_guard_mode", "observe")
  monkeypatch.setattr(worker.settings, "auto_trade_zone_cooldown_enabled", False)
  monkeypatch.setattr(worker.settings, "auto_trade_news_guard_minutes", 0)
  monkeypatch.setattr(worker.settings, "auto_trade_min_confluence", 2)
  monkeypatch.setattr(worker.settings, "auto_trade_opposing_barrier_veto_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_overlap_veto_enabled", True)


@pytest.mark.asyncio
async def test_supply_incident_inside_zone_publishes_with_mapped_zone_disabled(
  monkeypatch,
):
  _enable_supply(monkeypatch)
  monkeypatch.setattr(worker, "event_in_window", _no_news)
  client = redis_state.get_client()
  match = _supply_match()
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([match]),
  )

  candidate_id = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(price=4063.03, ts=int(time.time()), fresh=True),
    match,
    htf_zones=[],
    htf_levels=[],
  )

  assert candidate_id == match.match_id
  entries = await client.xrange(worker.settings.auto_trade_stream)
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload["source_strategy"] == "Supply Zone Reaction"
  assert payload["match_id"] == match.match_id
  route = json.loads(
    await client.get(f"auto_trade:last_route_outcome:XAU")
  )
  assert route["status"] == "candidate_published"
  assert route["measured"]["distance_pips"] == 0
  assert route["candidate_id"] == match.match_id
  assert deserialize_matches(
    await client.get(strategy_matches_key("XAU"))
  ) == []


@pytest.mark.asyncio
async def test_stream_failure_rolls_back_claim_and_retains_match(monkeypatch):
  _enable_supply(monkeypatch)
  monkeypatch.setattr(worker, "event_in_window", _no_news)
  client = redis_state.get_client()
  match = _supply_match()
  await client.set(
    strategy_matches_key("XAU"),
    serialize_matches([match]),
  )
  original_xadd = client.xadd

  async def fail_candidate_stream(name, *args, **kwargs):
    if name == worker.settings.auto_trade_stream:
      raise ConnectionError("forced XADD failure")
    return await original_xadd(name, *args, **kwargs)

  monkeypatch.setattr(client, "xadd", fail_candidate_stream)
  result = await worker._publish_strategy_match(
    client,
    "XAU",
    worker.AutoTradeSpot(price=4063.03, ts=int(time.time()), fresh=True),
    match,
    htf_zones=[],
    htf_levels=[],
  )

  assert result is None
  assert not await client.exists(f"auto_trade:candidate:{match.match_id}")
  route = json.loads(await client.get("auto_trade:last_route_outcome:XAU"))
  assert route["status"] == "waiting"
  assert route["reason_code"] == "stream_publish_failed"
  retained_raw = await client.get(strategy_matches_key("XAU"))
  assert retained_raw is not None
  retained = deserialize_matches(retained_raw)
  assert [item.match_id for item in retained] == [match.match_id]


def test_oversized_singleton_zone_is_context_only():
  classification = worker.classify_execution_zone(
    Zone(4040.57, 4075.04, "supply", source="supply_demand"),
    atr=10.0,
    pip_size=0.1,
    cfg=worker.settings,
  )
  assert classification.width_pips == pytest.approx(344.7)
  assert classification.context_only
  assert not classification.execution_grade


async def _no_news(*args, **kwargs):
  return None

