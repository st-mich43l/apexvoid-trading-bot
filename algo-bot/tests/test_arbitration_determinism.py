"""Arbitration is a pure, order-independent function of the eligible intents.

The selection must not depend on Kafka delivery order, on a missing or non-finite
score, or on which strategy produced an intent. Arbitration may suppress execution; it
never drops an intent's identity or attribution.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace

import pytest

from app.autotrade.arbitration import (
  ExecutionIntent,
  arbitrate_execution_intents,
  same_thesis,
)

pytestmark = pytest.mark.no_database


def intent(index: int, **overrides) -> ExecutionIntent:
  values = dict(
    intent_id=f"i{index}", source="go_analysis_engine", strategy=f"strategy_{index}",
    direction="BUY", confluence=2, freshness=1000.0 + index, distance_pips=0.0, symbol="XAU",
    entry_low=100.0 + index * 100, entry_high=101.0 + index * 100, structural_id=f"zone-{index}",
    quality_overall=0.7, structural_quality=10.0, atr=0.5,
  )
  values.update(overrides)
  return ExecutionIntent(**values)


def outcome(result):
  return (
    tuple(item.intent_id for item in result.ordered),
    frozenset(item.intent_id for item in result.suppressed),
    result.reason_code,
    tuple(sorted(result.thesis_losers.items())),
  )


def every_delivery_order(intents):
  return {outcome(arbitrate_execution_intents(list(order))) for order in itertools.permutations(intents)}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None])
@pytest.mark.parametrize("field", ["quality_overall", "structural_quality", "freshness"])
def test_unavailable_or_non_finite_scores_never_make_the_winner_order_dependent(field, bad):
  intents = [intent(0, quality_overall=0.5), intent(1, **{field: bad}), intent(2, quality_overall=0.7), intent(3, quality_overall=0.9)]
  if field == "freshness" and bad is None:
    intents[1] = intent(1)  # freshness is required, not optional
  assert len(every_delivery_order(intents)) == 1


def test_a_non_finite_score_ranks_as_unavailable_not_as_best_or_worst_by_accident():
  best = intent(0, quality_overall=0.9)
  broken = intent(1, quality_overall=float("inf"))
  result = arbitrate_execution_intents([broken, best])
  assert [item.intent_id for item in result.ordered] == ["i0", "i1"]
  assert all(math.isfinite(item.quality_overall or 0.0) or item is broken for item in result.ordered)


def test_equal_scores_fall_to_the_stable_intent_id_in_every_delivery_order():
  intents = [intent(i, quality_overall=0.67, confluence=2, structural_quality=None, freshness=1000.0) for i in range(5)]
  assert len(every_delivery_order(intents)) == 1
  assert arbitrate_execution_intents(intents).ordered[0].intent_id == "i0"


def test_an_executable_intent_outranks_a_higher_quality_waiting_one():
  waiting = intent(0, quality_overall=1.0, executable_now=False)
  ready = intent(1, quality_overall=0.5, executable_now=True)
  assert arbitrate_execution_intents([waiting, ready]).ordered[0].intent_id == "i1"


def test_same_thesis_group_is_order_independent_and_keeps_the_loser_attributed():
  # Two strategies on one corridor: one executable winner, the other suppressed with its winner named.
  a = intent(0, strategy="key_level", quality_overall=1.0, entry_low=4100.0, entry_high=4104.0, structural_id="k")
  b = intent(1, strategy="confluence_zone", quality_overall=0.5, entry_low=4102.0, entry_high=4106.0, structural_id="c")
  results = every_delivery_order([a, b])
  assert len(results) == 1
  (ordered, suppressed, _reason, losers), = results
  assert ordered == ("i0",) and suppressed == frozenset({"i1"}) and losers == (("i1", "i0"),)
  # The loser is untouched: nothing about its strategy, quality or zone is rewritten.
  result = arbitrate_execution_intents([a, b])
  assert [item for item in result.suppressed if item.intent_id == "i1"] == [b]


def test_confluence_zone_wins_or_loses_on_rank_alone_never_on_its_name():
  strong_other = intent(0, strategy="supply", quality_overall=1.0, entry_low=10.0, entry_high=11.0)
  zone = intent(1, strategy="confluence_zone", quality_overall=0.5, entry_low=10.5, entry_high=11.5)
  assert arbitrate_execution_intents([zone, strong_other]).ordered[0].strategy == "supply"
  strong_zone = replace(zone, quality_overall=1.0, confluence=3)
  assert arbitrate_execution_intents([strong_other, strong_zone]).ordered[0].strategy == "confluence_zone"


def test_different_theses_stay_separate_and_both_remain_publishable_as_fallback():
  a, b = intent(0), intent(1)  # 100 price units apart
  assert not same_thesis(a, b)
  result = arbitrate_execution_intents([b, a])
  # Equal quality, confluence and structure: the fresher confirmation ranks first.
  assert [item.intent_id for item in result.ordered] == ["i1", "i0"] and not result.suppressed


def test_overlap_is_not_transitive_and_the_result_is_still_delivery_order_independent():
  # A~B and B~C but A and C are 3 ATR apart: {A,B} is one thesis, C a separate one.
  a = intent(0, quality_overall=1.0, entry_low=100.0, entry_high=101.0, structural_id="a", atr=0.5)
  b = intent(1, quality_overall=0.9, entry_low=101.4, entry_high=102.0, structural_id="b", atr=0.5)
  c = intent(2, quality_overall=0.8, entry_low=102.4, entry_high=103.0, structural_id="c", atr=0.5)
  assert same_thesis(a, b) and same_thesis(b, c) and not same_thesis(a, c)
  results = every_delivery_order([a, b, c])
  assert len(results) == 1
  (ordered, suppressed, _reason, losers), = results
  assert ordered == ("i0", "i2") and suppressed == frozenset({"i1"}) and losers == (("i1", "i0"),)


def test_opposite_directions_are_never_one_thesis_and_symbols_never_mix():
  buy = intent(0, direction="BUY", entry_low=100.0, entry_high=101.0)
  sell = intent(1, direction="SELL", entry_low=100.0, entry_high=101.0)
  other_symbol = intent(2, symbol="EURUSD", entry_low=100.0, entry_high=101.0)
  assert not same_thesis(buy, sell) and not same_thesis(buy, other_symbol)


def test_close_opposite_directions_hold_unless_exactly_one_agrees_with_the_higher_timeframe():
  buy = intent(0, direction="BUY", quality_overall=0.80)
  sell = intent(1, direction="SELL", quality_overall=0.75)
  held = arbitrate_execution_intents([buy, sell])
  assert held.reason_code == "opposite_direction_conflict" and not held.ordered
  assert {item.intent_id for item in held.suppressed} == {"i0", "i1"}  # recorded, never dropped
  aligned = arbitrate_execution_intents([replace(buy, bias_relationship="counter_bias"), replace(sell, bias_relationship="with_bias")])
  assert [item.intent_id for item in aligned.ordered] == ["i1"]
  decisive = arbitrate_execution_intents([replace(buy, quality_overall=1.0), sell])
  assert [item.intent_id for item in decisive.ordered] == ["i0"]


def test_a_waiting_opposite_intent_does_not_create_a_direction_conflict():
  ready = intent(0, direction="BUY", quality_overall=0.8)
  waiting = intent(1, direction="SELL", quality_overall=0.79, executable_now=False)
  assert [item.intent_id for item in arbitrate_execution_intents([waiting, ready]).ordered] == ["i0"]
  assert {item.intent_id for item in arbitrate_execution_intents([waiting, ready]).suppressed} == {"i1"}


def test_missing_quality_is_treated_as_unavailable_in_the_conflict_margin_too():
  buy = intent(0, direction="BUY", quality_overall=None)
  sell = intent(1, direction="SELL", quality_overall=0.1)
  assert arbitrate_execution_intents([buy, sell]).reason_code in {"opposite_direction_conflict", "ranked_single_direction"}
  assert len({outcome(arbitrate_execution_intents(list(order))) for order in itertools.permutations([buy, sell])}) == 1


def test_random_non_finite_inputs_never_make_selection_or_conflict_resolution_order_dependent():
  """Property check over every numeric field arbitration reads, in both the ranking and the
  opposite-direction conflict (the quality gap) and the thesis corridor (ATR, entry band)."""
  import random

  rng = random.Random(20261009)
  optional = ("quality_overall", "structural_quality")
  floats = ("freshness", "atr", "entry_low", "entry_high", "distance_pips")
  non_finite = [float("nan"), float("inf"), float("-inf")]
  conflicts = 0
  for _ in range(600):
    count = rng.randint(2, 5)
    values = [
      dict(
        intent_id=f"i{index}", source="go", strategy="s", direction=rng.choice(["BUY", "SELL"]), confluence=2,
        freshness=100.0, distance_pips=0.0, symbol="XAU", entry_low=100.0 + rng.choice([0, 1, 5]),
        entry_high=102.0 + rng.choice([0, 1, 5]), structural_id=f"z{rng.randint(0, 3)}",
        quality_overall=rng.choice([0.5, 0.7, 0.9]), structural_quality=5.0, atr=1.0,
        executable_now=rng.random() < 0.8, bias_relationship=rng.choice([None, "with_bias"]),
      )
      for index in range(count)
    ]
    for _ in range(rng.randint(1, 2)):
      target = values[rng.randrange(count)]
      if rng.random() < 0.4:
        target[rng.choice(optional)] = rng.choice([None, *non_finite])
      else:
        target[rng.choice(floats)] = rng.choice(non_finite)
    intents = [ExecutionIntent(**value) for value in values]
    results = every_delivery_order(intents) if count <= 4 else {
      outcome(arbitrate_execution_intents(list(order)))
      for order in itertools.islice(itertools.permutations(intents), 30)
    }
    conflicts += any(r[2] == "opposite_direction_conflict" for r in results)
    assert len(results) == 1, values
  assert conflicts > 0       # the conflict path was exercised, not only the ranking
