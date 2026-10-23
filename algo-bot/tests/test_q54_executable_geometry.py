"""Q5.4: technical against executable geometry, from the Go invalidation to the room in front of the executed ladder.

The stop the executor places is Go's invalidation, widened to the instrument floor; the ladder is fixed multiples of that stop. These fixtures
pin the numbers end to end (no behaviour change: they describe the contract the room diagnostic reads).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.autotrade.protective_stop import plan_go_invalidation_stop
from app.autotrade.structural_target_room import ZoneOpposingEntry, assess_executable_room, evaluate_structural_target_room

pytestmark = pytest.mark.no_database


def _fx_stop(direction, *, entry, invalidation, floor, pip="0.0001", digits=5, ceiling=40):
  return plan_go_invalidation_stop(
    direction=direction, entry_zone_low=Decimal(entry) - Decimal(pip), entry_zone_high=Decimal(entry), planned_leg_prices=[Decimal(entry)],
    resolved_leg_volumes=[1], invalidation_price=Decimal(invalidation), minimum_stop_pips=floor, maximum_stop_pips=ceiling,
    pip_size=Decimal(pip), digits=digits, widen_to_minimum=True,
  )


def _ladder(stop_pips):
  stop = float(stop_pips)
  return [stop, 2 * stop]  # FX fixed 1R / 2R of the executable stop


def _room(direction, room_pips, *, entry=1.1, pip=0.0001):
  """An admitted plan with an opposing barrier ``room_pips`` ahead (no buffer, so usable == raw)."""
  if direction == "BUY":
    barrier = ZoneOpposingEntry("sell", entry + room_pips * pip, entry + (room_pips + 10) * pip, tier="zone", score=0.8, touches=1)
  else:
    barrier = ZoneOpposingEntry("buy", entry - (room_pips + 10) * pip, entry - room_pips * pip, tier="zone", score=0.8, touches=1)
  decision = evaluate_structural_target_room(
    direction=direction, planned_entry_price=entry, candidate_entry_low=entry, candidate_entry_high=entry, configured_target_pips=(40,),
    actionable_entries=[barrier], atr=0.0010, pip_size=pip, barrier_buffer_atr=0.0, execution_cost_pips=1.0, executable_entry_price=entry,
  )
  assert decision.allowed
  return decision


@pytest.mark.parametrize("direction,invalidation,widened_stop", [("BUY", "1.09950", "1.09880"), ("SELL", "1.10050", "1.10120")])
def test_an_fx_stop_below_the_floor_is_widened_to_it_and_the_technical_stop_is_kept(direction, invalidation, widened_stop):
  plan = _fx_stop(direction, entry="1.10000", invalidation=invalidation, floor=12)
  assert plan.base_stop_pips == Decimal("5") and plan.final_stop_pips == Decimal("12")
  assert plan.final_stop_price == Decimal(widened_stop) and plan.adjustment == "envelope_minimum"
  assert plan.final_stop_pips / plan.base_stop_pips == Decimal("2.4")


@pytest.mark.parametrize("direction,invalidation", [("BUY", "1.09880"), ("SELL", "1.10120")])
def test_a_stop_exactly_at_the_floor_is_not_widened(direction, invalidation):
  plan = _fx_stop(direction, entry="1.10000", invalidation=invalidation, floor=12)
  assert plan.final_stop_pips == plan.base_stop_pips == Decimal("12") and plan.adjustment == "none"


@pytest.mark.parametrize("direction,invalidation", [("BUY", "1.09850"), ("SELL", "1.10150")])
def test_a_stop_above_the_floor_is_left_alone_and_never_moved_closer(direction, invalidation):
  plan = _fx_stop(direction, entry="1.10000", invalidation=invalidation, floor=12)
  assert plan.final_stop_pips == Decimal("15") and plan.final_stop_price == Decimal(invalidation) and plan.adjustment == "none"


def test_a_jpy_pair_is_measured_in_hundredths_and_rounds_to_its_tick():
  buy = _fx_stop("BUY", entry="150.000", invalidation="149.950", floor=18, pip="0.01", digits=3)
  sell = _fx_stop("SELL", entry="150.000", invalidation="150.050", floor=18, pip="0.01", digits=3)
  assert (buy.base_stop_pips, buy.final_stop_pips, buy.final_stop_price) == (Decimal("5"), Decimal("18"), Decimal("149.820"))
  assert (sell.base_stop_pips, sell.final_stop_pips, sell.final_stop_price) == (Decimal("5"), Decimal("18"), Decimal("150.180"))


def test_an_xau_split_entry_is_widened_from_its_widest_leg():
  plan = plan_go_invalidation_stop(
    direction="BUY", entry_zone_low=Decimal("4183.10"), entry_zone_high=Decimal("4186.10"), planned_leg_prices=[Decimal("4186.10"), Decimal("4184.60")],
    resolved_leg_volumes=[0.8, 0.2], invalidation_price=Decimal("4183.00"), minimum_stop_pips=40, maximum_stop_pips=60,
    pip_size=Decimal("0.1"), digits=2, widen_to_minimum=True,
  )
  # the widest leg is 31 pips from Go's stop, so the stop moves out by 9 and the nearer leg is 34 pips from it
  assert plan.base_stop_pips == Decimal("31") and plan.final_stop_pips == Decimal("40") and plan.final_stop_price == Decimal("4182.10")
  assert plan.entry_price == Decimal("4185.80")  # volume-weighted, not the mean of the legs


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("room,classification,fits", [(8, "first_target_beyond_room", 0), (18, "ladder_exceeds_room", 1), (40, "ladder_within_room", 2)])
def test_the_barrier_is_read_against_the_ladder_of_the_widened_stop(direction, room, classification, fits):
  plan = _fx_stop(direction, entry="1.10000", invalidation="1.09950" if direction == "BUY" else "1.10050", floor=12)
  out = assess_executable_room(_room(direction, room), executable_stop_pips=plan.final_stop_pips, technical_stop_pips=plan.base_stop_pips,
                               target_pips=_ladder(plan.final_stop_pips))
  assert out["classification"] == classification and out["targets_within_room"] == fits
  assert out["room_r"] == pytest.approx(room / 12, abs=1e-3) and out["stop_widening_ratio"] == pytest.approx(2.4)


def test_a_room_that_looks_ample_against_the_technical_stop_is_short_against_the_executable_one():
  plan = _fx_stop("BUY", entry="1.10000", invalidation="1.09950", floor=12)
  out = assess_executable_room(_room("BUY", 8), executable_stop_pips=plan.final_stop_pips, technical_stop_pips=plan.base_stop_pips,
                               target_pips=_ladder(plan.final_stop_pips))
  assert 8 / float(plan.base_stop_pips) == pytest.approx(1.6)  # 1.6R on the technical stop
  assert out["room_r"] == pytest.approx(0.667, abs=1e-3)  # 0.67R on the stop the executor uses
  assert out["classification"] == "first_target_beyond_room"
