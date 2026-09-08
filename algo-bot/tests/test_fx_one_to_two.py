"""Config-driven fixed-RR policy for FX instruments."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match
from app.configuration.models.instruments import (
  FX_FIXED_2R_V1_POLICY,
  InstrumentConfig,
  InstrumentTargetMode,
  InstrumentTargetingConfig,
)
from app.core.instrument_geometry import fixed_reward_risk
from app.scalping.strategies import _select_target
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database

_FX_TARGET_R_MULTIPLES = (1.0, 2.0)
_FX_CLOSE_RATIOS = (0.5, 0.5)


def _targeting(reward_risk: float = 2.0) -> dict[str, object]:
  levels = tuple(
    value * reward_risk / 2.0 for value in _FX_TARGET_R_MULTIPLES
  )
  return {
    "mode": "fixed_rr",
    "reward_risk": reward_risk,
    "target_r_multiples": levels,
    "close_ratios": _FX_CLOSE_RATIOS,
    "breakeven_after_r": 1.0 * reward_risk / 2.0,
    "entry_clips": 2,
  }


def test_fixed_rr_targeting_requires_ratio_and_matching_policy():
  with pytest.raises(ValueError, match="requires reward_risk"):
    InstrumentTargetingConfig(mode="fixed_rr")
  with pytest.raises(ValueError, match="must not set fixed-RR fields"):
    InstrumentTargetingConfig(mode="ladder_pips", reward_risk=2.0)
  with pytest.raises(ValueError, match="requires target_r_multiples"):
    InstrumentTargetingConfig(mode="fixed_rr", reward_risk=2.0)
  with pytest.raises(ValueError, match="must sum to 1.0"):
    InstrumentTargetingConfig(
      mode="fixed_rr",
      reward_risk=2.0,
      target_r_multiples=_FX_TARGET_R_MULTIPLES,
      close_ratios=(0.2, 0.2),
    )
  with pytest.raises(ValueError, match="must be set together"):
    InstrumentTargetingConfig(
      mode="fixed_rr",
      reward_risk=2.0,
      target_r_multiples=_FX_TARGET_R_MULTIPLES,
      close_ratios=_FX_CLOSE_RATIOS,
      trail_after_r=1.5,
    )
  with pytest.raises(
    ValueError, match="breakeven_after_r and trail_after_r are mutually exclusive"
  ):
    InstrumentTargetingConfig(
      mode="fixed_rr",
      reward_risk=2.0,
      target_r_multiples=_FX_TARGET_R_MULTIPLES,
      close_ratios=_FX_CLOSE_RATIOS,
      breakeven_after_r=1.0,
      trail_after_r=1.5,
      trail_to_r=1.0,
    )
  with pytest.raises(
    ValueError, match="breakeven_after_r must equal one of target_r_multiples"
  ):
    InstrumentTargetingConfig(
      mode="fixed_rr",
      reward_risk=2.0,
      target_r_multiples=_FX_TARGET_R_MULTIPLES,
      close_ratios=_FX_CLOSE_RATIOS,
      breakeven_after_r=1.5,
    )
  with pytest.raises(ValueError, match="fixed_rr targeting requires policy in"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy="xau_current_v1",
      targeting=_targeting(),
    )


def test_fx_policy_is_locked_to_uniform_two_r_breakeven_contract():
  with pytest.raises(ValueError, match="targeting.reward_risk must be 2.0"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting=_targeting(1.5),
    )
  with pytest.raises(
    ValueError, match="targeting.target_r_multiples must be \\(1.0, 2.0\\)"
  ):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting={
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": (1.0, 1.5, 2.0),
        "close_ratios": (0.25, 0.25, 0.50),
        "breakeven_after_r": 1.0,
        "entry_clips": 2,
      },
    )
  with pytest.raises(
    ValueError, match="targeting.close_ratios must be \\(0.5, 0.5\\)"
  ):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting={
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": _FX_TARGET_R_MULTIPLES,
        "close_ratios": (0.4, 0.6),
        "breakeven_after_r": 1.0,
        "entry_clips": 2,
      },
    )
  with pytest.raises(
    ValueError, match="targeting.close_ratios must be \\(0.5, 0.5\\)"
  ):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="GBPJPY",
      broker_symbol="GBPJPY",
      policy="fx_fixed_2r_frontload_v1",
      targeting={
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": _FX_TARGET_R_MULTIPLES,
        "close_ratios": (0.4, 0.6),
        "breakeven_after_r": 1.0,
        "entry_clips": 2,
      },
    )
  with pytest.raises(ValueError, match="targeting.entry_clips must be 2"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting={
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": _FX_TARGET_R_MULTIPLES,
        "close_ratios": _FX_CLOSE_RATIOS,
        "breakeven_after_r": 1.0,
        "entry_clips": 5,
      },
    )


def _fx_match(symbol: str = "EURUSD") -> StrategyMatch:
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id="fx-fixed-rr-match",
    symbol=symbol,
    source_tf="M5",
    event_ts="1719999600",
    issued_at=1719999600,
    expires_at=1720003200,
    strategy="Trend Pullback",
    strategy_mode="with_trend",
    direction="BUY",
    key_level=1.1002,
    entry_low=1.1000,
    entry_high=1.1004,
    current_price=1.1002,
    confluence=3,
    reasons=("htf_uptrend", "demand_reaction"),
    atr=0.0008,
    structure_swing=1.0998,
    # Provisional room only. The final target must be derived from the stop.
    targets_pips=(50,),
    tier="A",
    family="trend_pullback",
    structural_zone_id="eurusd-demand-1.1000",
    structural_zone_low=1.1000,
    structural_zone_high=1.1004,
    structural_kind="demand",
    structural_timeframe="M15",
    htf_bias="up",
    regime_kind="trend",
  )


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_fx_technique_route_uses_its_declared_zone_scale_policy(direction: str):
  # Owner 2026-09-08 (bad technique entries): technique strategies no
  # longer share scalp's single-leg-market-only short-circuit - see
  # test_technique_fvg_uses_its_declared_zone_scale_policy
  # (test_scalp_micro_grid.py) for the full incident/fix history.
  from app.autotrade.execution_route import (
    ROUTE_ZONE_SPLIT,
    resolve_execution_route_plan,
  )

  plan = resolve_execution_route_plan(
    direction=direction,
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=1.1005,
    zone_low=1.1000,
    zone_high=1.1010,
    atr=0.0008,
    zone_fill_enabled=True,
    digits=5,
    strategy="FVG",
    strategy_family="zone",
    entry_clips=2,
  )
  assert plan.valid is True
  assert plan.route == ROUTE_ZONE_SPLIT
  assert len(plan.planned_leg_entry_prices) == 2


def test_fx_auto_plan_falls_back_to_market_watch_when_fvg_zone_too_narrow_to_split():
  # Owner 2026-09-08 (bad technique entries): FVG's own declared policy is
  # limit + zone_scale (FAMILY_SUPPLY_DEMAND), not a forced single-leg
  # market - _fx_match()'s zone here is just too narrow relative to ATR to
  # qualify for a 2-leg split, so it falls back to a single entry same as
  # any other limit-preference strategy would. That fallback now goes
  # through market_watch's broker-side zone revalidation instead of firing
  # an unconditional immediate market order (see
  # test_technique_fvg_uses_its_declared_zone_scale_policy for the wide-
  # zone case that now gets the real zone_scale ladder).
  cfg = _load_production_example().config
  match = replace(_fx_match(), strategy="FVG", family="zone")
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["planned_execution_route"] == "market"
  assert evaluation.measured.get("planned_leg_volume_ratios") in (None, [], ())

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="fx-fvg-single-plan",
    setup_id="fx-fvg-single-setup",
    thesis_id="fx-fvg-single-thesis",
    pip_size=Decimal("0.0001"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
    approved_measured=evaluation.measured,
  )
  assert plan.entry.type == "market_watch"
  assert plan.entry.legs == ()


def test_fx_targeting_is_explicit_configuration_not_symbol_detection():
  cfg = _load_production_example().config
  # Uniform 50/50 across FX pairs — front-load retired.
  for symbol in ("EURUSD", "USDJPY", "GBPJPY"):
    effective = cfg.for_instrument(symbol)
    assert effective.policy_name in (
      FX_FIXED_2R_V1_POLICY, "fx_fixed_2r_frontload_v1",
    )
    assert effective.targeting.mode is InstrumentTargetMode.FIXED_RR
    assert effective.targeting.target_r_multiples == _FX_TARGET_R_MULTIPLES
    assert effective.targeting.close_ratios == _FX_CLOSE_RATIOS
    assert effective.targeting.breakeven_after_r == 1.0
    assert effective.targeting.trail_after_r is None
    assert effective.targeting.trail_to_r is None
    assert effective.targeting.entry_clips == 2
    assert effective.execution.technique.require_sweep_body is False
    assert fixed_reward_risk(symbol, cfg) == 2.0
  # XAU deliberately diverges from the FX policies' shared 1R/2R shape -
  # see test_xau_technique_uses_the_owner_requested_r_ladder below.
  assert fixed_reward_risk("XAU", cfg) == 3.0
  assert fixed_reward_risk("XAUUSD", cfg) == 3.0


def test_hfs_fixed_rr_prefers_two_r_then_falls_back_to_one_r():
  cfg = _load_production_example().config
  # Room fits 1R (15) but not preferred 2R (30): FX takes exactly 1R.
  gold = _select_target(
    direction="BUY",
    worst_fill=1.16,
    room_pips=18,
    stop_pips=15,
    min_net=10,
    pip_size=0.0001,
    symbol="XAU",
    cfg=cfg,
  )
  fx = _select_target(
    direction="BUY",
    worst_fill=1.16,
    room_pips=18,
    stop_pips=15,
    min_net=10,
    pip_size=0.0001,
    symbol="EURUSD",
    cfg=cfg,
  )
  assert gold is not None
  assert gold[1] == 15.0
  assert fx is not None
  assert fx[1] == 15.0


def test_hfs_fixed_rr_takes_two_r_when_room_fits():
  cfg = _load_production_example().config
  target = _select_target(
    direction="SELL",
    worst_fill=216.0,
    room_pips=40,
    stop_pips=15,
    min_net=10,
    pip_size=0.01,
    symbol="GBPJPY",
    cfg=cfg,
  )
  assert target is not None
  assert target[1] == 30.0


def test_fx_reaction_stop_envelopes_diverge_while_gold_uses_structure_band():
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room

  cfg = _load_production_example().config
  eurusd_min, eurusd_max, eurusd_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=50,
    pip_size=0.0001,
    cfg=cfg,
    symbol="EURUSD",
  )
  gbpjpy_min, gbpjpy_max, gbpjpy_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=50,
    pip_size=0.01,
    cfg=cfg,
    symbol="GBPJPY",
  )
  gold_min, gold_max, gold_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
    symbol="XAU",
  )
  assert (eurusd_min, eurusd_max) == (10, 18)
  assert eurusd_measured["fixed_rr_targeting"] is True
  assert (gbpjpy_min, gbpjpy_max) == (15, 30)
  assert gbpjpy_measured["fixed_rr_targeting"] is True
  assert (gold_min, gold_max) == (50, 100)
  assert gold_measured["fixed_rr_targeting"] is True


def test_fx_auto_reaction_books_pack_volume_multiplier():
  """Autonomous FX reaction must stamp 1.5× like manual /algo FX.

  Live 2026-08-21: GBPJPY Key Level filled 0.12 lots (raw equity table)
  while pack ``manual.risk_multiplier`` / ``fx_volume_multiplier`` promised
  1.5×. Scalp books the same flat equity-table lot as any other trade
  (owner 2026-09-07, PR #486's equity_table sizing_mode default) without
  stacking the pack scale on top.
  """
  from tests.test_execution_pipeline_integrity import _policy_match

  cfg = _load_production_example().config
  fx_match = _fx_match("EURUSD")
  fx = evaluate_execution_policy(
    fx_match,
    spot_price=fx_match.current_price,
    executable_quote=fx_match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
  )
  assert fx.allowed is True
  assert fx.measured["instrument_volume_multiplier"] == pytest.approx(1.5)
  assert fx.measured["effective_risk_multiplier"] == pytest.approx(1.5)

  gold = evaluate_execution_policy(
    _policy_match(),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
    cfg=cfg,
  )
  assert gold.allowed is True
  assert gold.measured.get("instrument_volume_multiplier", 1.0) == pytest.approx(
    1.0
  )
  assert gold.measured["effective_risk_multiplier"] == pytest.approx(1.0)

  scalp_match = replace(
    fx_match,
    strategy="Range Sweep Scalp",
    family="scalp",
    strategy_mode="scalp",
    tier="A",
  )
  scalp = evaluate_execution_policy(
    scalp_match,
    spot_price=fx_match.current_price,
    executable_quote=fx_match.current_price,
    regime="range",
    pip_size=0.0001,
    cfg=cfg,
  )
  # May reject on room/geometry; scalp never carries a standalone multiplier.
  if scalp.allowed:
    assert scalp.measured["instrument_volume_multiplier"] == pytest.approx(1.0)
    assert scalp.measured["effective_risk_multiplier"] == pytest.approx(1.0)


def test_fx_fixed_rr_builds_one_r_two_r_with_breakeven():
  cfg = _load_production_example().config
  match = _fx_match()
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_policy_mode"] == "fixed_rr"
  assert evaluation.measured["effective_risk_multiplier"] == pytest.approx(1.5)
  assert evaluation.measured["breakeven_after_r"] == pytest.approx(1.0)
  assert evaluation.measured["target_room_fallback_used"] is False
  assert evaluation.measured["planned_target_r_multiples"] == ["1.0", "2.0"]
  assert evaluation.measured["planned_target_close_ratios"] == ["0.5", "0.5"]
  assert "planned_trail_after_target_id" not in evaluation.measured

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="fx-plan-1",
    setup_id="fx-setup-1",
    thesis_id="fx-thesis-1",
    pip_size=Decimal("0.0001"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
    approved_measured=evaluation.measured,
  )
  assert plan.risk.risk_multiplier == Decimal("1.5")

  entry = Decimal(str(evaluation.measured["planned_entry_price"]))
  risk = abs(entry - plan.stop.price)
  assert len(plan.targets) == 2
  assert [target.close_ratio for target in plan.targets] == [
    Decimal("0.5"),
    Decimal("0.5"),
  ]
  for target, multiple in zip(
    plan.targets,
    (Decimal("1"), Decimal("2")),
  ):
    assert abs(target.price - entry) == risk * multiple
  assert plan.management.be_after_target_id == "TP1"
  assert plan.management.trail_after_target_id is None
  assert plan.management.trail_to_target_id is None
  # The 50-pip match target was only provisional; stop geometry owns TP.
  assert abs(plan.targets[-1].price - entry) / Decimal("0.0001") != Decimal("50")


def test_fixed_rr_falls_back_to_one_r_when_two_r_does_not_fit():
  cfg = _load_production_example().config
  match = _fx_match()
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
    available_target_room_pips=15.0,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_room_fallback_used"] is True
  assert evaluation.measured["target_reward_risk"] == pytest.approx(1.0)
  assert evaluation.measured["planned_target_r_multiples"] == ["1.0"]
  assert evaluation.measured["planned_target_close_ratios"] == ["1.0"]
  assert evaluation.measured["breakeven_after_r"] is None
  assert "planned_trail_after_target_id" not in evaluation.measured

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="fx-plan-fallback",
    setup_id="fx-setup-fallback",
    thesis_id="fx-thesis-fallback",
    pip_size=Decimal("0.0001"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
    approved_measured=evaluation.measured,
  )
  assert len(plan.targets) == 1
  assert plan.targets[0].close_ratio == Decimal("1")
  assert plan.management.be_after_target_id is None
  assert plan.management.trail_after_target_id is None
  assert plan.management.trail_to_target_id is None


def test_fixed_rr_rejects_when_opposing_room_cannot_hold_one_r():
  cfg = _load_production_example().config
  match = _fx_match()
  sink_calls: list[tuple[str, str, dict[str, str]]] = []

  def _sink(name: str, symbol: str, labels: dict[str, str]) -> None:
    sink_calls.append((name, symbol, labels))

  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
    available_target_room_pips=9.0,
    metric_sink=_sink,
  )
  assert evaluation.allowed is False
  assert evaluation.reason_code == "fixed_rr_room_insufficient"
  assert evaluation.terminal is True
  assert evaluation.measured["target_fallback_reward_risk"] == 1.0
  assert sink_calls == [
    (
      "fixed_rr_room_below_1r",
      "EURUSD",
      {"setup": "Trend Pullback"},
    ),
  ]


def test_fixed_rr_one_r_fallback_is_symmetric_for_sell():
  cfg = _load_production_example().config
  match = replace(
    _fx_match("GBPJPY"),
    match_id="gbpjpy-fallback-sell",
    direction="SELL",
    key_level=190.04,
    entry_low=190.00,
    entry_high=190.08,
    current_price=190.04,
    atr=0.12,
    structure_swing=190.12,
    targets_pips=(70,),
    structural_zone_id="gbpjpy-supply-fallback",
    structural_zone_low=190.00,
    structural_zone_high=190.08,
    structural_kind="supply",
    htf_bias="down",
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.01,
    cfg=cfg,
    available_target_room_pips=20.0,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_room_fallback_used"] is True
  assert evaluation.measured["breakeven_after_r"] is None
  entry = Decimal(str(evaluation.measured["planned_entry_price"]))
  targets = tuple(
    Decimal(value) for value in evaluation.measured["planned_target_prices"]
  )
  assert len(targets) == 1
  assert targets[0] < entry
  assert evaluation.measured["planned_target_close_ratios"] == ["1.0"]


def test_gbpjpy_sell_uses_uniform_two_r_contract():
  cfg = _load_production_example().config
  match = replace(
    _fx_match("GBPJPY"),
    match_id="gbpjpy-fixed-rr-match",
    direction="SELL",
    key_level=190.04,
    entry_low=190.00,
    entry_high=190.08,
    current_price=190.04,
    atr=0.12,
    structure_swing=190.12,
    targets_pips=(70,),
    structural_zone_id="gbpjpy-supply-190.00",
    structural_zone_low=190.00,
    structural_zone_high=190.08,
    structural_kind="supply",
    htf_bias="down",
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.01,
    cfg=cfg,
  )
  assert evaluation.allowed is True
  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="gbpjpy-plan-1",
    setup_id="gbpjpy-setup-1",
    thesis_id="gbpjpy-thesis-1",
    pip_size=Decimal("0.01"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
  )
  entry = Decimal(str(evaluation.measured["planned_entry_price"]))
  assert plan.stop.price > entry > plan.targets[-1].price
  assert entry - plan.targets[-1].price == (
    plan.stop.price - entry
  ) * Decimal("2")
  assert [target.close_ratio for target in plan.targets] == [
    Decimal("0.5"),
    Decimal("0.5"),
  ]
  assert plan.management.be_after_target_id == "TP1"
  assert plan.management.trail_after_target_id is None
  assert plan.management.trail_to_target_id is None


def test_xau_technique_uses_the_owner_requested_r_ladder():
  """2026-09: XAU deliberately diverges from the FX policies' shared 1R/2R
  shape onto the same 4-level R ladder as manual /algo (0.5R/1R/2R/3R) -
  the uniform-across-fixed_rr contract is now per-policy, not global.
  """
  cfg = _load_production_example().config
  xau = cfg.for_instrument("XAU")
  assert xau.targeting.target_r_multiples == (0.5, 1.0, 2.0, 3.0)
  assert xau.targeting.close_ratios == (0.4, 0.2, 0.2, 0.2)
  assert xau.targeting.breakeven_after_r == 0.5
  assert xau.targeting.trail_after_r is None
  # FX keeps the old shape unchanged.
  eurusd = cfg.for_instrument("EURUSD")
  assert eurusd.targeting.target_r_multiples == (1.0, 2.0)
  assert eurusd.targeting.close_ratios == (0.5, 0.5)
  assert eurusd.targeting.breakeven_after_r == 1.0


def test_root_card_r_multiples_use_the_configured_ladder_not_card_prices(
  monkeypatch,
):
  # Live 2026-09-07: a GBPJPY SELL (iFVG) root card showed "+10R"/"+15.6R"
  # for what was actually a uniform 1R/2R fixed_rr trade. The card's stop
  # is anchored to structure while the displayed entry-zone edge is only a
  # reward-side planning reference -- the two don't share a basis, so
  # deriving R from (target - zone edge) / (stop - zone edge) landed far
  # from the real ratio. The root card must read target_r_multiples
  # straight from the instrument's own fixed_rr config instead.
  from app.autotrade import setup_card
  from app.core import instrument_geometry

  cfg = _load_production_example().config
  monkeypatch.setattr(instrument_geometry, "runtime_config", cfg)

  gbpjpy_match = replace(
    _fx_match("GBPJPY"),
    strategy="iFVG",
    direction="SELL",
    entry_low=209.132,
    entry_high=209.337,
    key_level=209.337,
  )
  # A stop close to the zone edge and far targets -- exactly the shape
  # that made the old price-derived formula produce "+10R"/"+15.6R".
  text = setup_card.format_plan_published_root_card(
    gbpjpy_match,
    stop_price=209.369,
    target_prices=(209.013, 208.835),
  )
  assert "(+1R)</b>" in text
  assert "(+2R)</b>" in text

  xau_match = replace(
    _fx_match("XAU"),
    strategy="Key Level Reaction",
    direction="SELL",
    entry_low=4383.0,
    entry_high=4388.0,
    key_level=4388.0,
  )
  xau_text = setup_card.format_plan_published_root_card(
    xau_match,
    stop_price=4396.0,
    target_prices=(4380.0, 4376.0, 4368.0, 4360.0),
  )
  assert "• <b>TP1:</b> <b>4,380.00 (+0.5R)</b>" in xau_text
  assert "• <b>TP2:</b> <b>4,376.00 (+1R)</b>" in xau_text
  assert "• <b>TP3:</b> <b>4,368.00 (+2R)</b>" in xau_text
  assert "• <b>TP4:</b> <b>4,360.00 (+3R)</b>" in xau_text


def test_xau_gets_a_smaller_opposing_barrier_buffer_than_fx():
  # XAU's ATR is dollar-denominated; the FX-tuned 0.5x multiple buffers
  # away 40-115+ pips of real opposing-structure room on XAU (measured in
  # prod), comparable to or larger than XAU's own 25-100 pip stop
  # envelope. XAU overrides to 0.15; FX keeps the global 0.5 default.
  cfg = _load_production_example().config
  xau = cfg.for_instrument("XAU")
  eurusd = cfg.for_instrument("EURUSD")
  assert xau.actionability.target_room.barrier_buffer_atr == pytest.approx(0.15)
  assert eurusd.actionability.target_room.barrier_buffer_atr == pytest.approx(0.5)
  assert cfg.actionability.target_room.barrier_buffer_atr == pytest.approx(0.5)
