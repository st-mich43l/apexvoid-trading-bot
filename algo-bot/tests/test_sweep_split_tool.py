"""The sweep / structure-break split of the simulated outcomes (tools/sweep_split.py) is evidence, so its arithmetic is tested."""

from __future__ import annotations

import random

import pytest

from tools import sweep_split as split

pytestmark = pytest.mark.no_database

DAY = 86400


def _record(strategy: str, sweep: float, kind: float, r: float, index: int, result: str = "target") -> dict:
  sim = {"result": result, "r": r}
  return {
    "strategy": strategy, "symbol": "XAU", "eval_time": 1_790_000_000 + (index % 9) * DAY, "has_reaction": True,
    "components": {"prior_sweep": sweep, "break_kind": kind},
    "sim": {"oneR_cost": sim, "twoR_cost": sim, "ladder_cost": sim},
  }


def _records() -> list[dict]:
  out = []
  for i in range(40):
    out.append(_record("fvg", 1.0, 1.0, 1.0 if i % 2 else -1.0, i, "target" if i % 2 else "stop"))
  for i in range(40):
    out.append(_record("fvg", 0.0, 0.0, -1.0, i, "stop"))
  for i in range(12):
    out.append(_record("order_block", 0.0, -1.0, -1.0, i, "stop"))
  out.append({**_record("order_block", 1.0, 1.0, 0.0, 0), "has_reaction": False})  # a resting observation is not an opportunity
  out.append({**_record("crt", 1.0, 1.0, 1.0, 0)})  # another strategy is not split
  return out


def test_the_cells_count_filled_opportunities_and_the_win_rate():
  report = split.split_table(_records(), random.Random(1))
  assert set(report) == {"fvg", "order_block"}
  fvg = report["fvg"]
  assert fvg["opportunities"] == 80
  cells = fvg["splits"]["prior_sweep"]["cells"]
  assert cells["sweep"]["opportunities"] == 40 and cells["sweep"]["filled"] == 40
  assert cells["sweep"]["win_rate_1r"] == 0.5
  assert cells["sweep"]["oneR_cost"]["mean_r"] == 0.0
  assert cells["no sweep"]["oneR_cost"]["mean_r"] == -1.0 and cells["no sweep"]["win_rate_1r"] == 0.0
  assert report["order_block"]["opportunities"] == 12


def test_an_unfilled_opportunity_is_in_the_cell_but_not_in_the_mean():
  records = [_record("fvg", 1.0, 1.0, 1.0, i) for i in range(5)] + [_record("fvg", 1.0, 1.0, 0.0, 9, "unfilled")]
  cell = split.cell(records, random.Random(1))
  assert cell["opportunities"] == 6 and cell["filled"] == 5
  assert cell["oneR_cost"]["mean_r"] == 1.0


def test_the_sweep_split_tests_the_difference_and_is_seeded():
  first = split.split_table(_records(), random.Random(7))["fvg"]["splits"]["prior_sweep"]["tests"]
  second = split.split_table(_records(), random.Random(7))["fvg"]["splits"]["prior_sweep"]["tests"]
  assert first == second
  test = first["sweep vs no sweep / oneR_cost"]
  assert test["difference"] == 1.0
  assert test["permutation_p"] < 0.01
  assert test["ci95"] is None or test["ci95"][0] <= 1.0 <= test["ci95"][1]


def test_the_break_split_names_the_kinds_present_only():
  tests = split.split_table(_records(), random.Random(1))["fvg"]["splits"]["break_kind"]["tests"]
  assert set(t.split(" / ")[0] for t in tests) == {"BOS vs no break"}
  cells = split.split_table(_records(), random.Random(1))["order_block"]["splits"]["break_kind"]["cells"]
  assert set(cells) == {"CHoCH"}


def test_a_permutation_test_needs_three_in_each_group():
  assert split.permutation_p([1.0, 1.0], [0.0, 0.0, 0.0], random.Random(1)) is None
  assert split.permutation_p([1.0] * 5, [1.0] * 5, random.Random(1)) == 1.0


def test_the_markdown_renders_every_cell():
  text = split.markdown(split.split_table(_records(), random.Random(1)))
  assert "**fvg**" in text and "| prior_sweep | sweep |" in text and "| break_kind | BOS |" in text
