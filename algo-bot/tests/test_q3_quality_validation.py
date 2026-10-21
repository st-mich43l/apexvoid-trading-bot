"""tools/q3_quality_validation.py: the decision rule the Q3 conclusions rest on must not call noise a result."""

import random

import pytest

from tools import q1_opportunity_quality as q1
from tools import q3_quality_validation as q3

pytestmark = pytest.mark.no_database

DAY = 86400


def _row(i, t, high, diff, strategy="key_level", group="FX", stars=2):
  """One filled observation whose hypothetical R beat its random control by ``diff``; ``high`` is the fact being split on."""
  return {
    "id": f"o{i}", "strategy": strategy, "symbol": "EURUSD" if group == "FX" else "XAU", "group": group, "t": t, "direction": "BUY",
    "r": diff - 0.1, "r_nocost": diff - 0.05, "rand": -0.1, "diff": diff, "result": "target", "mfe": 1.0, "mae": 0.5, "stars": stars, "quality": None,
    "cluster": ("EURUSD", t // DAY), "facts": {"flag": 1.0 if high else 0.0}, "components": {}, "room_cover": None, "tech_rr": None, "pattern": None,
    "exec_risk_atr": None, "entry_width_atr": None,
  }


def _population(effect_dev, effect_oos, per_window=120, seed=3):
  """Noise of SD 0.5 around 0, plus ``effect`` for rows where the fact is true, across 30 symbol-days in each window."""
  rnd = random.Random(seed)
  rows = []
  for window, effect, base in (("dev", effect_dev, q1.CUT - 60 * DAY), ("oos", effect_oos, q1.CUT + DAY)):
    for k in range(per_window):
      high = k % 2 == 0
      t = base + (k % 30) * DAY + k
      rows.append(_row(len(rows), t, high, rnd.gauss(0, 0.5) + (effect if high else 0.0)))
  return rows


def test_summary_reports_win_rate_profit_factor_and_cost_drag():
  rows = [_row(0, q1.CUT, True, 0.9), _row(1, q1.CUT, True, 0.9), _row(2, q1.CUT, True, -0.9)]
  for r, v in zip(rows, (1.0, 1.0, -1.0)):
    r["r"], r["r_nocost"] = v, v + 0.05
  s = q3.summary(rows)
  assert s["n"] == 3 and s["win_rate"] == round(2 / 3, 3)
  assert s["profit_factor"] == 2.0 and s["avg_win_r"] == 1.0 and s["avg_loss_r"] == -1.0
  assert s["cost_drag_r"] == 0.05 and s["mean_mfe_r"] == 1.0 and s["mean_mae_r"] == 0.5
  assert q3.summary([]) == {"n": 0}


def test_a_boolean_fact_splits_as_it_is_and_a_number_at_its_median():
  rows = _population(0, 0)
  for i, r in enumerate(rows):
    r["facts"]["size"] = float(i % 10)
  splits = q3.split_candidates(rows)
  assert splits["fact:flag"][2] == "yes vs no"
  assert splits["fact:size"][2].startswith("> ")
  assert q3.split_candidates(rows[:10]) == {}, "fewer than 2 x MIN_SIDE values cannot be split"


def test_an_effect_present_in_both_windows_is_supported():
  verdicts = {s["split"]: s["verdict"] for s in q3.evaluate_splits(_population(0.6, 0.6), tests_total=1)}
  assert verdicts["fact:flag"] == "supported"


def test_an_effect_that_vanishes_out_of_sample_is_not_supported():
  verdicts = {s["split"]: s["verdict"] for s in q3.evaluate_splits(_population(0.6, 0.0), tests_total=1)}
  assert verdicts["fact:flag"] == "not supported"


def test_an_effect_that_reverses_out_of_sample_is_not_supported():
  verdicts = {s["split"]: s["verdict"] for s in q3.evaluate_splits(_population(0.6, -0.6), tests_total=1)}
  assert verdicts["fact:flag"] == "not supported"


def test_pure_noise_is_not_supported_even_after_many_looks():
  supported = 0
  for seed in range(8):
    supported += sum(s["verdict"] == "supported" for s in q3.evaluate_splits(_population(0, 0, seed=seed), tests_total=143))
  assert supported == 0


def test_a_thin_window_is_insufficient_not_a_negative_result():
  rows = [r for r in _population(0.6, 0.6) if r["t"] < q1.CUT] + [r for r in _population(0.6, 0.6) if r["t"] >= q1.CUT][:20]
  verdicts = {s["split"]: s["verdict"] for s in q3.evaluate_splits(rows, tests_total=1)}
  assert verdicts["fact:flag"] == "insufficient"


def test_the_bootstrap_needs_more_than_two_clusters():
  few = [_row(i, q1.CUT, i % 2 == 0, 0.1) for i in range(60)]
  assert q3.boot_difference([r for r in few if r["facts"]["flag"]], [r for r in few if not r["facts"]["flag"]]) is None


def test_pooled_stars_compares_a_three_star_with_the_two_star_of_its_own_strategy():
  rows = []
  for strategy, base in (("key_level", 0.5), ("supply", -0.5)):  # the strategies differ a lot; the stars do not
    for window_start in (q1.CUT - 60 * DAY, q1.CUT + DAY):
      for k in range(60):
        rows.append(_row(len(rows), window_start + (k % 30) * DAY + k, True, base + (0.01 if k % 2 else -0.01), strategy=strategy, stars=3 if k % 2 else 2))
  pooled = q3.pooled_stars(rows)
  assert abs(pooled["development"]["stratified_difference"]) < 0.05, "a pooled mean would have mixed +0.5 and -0.5"
  assert abs(pooled["out_of_sample"]["stratified_difference"]) < 0.05


def test_groups_below_the_sample_floor_are_listed_as_insufficient():
  records = []
  randoms = {}
  for i in range(30):
    rid = f"r{i}"
    t = q1.CUT + i * DAY
    records.append({"id": rid, "symbol": "EURUSD", "strategy": "ifvg", "direction": "BUY", "eval_time": t, "stars": 2, "atr": 0.0003,
                    "sim": {"ladder_cost": {"result": "target", "r": 1.0, "mfe": 1.2, "mae": 0.3}, "ladder": {"r": 1.05}}})
    randoms[rid] = {"id": rid, "mean_r": -0.1}
  out = q3.run(records, randoms)
  assert out["groups"] == {} and out["insufficient_groups"]["ifvg|FX"] == {"development": 0, "out_of_sample": 30}
