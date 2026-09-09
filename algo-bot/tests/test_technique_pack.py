"""Technique pack: killzone, sweep/body, PD, group-stop hard-cap."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.analysis.entry_location import EntryLocationDecision
from app.analysis.m1_trigger import M1TriggerResult
from app.autotrade.entry_activation import evaluate_entry_activation
from app.autotrade.killzone import (
  classify_killzone,
  confirmation_is_sweep_body,
)
from app.autotrade.protective_stop import ProtectiveStopError, plan_group_protective_stop
from app.scalping.context import permitted_archetypes_for_session
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
)


pytestmark = pytest.mark.no_database

_SCALP_ALL = (
  ARCHETYPE_RANGE_SWEEP,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_BREAKOUT_RETEST,
)


def _technique_cfg(
  *,
  enforce: bool = True,
  require_sweep: bool = True,
  strict_pd: bool = True,
  hfs_kz: bool = True,
):
  return SimpleNamespace(
    market_data=SimpleNamespace(
      sessions=SimpleNamespace(
        london_start=7,
        ny_start=13,
        asia_start=22,
        daily_rollover_utc_hour=21,
      ),
    ),
    execution=SimpleNamespace(
      technique=SimpleNamespace(
        enforce=enforce,
        include_late_ny=True,
        london_window_hours=3,
        ny_window_hours=3,
        reaction_require_killzone=True,
        scalp_require_killzone=hfs_kz,
        require_sweep_body=require_sweep,
        strict_premium_discount=strict_pd,
      ),
      activation=SimpleNamespace(
        mode="enforce",
        reaction_trigger_maximum_age_bars=2,
      ),
      reaction=SimpleNamespace(stop_min_pips=40, stop_max_pips=60),
    ),
    strategies=SimpleNamespace(
      reaction=SimpleNamespace(
        key_level=SimpleNamespace(
          enabled=True,
          require_killzone=False,
          min_grade="B",
        ),
      ),
      scalping=SimpleNamespace(
        archetypes=SimpleNamespace(
          range_sweep_enabled=True,
          impulse_pullback_enabled=True,
          breakout_retest_enabled=True,
        ),
      ),
    ),
  )


@pytest.mark.parametrize(
  "hour,allowed",
  [
    (7, True),
    (8, True),
    (9, True),
    (13, True),
    (14, True),
    (15, True),
    (22, True),
    (23, True),
    (1, False),
    (3, False),
    (5, False),
    (10, False),  # past London +3
    (11, False),
    (12, False),
    (16, False),
    (20, False),  # rollover −1 (rollover=21); 22 is late-NY override
    (21, False),
  ],
)
def test_killzone_hour_matrix(hour: int, allowed: bool):
  cfg = _technique_cfg()
  decision = classify_killzone(hour=hour, cfg=cfg)
  assert decision.allowed is allowed
  if not allowed:
    assert decision.reason_code == "outside_killzone"


def test_hfs_permitted_archetypes_are_structure_not_clock():
  """Enabled archetypes print in every session; analysis rejects weak hours."""
  cfg = _technique_cfg()
  for session, hour in (
    ("asia", 3),
    ("asia", 5),
    ("asia", 22),
    ("london", 8),
    ("london_ny_overlap", 14),
    ("new_york", 16),
    ("new_york", 17),
    ("rollover", 21),
  ):
    assert permitted_archetypes_for_session(session, hour=hour, cfg=cfg) == _SCALP_ALL


def test_hfs_session_fallback_without_clock():
  cfg = _technique_cfg()
  assert permitted_archetypes_for_session("asia", cfg=cfg) == _SCALP_ALL
  assert permitted_archetypes_for_session("london", cfg=cfg) == _SCALP_ALL
  assert permitted_archetypes_for_session("new_york", cfg=cfg) == _SCALP_ALL
  assert permitted_archetypes_for_session("rollover", cfg=cfg) == _SCALP_ALL


def test_publish_choke_blocks_outside_killzone_with_frozen_hour():
  """Worker TradePlan choke uses evaluate_killzone_gate(spot.ts); freeze UTC hour."""
  from app.autotrade.killzone import evaluate_killzone_gate

  cfg = _technique_cfg()
  dead = evaluate_killzone_gate(hour=3, cfg=cfg, require=True)
  assert dead.allowed is False
  assert dead.reason_code == "outside_killzone"
  assert dead.utc_hour == 3

  live = evaluate_killzone_gate(hour=14, cfg=cfg, require=True)
  assert live.allowed is True
  assert live.killzone_name == "london_ny"

  off = evaluate_killzone_gate(hour=3, cfg=_technique_cfg(enforce=False), require=True)
  assert off.allowed is True
  assert off.measured.get("would_block") is True


def test_key_level_can_raise_killzone_without_global_reaction_gate():
  from app.autotrade.killzone import key_level_min_grade, reaction_require_killzone

  cfg = _technique_cfg()
  cfg.execution.technique.reaction_require_killzone = False
  assert reaction_require_killzone(cfg, strategy="Demand Zone") is False
  assert reaction_require_killzone(cfg, strategy="Key Level") is False

  cfg.strategies.reaction.key_level.require_killzone = True
  assert reaction_require_killzone(cfg, strategy="Demand Zone") is False
  assert reaction_require_killzone(cfg, strategy="Key Level") is True

  cfg.strategies.reaction.key_level.min_grade = "A"
  assert key_level_min_grade(cfg) == "A"


def _location_ok() -> EntryLocationDecision:
  return EntryLocationDecision(
    allowed=True,
    reason_code="entry_location_allowed",
    hard_block=False,
    archetype="reversal",
    would_block=False,
    measured={},
  )


def test_activation_blocks_pin_bar_under_technique():
  now = 1_700_000_000
  trigger = M1TriggerResult(
    "pin_bar", "BUY", 4080.0, now - 60, "pin only",
  )
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="BUY",
    zone_entered_at=now - 120,
    quote_inside=True,
    decisive_break=False,
    trigger=trigger,
    location_decision=_location_ok(),
    now=now,
    cfg=_technique_cfg(),
  )
  assert decision.allowed is False
  assert decision.reason_code == "confirmation_requires_sweep_body"


def test_activation_allows_sweep_reclaim_under_technique():
  now = 1_700_000_000
  trigger = M1TriggerResult(
    "sweep_reclaim", "BUY", 4080.0, now - 60, "sweep",
  )
  decision = evaluate_entry_activation(
    strategy="Key Level",
    direction="BUY",
    zone_entered_at=now - 120,
    quote_inside=True,
    decisive_break=False,
    trigger=trigger,
    location_decision=_location_ok(),
    now=now,
    cfg=_technique_cfg(),
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"


def test_group_stop_hard_rejects_furthest_leg_over_max():
  with pytest.raises(ProtectiveStopError, match="stop_exceeds_envelope_furthest_leg") as exc:
    plan_group_protective_stop(
      direction="BUY",
      entry_zone_low="4260.19",
      entry_zone_high="4262.66",
      planned_leg_prices=("4260.19", "4262.66"),
      resolved_leg_volumes=("0.02", "0.04"),
      structure_swing="4258.66",
      atr="1",
      structure_buffer_atr="0.0",
      sweep_extreme=None,
      wick_buffer_atr="0.15",
      minimum_stop_pips=40,
      maximum_stop_pips=60,
      pip_size="0.1",
      digits=2,
    )
  assert Decimal(exc.value.measured["furthest_leg_stop_pips"]) > Decimal("60")


def test_entry_location_blocks_buy_premium_sell_discount():
  from app.analysis.entry_location import (
    build_entry_location_context,
    evaluate_entry_location,
  )

  cfg = SimpleNamespace(
    actionability=SimpleNamespace(
      entry_location=SimpleNamespace(
        mode="enforce",
        missing_context_policy="block",
        reversal=SimpleNamespace(
          buy_maximum_position=0.50,
          sell_minimum_position=0.50,
          extreme_buy_block_position=0.90,
          extreme_sell_block_position=0.10,
        ),
        range_reversion=SimpleNamespace(
          buy_maximum_position=0.40,
          sell_minimum_position=0.60,
          equilibrium_exclusion_width=0.20,
        ),
        trend_pullback=SimpleNamespace(
          buy_maximum_position=0.70,
          sell_minimum_position=0.30,
        ),
        breakout_retest=SimpleNamespace(allow_directional_expansion=True),
      ),
    ),
  )
  buy_ctx = build_entry_location_context(
    execution_price=4080.0,
    direction="BUY",
    m15_range_low=4000.0,
    m15_range_high=4100.0,
  )
  buy = evaluate_entry_location(
    strategy="Key Level",
    direction="BUY",
    context=buy_ctx,
    cfg=cfg,
  )
  assert buy.allowed is False
  assert buy.reason_code == "buy_in_premium"

  sell_ctx = build_entry_location_context(
    execution_price=4020.0,
    direction="SELL",
    m15_range_low=4000.0,
    m15_range_high=4100.0,
  )
  sell = evaluate_entry_location(
    strategy="Key Level",
    direction="SELL",
    context=sell_ctx,
    cfg=cfg,
  )
  assert sell.allowed is False
  assert sell.reason_code == "sell_in_discount"


def test_strict_pd_gate_buy_only_discount():
  from app.analysis import detectors
  from app.analysis.types import DealingRange

  premium = detectors.StructureSet(
    swings=[],
    bias="up",
    levels=[],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    dealing_range=DealingRange(
      high=4100.0, low=4000.0, eq=4050.0, position=0.8, zone="premium",
    ),
  )
  discount = detectors.StructureSet(
    swings=[],
    bias="up",
    levels=[],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    dealing_range=DealingRange(
      high=4100.0, low=4000.0, eq=4050.0, position=0.2, zone="discount",
    ),
  )
  soft = detectors.DetectorSettings(strict_pd_gate=False)
  strict = detectors.DetectorSettings(strict_pd_gate=True)
  assert detectors._pd_gate(premium, "BUY", soft) is False
  assert detectors._pd_gate(premium, "BUY", strict) is False
  assert detectors._pd_gate(discount, "BUY", strict) is True
  assert detectors._pd_gate(discount, "SELL", strict) is False
  assert detectors._pd_gate(premium, "SELL", strict) is True
