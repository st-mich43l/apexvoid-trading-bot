"""Direction conflicts only count between intents that can execute right now.

Production 2026-09-30: 607 of 1183 route outcomes were suppressed as
``opposite_direction_conflict``, yet only ~12% of the suppressed intents had the
executable quote inside their entry zone. The rest merely waited for a retest.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade import worker
from app.autotrade.arbitration import ExecutionIntent, arbitrate_execution_intents


pytestmark = pytest.mark.no_database


def _intent(
  intent_id: str,
  *,
  direction: str,
  confluence: int = 3,
  tier: str = "A",
  executable_now: bool = True,
  bias_relationship: str | None = None,
) -> ExecutionIntent:
  return ExecutionIntent(
    intent_id=intent_id,
    source="go_analysis_engine",
    strategy="Supply Demand",
    direction=direction,
    confluence=confluence,
    tier=tier,
    freshness=100.0,
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
    use_quality_ranking=True,
  )

  assert [item.intent_id for item in result.ordered] == ["buy"]
  assert [item.intent_id for item in result.suppressed] == ["sell"]


def test_ambiguous_bias_does_not_break_a_close_quality_tie():
  result = arbitrate_execution_intents(
    [
      _intent("buy", direction="BUY", bias_relationship="with_bias"),
      _intent("sell", direction="SELL", bias_relationship="with_bias"),
    ],
    use_quality_ranking=True,
  )

  assert result.ordered == ()
  assert result.reason_code == "opposite_direction_conflict"


def test_the_executable_direction_wins_even_when_a_waiting_intent_ranks_higher():
  result = arbitrate_execution_intents([
    _intent("sell-waiting", direction="SELL", confluence=5, tier="A", executable_now=False),
    _intent("buy-now", direction="BUY", confluence=3, tier="B", executable_now=True),
  ])

  assert [item.intent_id for item in result.ordered] == ["buy-now"]
  assert [item.intent_id for item in result.suppressed] == ["sell-waiting"]


def test_waiting_intents_of_the_winning_direction_stay_in_the_fallback_order():
  # They must keep being evaluated so their retest state advances and they can
  # publish the moment price enters their zone.
  result = arbitrate_execution_intents([
    _intent("buy-now", direction="BUY", confluence=4, executable_now=True),
    _intent("buy-waiting", direction="BUY", confluence=3, executable_now=False),
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
    confluence=1, tier="A", freshness=0.0, distance_pips=0.0,
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
