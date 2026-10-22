"""tools/q4_quality_eval.py: the rule that decides whether a quality score may rank anything."""

import random

import pytest

from tools import q1_opportunity_quality as q1
from tools import q4_quality_eval as ev

pytestmark = pytest.mark.no_database

DAY = 86400


def test_spearman_handles_ties_and_constants():
  assert ev.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
  assert ev.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
  assert ev.spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None, "a constant score has no rank correlation"
  assert ev.spearman([1, 2], [1, 2]) is None
  assert ev.spearman([1, 1, 2, 2], [1, 2, 3, 4]) == pytest.approx(0.894, abs=1e-3)


def _row(i, t, score, diff, strategy="key_level", group="XAU"):
  return {"id": f"o{i}", "t": t, "quality": score, "old_quality": 2 / 3, "diff": diff, "r": diff, "strategy": strategy, "group": group, "cluster": ("S", t // DAY), "components": {}}


def test_the_split_uses_the_development_median_and_falls_back_to_the_top_value_of_a_two_valued_score():
  rows = [_row(i, q1.CUT - 10 * DAY + i, s, 0.0) for i, s in enumerate([0.1, 0.2, 0.3, 0.4, 0.5])]
  cut = ev.development_cut(rows, lambda r: r["quality"])
  assert cut == 0.3
  high, low = ev.split(rows, lambda r: r["quality"], cut)
  assert [r["quality"] for r in high] == [0.4, 0.5] and len(low) == 3
  stars = [_row(i, q1.CUT - DAY, 2 / 3 if i % 3 else 1.0, 0.0) for i in range(12)]
  cut = ev.development_cut(stars, lambda r: r["quality"])
  high, low = ev.split(stars, lambda r: r["quality"], cut)
  assert {r["quality"] for r in high} == {1.0} and {r["quality"] for r in low} == {2 / 3}


def _population(effect, seed=1, n=160):
  rnd = random.Random(seed)
  rows = []
  for window, base in (("dev", q1.CUT - 40 * DAY), ("oos", q1.CUT + DAY)):
    for k in range(n):
      score = rnd.random()
      rows.append(_row(len(rows), base + (k % 30) * DAY + k, score, rnd.gauss(0, 0.5) + effect * (score - 0.5)))
  return rows


def test_a_score_that_orders_outcomes_in_both_windows_is_ready_and_noise_is_not():
  def report(rows):
    groups = {("key_level", "XAU"): rows}
    cut = {("key_level", "XAU"): ev.development_cut(rows, lambda r: r["quality"])}
    old_cut = {("key_level", "XAU"): ev.development_cut(rows, lambda r: r["old_quality"])}
    out = {"groups": {"key_level|XAU": {"new": ev.separation(rows, lambda r: r["quality"], cut[("key_level", "XAU")])}},
           "pooled": {"new": ev.pooled(groups, lambda r: r["quality"], cut), "old": ev.pooled(groups, lambda r: r["old_quality"], old_cut)}}
    return ev.readiness(out)

  assert report(_population(effect=2.0))["ready"] is True
  noise = report(_population(effect=0.0, seed=7))
  assert noise["ready"] is False and noise["reasons"]


def test_an_inverted_score_is_never_ready():
  rows = _population(effect=-2.0)
  groups = {("key_level", "XAU"): rows}
  cut = {("key_level", "XAU"): ev.development_cut(rows, lambda r: r["quality"])}
  pooled = ev.pooled(groups, lambda r: r["quality"], cut)
  assert pooled["out_of_sample"]["separation"] < 0


def test_resting_zone_observations_are_not_evaluated_but_other_strategies_without_a_reaction_are():
  records = [{"strategy": "supply", "has_reaction": False}, {"strategy": "supply", "has_reaction": True}, {"strategy": "break_retest", "has_reaction": False},
             {"strategy": "fvg"}, {"strategy": "impulse_pullback", "has_reaction": False}]
  kept = ev.confirmed(records)
  assert [r["strategy"] for r in kept] == ["supply", "break_retest", "impulse_pullback"]
