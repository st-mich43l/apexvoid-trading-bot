"""Tests for final protective stop planning and opposing-zone push."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from tests.configuration.canonical_fixtures import execution_cfg

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.protective_stop import (
  FinalProtectiveStopPlan,
  OpposingZoneStopContext,
  ProtectiveStopError,
  STOP_PLAN_VERSION,
  plan_protective_stop,
)


pytestmark = pytest.mark.no_database


@pytest.mark.parametrize(
  (
    "direction", "entry", "swing", "sweep", "minimum", "maximum",
    "expected_stop", "expected_pips", "expected_raw", "clamped", "source",
  ),
  [
    ("BUY", "4100", "4097", None, 20, 60, "4096.70", "33.0", "4096.7", False, "structure"),
    ("SELL", "4100", "4103", None, 20, 60, "4103.30", "33.0", "4103.3", False, "structure"),
    ("BUY", "4100", "4097", "4096", 20, 60, "4095.85", "41.5", "4095.85", False, "wick"),
    ("SELL", "4100", "4103", "4104", 20, 60, "4104.15", "41.5", "4104.15", False, "wick"),
    ("BUY", "4100", "4099.9", None, 20, 60, "4098.00", "20.0", "4099.6", True, "structure"),
    ("BUY", "4100", "4100.29", None, 20, 60, "4098.00", "20.0", "4099.99", True, "structure"),
    ("BUY", "4100.005", "4097.302", None, 20, 60, "4097.30", "27.05", "4097.302", True, "structure"),
  ],
)
def test_protective_stop_table_matches_executor_sequence(
  direction,
  entry,
  swing,
  sweep,
  minimum,
  maximum,
  expected_stop,
  expected_pips,
  expected_raw,
  clamped,
  source,
):
  plan = plan_protective_stop(
    direction=direction,
    entry_price=entry,
    structure_swing=swing,
    atr="1",
    structure_buffer_atr="0.3" if swing != "4097.302" else "0",
    sweep_extreme=sweep,
    wick_buffer_atr="0.15",
    minimum_stop_pips=minimum,
    maximum_stop_pips=maximum,
    pip_size="0.1",
    digits=2,
  )

  assert plan.final_stop_price == Decimal(expected_stop)
  assert plan.final_stop_pips == Decimal(expected_pips)
  assert plan.raw_stop_price == Decimal(expected_raw)
  assert plan.clamped is clamped
  assert plan.source == source
  assert plan.adjustment == "none"
  assert plan.base_stop_price == plan.final_stop_price


def test_structural_stop_beyond_max_envelope_clamps():
  plan = plan_protective_stop(
    direction="BUY",
    entry_price="4100",
    structure_swing="4090",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=20,
    maximum_stop_pips=60,
    pip_size="0.1",
    digits=2,
  )
  assert plan.final_stop_pips == Decimal("60")
  assert plan.clamped is True
  assert plan.final_stop_price == Decimal("4094.00")


def test_wick_beyond_envelope_rejects():
  with pytest.raises(
    ProtectiveStopError, match="stop_exceeds_envelope_after_wick",
  ):
    plan_protective_stop(
      direction="BUY",
      entry_price="4100",
      structure_swing="4097",
      atr="1",
      structure_buffer_atr="0.3",
      sweep_extreme="4090",
      wick_buffer_atr="0.15",
      minimum_stop_pips=20,
      maximum_stop_pips=60,
      pip_size="0.1",
      digits=2,
    )


def test_stop_inside_opposing_zone_rejects_when_push_disabled():
  with pytest.raises(ProtectiveStopError, match="stop_inside_opposing_zone") as excinfo:
    plan_protective_stop(
      direction="BUY",
      entry_price="4000",
      structure_swing="3998",
      atr="1",
      structure_buffer_atr="0.3",
      sweep_extreme=None,
      wick_buffer_atr="0.15",
      minimum_stop_pips=30,
      maximum_stop_pips=65,
      pip_size="0.1",
      digits=2,
      opposing_zone=OpposingZoneStopContext(
        zone_id="demand-1",
        low=Decimal("3997"),
        high=Decimal("3998.5"),
        execution_grade=True,
        push_beyond_zone=False,
        buffer_atr=Decimal("0.3"),
      ),
    )
  measured = excinfo.value.measured
  assert measured["stop_reject_detail"] == "push_disabled"
  assert measured["stop_side_opposing_zone_id"] == "demand-1"
  assert measured["stop_side_opposing_zone_low"] == 3997.0
  assert measured["stop_side_opposing_zone_high"] == 3998.5
  assert measured["planned_base_stop_price"] is not None
  assert measured["stop_max_envelope_pips"] == 65
  assert "planned_pushed_stop_price" not in measured


def test_stop_inside_execution_grade_zone_is_pushed_beyond_it():
  plan = plan_protective_stop(
    direction="BUY",
    entry_price="4000.2",
    structure_swing="3998",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=30,
    maximum_stop_pips=65,
    pip_size="0.1",
    digits=2,
    opposing_zone=OpposingZoneStopContext(
      zone_id="demand-1",
      low=Decimal("3997"),
      high=Decimal("3998.5"),
      execution_grade=True,
      push_beyond_zone=True,
      buffer_atr=Decimal("0.3"),
    ),
  )
  assert plan.base_stop_price == Decimal("3997.20")
  assert plan.final_stop_price == Decimal("3996.70")
  assert plan.adjustment == "opposing_zone_push"
  assert plan.adjustment_zone_low == Decimal("3997")
  assert plan.adjustment_zone_high == Decimal("3998.5")


def test_reaction_family_ceiling_still_allows_a_legitimate_opposing_zone_push():
  # Opposing-zone push that needs 63 pips clears under a 65 ceiling and
  # rejects under the owner 60 max envelope.
  kwargs = dict(
    direction="BUY",
    entry_price="4100.00",
    structure_swing="4096.70",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=20,
    pip_size="0.1",
    digits=2,
    opposing_zone=OpposingZoneStopContext(
      zone_id="supply-1",
      low=Decimal("4094.00"),
      high=Decimal("4097.00"),
      execution_grade=True,
      push_beyond_zone=True,
      buffer_atr=Decimal("0.3"),
    ),
  )

  plan = plan_protective_stop(maximum_stop_pips=65, **kwargs)
  assert plan.adjustment == "opposing_zone_push"
  assert plan.final_stop_price == Decimal("4093.70")
  assert plan.final_stop_pips == Decimal("63.0")

  with pytest.raises(ProtectiveStopError, match="stop_inside_opposing_zone") as excinfo:
    plan_protective_stop(maximum_stop_pips=60, **kwargs)
  measured = excinfo.value.measured
  assert measured["stop_reject_detail"] == "pushed_exceeds_max_envelope"
  assert measured["stop_side_opposing_zone_low"] == 4094.0
  assert measured["stop_side_opposing_zone_high"] == 4097.0
  assert measured["planned_pushed_stop_price"] == "4093.70"
  assert measured["planned_pushed_stop_pips"] == "63.0"
  assert measured["stop_max_envelope_pips"] == 60
  assert Decimal(measured["pushed_over_envelope_pips"]) == Decimal("3.0")


def test_context_only_opposing_zone_leaves_base_stop():
  plan = plan_protective_stop(
    direction="BUY",
    entry_price="4000",
    structure_swing="3998",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=30,
    maximum_stop_pips=65,
    pip_size="0.1",
    digits=2,
    opposing_zone=OpposingZoneStopContext(
      zone_id="demand-wide",
      low=Decimal("3990"),
      high=Decimal("4025"),
      execution_grade=False,
      push_beyond_zone=True,
      buffer_atr=Decimal("0.3"),
    ),
  )
  assert plan.final_stop_price == plan.base_stop_price
  assert plan.adjustment == "none"


def test_candidate_fields_emit_v2_contract():
  plan = FinalProtectiveStopPlan(
    entry_price=Decimal("4000.2"),
    base_stop_price=Decimal("3997.70"),
    base_stop_pips=Decimal("25"),
    final_stop_price=Decimal("3996.70"),
    final_stop_distance=Decimal("3.5"),
    final_stop_pips=Decimal("35"),
    raw_stop_price=Decimal("3997.7"),
    clamped=False,
    source="structure",
    adjustment="opposing_zone_push",
    adjustment_zone_id="demand-1",
    adjustment_zone_low=Decimal("3997"),
    adjustment_zone_high=Decimal("3998.5"),
    version=STOP_PLAN_VERSION,
  )
  fields = plan.candidate_fields(entry_price=Decimal("4000.2"))
  assert fields["stop_plan_version"] == 2
  assert fields["planned_stop_price"] == "3996.70"
  assert fields["planned_final_stop_price"] == "3996.70"
  assert fields["planned_base_stop_price"] == "3997.70"
  assert fields["stop_adjustment"] == "opposing_zone_push"


def _policy_subject(**overrides):
  values = {
    "strategy": "Mapped Zone Reaction",
    "direction": "BUY",
    "entry_low": 4100.0,
    "entry_high": 4100.2,
    "current_price": 4100.1,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4099.9,
    "targets_pips": (30,),
    "target_model": "fill_relative",
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def test_reward_risk_uses_clamped_stop_and_records_insufficient_rr_preference():
  result = evaluate_execution_policy(
    _policy_subject(),
    spot_price=4100.0,
    regime="trend",
    pip_size=0.1,
  )

  # Insufficient RR is preference telemetry on the measured payload.
  assert result.allowed
  assert result.reason_code == "policy_reward_risk_insufficient"
  assert result.measured.get("preference_telemetry") is True
  assert result.measured["planned_stop_pips"] == "40.0"
  assert result.measured["planned_final_stop_pips"] == "40.0"
  assert result.measured["reward_risk"] == 0.75


def test_reward_risk_accepts_near_entry_structure_after_minimum_clamp():
  result = evaluate_execution_policy(
    _policy_subject(
      structure_swing=4100.29,
      targets_pips=(60,),
    ),
    spot_price=4100.0,
    regime="trend",
    pip_size=0.1,
  )

  assert result.allowed
  assert result.measured["planned_stop_pips"] == "40.0"
  assert result.measured["reward_risk"] == 1.5


def test_reaction_family_room_synced_stop_tracks_primary_tp():
  # Room sync still pins to primary TP, but never below the owner 40–60
  # reaction envelope (live: floor-20 + TP-30 produced a 30-pip SL).
  thin = evaluate_execution_policy(
    _policy_subject(
      strategy="Key Level",
      targets_pips=(25,),
      structure_swing=4099.9,
    ),
    spot_price=4100.0,
    regime="range",
    pip_size=0.1,
  )
  mid = evaluate_execution_policy(
    _policy_subject(
      strategy="Session Level",
      targets_pips=(50,),
      structure_swing=4099.9,
    ),
    spot_price=4100.0,
    regime="range",
    pip_size=0.1,
  )
  capped = evaluate_execution_policy(
    _policy_subject(
      strategy="Trendline",
      targets_pips=(90,),
      structure_swing=4099.9,
    ),
    spot_price=4100.0,
    regime="range",
    pip_size=0.1,
  )

  assert thin.measured["stop_bounds_source"] == "reaction_room"
  assert thin.measured["primary_tp_pips"] == 25.0
  assert thin.measured["desired_stop_pips"] == 40
  assert thin.measured["planned_stop_pips"] == "40.0"

  assert mid.measured["stop_bounds_source"] == "reaction_room"
  assert mid.measured["desired_stop_pips"] == 50
  assert mid.measured["planned_stop_pips"] == "50.0"

  assert capped.measured["stop_bounds_source"] == "reaction_room"
  assert capped.measured["desired_stop_pips"] == 90
  assert capped.measured["planned_stop_pips"] == "60.0"


def test_supply_demand_zone_is_independent_of_reaction_room_stop():
  # Zone / Demand / Supply are supply_demand family — owner 40–60, not room sync.
  zone = evaluate_execution_policy(
    _policy_subject(
      strategy="Zone Reaction",
      targets_pips=(30,),
      structure_swing=4099.9,
    ),
    spot_price=4100.0,
    regime="range",
    pip_size=0.1,
  )
  assert zone.measured.get("stop_bounds_source") == "strategy_default"
  assert zone.measured["planned_stop_pips"] == "40.0"
  assert zone.measured.get("desired_stop_pips") is None


def test_reaction_room_stop_missing_tp_falls_back_to_legacy_envelope():
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room

  cfg = execution_cfg(
    auto_trade_trend_stop_min_pips=40,
    auto_trade_trend_stop_max_pips=60,
    auto_trade_reaction_room_stop_min_rr=1.0,
    auto_trade_reaction_room_stop_floor_pips=20,
  )
  minimum, maximum, measured = stop_bounds_for_reaction_room(
    strategy="Key Level",
    primary_tp_pips=None,
    pip_size=0.1,
    cfg=cfg,
  )
  assert (minimum, maximum) == (40, 60)
  assert measured["stop_bounds_source"] == "legacy_envelope"

  # Mapped Zone Reaction is outside the room-synced set.
  mapped_min, mapped_max, mapped = stop_bounds_for_reaction_room(
    strategy="Mapped Zone Reaction",
    primary_tp_pips=25,
    pip_size=0.1,
    cfg=cfg,
  )
  assert (mapped_min, mapped_max) == (40, 60)
  assert mapped["stop_bounds_source"] == "strategy_default"

  # Non-room strategies still use the owner 40–60 envelope via policy.
  trend = evaluate_execution_policy(
    _policy_subject(strategy="Mapped Zone Reaction", targets_pips=(60,)),
    spot_price=4100.0,
    regime="trend",
    pip_size=0.1,
  )
  assert trend.measured["planned_stop_pips"] == "40.0"
  assert trend.measured.get("stop_bounds_source") == "strategy_default"


def test_scalp_stop_bounds_use_scalp_envelope_not_reaction_40_60():
  """Dig 2026-08-25: Range Sweep Scalp used reaction 40–60 because
  uses_scalp_room_stop excluded M1 scalp strategies."""
  from types import SimpleNamespace

  from app.autotrade.protective_stop import (
    stop_bounds_for_reaction_room,
    stop_bounds_for_strategy,
    uses_scalp_room_stop,
  )

  assert uses_scalp_room_stop("Range Sweep Scalp") is True
  assert uses_scalp_room_stop("Breakout Retest Scalp") is True
  assert uses_scalp_room_stop("Range Sweep Scalp") is True
  assert uses_scalp_room_stop("Breakout Retest Scalp") is True
  assert uses_scalp_room_stop("Key Level") is False

  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      range=SimpleNamespace(min_rr=1.0, room_stop_floor_pips=15),
      reaction=SimpleNamespace(
        room_stop_min_rr=1.0, stop_min_pips=40, stop_max_pips=60,
      ),
      stops=SimpleNamespace(
        reaction=SimpleNamespace(room_floor_pips=40),
        trend=SimpleNamespace(minimum_pips=40),
        sl_distance=6.5,
      ),
      trend=SimpleNamespace(stop_max_pips=60),
      scaling=SimpleNamespace(add=SimpleNamespace(min_stop_pips=30)),
    ),
    strategies=SimpleNamespace(
      scalping=SimpleNamespace(
        stop=SimpleNamespace(minimum_pips=12, maximum_pips=30),
      ),
    ),
    for_instrument=None,
  )
  assert stop_bounds_for_strategy(
    strategy="Range Sweep Scalp", pip_size=0.1, cfg=cfg,
  ) == (12, 30)
  minimum, maximum, measured = stop_bounds_for_reaction_room(
    strategy="Range Sweep Scalp",
    primary_tp_pips=20,
    pip_size=0.1,
    cfg=cfg,
  )
  # Single-leg pins min to 1:1 with primary TP; max stays scalp envelope
  # (not reaction 40–60). Group path keeps the raw floor at 12.
  assert minimum == 20
  assert maximum == 30
  assert measured["stop_bounds_source"] == "scalp_stop_envelope"
  group_min, group_max, _ = stop_bounds_for_reaction_room(
    strategy="Range Sweep Scalp",
    primary_tp_pips=20,
    pip_size=0.1,
    cfg=cfg,
    for_group_stop=True,
  )
  assert group_min == 12
  assert group_max == 30


def test_stop_bounds_for_reaction_room_pins_and_caps():
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room

  cfg = execution_cfg(
    auto_trade_trend_stop_min_pips=40,
    auto_trade_trend_stop_max_pips=60,
    auto_trade_reaction_stop_min_pips=40,
    auto_trade_reaction_stop_max_pips=60,
    auto_trade_reaction_room_stop_min_rr=1.0,
    auto_trade_reaction_room_stop_floor_pips=20,
  )
  # Independent zone family — must not pin to primary TP.
  assert stop_bounds_for_reaction_room(
    strategy="Zone Reaction",
    primary_tp_pips=25,
    pip_size=0.1,
    cfg=cfg,
  )[:2] == (40, 60)
  assert stop_bounds_for_reaction_room(
    strategy="Demand Zone Reaction",
    primary_tp_pips=25,
    pip_size=0.1,
    cfg=cfg,
  )[2]["stop_bounds_source"] == "strategy_default"
  assert stop_bounds_for_reaction_room(
    strategy="Supply Zone Reaction",
    primary_tp_pips=18,
    pip_size=0.1,
    cfg=cfg,
  )[2]["stop_bounds_source"] == "strategy_default"
  assert stop_bounds_for_reaction_room(
    strategy="Key Level",
    primary_tp_pips=18,
    pip_size=0.1,
    cfg=cfg,
  )[:2] == (40, 60)  # owner floor 40, hard cap stays 60 (not collapsed)
  assert stop_bounds_for_reaction_room(
    strategy="Key Level",
    primary_tp_pips=30,
    pip_size=0.1,
    cfg=cfg,
  )[:2] == (40, 60)  # TP-30 must not produce a 30-pip SL
  assert stop_bounds_for_reaction_room(
    strategy="Key Level",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
  )[:2] == (60, 60)


def test_stop_bounds_for_reaction_room_keeps_band_for_group_stop():
  # Live 2026-08-06: a wide room (primary_tp_pips=90) collapsed [min, max]
  # to (60, 60) for a Supply/Demand multi-leg group stop. A single
  # absolute stop price is a different pip distance from every leg by
  # construction, so a single-point band made every group stop with any
  # real leg spread mathematically unsatisfiable
  # (stop_exceeds_envelope_furthest_leg on every live candidate).
  # for_group_stop=True must keep the floor at floor_pips regardless of
  # how wide the room is, so [min, max] always has real width.
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room

  cfg = execution_cfg(
    auto_trade_trend_stop_min_pips=40,
    auto_trade_trend_stop_max_pips=60,
    auto_trade_reaction_stop_min_pips=40,
    auto_trade_reaction_stop_max_pips=60,
    auto_trade_reaction_room_stop_min_rr=1.0,
    auto_trade_reaction_room_stop_floor_pips=20,
  )
  minimum, maximum, measured = stop_bounds_for_reaction_room(
    strategy="Key Level",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
    for_group_stop=True,
  )
  assert (minimum, maximum) == (40, 60)
  assert measured["stop_bounds_for_group_stop"] is True
  # Single-leg (default) at the same inputs is unaffected -- still (60, 60).
  assert stop_bounds_for_reaction_room(
    strategy="Key Level",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
  )[:2] == (60, 60)


def test_group_stop_survives_realistic_leg_spread_after_bounds_fix():
  # End-to-end, live 2026-08-06 shape (Supply/Demand SELL, zone
  # 4388.07-4393.07): a 15-pip leg spread fits the restored 40-60 band
  # (nearest leg lands on the 40p floor, furthest on the 60p cap) and must
  # now build a real stop instead of stop_exceeds_envelope_furthest_leg.
  from app.autotrade.protective_stop import plan_group_protective_stop

  plan = plan_group_protective_stop(
    direction="SELL",
    entry_zone_low="4388.07",
    entry_zone_high="4393.07",
    planned_leg_prices=("4388.50", "4390.00"),
    resolved_leg_volumes=("0.7", "0.3"),
    structure_swing="4394.5",
    atr="1.0",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=40,
    maximum_stop_pips=60,
    pip_size="0.1",
    digits=2,
  )
  assert plan.final_stop_price == Decimal("4394.50")
  assert plan.final_stop_pips == Decimal("60")


def test_scalp_room_synced_stop_allows_thin_targets():
  scalp = evaluate_execution_policy(
    _policy_subject(
      strategy="Range Box Scalp",
      targets_pips=(15,),
      structure_swing=4099.9,
      atr=1.0,
    ),
    spot_price=4100.0,
    regime="chop",
    pip_size=0.1,
    cfg=execution_cfg(
      auto_trade_add_min_stop_pips=30,
      auto_trade_sl_distance=6.5,
      auto_trade_range_min_rr=1.0,
      auto_trade_range_room_stop_floor_pips=15,
      auto_trade_range_max_risk_multiplier=2.0,
      auto_trade_trend_stop_min_pips=40,
      auto_trade_trend_stop_max_pips=60,
      auto_trade_xau_price_digits=2,
      auto_trade_add_stop_buffer_atr=0.3,
      auto_trade_wick_stop_buffer_atr=0.15,
      auto_trade_zone_fill_enabled=False,
      auto_trade_inside_zone_market_entry_enabled=True,
      auto_trade_reaction_scale_enabled=False,
    ),
  )
  assert scalp.allowed
  assert scalp.measured["stop_bounds_source"] == "scalp_room"
  assert scalp.measured["desired_stop_pips"] == 15
  # Owner 2026-09-07: scalp books the same flat equity-table lot as any
  # other trade now (PR #486's equity_table sizing_mode default), so no
  # multiplier applies here any more and the pip envelope is never
  # shrunk - the prior 2x-multiplier/halved-envelope pairing was a
  # leftover from the old risk-percent sizing formula.
  assert scalp.measured["match_risk_multiplier"] == 1.0
  assert scalp.measured["effective_risk_multiplier"] == 1.0
  assert scalp.measured["planned_stop_pips"] == "15.0"


def test_scalp_with_fitted_target_skips_opposing_zone_stop_reject():
  cfg = execution_cfg(
    auto_trade_add_min_stop_pips=30,
    auto_trade_sl_distance=6.5,
    auto_trade_range_min_rr=1.0,
    auto_trade_range_room_stop_floor_pips=15,
    auto_trade_range_max_risk_multiplier=2.0,
    auto_trade_trend_stop_min_pips=40,
    auto_trade_trend_stop_max_pips=60,
    auto_trade_xau_price_digits=2,
    auto_trade_add_stop_buffer_atr=0.3,
    auto_trade_wick_stop_buffer_atr=0.15,
    auto_trade_zone_fill_enabled=False,
    auto_trade_inside_zone_market_entry_enabled=True,
    auto_trade_reaction_scale_enabled=False,
    auto_trade_opposing_zone_push_enabled=False,
    auto_trade_opposing_zone_buffer_atr=0.3,
  )
  scalp = evaluate_execution_policy(
    _policy_subject(
      strategy="Range Box Scalp",
      targets_pips=(20,),
      full_take_profit_pips=20,
      structure_swing=4099.9,
      atr=1.0,
      entry_low=4100.0,
      entry_high=4100.2,
      current_price=4100.1,
    ),
    spot_price=4100.0,
    regime="chop",
    pip_size=0.1,
    cfg=cfg,
    opposing_zone_low=4097.0,
    opposing_zone_high=4098.5,
    opposing_zone_id="demand-block",
  )
  assert scalp.allowed
  assert scalp.reason_code != "stop_inside_opposing_zone"
  assert scalp.measured.get("stop_side_opposing_zone_id") is None


def test_sell_group_stop_clears_zone_high_and_uses_weighted_reference():
  from app.autotrade.protective_stop import (
    plan_group_protective_stop,
    resolve_entry_leg_lots,
    volume_weighted_reference_entry,
  )

  leg_prices = ("4098.50", "4100.50")
  resolved = resolve_entry_leg_lots("0.11", ("0.70", "0.30"))
  assert resolved == (Decimal("0.08"), Decimal("0.03"))
  reference = volume_weighted_reference_entry(leg_prices, resolved)
  assert reference == (
    Decimal("4098.50") * Decimal("0.08")
    + Decimal("4100.50") * Decimal("0.03")
  ) / Decimal("0.11")

  plan = plan_group_protective_stop(
    direction="SELL",
    entry_zone_low="4097.07",
    entry_zone_high="4101.03",
    planned_leg_prices=leg_prices,
    resolved_leg_volumes=resolved,
    structure_swing="4102.00",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme="4102.50",
    wick_buffer_atr="0.15",
    minimum_stop_pips=40,
    maximum_stop_pips=60,
    pip_size="0.1",
    digits=2,
  )
  assert plan.entry_price == reference
  assert plan.final_stop_price > Decimal("4101.03")
  assert plan.final_stop_price > Decimal("4100.50")
  assert plan.final_stop_price > Decimal("4102.00")
  assert Decimal("40") <= plan.final_stop_pips <= Decimal("60")


def test_group_structural_stop_beyond_max_clamps_to_envelope():
  from app.autotrade.protective_stop import plan_group_protective_stop

  # Owner directive 2026-08-06: do not fail-closed when structural/clearance
  # distance exceeds the max envelope. Clamp to max (same as single-leg
  # structure-stop path) so a ready plan still publishes. Live incident
  # lost a Key Level BUY to a 0.02-pip post-floor overshoot.
  plan = plan_group_protective_stop(
    direction="SELL",
    entry_zone_low="4097.07",
    entry_zone_high="4101.03",
    planned_leg_prices=("4098.50", "4100.50"),
    resolved_leg_volumes=("0.08", "0.03"),
    structure_swing="4110.00",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=40,
    maximum_stop_pips=60,
    pip_size="0.1",
    digits=2,
  )

  assert plan.clamped is True
  assert plan.final_stop_pips <= Decimal("60")
  assert plan.final_stop_pips >= Decimal("40")
  # Raw structural/clearance would have been well outside the envelope.
  assert (plan.raw_stop_price - plan.entry_price) / Decimal("0.1") > Decimal("60")
  assert plan.final_stop_price < plan.raw_stop_price


def test_group_stop_floors_every_planned_leg_not_just_weighted():
  from app.autotrade.protective_stop import plan_group_protective_stop

  # Narrow leg span (10 pips) so 40–60 can fit. Structural stop would leave
  # the near leg ~30 pips away while weighted sits mid — floor expands until
  # the nearest leg clears 40.
  plan = plan_group_protective_stop(
    direction="BUY",
    entry_zone_low="4252.00",
    entry_zone_high="4253.00",
    planned_leg_prices=("4252.00", "4253.00"),
    resolved_leg_volumes=("0.04", "0.02"),
    structure_swing="4250.00",
    atr="1",
    structure_buffer_atr="0.1",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=40,
    maximum_stop_pips=60,
    pip_size="0.1",
    digits=2,
  )
  near = Decimal("4252.00") - plan.final_stop_price
  far = Decimal("4253.00") - plan.final_stop_price
  assert near / Decimal("0.1") >= Decimal("40")
  assert far / Decimal("0.1") <= Decimal("60")
  assert plan.final_stop_pips == far / Decimal("0.1")


def test_group_stop_rejects_when_near_floor_overshoots_far_leg_cap():
  from app.autotrade.protective_stop import ProtectiveStopError, plan_group_protective_stop

  # Live Trend Pullback-shaped ladder: floor from nearest clears 40 but
  # furthest exceeds 60 — always reject (no soft-max legacy path).
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


def test_group_stop_rejects_when_leg_span_cannot_fit_envelope():
  from app.autotrade.protective_stop import ProtectiveStopError, plan_group_protective_stop

  with pytest.raises(ProtectiveStopError, match="stop_exceeds_envelope_furthest_leg"):
    plan_group_protective_stop(
      direction="BUY",
      entry_zone_low="4252.20",
      entry_zone_high="4256.84",
      planned_leg_prices=("4252.20", "4256.84"),
      resolved_leg_volumes=("0.04", "0.02"),
      structure_swing="4251.56",
      atr="1",
      structure_buffer_atr="0.0",
      sweep_extreme=None,
      wick_buffer_atr="0.15",
      minimum_stop_pips=40,
      maximum_stop_pips=60,
      pip_size="0.1",
      digits=2,
    )
