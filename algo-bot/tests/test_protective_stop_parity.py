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

from app.autotrade.execution_policy import planned_execution_route
from app.autotrade.protective_stop import (
  OpposingZoneStopContext,
  ProtectiveStopError,
  opposing_zone_fingerprint,
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


@pytest.mark.parametrize("case", _cases("routes"), ids=_ids("routes"))
def test_shared_route_fixture_matches_python_route_resolution(case):
  assert planned_execution_route(
    order_type_preference=case["order_type_preference"],
    entry_distribution=case["entry_distribution"],
  ) == case["expected_route"]


@pytest.mark.parametrize(
  "case",
  _cases("zone_identities"),
  ids=_ids("zone_identities"),
)
def test_shared_zone_identity_fixture_matches_python_fingerprint(case):
  # The executor derives the same string from the trade direction, so the
  # fixture states the direction and both sides map it to the zone's own side.
  assert opposing_zone_fingerprint(
    symbol=case["symbol"],
    timeframe=case["timeframe"],
    side="demand" if case["direction"] == "BUY" else "supply",
    low=case["zone_low"],
    high=case["zone_high"],
    created_bar_ts=case["created_bar_ts"],
    source=case["source"],
  ) == case["expected_fingerprint"]


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


def test_zone_fingerprint_separates_identical_geometry_from_different_origins():
  first = opposing_zone_fingerprint(
    symbol="XAU",
    timeframe="M15",
    side="demand",
    low="3997",
    high="3998.5",
    created_bar_ts=1_720_000_000,
    source="supply_demand",
  )
  same = opposing_zone_fingerprint(
    symbol="xau",
    timeframe="m15",
    side="Demand",
    low="3997.00",
    high="3998.50",
    created_bar_ts=1_720_000_000,
    source="Supply_Demand",
  )
  later_bar = opposing_zone_fingerprint(
    symbol="XAU",
    timeframe="M15",
    side="demand",
    low="3997",
    high="3998.5",
    created_bar_ts=1_720_000_900,
    source="supply_demand",
  )
  other_detector = opposing_zone_fingerprint(
    symbol="XAU",
    timeframe="M15",
    side="demand",
    low="3997",
    high="3998.5",
    created_bar_ts=1_720_000_000,
    source="order_block",
  )

  assert first == same
  assert first != later_bar
  assert first != other_detector

