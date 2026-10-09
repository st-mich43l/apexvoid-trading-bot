"""A Range Edge Scalp is a scalp on gold too: its stop is sized to its range.

Production 2026-10-09 (XAU SELL, two fills in 24 hours, both stopped out at -50):
Go's zone was 4176.60-4178.08 with the invalidation at 4178.84, 23 pips from the
entry. The plan carried a 50 pip stop (``go_invalidation_widened``) because the
envelope Go attached was gold's structural 50-70 band, the one the zone strategies
need on an instrument that stop-hunts. A range scalp now takes the instrument's own
scalp band (``stop_envelope.scalp_min_pips`` / ``scalp_max_pips``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade.execution_policy import evaluate_execution_policy
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database

ZONE_LOW, ZONE_HIGH, INVALIDATION = 4176.60, 4178.08, 4178.84


def _go_match(strategy: str, *, floor: float, cap: float, desired: float, **overrides):
  return _policy_match(
    strategy=strategy,
    symbol="XAU",
    direction="SELL",
    entry_low=ZONE_LOW,
    entry_high=ZONE_HIGH,
    current_price=4176.57,
    atr=4.0,
    structure_swing=None,
    tags=(GO_ORIGIN_TAG,),
    go_invalidation_price=INVALIDATION,
    go_stop_envelope_floor_pips=floor,
    go_stop_envelope_cap_pips=cap,
    go_stop_envelope_desired_minimum_pips=desired,
    go_stop_envelope_source="scalp_room",
    targets_pips=(23,),
    **overrides,
  )


def _evaluate(match):
  return evaluate_execution_policy(
    match, spot_price=4176.57, executable_quote=4176.57,
    regime="range", pip_size=0.1, cfg=_cfg(),
  )


def test_range_edge_scalp_keeps_its_range_sized_stop_on_gold():
  evaluation = _evaluate(_go_match("Range Edge Scalp", floor=15, cap=45, desired=23))
  assert evaluation.allowed, evaluation.message
  stop_pips = float(evaluation.measured["planned_stop_pips"])
  assert 15 <= stop_pips <= 45
  # Go's invalidation is 7.6 pips past the zone high; the stop is not pushed to 50.
  assert stop_pips < 35, stop_pips


def test_the_old_gold_structural_envelope_is_what_made_it_50():
  # Same trade under the envelope Go used to attach (floor = cap pinned at the
  # structural 50): the stop is widened to the floor. This is the defect's mechanism.
  evaluation = _evaluate(_go_match("Range Edge Scalp", floor=50, cap=70, desired=50))
  assert evaluation.allowed, evaluation.message
  assert float(evaluation.measured["planned_stop_pips"]) == pytest.approx(50.0, abs=0.5)


def test_a_zone_strategy_keeps_gold_s_structural_floor():
  evaluation = _evaluate(_go_match("Supply Zone Reaction", floor=50, cap=70, desired=50))
  assert evaluation.allowed, evaluation.message
  assert float(evaluation.measured["planned_stop_pips"]) >= 50.0


def test_the_xau_instrument_declares_a_scalp_band_and_python_reads_it():
  from app.core.config import runtime_config

  xau = runtime_config.for_instrument("XAU")
  assert xau.execution.range.room_stop_floor_pips == 15
  # The structural gold envelope the zone strategies use is unchanged.
  assert xau.execution.reaction.stop_min_pips == 50
  assert xau.execution.reaction.stop_max_pips == 70


def test_a_range_scalp_on_gold_books_one_and_two_r_not_the_four_r_ladder():
  evaluation = _evaluate(_go_match("Range Edge Scalp", floor=15, cap=45, desired=23))
  assert evaluation.allowed, evaluation.message
  measured = evaluation.measured
  assert measured["planned_target_r_multiples"] == ["1.0", "2.0"]
  assert measured["planned_target_close_ratios"] == ["0.5", "0.5"]
  risk = float(measured["planned_stop_pips"])
  assert [float(p) for p in measured["planned_target_pips"]] == pytest.approx(
    [risk, 2 * risk], abs=0.1,
  )
  assert not measured.get("planned_trail_after_target_id")


def test_a_structural_gold_strategy_keeps_the_four_r_ladder():
  evaluation = _evaluate(_go_match("Supply Zone Reaction", floor=50, cap=70, desired=50))
  assert evaluation.allowed, evaluation.message
  assert evaluation.measured["planned_target_r_multiples"] == ["1.0", "2.0", "3.0", "4.0"]


def test_a_range_scalp_with_room_for_only_one_r_books_one_r():
  evaluation = evaluate_execution_policy(
    _go_match("Range Edge Scalp", floor=15, cap=45, desired=23),
    spot_price=4176.57, executable_quote=4176.57, regime="range", pip_size=0.1,
    cfg=_cfg(), available_target_room_pips=30.0,
  )
  assert evaluation.allowed, evaluation.message
  assert evaluation.measured["planned_target_r_multiples"] == ["1.0"]
  assert evaluation.measured["planned_target_close_ratios"] == ["1.0"]
