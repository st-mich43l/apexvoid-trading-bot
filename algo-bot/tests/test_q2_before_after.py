import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import q2_before_after as q2  # noqa: E402


def rec(i, strategy, stars=2, wick=False, reaction=True):
  return {"id": i, "strategy": strategy, "stars": stars, "has_reaction": reaction,
          "factors": {"wick": wick, "structural": True, "displacement": False, "htf_aligned": True, "session": False, "choch": False, "fib": False}}


def test_counts_split_kept_removed_and_new():
  before = {"a": rec("a", "fvg"), "b": rec("b", "fvg"), "c": rec("c", "demand")}
  after = {"a": rec("a", "fvg"), "c": rec("c", "demand"), "d": rec("d", "demand")}
  assert q2.counts(before, after) == {"fvg": {"kept": 1, "removed": 1, "new": 0}, "demand": {"kept": 1, "removed": 0, "new": 1}}


def test_factors_report_the_share_published_true_on_confirmed_opportunities_only():
  records = {"a": rec("a", "fvg", wick=True), "b": rec("b", "fvg"), "c": rec("c", "fvg", reaction=False)}
  got = q2.factors(records)["fvg"]
  assert got["n"] == 2 and got["wick"] == 0.5 and got["structural"] == 1.0


def test_stars_ignore_resting_candidates():
  records = {"a": rec("a", "fvg", stars=3), "b": rec("b", "fvg", stars=2, reaction=False)}
  assert q2.stars(records) == {"fvg": {3: 1}}
