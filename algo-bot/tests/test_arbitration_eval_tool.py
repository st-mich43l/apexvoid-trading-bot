"""The offline arbitration evaluation is itself evidence, so it is tested.

Model A must be the production arbitration exactly, and the outcome simulator must be
conservative: stops act before targets inside one bar, a buy limit needs the ask to reach
it, and a stop beyond the envelope cap is never traded.
"""

from __future__ import annotations

import random

import pytest

from app.autotrade.arbitration import ExecutionIntent, arbitrate_execution_intents
from tools import arbitration_eval as ev

pytestmark = pytest.mark.no_database


def _random_intents(rng: random.Random) -> list[ExecutionIntent]:
  intents = []
  for index in range(rng.randint(1, 7)):
    low = 4000 + rng.choice([0, 0, 3, 8, 40])
    intents.append(ExecutionIntent(
      intent_id=f"i{index}", source="go", strategy=rng.choice(["supply", "key_level", "range_edge", "fvg"]),
      direction=rng.choice(["BUY", "SELL"]), confluence=rng.choice([1, 2, 3]), freshness=1000.0 + rng.randint(0, 5),
      distance_pips=0.0, entry_low=low, entry_high=low + rng.choice([1, 2, 4]), structural_id=f"z{rng.randint(0, 4)}",
      quality_overall=rng.choice([0.5, 0.67, 0.85, 1.0, None]), structural_quality=rng.choice([None, 8.0, 12.5]),
      atr=rng.choice([0.0, 1.0, 2.5]), bias_relationship=rng.choice([None, "with_bias", "counter_bias"]),
      executable_now=rng.random() < 0.7,
    ))
  return intents


def test_model_a_is_the_production_arbitration_exactly():
  rng = random.Random(7)
  model = ev.model_a()
  for _ in range(400):
    intents = _random_intents(rng)
    production = arbitrate_execution_intents(intents, conflict_margin_quality=ev.CONFLICT_MARGIN)
    winners, suppressed, reason, losers = model.arbitrate(intents)
    assert tuple(i.intent_id for i in winners) == tuple(i.intent_id for i in production.ordered)
    assert {i.intent_id for i in suppressed} == {i.intent_id for i in production.suppressed}
    assert reason == production.reason_code and losers == production.thesis_losers


def _row(**overrides):
  row = dict(
    symbol="XAU", direction="BUY", entry_low=100.0, entry_high=101.0, reference_price=105.0, invalidation=99.0,
    bar_time=1000, bar_tf="M5", expires_at=1000 + 86400, stop_floor_pips=50.0, stop_cap_pips=60.0,
    id="x", structural_id="x", strategy="supply",
  )
  row.update(overrides)
  return row


def _bar(t, o, h, l, c):
  return (t, o, h, l, c, 0.0)


def test_a_buy_limit_waits_for_the_ask_to_reach_it():
  # proximal = 101.0; the ask is the bid low + 0.25. Low 101.2 -> ask 101.45: no fill.
  bars = [_bar(1300, 105, 105, 101.2, 103), _bar(1600, 103, 104, 101.2, 103)]
  assert ev.simulate(_row(), bars, "M5")["reason"] == "unfilled_until_expiry"
  bars = [_bar(1300, 105, 105, 100.6, 103)]  # low 100.6 + 0.25 = 100.85 <= 101.0
  assert ev.simulate(_row(), bars, "M5")["filled"] is True


def test_stop_acts_before_targets_in_the_same_bar_and_costs_one_r():
  # fill bar then a bar that spans both the stop (96.0) and TP1: the stop wins.
  bars = [_bar(1300, 105, 105, 100.6, 103), _bar(1600, 103, 130, 90, 100)]
  outcome = ev.simulate(_row(), bars, "M5")
  assert outcome["filled"] and outcome["r"] == pytest.approx(-1.0) and outcome["reason"] == "stop"


def test_break_even_after_the_first_target():
  # entry 101.0, stop 96.0 (50 pips), TP1 106.0 -> +0.4R booked, then the stop at entry.
  bars = [_bar(1300, 105, 105, 100.6, 103), _bar(1600, 103, 106.5, 102, 105), _bar(1900, 105, 105, 100.5, 101)]
  outcome = ev.simulate(_row(), bars, "M5")
  assert outcome["r"] == pytest.approx(0.4, abs=1e-6) and outcome["reason"] == "be_or_trail_stop"


def test_a_stop_beyond_the_envelope_cap_is_never_traded():
  outcome = ev.simulate(_row(invalidation=90.0), [_bar(1300, 105, 105, 100.0, 103)], "M5")
  assert outcome == {"filled": False, "r": 0.0, "reason": "stop_above_cap"}


def test_a_stop_inside_the_floor_is_widened_to_the_floor():
  # invalidation 100.5 is 5 pips from 101.0; widened to 50 pips -> a drop to 96.5 is not yet a stop.
  bars = [_bar(1300, 105, 105, 100.6, 103), _bar(1600, 103, 103, 96.6, 98)]
  outcome = ev.simulate(_row(invalidation=100.5), bars, "M5")
  assert outcome["reason"] in {"timeout"}


def test_a_wider_spread_never_improves_a_buy_entry():
  bars = [_bar(1300, 105, 105, 100.9, 103)]
  assert ev.simulate(_row(), bars, "M5", spread_scale=1.0)["filled"] is False  # 100.9 + 0.25 > 101.0
  bars = [_bar(1300, 105, 105, 100.7, 103)]
  assert ev.simulate(_row(), bars, "M5", spread_scale=1.0)["filled"] is True
  assert ev.simulate(_row(), bars, "M5", spread_scale=2.0)["filled"] is False  # 100.7 + 0.5 > 101.0
