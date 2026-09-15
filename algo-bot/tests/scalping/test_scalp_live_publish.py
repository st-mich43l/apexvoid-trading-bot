"""Tests for M1 scalp live StrategyMatch + publish bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.autotrade import worker
from app.scalping.models import (
  ARCHETYPE_RANGE_SWEEP,
  CONTEXT_VERSION,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpOpportunity,
)
from app.scalping.publish import build_scalp_strategy_match, publish_scalp_live
from app.autotrade.strategy_match import _identity_ok, _valid_match


pytestmark = pytest.mark.no_database


def _opp() -> ScalpOpportunity:
  return ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="opp1",
    context_id="ctx1",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=90,
    zone_low=4000.0,
    zone_high=4001.0,
    key_level=4000.5,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4000.5,
    invalidation_price=3999.5,
    expected_target_price=4003.5,
    expected_target_pips=25.0,
    expected_stop_pips=15.0,
    expected_reward_risk=1.5,
    location_position=0.25,
    score=1.0,
    reasons=("lower_edge_sweep_reclaim",),
    expires_at=400,
    episode_id="ep1",
  )


def _ctx() -> ScalpContextSnapshot:
  return ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx1",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3980.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.25,
    active_range_low=4000.0,
    active_range_high=4050.0,
    active_range_eq=4025.0,
    nearest_support_low=4000.0,
    nearest_support_high=4002.0,
    nearest_resistance_low=4048.0,
    nearest_resistance_high=4050.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=4.0,
  )


def test_build_scalp_strategy_match_is_valid():
  match = build_scalp_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.strategy == "Range Sweep Scalp"
  assert match.structural_source == "scalp"
  assert match.structural_kind == "demand"
  assert match.direction == "BUY"
  assert match.full_take_profit_pips == 25
  assert match.targets_pips == (15, 25)
  # 2026-09-15 (owner-reported): the root card must show R, not just raw
  # pips, for scalp too - each entry is targets_pips[i] / stop (15).
  assert match.scalp_target_r_multiples == (1.0, 1.67)
  # invalidation_price is the source of truth for structure_swing.
  assert match.structure_swing == pytest.approx(3999.5)
  assert match.structure_swing == pytest.approx(_opp().invalidation_price)
  assert match.family == "scalp"
  assert match.strategy_mode == "scalp_m1"
  assert _identity_ok(match)
  assert _valid_match(match)


def test_scalp_root_card_shows_r_multiples_not_bare_pips():
  # Owner-reported 2026-09-15: the scalp root/forming card showed a bare
  # "+65" pip offset instead of an R level, unlike every fixed_rr
  # strategy's card. technique_fixed_rr_targeting always returns None for
  # M1 scalp strategies by design (scalp keeps its own book) - the card
  # must fall back to scalp_target_r_multiples, not to raw pips.
  from app.autotrade.setup_card import format_plan_published_root_card

  match = build_scalp_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  text = format_plan_published_root_card(
    match,
    stop_price=match.structure_swing,
    target_prices=(4003.0, 4003.5),
  )
  assert "(+1R)</b>" in text
  assert "(+1.7R)</b>" in text
  assert "+65" not in text
  assert " pips" not in text


def test_build_scalp_strategy_match_computes_real_bias_relationship():
  """Live 2026-08-25: card display hardcoded strategy_mode='scalp_m1'
  always rendered as counter-trend regardless of true bias. bias_relationship
  is now computed from real HTF bias + direction (like structural setups),
  without touching strategy_mode - other code keys on that literal value
  for scalp-family classification and trade-plan building.
  """
  from dataclasses import replace

  aligned_ctx = replace(_ctx(), htf_bias="up")
  match = build_scalp_strategy_match(
    _opp(), aligned_ctx, bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.direction == "BUY"
  assert match.bias_relationship == "with_bias"
  assert match.strategy_mode == "scalp_m1"

  opposed_ctx = replace(_ctx(), htf_bias="down")
  match = build_scalp_strategy_match(
    _opp(), opposed_ctx, bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.bias_relationship == "counter_bias"
  assert match.strategy_mode == "scalp_m1"


def test_build_scalp_strategy_match_sell_uses_invalidation_exactly():
  from dataclasses import replace

  opp = replace(
    _opp(),
    direction="SELL",
    trigger_price=4050.0,
    invalidation_price=4052.5,
    expected_stop_pips=15.0,
    expected_target_pips=30.0,
    expected_target_price=4047.0,
    expected_reward_risk=2.0,
    zone_low=4050.0,
    zone_high=4051.0,
    key_level=4050.5,
  )
  match = build_scalp_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4049.0, quote_ask=4050.0,
  )
  assert match.direction == "SELL"
  assert match.structure_swing == pytest.approx(opp.invalidation_price)
  assert match.structure_swing == pytest.approx(4052.5)


def test_build_scalp_1to2_publishes_half_at_one_r():
  # Owner 2026-08-11: 1:2 books half at 1R and half at 2R; final TP stays 2R.
  opp = _opp()
  # Rebuild with exact 1:2 geometry (stop 15 → target 30).
  from dataclasses import replace
  opp = replace(
    opp,
    expected_target_pips=30.0,
    expected_target_price=4004.0,
    expected_stop_pips=15.0,
    expected_reward_risk=2.0,
  )
  match = build_scalp_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.targets_pips == (15, 30)
  assert match.full_take_profit_pips == 30
  assert match.scalp_target_r_multiples == (1.0, 2.0)
  assert _valid_match(match)


def test_build_scalp_fx_publishes_single_two_r_target():
  from dataclasses import replace
  from tests.test_config_effective_instrument_context import (
    _load_production_example,
  )

  opp = replace(
    _opp(),
    symbol="EURUSD",
    expected_target_pips=30.0,
    expected_target_price=1.1630,
    expected_stop_pips=15.0,
    expected_reward_risk=2.0,
    zone_low=1.1600,
    zone_high=1.1605,
    key_level=1.1602,
    trigger_price=1.1602,
    invalidation_price=1.1587,
  )
  ctx = replace(_ctx(), symbol="EURUSD")
  match = build_scalp_strategy_match(
    opp,
    ctx,
    bar_ts=120,
    quote_bid=1.1601,
    quote_ask=1.1602,
    cfg=_load_production_example().config,
  )
  assert match.targets_pips == (15, 30)
  assert match.full_take_profit_pips == 30
  assert _valid_match(match)


def test_build_scalp_1to1_stays_single_full_exit():
  from dataclasses import replace
  opp = replace(
    _opp(),
    expected_target_pips=15.0,
    expected_target_price=4002.5,
    expected_stop_pips=15.0,
    expected_reward_risk=1.0,
  )
  match = build_scalp_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  # Discovery stop=15 / target=15 → single 1R exit; plan books 100% there.
  assert match.targets_pips == (15,)
  assert match.full_take_profit_pips == 15
  assert match.scalp_target_r_multiples == (1.0,)
  assert _valid_match(match)


def test_build_scalp_1to2_trade_plan_moves_sl_to_be_after_tp1():
  """1:2 scalp: half at 1R, then BE protects the 2R runner."""
  from dataclasses import replace
  from decimal import Decimal

  from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match

  opp = replace(
    _opp(),
    expected_target_pips=30.0,
    expected_target_price=4004.0,
    expected_stop_pips=15.0,
    expected_reward_risk=2.0,
  )
  match = build_scalp_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.targets_pips == (15, 30)
  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="v8:test-1to2-be",
    setup_id=match.match_id,
    thesis_id=match.thesis_id or match.match_id,
    pip_size=Decimal("0.1"),
    spot_price=4001.5,
    executable_quote=4001.5,
    regime="range",
    max_volume=100_000,
    be_after_target_index=0,
  )
  assert [t.close_ratio for t in plan.targets] == [
    Decimal("0.5"), Decimal("0.5"),
  ]
  assert plan.management.be_after_target_id == "TP1"
  assert plan.sizing is not None
  # Owner 2026-09-04: scalp now sizes off the same equity_table lot as any
  # other trade by default; risk_percent still tracks the configured
  # per-trade budget even though "risk" mode no longer consumes it.
  assert plan.sizing.mode == "equity_table"
  assert plan.risk.risk_percent == Decimal("0.5")


def test_build_scalp_1to1_trade_plan_books_full_volume():
  """1:1 scalp must not leave a runner — close_ratio on the sole TP is 1.0."""
  from dataclasses import replace
  from decimal import Decimal

  from app.autotrade.trade_plan_builder import (
    _equal_close_ratios,
    build_trade_plan_from_strategy_match,
  )

  opp = replace(
    _opp(),
    expected_target_pips=15.0,
    expected_target_price=4002.5,
    expected_stop_pips=15.0,
    expected_reward_risk=1.0,
  )
  match = build_scalp_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.targets_pips == (15,)
  assert _equal_close_ratios(len(match.targets_pips)) == (Decimal("1"),)

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="v8:test-1to1",
    setup_id=match.match_id,
    thesis_id=match.thesis_id or match.match_id,
    pip_size=Decimal("0.1"),
    spot_price=4001.5,
    executable_quote=4001.5,
    regime="range",
    max_volume=100_000,
  )
  assert len(plan.targets) == 1
  assert plan.targets[0].close_ratio == Decimal("1")
  assert plan.targets[0].target_id == "TP1"
  # Full exit at TP1 — no BE trail contract on a single-target plan.
  assert plan.management.be_after_target_id is None


def test_scalping_risk_percent_is_bounded_and_unambiguous():
  from app.configuration.models.strategies import StrategiesScalpingRiskConfig

  assert StrategiesScalpingRiskConfig().risk_percent_per_trade == 0.5
  # Owner 2026-09-04: scalp now defaults to the same equity_table lot as
  # any other trade; "risk" (the risk-percent-of-stop-distance formula)
  # remains a selectable mode, just no longer the default.
  assert StrategiesScalpingRiskConfig().sizing_mode == "equity_table"
  with pytest.raises(ValueError):
    StrategiesScalpingRiskConfig(risk_percent_per_trade=0.001)
  with pytest.raises(ValueError):
    StrategiesScalpingRiskConfig(risk_percent_per_trade=10)


def test_scalping_sizing_mode_allows_both_documented_values():
  from app.configuration.models.strategies import StrategiesScalpingRiskConfig

  assert StrategiesScalpingRiskConfig(sizing_mode="risk").sizing_mode == "risk"
  assert (
    StrategiesScalpingRiskConfig(sizing_mode="equity_table").sizing_mode
    == "equity_table"
  )
  with pytest.raises(ValueError):
    StrategiesScalpingRiskConfig(sizing_mode="turbo")


def test_build_scalp_strategy_match_carries_execution_eligibility():
  # Regression: strategy_mode="scalp_m1" routes through the generic
  # source="scanner_strategy_match" intent branch in worker.py (only
  # "mapped_zone_reaction" is exempt), and that branch hard-rejects any
  # match whose execution_eligibility is None as static_eligibility_missing.
  # HFS never runs the classic scanner.py detection path that normally
  # populates it, so every HFS opportunity died here unconditionally --
  # 34 of 55 live HFS publishes in production on 2026-08-06.
  match = build_scalp_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.execution_eligibility is not None
  assert match.execution_eligibility.allowed is True
  assert match.execution_eligibility.reason_code == "scalp_m1_eligible"
  assert match.execution_eligibility.direction == "BUY"


def _patch_common(monkeypatch, pub, *, status, thesis_id="thesis-1"):
  monkeypatch.setattr(
    pub.scanner,
    "_advance_setup_to_confirmed",
    AsyncMock(return_value=("setup-1", thesis_id)),
  )
  persist = AsyncMock(side_effect=lambda _client, stamped: stamped)
  monkeypatch.setattr(pub, "_persist_scalp_match", persist)
  publish = AsyncMock(return_value=worker.PublishResult(
    status=status,
    plan_id="v8:setup-1",
    reason_code="candidate_published",
    zone_id="ep1",
    setup_id="setup-1",
  ))
  monkeypatch.setattr(pub.worker, "try_publish_executable_signal", publish)
  monkeypatch.setattr(
    pub,
    "runtime_config",
    type("C", (), {
      "runtime": type("R", (), {
        "auto_trade": type("A", (), {"enabled": True})(),
      })(),
    })(),
  )
  return persist, publish


@pytest.mark.asyncio
async def test_publish_scalp_live_calls_worker(monkeypatch):
  from app.scalping import publish as pub

  match = build_scalp_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  persist, publish = _patch_common(
    monkeypatch, pub, status=worker.PUBLISH_STATUS_PUBLISHED,
  )

  result = await publish_scalp_live(
    object(), match, symbol="XAU", bar_ts=120,
  )
  assert result is not None
  assert result.status == worker.PUBLISH_STATUS_PUBLISHED
  persist.assert_awaited_once()
  publish.assert_awaited_once()
  stamped = publish.await_args.args[1]
  assert stamped.thesis_id == "thesis-1"


@pytest.mark.asyncio
async def test_persist_scalp_match_writes_worker_keys():
  from app.scalping.publish import _persist_scalp_match

  match = build_scalp_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  stored: dict[str, str] = {}

  class FakeRedis:
    async def get(self, key):
      return stored.get(key)

    async def set(self, key, value, ex=None):
      stored[key] = value

  out = await _persist_scalp_match(FakeRedis(), match)
  assert out.match_id == match.match_id
  assert "auto_trade:strategy_match:XAU" in stored or any(
    "strategy_match" in k for k in stored
  )
  assert any("strategy_matches" in k for k in stored)
