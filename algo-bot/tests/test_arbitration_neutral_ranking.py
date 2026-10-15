"""Arbitration with no quality score (``quality_ranking=False``, the committed configuration).

The quality certification found that no strategy's ``quality_overall`` is monotone in outcome and
that the scores sit on different scales per strategy, so neither the order of intents nor a
direction conflict may depend on one of them.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from app.autotrade.arbitration import arbitrate_execution_intents
from app.core.config import runtime_config
from tests.test_arbitration_determinism import intent, outcome

pytestmark = pytest.mark.no_database


def neutral(intents, **kwargs):
  return arbitrate_execution_intents(intents, quality_ranking=False, **kwargs)


def test_the_committed_configuration_turns_quality_ranking_off():
  assert runtime_config.auto_algo.actionability.scanner_gates.quality_ranking is False


def test_quality_confluence_and_structural_score_do_not_order_intents():
  strong = intent(1, quality_overall=1.0, confluence=3, structural_quality=20.0, freshness=1000.0)
  weak = intent(2, quality_overall=0.1, confluence=2, structural_quality=1.0, freshness=2000.0)

  assert [i.intent_id for i in neutral([strong, weak]).ordered] == ["i2", "i1"], "the fresher intent leads"
  assert [i.intent_id for i in arbitrate_execution_intents([strong, weak]).ordered] == ["i1", "i2"], "default unchanged"


def test_a_decisive_quality_gap_no_longer_settles_a_direction_conflict():
  buy = intent(1, direction="BUY", quality_overall=1.0, bias_relationship="neutral")
  sell = intent(2, direction="SELL", quality_overall=0.3, bias_relationship="neutral")

  assert arbitrate_execution_intents([buy, sell], conflict_margin_quality=0.15).reason_code == "ranked_single_direction"
  held = neutral([buy, sell], conflict_margin_quality=0.15)
  assert held.reason_code == "opposite_direction_conflict" and held.ordered == ()


def test_the_higher_timeframe_bias_still_settles_a_conflict_whatever_the_quality():
  buy = intent(1, direction="BUY", quality_overall=0.2, bias_relationship="with_bias")
  sell = intent(2, direction="SELL", quality_overall=1.0, bias_relationship="counter_bias")

  result = neutral([buy, sell])
  assert result.reason_code == "ranked_single_direction"
  assert [i.intent_id for i in result.ordered] == ["i1"]
  assert [i.intent_id for i in result.suppressed] == ["i2"]


def test_both_sides_aligned_with_bias_is_still_a_conflict():
  buy = intent(1, direction="BUY", bias_relationship="with_bias")
  sell = intent(2, direction="SELL", bias_relationship="with_bias")

  assert neutral([buy, sell]).reason_code == "opposite_direction_conflict"


def test_a_waiting_intent_still_never_outranks_an_executable_one():
  waiting = intent(1, executable_now=False, quality_overall=1.0, freshness=9000.0)
  ready = intent(2, executable_now=True, quality_overall=0.1, freshness=1.0)

  assert [i.intent_id for i in neutral([waiting, ready]).ordered][0] == "i2"


def test_same_thesis_group_keeps_the_freshest_intent_not_the_highest_quality():
  older = intent(1, structural_id="zone", entry_low=100.0, entry_high=101.0, quality_overall=0.95, freshness=1000.0)
  newer = intent(2, structural_id="zone", entry_low=100.0, entry_high=101.0, quality_overall=0.40, freshness=2000.0)

  result = neutral([older, newer])
  assert [i.intent_id for i in result.ordered] == ["i2"]
  assert result.thesis_losers == {"i1": "i2"}


def test_the_decision_is_order_independent_and_ignores_quality_values():
  base = [
    intent(1, quality_overall=0.9, freshness=1000.0),
    intent(2, quality_overall=0.1, freshness=1000.0),
    intent(3, direction="SELL", quality_overall=0.5, freshness=1000.0),
  ]
  results = {outcome(neutral(list(order))) for order in itertools.permutations(base)}
  assert len(results) == 1

  rescored = [replace(i, quality_overall=1.0 - (i.quality_overall or 0.0), confluence=5 - i.confluence) for i in base]
  assert outcome(neutral(rescored)) == results.copy().pop(), "changing every score changes nothing"
