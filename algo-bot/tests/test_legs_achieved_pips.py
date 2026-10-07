"""History /trade_stats uses highest TP/pips hit, not lot-weighted net."""

from app.signals.pips_format import legs_achieved_pips


def test_pure_stop_loss_uses_final_exit():
  assert legs_achieved_pips([{"frac": 1.0, "pips": -47}]) == -47


def test_partial_then_worse_exit_keeps_booked_tp():
  assert legs_achieved_pips([
    {"frac": 0.5, "pips": 50},
    {"frac": 0.5, "pips": -30},
  ]) == 50


def test_empty_legs_are_zero():
  assert legs_achieved_pips([]) == 0
