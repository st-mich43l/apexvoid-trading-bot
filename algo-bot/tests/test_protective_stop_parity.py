"""Python half of the shared final-stop and entry-route parity fixture.

The same file is read by `ctrader-engine/tests/StopContractParityTests.cs`, so a
divergence between the publisher and the executor fails on both sides instead of
drifting silently between two hand-maintained tables.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.autotrade.protective_stop import (
  OpposingZoneStopContext,
  ProtectiveStopError,
  plan_protective_stop,
)


pytestmark = pytest.mark.no_database


FIXTURE = (
  Path(__file__).resolve().parents[2]
  / "contracts"
  / "autotrade"
  / "final-stop-parity.json"
)


def _fixture() -> dict:
  return json.loads(FIXTURE.read_text())


def _cases(section: str) -> list:
  return _fixture()[section]


def _ids(section: str) -> list[str]:
  return [case["name"] for case in _cases(section)]


def _zone(case: dict) -> OpposingZoneStopContext | None:
  zone = case.get("opposing_zone")
  if zone is None:
    return None
  return OpposingZoneStopContext(
    zone_id=zone["zone_id"],
    low=Decimal(zone["low"]),
    high=Decimal(zone["high"]),
    execution_grade=bool(zone["execution_grade"]),
    push_beyond_zone=bool(zone["push_beyond_zone"]),
    buffer_atr=Decimal(zone["buffer_atr"]),
  )


@pytest.mark.parametrize("case", _cases("stops"), ids=_ids("stops"))
def test_shared_stop_fixture_matches_python_planner(case):
  fixture = _fixture()
  kwargs = {
    "direction": case["direction"],
    "entry_price": case["planned_entry"],
    "structure_swing": case["structure_swing"],
    "atr": fixture["atr"],
    "structure_buffer_atr": case["structure_buffer_atr"],
    "sweep_extreme": case["sweep_extreme"],
    "wick_buffer_atr": fixture["wick_buffer_atr"],
    "minimum_stop_pips": case["minimum_stop_pips"],
    "maximum_stop_pips": case["maximum_stop_pips"],
    "pip_size": fixture["pip_size"],
    "digits": fixture["digits"],
    "opposing_zone": _zone(case),
  }
  if "expected_error" in case:
    with pytest.raises(ProtectiveStopError, match=case["expected_error"]):
      plan_protective_stop(**kwargs)
    return

  plan = plan_protective_stop(**kwargs)

  assert plan.base_stop_price == Decimal(case["expected_base_stop"])
  assert plan.final_stop_price == Decimal(case["expected_final_stop"])
  assert plan.final_stop_pips == Decimal(case["expected_final_stop_pips"])
  assert plan.raw_stop_price == Decimal(case["expected_raw_stop"])
  assert plan.clamped is case["expected_clamped"]
  assert plan.source == case["expected_source"]
  assert plan.adjustment == case["expected_adjustment"]
  if case["expected_adjustment"] == "opposing_zone_push":
    assert plan.adjustment_zone_id == case["expected_adjustment_zone_id"]
    assert plan.adjustment_zone_low == Decimal(
      case["expected_adjustment_zone_low"],
    )
    assert plan.adjustment_zone_high == Decimal(
      case["expected_adjustment_zone_high"],
    )


def test_pushed_stop_always_carries_the_exact_zone_identity():
  fixture = _fixture()
  case = next(
    item for item in fixture["stops"]
    if item["name"] == "buy_limit_opposing_zone_push"
  )
  plan = plan_protective_stop(
    direction=case["direction"],
    entry_price=case["planned_entry"],
    structure_swing=case["structure_swing"],
    atr=fixture["atr"],
    structure_buffer_atr=case["structure_buffer_atr"],
    sweep_extreme=None,
    wick_buffer_atr=fixture["wick_buffer_atr"],
    minimum_stop_pips=case["minimum_stop_pips"],
    maximum_stop_pips=case["maximum_stop_pips"],
    pip_size=fixture["pip_size"],
    digits=fixture["digits"],
    opposing_zone=_zone(case),
  )
  fields = plan.candidate_fields(entry_price=Decimal(case["planned_entry"]))

  assert fields["stop_adjustment"] == "opposing_zone_push"
  assert fields["stop_adjustment_zone_id"] == case["expected_adjustment_zone_id"]
  assert fields["stop_adjustment_zone_low"] == "3997"
  assert fields["stop_adjustment_zone_high"] == "3998.5"


