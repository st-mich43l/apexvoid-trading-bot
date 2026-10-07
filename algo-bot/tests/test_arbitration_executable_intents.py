"""Direction conflicts only count between intents that can execute right now.

Production 2026-09-30: 607 of 1183 route outcomes were suppressed as
``opposite_direction_conflict``, yet only ~12% of the suppressed intents had the
executable quote inside their entry zone. The rest merely waited for a retest.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade import worker
from app.autotrade.arbitration import (
  ExecutionIntent,
  arbitrate_execution_intents,
  same_thesis,
)


pytestmark = pytest.mark.no_database


def _intent(
  intent_id: str,
  *,
  direction: str,
  confluence: int = 3,
  executable_now: bool = True,
  bias_relationship: str | None = None,
  quality_overall: float | None = 0.8,
  freshness: float = 100.0,
  structural_quality: float | None = None,
  entry_low: float = 0.0,
  entry_high: float = 0.0,
  structural_id: str = "",
  go_thesis_id: str | None = None,
  atr: float = 0.0,
  strategy: str = "Supply Demand",
) -> ExecutionIntent:
  return ExecutionIntent(
    intent_id=intent_id,
    source="go_analysis_engine",
    strategy=strategy,
    direction=direction,
    confluence=confluence,
    quality_overall=quality_overall,
    freshness=freshness,
    structural_quality=structural_quality,
    entry_low=entry_low,
    entry_high=entry_high,
    structural_id=structural_id,
    go_thesis_id=go_thesis_id,
    atr=atr,
    distance_pips=0.0,
    executable_now=executable_now,
    bias_relationship=bias_relationship,
  )


def test_a_waiting_opposite_intent_does_not_suppress_an_executable_one():
  # A demand zone the price is standing in must not be held back by a supply
  # zone that is merely waiting above it.
  result = arbitrate_execution_intents([
    _intent("buy", direction="BUY", executable_now=True),
    _intent("sell", direction="SELL", executable_now=False),
  ])

  assert [item.intent_id for item in result.ordered] == ["buy"]
  assert [item.intent_id for item in result.suppressed] == ["sell"]
  assert result.reason_code == "ranked_single_direction"


def test_two_executable_opposite_intents_still_conflict():
  result = arbitrate_execution_intents([
    _intent("buy", direction="BUY", executable_now=True),
    _intent("sell", direction="SELL", executable_now=True),
  ])

  assert result.ordered == ()
  assert {item.intent_id for item in result.suppressed} == {"buy", "sell"}
  assert result.reason_code == "opposite_direction_conflict"


def test_unique_with_bias_direction_breaks_a_close_quality_tie():
  result = arbitrate_execution_intents(
    [
      _intent(
        "buy", direction="BUY", bias_relationship="with_bias",
      ),
      _intent(
        "sell", direction="SELL", bias_relationship="counter_bias",
      ),
    ],
  )

  assert [item.intent_id for item in result.ordered] == ["buy"]
  assert [item.intent_id for item in result.suppressed] == ["sell"]


def test_ambiguous_bias_does_not_break_a_close_quality_tie():
  result = arbitrate_execution_intents(
    [
      _intent("buy", direction="BUY", bias_relationship="with_bias"),
      _intent("sell", direction="SELL", bias_relationship="with_bias"),
    ],
  )

  assert result.ordered == ()
  assert result.reason_code == "opposite_direction_conflict"


def test_the_executable_direction_wins_even_when_a_waiting_intent_ranks_higher():
  result = arbitrate_execution_intents([
    _intent("sell-waiting", direction="SELL", confluence=5, quality_overall=0.95, executable_now=False),
    _intent("buy-now", direction="BUY", confluence=3, quality_overall=0.6, executable_now=True),
  ])

  assert [item.intent_id for item in result.ordered] == ["buy-now"]
  assert [item.intent_id for item in result.suppressed] == ["sell-waiting"]


def test_waiting_intents_of_the_winning_direction_stay_in_the_fallback_order():
  # They must keep being evaluated so their retest state advances and they can
  # publish the moment price enters their zone.
  result = arbitrate_execution_intents([
    _intent("buy-now", direction="BUY", confluence=4, executable_now=True),
    _intent("buy-waiting", direction="BUY", confluence=3, executable_now=False, entry_low=200.0, entry_high=201.0),
    _intent("sell-waiting", direction="SELL", confluence=3, executable_now=False),
  ])

  assert [item.intent_id for item in result.ordered] == ["buy-now", "buy-waiting"]
  assert [item.intent_id for item in result.suppressed] == ["sell-waiting"]


def test_when_nothing_can_execute_the_legacy_conflict_rule_still_applies():
  result = arbitrate_execution_intents([
    _intent("buy", direction="BUY", executable_now=False),
    _intent("sell", direction="SELL", executable_now=False),
  ])

  assert result.ordered == ()
  assert result.reason_code == "opposite_direction_conflict"


def test_an_intent_defaults_to_executable_so_unaware_callers_keep_legacy_behavior():
  assert ExecutionIntent(
    intent_id="x", source="s", strategy="t", direction="BUY",
    confluence=1, freshness=0.0, distance_pips=0.0,
  ).executable_now is True


def test_execution_quote_access_is_true_only_inside_the_entry_contract():
  from app.core import instrument_geometry

  inst = instrument_geometry.instrument_runtime("XAU")
  match = SimpleNamespace(
    direction="SELL", entry_low=4179.03, entry_high=4179.72,
    strategy="FVG", family="fvg", strategy_mode="", structure_swing=None,
  )

  _, inside = worker._execution_quote_access(
    match, SimpleNamespace(bid=4179.30, ask=4179.50), "XAU", inst,
  )
  _, below = worker._execution_quote_access(
    match, SimpleNamespace(bid=4175.00, ask=4175.20), "XAU", inst,
  )
  _, above = worker._execution_quote_access(
    match, SimpleNamespace(bid=4183.00, ask=4183.20), "XAU", inst,
  )

  assert inside is True
  assert below is False
  assert above is False


# --- best-first, same-thesis arbitration ------------------------------------


def _ids(items):
  return [item.intent_id for item in items]


def test_a_larger_quality_gap_decides_opposite_directions_without_any_tier():
  result = arbitrate_execution_intents([
    _intent("buy", direction="BUY", quality_overall=0.95),
    _intent("sell", direction="SELL", quality_overall=0.60),
  ])

  assert _ids(result.ordered) == ["buy"]
  assert _ids(result.suppressed) == ["sell"]


def test_executable_intent_outranks_a_better_quality_waiting_intent():
  result = arbitrate_execution_intents([
    _intent("waiting", direction="BUY", quality_overall=0.99, executable_now=False,
            entry_low=100.0, entry_high=101.0),
    _intent("now", direction="BUY", quality_overall=0.50, executable_now=True,
            entry_low=100.0, entry_high=101.0),
  ])

  assert _ids(result.ordered) == ["now"]
  assert result.thesis_losers == {"waiting": "now"}


def test_hierarchy_is_quality_then_confluence_then_structure_then_freshness_then_id():
  base = {"direction": "BUY", "entry_low": 100.0, "entry_high": 101.0}
  best_quality = _intent("a", quality_overall=0.9, confluence=1, **base)
  better_confluence = _intent("b", quality_overall=0.8, confluence=4, **base)
  better_structure = _intent("c", quality_overall=0.8, confluence=3,
                             structural_quality=0.9, **base)
  fresher = _intent("d", quality_overall=0.8, confluence=3,
                    structural_quality=0.5, freshness=200.0, **base)
  by_id = _intent("e", quality_overall=0.8, confluence=3,
                  structural_quality=0.5, freshness=100.0, **base)
  tie = _intent("f", quality_overall=0.8, confluence=3,
                structural_quality=0.5, freshness=100.0, **base)

  def winner(*items):
    return arbitrate_execution_intents(list(items)).ordered[0].intent_id

  assert winner(better_confluence, best_quality) == "a"
  assert winner(better_structure, better_confluence) == "b"
  assert winner(fresher, better_structure) == "c"
  assert winner(by_id, fresher) == "d"
  assert winner(tie, by_id) == "e"


def test_same_thesis_publishes_one_winner_whatever_the_arrival_order():
  # Four strategies on one band: the best one is selected in any input order.
  band = {"direction": "BUY", "entry_low": 4183.0, "entry_high": 4186.0, "atr": 3.0}
  candidates = [
    _intent("order_block", strategy="order_block", quality_overall=0.71, **band),
    _intent("key_level", strategy="key_level", quality_overall=0.84, **band),
    _intent("range_sweep_1", strategy="range_sweep", quality_overall=0.62, **band),
    _intent("range_sweep_2", strategy="range_sweep", quality_overall=0.66, **band),
  ]
  for arrival in (candidates, list(reversed(candidates)), candidates[2:] + candidates[:2]):
    result = arbitrate_execution_intents(arrival)
    assert _ids(result.ordered) == ["key_level"]
    assert set(result.thesis_losers) == {"order_block", "range_sweep_1", "range_sweep_2"}
    assert set(_ids(result.suppressed)) == set(result.thesis_losers)


def test_distinct_corridors_remain_independent_fallbacks():
  result = arbitrate_execution_intents([
    _intent("near", direction="BUY", entry_low=100.0, entry_high=101.0, atr=1.0),
    _intent("far", direction="BUY", entry_low=140.0, entry_high=141.0, atr=1.0,
            quality_overall=0.7),
  ])

  assert _ids(result.ordered) == ["near", "far"]
  assert result.thesis_losers == {}


def test_shared_structural_or_thesis_identity_is_one_thesis_without_zone_overlap():
  by_structure = arbitrate_execution_intents([
    _intent("a", direction="BUY", structural_id="zone:1", entry_low=100.0, entry_high=101.0),
    _intent("b", direction="BUY", structural_id="zone:1", entry_low=300.0, entry_high=301.0,
            quality_overall=0.7),
  ])
  by_thesis = arbitrate_execution_intents([
    _intent("a", direction="BUY", go_thesis_id="t1", entry_low=100.0, entry_high=101.0),
    _intent("b", direction="BUY", go_thesis_id="t1", entry_low=300.0, entry_high=301.0,
            quality_overall=0.7),
  ])

  assert _ids(by_structure.ordered) == ["a"] and by_structure.thesis_losers == {"b": "a"}
  assert _ids(by_thesis.ordered) == ["a"] and by_thesis.thesis_losers == {"b": "a"}


def test_opposite_directions_never_share_a_thesis():
  buy = _intent("buy", direction="BUY", entry_low=100.0, entry_high=101.0, quality_overall=0.9)
  sell = _intent("sell", direction="SELL", entry_low=100.0, entry_high=101.0, quality_overall=0.5)

  assert not same_thesis(buy, sell)
  assert _ids(arbitrate_execution_intents([buy, sell]).ordered) == ["buy"]


def test_a_missing_entry_band_is_not_treated_as_overlap():
  result = arbitrate_execution_intents([
    _intent("a", direction="BUY"),
    _intent("b", direction="BUY", quality_overall=0.7),
  ])

  assert _ids(result.ordered) == ["a", "b"]


def test_box_breakout_and_breakout_retest_scalp_on_one_breakout_execute_once():
  # Both archetypes retest the same broken box edge, one from M5 and one from
  # M1. They are independent strategies, but on one thesis only one trades.
  edge = {"direction": "BUY", "entry_low": 4311.4, "entry_high": 4313.2, "atr": 2.4}
  box = _intent("box", strategy="Box Breakout", quality_overall=0.74, **edge)
  scalp = _intent("scalp", strategy="Breakout Retest Scalp", quality_overall=0.69,
                  freshness=200.0, **edge)

  for arrival in ([box, scalp], [scalp, box]):
    result = arbitrate_execution_intents(arrival)
    assert _ids(result.ordered) == ["box"]
    assert result.thesis_losers == {"scalp": "box"}
