"""XAU Break & Retest scales in like the zone strategies.

Production 2026-10-08: a Break & Retest BUY with a retest band of 0.23
(4114.91-4115.14) was one market_watch order and the card printed
"Entry Zone 4,115 - 4,115". A level retest on gold uses the shallow/deep ladder;
the thin band is widened by the ladder's own rule. FX keeps its single entry.
"""

from __future__ import annotations

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade.execution_policy import evaluate_execution_policy
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database


def _evaluate(**overrides):
  quote = overrides.pop("quote", 4118.3)
  direction = overrides.pop("direction", "BUY")
  symbol = overrides.get("symbol", "XAU")
  buy = direction == "BUY"
  return evaluate_execution_policy(
    _policy_match(
      strategy="Break & Retest",
      direction=direction,
      entry_low=4114.91,
      entry_high=4115.14,
      current_price=quote,
      atr=2.0,
      structure_swing=4113.76 if buy else 4116.29,
      go_invalidation_price=4113.76 if buy else 4116.29,
      tags=(GO_ORIGIN_TAG,),
      structural_kind="break_retest",
      targets_pips=(50,),
      **overrides,
    ),
    spot_price=quote,
    executable_quote=quote,
    regime="trend",
    pip_size=0.1 if symbol == "XAU" else 0.0001,
    cfg=_cfg(),
  )


def _pips(a: float, b: float) -> float:
  return round(abs(a - b) / 0.1, 3)


def test_a_thin_xau_retest_level_becomes_a_50_pip_manual_style_ladder():
  evaluation = _evaluate()
  assert evaluation.allowed, evaluation.reason_code
  measured = evaluation.measured
  assert measured["planned_execution_route"] == "zone_split"
  # The band is widened to 50 pips toward the stop; Manual Algo's legs: shallow at the
  # near edge, deep at the band's midpoint; 80/20.
  assert _pips(measured["planned_entry_zone_low"], measured["planned_entry_zone_high"]) == pytest.approx(50.0, abs=0.01)
  assert measured["planned_leg_entry_prices"] == pytest.approx([4115.14, 4112.64])
  assert measured["planned_leg_volume_ratios"] == pytest.approx([0.80, 0.20])
  # The stop follows the band and stays inside the 50-70 pip envelope from the shallow leg.
  risk = _pips(float(measured["planned_stop_price"]), 4115.14)
  assert 50.0 <= risk <= 70.0, risk
  assert float(measured["planned_stop_price"]) < measured["planned_entry_zone_low"]


def test_the_ladder_does_not_depend_on_where_the_quote_is():
  for quote in (4118.3, 4115.0):
    evaluation = _evaluate(quote=quote)
    assert evaluation.allowed, evaluation.reason_code
    assert evaluation.measured["planned_leg_entry_prices"] == pytest.approx([4115.14, 4112.64])


def test_sell_mirrors_buy():
  evaluation = _evaluate(direction="SELL")
  assert evaluation.allowed, evaluation.reason_code
  measured = evaluation.measured
  assert measured["planned_leg_entry_prices"] == pytest.approx([4114.91, 4117.41])
  assert _pips(measured["planned_entry_zone_low"], measured["planned_entry_zone_high"]) == pytest.approx(50.0, abs=0.01)
  risk = _pips(float(measured["planned_stop_price"]), 4114.91)
  assert 50.0 <= risk <= 70.0, risk


def test_the_width_gate_judges_go_band_not_the_widened_band():
  # ATR 1.0: the widened 5.0 band would be 5 ATR (> the 2.5 ATR gate); Go's own 0.23 band passes.
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Break & Retest", direction="BUY", entry_low=4114.91, entry_high=4115.14,
      current_price=4118.3, atr=1.0, structure_swing=4113.76, go_invalidation_price=4113.76,
      tags=(GO_ORIGIN_TAG,), structural_kind="break_retest", targets_pips=(50,),
    ),
    spot_price=4118.3, executable_quote=4118.3, regime="trend", pip_size=0.1, cfg=_cfg(),
  )
  assert evaluation.allowed, evaluation.reason_code


def test_other_market_strategies_and_fx_keep_their_single_entry():
  momentum = evaluate_execution_policy(
    _policy_match(
      strategy="Momentum Ride", direction="BUY", entry_low=4114.91, entry_high=4115.14,
      current_price=4115.0, atr=2.0, structure_swing=4113.76, targets_pips=(50,),
    ),
    spot_price=4115.0, executable_quote=4115.0, regime="trend", pip_size=0.1, cfg=_cfg(),
  )
  assert momentum.measured.get("planned_leg_entry_prices") in ([], None) or len(momentum.measured["planned_leg_entry_prices"]) <= 1


# ---- the card prints the prices the ladder really rests at ----------------------------

import json

from app.autotrade.setup_card import (
  apply_forming_card_entry,
  format_plan_entry_line,
  published_plan_entry_span,
)
from app.autotrade.trade_plan import TradePlan
from app.persistence import redis_state
from app.signals.manual_plan import build_manual_trade_plan
from tests.test_manual_plan import _intent


def test_a_single_price_is_never_printed_as_a_zone():
  assert format_plan_entry_line("XAU", 4115.0, 4115.14, digits=2) == "⚡️ Entry Price:  <b>4,115</b>"
  assert format_plan_entry_line("XAU", 4114.45, 4115.14, digits=2) == "⚡️ Entry Zone:  <b>4,114 - 4,115</b>"


def test_the_card_entry_line_is_replaced_in_place_and_idempotently():
  card = "\n".join(["head", "⚡️ Entry Zone:  <b>4,115 - 4,115</b>", "🛡 SL:     <b>4,110</b>"])
  line = "⚡️ Entry Zone:  <b>4,114 - 4,115</b>"
  once = apply_forming_card_entry(card, line)
  assert once.splitlines() == ["head", line, "🛡 SL:     <b>4,110</b>"]
  assert apply_forming_card_entry(once, line) == once
  assert apply_forming_card_entry("no entry here", line) == "no entry here"


async def _store(plan: TradePlan, setup_id: str) -> None:
  client = redis_state.get_client()
  data = plan.to_dict()
  data["plan_id"] = f"v8:{setup_id}"
  await client.set(f"execution:plan:v8:{setup_id}", json.dumps(data))


@pytest.mark.asyncio
async def test_a_ladder_plan_prints_its_resting_prices_and_other_entries_keep_the_zone():
  client = redis_state.get_client()
  ladder = build_manual_trade_plan(_intent(), account_equity=1595.0)
  await _store(ladder, "ladder")
  assert await published_plan_entry_span(client, "ladder") == (4100.0, 4102.5)

  single = build_manual_trade_plan(_intent(single_entry_override=True))
  await _store(single, "single")
  assert await published_plan_entry_span(client, "single") is None
  assert await published_plan_entry_span(client, "missing") is None
