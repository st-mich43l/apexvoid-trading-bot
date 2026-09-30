import json
from app.core.config import runtime_config
import time

import pytest

from app.analysis.types import Zone
from app.analysis.structural_reaction_support import structural_thesis_id
from app.autotrade import worker
from app.autotrade.multi_match import (
  deserialize_matches,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.strategy_match import StrategyMatch
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf


pytestmark = pytest.mark.no_database


def _supply_match() -> StrategyMatch:
  now = int(time.time())
  event_ts = str(now)
  structural_id = "supply:M5:4062.49:4066.18"
  touch_bar_ts = str(now - 60)
  confirmation_bar_ts = str(now)
  return StrategyMatch(
    version=1,
    match_id=structural_thesis_id(
      symbol="XAU",
      strategy="Supply Zone Reaction",
      direction="SELL",
      structural_source="supply_demand",
      structural_id=structural_id,
      touch_bar_ts=touch_bar_ts,
      confirmation_bar_ts=confirmation_bar_ts,
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
    targets_pips=(20, 30, 40, 60),
    tags=("counter_bias",),
    target_price=4057.03,
    family="supply_demand",
    structural_source="supply_demand",
    zone_id=structural_id,
    level_id=structural_id,
    structural_zone_id=structural_id,
    structural_zone_low=4062.49,
    structural_zone_high=4066.18,
    touch_bar_ts=touch_bar_ts,
    confirmation_bar_ts=confirmation_bar_ts,
    reaction_type="sweep_reclaim",
  )


def _enable_supply(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_dry_run": False})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_supply_reaction_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_mapped_zone_enabled": False})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_structural_guard_mode": "observe"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_cooldown_enabled": False})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_news_guard_minutes": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_min_confluence": 2})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_opposing_barrier_veto_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_overlap_veto_enabled": True})


def test_oversized_singleton_zone_is_context_only():
  classification = worker.classify_execution_zone(
    Zone(4040.57, 4075.04, "supply", source="supply_demand"),
    atr=10.0,
    pip_size=0.1,
    cfg=runtime_config,
  )
  assert classification.width_pips == pytest.approx(344.7)
  assert classification.context_only
  assert not classification.execution_grade


def test_structural_supply_match_round_trips_through_redis_contract():
  match = _supply_match()
  restored = StrategyMatch.from_json(match.to_json())
  assert restored is not None
  assert restored.match_id == match.match_id
  assert restored.structural_source == "supply_demand"
  assert restored.strategy == "Supply Zone Reaction"


async def _no_news(*args, **kwargs):
  return None
