"""Current execution-policy behavior owned by the Go opportunity path."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.autotrade.scalp_ladder import scalp_target_ladder
from app.autotrade.session_context import classify_session


def test_session_labels_are_execution_owned_and_configurable():
  cfg = SimpleNamespace(market_data=SimpleNamespace(sessions=SimpleNamespace(
    asia_start=22,
    london_start=7,
    ny_start=13,
    daily_rollover_utc_hour=21,
  )))
  def ts(hour: int) -> int:
    return int(datetime(2026, 1, 1, hour, tzinfo=timezone.utc).timestamp())

  assert classify_session(ts(2), cfg) == "asia"
  assert classify_session(ts(8), cfg) == "london"
  assert classify_session(ts(14), cfg) == "london_ny_overlap"
  assert classify_session(ts(17), cfg) == "new_york"
  assert classify_session(ts(21), cfg) == "rollover"


def test_scalp_ladder_stays_one_or_two_targets():
  one_r = SimpleNamespace(
    expected_target_pips=20,
    expected_stop_pips=20,
    expected_reward_risk=1.0,
  )
  two_r = SimpleNamespace(
    expected_target_pips=45,
    expected_stop_pips=20,
    expected_reward_risk=2.25,
  )
  assert scalp_target_ladder(one_r) == (20, (20,))
  assert scalp_target_ladder(two_r) == (40, (20, 40))
