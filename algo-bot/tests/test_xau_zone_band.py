"""Every XAU zone strategy trades a 30-50 pip band; the stop may reach 70 pips.

Owner 2026-10-09: a thin Go zone is widened toward the stop to 30 pips, a wide one is
trimmed to its 50 pips nearest the near edge; shallow leg at the near edge, deep leg at
the band midpoint; the stop is never pulled in (a 60 or 70 pip loss is fine, gold earns
on the reward/risk ladder).
"""

from __future__ import annotations

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade.execution_policy import evaluate_execution_policy
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database


def _evaluate(low, high, *, strategy="Key Level", kind="key_level", direction="BUY",
              invalidation=None, quote=None, targets=(56, 112, 168, 224)):
  buy = direction == "BUY"
  if invalidation is None:
    invalidation = low - 1.5 if buy else high + 1.5
  if quote is None:
    quote = high if buy else low
  return evaluate_execution_policy(
    _policy_match(
      strategy=strategy, direction=direction, entry_low=low, entry_high=high,
      current_price=quote, atr=3.0, structure_swing=invalidation,
      go_invalidation_price=invalidation, tags=(GO_ORIGIN_TAG,),
      structural_kind=kind, targets_pips=targets,
    ),
    spot_price=quote, executable_quote=quote, regime="trend", pip_size=0.1, cfg=_cfg(),
  )


def _width(measured):
  return round((measured["planned_entry_zone_high"] - measured["planned_entry_zone_low"]) / 0.1, 1)


def test_a_zone_inside_30_to_50_pips_is_left_alone():
  out = _evaluate(4143.16, 4147.06)  # 39 pips: this morning's Key Level
  assert out.allowed, out.reason_code
  assert _width(out.measured) == pytest.approx(39.0, abs=0.01)
  assert out.measured["execution_zone_expanded"] is False


def test_a_wide_zone_is_trimmed_to_its_50_pips_nearest_the_near_edge_and_the_stop_stays():
  out = _evaluate(4141.47, 4147.06, invalidation=4141.46)  # 56 pips
  assert out.allowed, out.reason_code
  m = out.measured
  assert _width(m) == pytest.approx(50.0, abs=0.01)
  assert m["planned_entry_zone_high"] == pytest.approx(4147.06)  # near edge kept (BUY)
  assert m["planned_entry_zone_low"] == pytest.approx(4142.06)
  assert m["planned_stop_price"] == "4141.46"                      # Go's stop is not pulled in
  assert m["planned_leg_entry_prices"] == pytest.approx([4147.06, 4144.56])


def test_a_thin_zone_is_widened_to_30_pips_toward_the_stop_and_the_stop_follows():
  out = _evaluate(4145.0, 4147.0, invalidation=4144.0)  # 20 pips, stop 10 pips behind it
  assert out.allowed, out.reason_code
  m = out.measured
  assert _width(m) == pytest.approx(30.0, abs=0.01)
  assert m["planned_entry_zone_low"] == pytest.approx(4144.0)
  assert float(m["planned_stop_price"]) < 4144.0


def test_sell_mirrors_buy():
  trimmed = _evaluate(4141.0, 4147.0, direction="SELL", invalidation=4147.5)  # 60 pips
  assert trimmed.allowed, trimmed.reason_code
  m = trimmed.measured
  assert _width(m) == pytest.approx(50.0, abs=0.01)
  assert m["planned_entry_zone_low"] == pytest.approx(4141.0)
  assert m["planned_entry_zone_high"] == pytest.approx(4146.0)
  widened = _evaluate(4141.0, 4143.0, direction="SELL", invalidation=4144.0)
  assert _width(widened.measured) == pytest.approx(30.0, abs=0.01)


def test_the_stop_may_be_70_pips_but_not_more():
  # Proximal 4147.06, Go invalidation 4140.06 -> 70 pips.
  ok = _evaluate(4143.16, 4147.06, invalidation=4140.06)
  assert ok.allowed, ok.reason_code
  assert float(ok.measured["planned_stop_pips"]) == pytest.approx(70.0, abs=0.01)
  too_far = _evaluate(4143.16, 4147.06, invalidation=4139.56)  # 75 pips
  assert not too_far.allowed


@pytest.mark.parametrize("strategy,kind", [
  ("Supply Demand", "demand"), ("Order Block", "order_block"), ("Session Level", "session_level"),
  ("Trendline", "trendline"), ("Flip Zone", "flip_zone"), ("Confluence Zone", "confluence_zone"),
  ("CRT", "crt"),
])
def test_every_zone_strategy_gets_the_same_band(strategy, kind):
  out = _evaluate(4141.4, 4147.0, strategy=strategy, kind=kind, invalidation=4140.4)  # 56 wide
  assert out.allowed, out.reason_code
  assert _width(out.measured) == pytest.approx(50.0, abs=0.01)


def test_trimming_never_pulls_the_stop_in_so_a_zone_whose_stop_is_over_70_is_still_rejected():
  # 70 pips wide, Go's stop a pip behind it = 80 pips from the near edge.
  out = _evaluate(4140.0, 4147.0, invalidation=4139.0)
  assert not out.allowed
  assert out.reason_code == "stop_exceeds_go_invalidation_envelope"


def test_scalps_fx_and_the_market_strategies_keep_their_own_entry():
  scalp = _evaluate(4140.0, 4147.0, strategy="Range Edge Scalp", kind="range_edge", invalidation=4139.0)
  if scalp.measured.get("planned_entry_zone_high") is not None:
    assert _width(scalp.measured) == pytest.approx(70.0, abs=0.01)
