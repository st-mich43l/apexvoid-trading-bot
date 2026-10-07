"""PR-L4: live scalp outcome instrumentation (measurement only)."""

from __future__ import annotations

import pytest

from app.scalping.outcomes import (
  EXIT_FULL_STOP,
  EXIT_TP1_BE_FLAT,
  EXIT_TP1_BE_TP2,
  EXIT_TP1_ONLY,
  EXIT_UNKNOWN,
  ExitTrace,
  ExcursionState,
  classify_exit_path,
  excursion_mfe_mae,
  finalize_live_outcome,
  legs_for_exit_path,
  volume_weighted_r,
)


pytestmark = pytest.mark.no_database


def test_volume_weighted_r_standard_paths_on_50_50_ladder():
  stop = 14.0
  assert volume_weighted_r(
    exit_path=EXIT_TP1_BE_TP2,
    stop_pips=stop,
    leg_close_ratios=(0.5, 0.5),
    leg_r_multiples=(1.0, 2.0),
  ) == pytest.approx(1.5)
  assert volume_weighted_r(
    exit_path=EXIT_TP1_BE_FLAT,
    stop_pips=stop,
    leg_close_ratios=(0.5, 0.5),
    leg_r_multiples=(1.0, 0.0),
  ) == pytest.approx(0.5)
  assert volume_weighted_r(
    exit_path=EXIT_FULL_STOP,
    stop_pips=stop,
    leg_close_ratios=(1.0,),
    leg_r_multiples=(-1.0,),
  ) == pytest.approx(-1.0)
  assert volume_weighted_r(
    exit_path=EXIT_TP1_ONLY,
    stop_pips=stop,
    leg_close_ratios=(0.5,),
    leg_r_multiples=(1.0,),
  ) == pytest.approx(0.5)


def test_volume_weighted_r_defaults_match_path_table():
  stop = 14.0
  assert volume_weighted_r(
    exit_path=EXIT_TP1_BE_TP2, stop_pips=stop,
    leg_close_ratios=(), leg_r_multiples=(),
  ) == pytest.approx(1.5)
  assert volume_weighted_r(
    exit_path=EXIT_TP1_BE_FLAT, stop_pips=stop,
    leg_close_ratios=(), leg_r_multiples=(),
  ) == pytest.approx(0.5)
  assert volume_weighted_r(
    exit_path=EXIT_FULL_STOP, stop_pips=stop,
    leg_close_ratios=(), leg_r_multiples=(),
  ) == pytest.approx(-1.0)


def test_2026_08_31_books_disagree_volume_vs_excursion():
  """Observed day: 3× tp1_be_tp2, 3× tp1_be_flat, 4× full_stop.

  Volume-weighted = +2.0R. Price-excursion equivalent (counting full R per
  path as if the whole position ran) = +5.0R. Assert both — and that they
  differ — so the two books stay documented.
  """
  paths = (
    [EXIT_TP1_BE_TP2] * 3
    + [EXIT_TP1_BE_FLAT] * 3
    + [EXIT_FULL_STOP] * 4
  )
  vw = 0.0
  excursion = 0.0
  for path in paths:
    ratios, multiples = legs_for_exit_path(path)
    vw += volume_weighted_r(
      exit_path=path,
      stop_pips=14.0,
      leg_close_ratios=ratios,
      leg_r_multiples=multiples,
    )
    # Price-excursion book: treat the furthest filled R as the whole-position R.
    if path == EXIT_TP1_BE_TP2:
      excursion += 2.0
    elif path == EXIT_TP1_BE_FLAT:
      excursion += 1.0
    else:
      excursion += -1.0
  assert vw == pytest.approx(2.0)
  assert excursion == pytest.approx(5.0)
  assert vw != pytest.approx(excursion)


def test_volume_weighted_r_respects_non_equal_ladder_ratios():
  # 30/70 book: TP1@1R then BE flat → 0.3*1 + 0.7*0 = 0.3R (not 0.5).
  assert volume_weighted_r(
    exit_path=EXIT_TP1_BE_FLAT,
    stop_pips=10.0,
    leg_close_ratios=(0.3, 0.7),
    leg_r_multiples=(1.0, 0.0),
  ) == pytest.approx(0.3)
  assert volume_weighted_r(
    exit_path=EXIT_TP1_BE_TP2,
    stop_pips=10.0,
    leg_close_ratios=(0.3, 0.7),
    leg_r_multiples=(1.0, 2.0),
  ) == pytest.approx(0.3 * 1.0 + 0.7 * 2.0)


def test_mfe_mae_signs_buy_and_sell():
  # BUY: high above entry is MFE; low below entry is MAE.
  mfe, mae = excursion_mfe_mae(
    direction="BUY",
    entry_price=100.0,
    max_high=102.0,
    min_low=99.0,
    pip_size=0.1,
  )
  assert mfe == pytest.approx(20.0)
  assert mae == pytest.approx(10.0)

  # SELL: low below entry is MFE; high above entry is MAE.
  mfe, mae = excursion_mfe_mae(
    direction="SELL",
    entry_price=100.0,
    max_high=101.0,
    min_low=97.0,
    pip_size=0.1,
  )
  assert mfe == pytest.approx(30.0)
  assert mae == pytest.approx(10.0)


def test_exit_path_classification_from_event_flags():
  assert classify_exit_path(ExitTrace(
    group_id="g", stopped=True, filled=True,
  )) == EXIT_FULL_STOP
  assert classify_exit_path(ExitTrace(
    group_id="g", tp1=True, be_moved=True, tp2=True, filled=True,
  )) == EXIT_TP1_BE_TP2
  assert classify_exit_path(ExitTrace(
    group_id="g", tp1=True, be_moved=True, filled=True,
  )) == EXIT_TP1_BE_FLAT
  assert classify_exit_path(ExitTrace(
    group_id="g", tp1=True, filled=True, single_target=True,
  )) == EXIT_TP1_ONLY
  assert classify_exit_path(ExitTrace(
    group_id="g", tp1=True, filled=True,
  )) == EXIT_TP1_ONLY
  assert classify_exit_path(ExitTrace(
    group_id="g", filled=True,
  )) == EXIT_UNKNOWN
  assert classify_exit_path({"filled": True, "tp3": True}) == EXIT_UNKNOWN


def test_live_outcome_preserves_l3_stop_invariant():
  pip = 0.1
  entry = 4438.91
  stop = 16.3
  invalidation = entry - stop * pip
  state = ExcursionState(
    opportunity_id="opp-inv",
    episode_id="ep",
    symbol="XAU",
    archetype="impulse_pullback",
    direction="BUY",
    session="london",
    htf_bias="down",
    regime="chop",
    entry_price=entry,
    invalidation_price=invalidation,
    stop_pips=stop,
    planned_target_pips=20.0,
    planned_rr=20.0 / stop,
    group_id="g",
    match_id="m",
    opened_at=1,
    pip_size=pip,
    max_high=entry + 1.3,
    min_low=entry - 0.5,
  )
  live = finalize_live_outcome(
    state, exit_path=EXIT_TP1_BE_FLAT, realized_pips=6.5, closed_at=2,
  )
  derived = abs(live.entry_price - live.invalidation_price) / pip
  assert derived == pytest.approx(live.stop_pips, abs=1e-6)
  assert live.stop_pips == pytest.approx(stop)
