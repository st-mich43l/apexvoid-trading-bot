"""The 2026-10-12 USDJPY incident through the real admission decision."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.instruments import SameDirectionLimits, SameDirectionPolicy
from tools.exposure_dataset import _PUBLISHED, _STACK, build
from tools.exposure_policy_replay import evaluate, policies, run

pytestmark = pytest.mark.no_database

FIXTURE = Path(__file__).parent / "fixtures" / "exposure_groups_20261005_20261014.json"
OCT_12 = 1_791_763_200


@pytest.fixture(scope="module")
def groups():
  return json.loads(FIXTURE.read_text())


def _usdjpy_incident(groups):
  return [
    g for g in groups
    if g["symbol"] == "USDJPY" and g["stream"] == "algo_auto"
    and OCT_12 - 8 * 3600 <= (g["admitted_at"] or g["first_fill_at"]) < OCT_12 + 13 * 3600
  ]


def _ids(rows):
  return {g["group_id"][10:18] for g in rows}


def test_the_incident_dataset_is_the_one_in_the_report(groups):
  rows = _usdjpy_incident(groups)
  assert {"4ac72614", "ab2e1a3a", "17d2b4fc", "f5cd83b5"} <= _ids(rows)
  stacks = [g for g in rows if g["stacked_on_admission"]]
  assert {g["group_id"][10:18] for g in stacks} >= {"ab2e1a3a", "17d2b4fc", "f5cd83b5"}


def test_today_the_four_sells_are_all_admitted_and_open_together(groups):
  result = run(_usdjpy_incident(groups), None)
  assert not result["blocked"]
  sells = [g for g in result["allowed"] if g["group_id"][10:18] in {"4ac72614", "ab2e1a3a", "17d2b4fc", "f5cd83b5"}]
  assert len(sells) == 4
  assert min(g["closed_at"] for g in sells) > max(g["first_fill_at"] for g in sells), "all four were open at once"


def test_a_one_group_policy_admits_the_first_and_refuses_the_stack(groups):
  policy = SameDirectionPolicy(symbol="USDJPY", limits=SameDirectionLimits(max_groups=1), duplicate_thesis_block=False)
  result = run(_usdjpy_incident(groups), policy)
  assert {"ab2e1a3a", "17d2b4fc", "f5cd83b5"} <= _ids(result["blocked"])
  assert "4ac72614" in _ids(result["allowed"])
  assert set(result["reasons"]) == {"same_direction_group_limit"}


def test_a_blocked_group_leaves_the_book_so_later_decisions_see_real_exposure(groups):
  """After the stack is refused, the next independent SELL is admitted once the first one closes."""
  policy = SameDirectionPolicy(symbol="USDJPY", limits=SameDirectionLimits(max_groups=1), duplicate_thesis_block=False)
  allowed = _ids(run(_usdjpy_incident(groups), policy)["allowed"])
  assert "b7d810e8" in allowed  # 11:12, after 4ac72614 closed at 09:39


def test_the_policies_never_add_exposure_and_only_the_one_group_cap_removes_every_cluster(groups):
  result = evaluate(groups)
  usdjpy = {name: scopes["USDJPY"] for name, scopes in result["by_policy"].items()}
  assert usdjpy["A_unlimited"]["groups_blocked"] == 0
  assert usdjpy["B_one_group"]["peak_concurrent_groups_per_symbol_direction"] == 1
  assert usdjpy["B_one_group"]["correlated_stop_clusters"] == 0
  assert usdjpy["A_unlimited"]["correlated_stop_clusters"] > usdjpy["C_two_groups_budget"]["correlated_stop_clusters"] > 0
  for name, scopes in result["by_policy"].items():
    for scope, row in scopes.items():
      assert row["groups_allowed"] + row["groups_blocked"] == result["by_policy"]["A_unlimited"][scope]["groups_allowed"], (name, scope)


def test_the_policy_set_is_the_four_documented_alternatives():
  assert list(policies()) == ["A_unlimited", "B_one_group", "C_two_groups_budget", "D_one_per_thesis_symbol_ceiling"]


def test_the_dataset_builder_reads_the_log_lines_it_documents(tmp_path):
  published = "2026-10-12 06:51:01,975 [INFO] app.autotrade.worker: v8 plan published id=v8:go_opp_ab2e1a3a574f symbol=USDJPY strategy=Range Edge Scalp direction=SELL entry_type=single_limit"
  stack = "2026-10-12 06:51:00,815 [INFO] app.autotrade.worker: v8 same-direction stack symbol=USDJPY match_id=go_opp_ab2e1 same-direction stack on active SELL @ 158.39: size 60% on a single leg"
  assert _PUBLISHED.match(published) and _STACK.match(stack)
  log = tmp_path / "log"
  log.write_text(published + "\n" + stack + "\n")
  groups_psv = tmp_path / "groups.psv"
  groups_psv.write_text("v8:go_opp_ab2e1a3a574f|algo_auto|USDJPY|Range Edge Scalp|SELL|london|full_stop|-18|17.9||2|1791797947|1791787865|1791787865|1100000|1|158.353\n")
  opps = tmp_path / "opps.psv"
  opps.write_text("v8:go_opp_ab2e1a3a574f|1791787500|range:resistance:1791782100|1791782100|range_edge|158.364|158.38|158.388\n")
  rows = build(groups_psv, opps, log)
  assert len(rows) == 1
  row = rows[0]
  assert row["stacked_on_admission"] is True
  assert row["structure_id"] == "range:resistance:1791782100"
  assert row["lots"] == pytest.approx(0.11)
  assert row["planned_risk"] == pytest.approx(0.11 * 17.9 * 6.27, abs=0.01)


def test_every_fx_pair_is_bounded_to_one_same_direction_group_and_xau_is_left_alone():
  """The committed configuration, read through the runtime loader (packs merged)."""
  from app.core import instrument_geometry

  for symbol in ("EURUSD", "GBPUSD", "GBPJPY", "USDJPY"):
    policy = instrument_geometry.same_direction_policy(symbol)
    assert policy is not None, symbol
    assert policy.limits.max_groups == 1 and policy.risk_free_groups_count and policy.duplicate_thesis_block, symbol
    assert policy.strategy_overrides == (), "no strategy may stack on FX"
  assert instrument_geometry.same_direction_policy("XAU") is None
