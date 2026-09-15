"""Scalping concurrent ledger: one group, not one clip; ghost book unsticks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.scalping.risk import (
  ScalpRiskState,
  evaluate_risk,
  live_exposure_ids,
  reconcile_open_positions,
  record_scalp_outcome,
)


pytestmark = pytest.mark.no_database


def _cfg():
  return SimpleNamespace(
    strategies=SimpleNamespace(
      scalping=SimpleNamespace(
        risk=SimpleNamespace(
          risk_fraction_per_trade=0.10,
          maximum_concurrent_positions=1,
          maximum_daily_trades=30,
          maximum_session_trades=12,
          maximum_consecutive_losses=3,
          cooldown_after_loss_minutes=5,
          daily_loss_limit_r=3.0,
          session_loss_limit_r=2.0,
        ),
      ),
    ),
    market_data=SimpleNamespace(
      sessions=SimpleNamespace(daily_rollover_utc_hour=21),
    ),
  )


def test_five_clip_fills_count_as_one_concurrent_group():
  state = ScalpRiskState()
  gid = "v8:scalp-grid"
  for _ in range(5):
    state = record_scalp_outcome(
      state, result_pips=0.0, stop_pips=20.0, now=1, opened=True, group_id=gid,
    )
  assert state.open_positions == 1
  assert state.daily_trades == 1
  assert state.open_group_ids == [gid]
  blocked = evaluate_risk(state, _cfg(), session="london", now=1)
  assert blocked.reason_code == "scalp_max_concurrent_positions"

  state = record_scalp_outcome(
    state, result_pips=20.0, stop_pips=20.0, now=2, closed=True, group_id=gid,
  )
  assert state.open_positions == 0
  assert state.open_group_ids == []
  allowed = evaluate_risk(state, _cfg(), session="london", now=2)
  assert allowed.allowed is True


def test_empty_broker_book_clears_ghost_concurrent():
  """Live 2026-08-14: open_positions=2 with SCARD auto_trade:positions=0."""
  state = ScalpRiskState(open_positions=2, daily_trades=8, daily_r=-2.4)
  stuck = evaluate_risk(state, _cfg(), session="london", now=1)
  assert stuck.reason_code == "scalp_max_concurrent_positions"

  cleared = reconcile_open_positions(state, set())
  assert cleared.open_positions == 0
  allowed = evaluate_risk(cleared, _cfg(), session="london", now=1)
  assert allowed.allowed is True


def test_tracked_group_survives_when_still_in_live_book():
  gid = "v8:still-open"
  state = ScalpRiskState(open_positions=1, open_group_ids=[gid])
  kept = reconcile_open_positions(state, {gid})
  assert kept.open_positions == 1
  assert kept.open_group_ids == [gid]


def test_tracked_group_drops_when_plan_id_uses_v8_prefix():
  state = ScalpRiskState(
    open_positions=1, open_group_ids=["abc123"],
  )
  dropped = reconcile_open_positions(state, live_exposure_ids([
    SimpleNamespace(group_id=None, plan_id="v8:other"),
  ]))
  assert dropped.open_positions == 0


def test_live_exposure_ids_accept_v8_prefix_either_way():
  ids = live_exposure_ids([
    SimpleNamespace(group_id="setup-1", plan_id="v8:setup-1"),
  ])
  assert "setup-1" in ids
  assert "v8:setup-1" in ids
