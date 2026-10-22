"""app.autotrade.result_accounting and its persistence: a group's net R, and the provenance of every figure."""

import pytest

from app.autotrade import result_accounting as ra
from app.persistence import store

pytestmark = pytest.mark.no_database


def test_net_r_is_weighted_pips_over_weighted_initial_risk_and_nothing_else():
  assert ra.net_r(220.0, 100.0) == pytest.approx(2.2)     # XAU 40/20/20/20 to 4R
  assert ra.net_r(-94.7, 94.7) == pytest.approx(-1.0)     # a full stop with the risk leg
  assert ra.net_r(0.0, 50.0) == 0.0
  for pips, risk in ((None, 10), (10, None), (10, 0), (10, -3), (float("nan"), 10), (10, float("inf")), (True, 10)):
    assert ra.net_r(pips, risk) is None, (pips, risk)


def test_a_close_event_yields_net_r_only_when_both_halves_are_there():
  assert ra.from_close_event({"volume_weighted_pips": 30.0, "volume_weighted_risk_pips": 60.0}) == (60.0, 0.5, ra.FILLS_V1)
  assert ra.from_close_event({"volume_weighted_pips": 30.0}) == (None, None, None)
  assert ra.from_close_event({"volume_weighted_pips": 30.0, "volume_weighted_risk_pips": 0}) == (None, None, None)
  assert ra.from_close_event({"volume_weighted_risk_pips": 60.0}) == (60.0, None, None)


def test_the_ladder_reconstruction_matches_the_real_ladders():
  assert ra.ladder_r("XAU", 0) == 0
  assert ra.ladder_r("XAU", 1) == pytest.approx(0.4)
  assert ra.ladder_r("XAU", 2) == pytest.approx(0.8)
  assert ra.ladder_r("XAU", 4) == pytest.approx(2.2)
  assert ra.ladder_r("XAU", 9) == pytest.approx(2.2), "more targets than the ladder has are capped"
  assert ra.ladder_r("EURUSD", 1) == pytest.approx(0.5)
  assert ra.ladder_r("GBPJPY", 2) == pytest.approx(1.5)


def test_every_estimate_names_its_method_and_a_fill_derived_figure_wins():
  filled = {"net_r": 1.4, "result_method": ra.FILLS_V1, "realized_pips": 5.0, "risk_pips": 50.0, "exit_path": "tp2_full", "symbol": "XAU"}
  assert ra.estimate(filled) == (1.4, ra.FILLS_V1)
  stored = {"exit_path": "tp2_full", "realized_pips": 37.34, "stop_pips": 52.6, "risk_pips": 40.0, "symbol": "XAU", "booked_tp_count": 2}
  value, method = ra.estimate(stored)
  assert method == ra.FILL_RECORD_RISK and value == pytest.approx(37.34 / 52.6), "the fill record's risk, not the reference fill's"
  assert ra.estimate({"exit_path": "full_stop", "realized_pips": -31.2, "stop_pips": 31.5, "risk_pips": 40.0}) == (pytest.approx(-31.2 / 31.5), ra.FILL_RECORD_RISK)
  assert ra.estimate({"exit_path": "full_stop", "realized_pips": 0.0, "stop_pips": 40.0}) == (-1.0, ra.LEGACY_LOSS_UNBOOKED)
  assert ra.estimate({"exit_path": "full_stop", "realized_pips": None}) == (-1.0, ra.RECONSTRUCTED_LADDER_V1)
  value, method = ra.estimate({"exit_path": "tp1_be", "symbol": "EURUSD", "booked_tp_count": 1})
  assert (round(value, 6), method) == (0.5, ra.RECONSTRUCTED_LADDER_V1)
  assert ra.estimate({"result_pips": 97.0, "stop_pips": 52.6}) == (pytest.approx(97.0 / 52.6), ra.LEGACY_HIGHEST_TP)
  assert ra.estimate({}) == (None, ra.LEGACY_HIGHEST_TP)


def test_a_net_r_without_the_fills_method_is_not_trusted():
  assert ra.estimate({"net_r": 9.0, "result_method": None, "exit_path": "full_stop", "realized_pips": -10, "stop_pips": 10})[1] == ra.FILL_RECORD_RISK
