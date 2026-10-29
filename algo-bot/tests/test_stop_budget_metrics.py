"""A legacy-family candidate Go published under the instrument stop budget is counted; the extra evidence codes never block a plan.

Go marks every budgeted candidate with ``stop_budget_structural`` (the structural stop is inside the instrument's floor and cap) or ``stop_budget_floor``
(tighter than the floor; execution widens it). A stop above the cap is never published, so there is no third code.
"""

from __future__ import annotations

import json

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.provenance import (
  GO_ORIGIN_TAG,
  STOP_BUDGET_FLOOR_EVIDENCE,
  STOP_BUDGET_STRUCTURAL_EVIDENCE,
)
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.lifecycle import increment_metric
from app.autotrade.worker import _collect_fixed_rr_metric_sink
from app.persistence import redis_state
from tests.test_go_opportunity_policy import golden
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database

STRUCTURAL = "go_stop_budget_structural"
FLOOR = "go_stop_budget_floor"


def _evaluate(low, high, invalidation, *, reasons=(), strategy_id="key_level", sink=None):
  return evaluate_execution_policy(
    _policy_match(
      strategy="Key Level", direction="BUY", entry_low=low, entry_high=high, current_price=high, atr=3.0,
      structure_swing=invalidation, go_invalidation_price=invalidation, reasons=tuple(reasons),
      tags=(GO_ORIGIN_TAG, f"catalog:{strategy_id}"), structural_kind="key_level", targets_pips=(56, 112, 168, 224),
    ),
    spot_price=high, executable_quote=high, regime="trend", pip_size=0.1, cfg=_cfg(), metric_sink=sink,
  )


def _collector():
  events = []
  return events, lambda name, symbol, dims: events.append((name, symbol, dims))


def _budget_events(events):
  return [e for e in events if e[0] in (STRUCTURAL, FLOOR)]


@pytest.mark.parametrize("code", [STOP_BUDGET_STRUCTURAL_EVIDENCE, STOP_BUDGET_FLOOR_EVIDENCE])
def test_the_adapter_accepts_the_stop_budget_evidence_codes(code):
  raw = golden()
  raw["payload"]["technical_context"]["confluence"] = {
    "version": "v1", "selected_stars": 3, "v1_stars": 3, "v2_stars": 2, "v2_raw": 14.0, "raw_factor_score": 12.0, "zone_quality_score": 2.0, "mad_bonus": 0.0,
    "factors": {"htf_aligned": True, "touches": 0, "wick_rejection": True, "displacement_grade": True, "session_context": False,
                "structural_agreement": True, "fib_touch": False, "choch": False},
  }
  raw["payload"]["evidence"] = [{"code": "m5_supply_zone_fresh"}, {"code": code}, {"code": "session_momentum_aligned"}]
  event = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["supply"], now=raw["payload"]["created_at"] + 1)
  assert match is not None
  assert code in match.reasons
  assert match.confluence == 3    # Go's selected stars, not the evidence-code count (three codes are present): the extra code changes no admission input


def test_a_structural_stop_inside_the_budget_is_counted_per_strategy():
  events, sink = _collector()
  out = _evaluate(4143.16, 4147.06, 4141.0, reasons=("m5_key_level_reaction", STOP_BUDGET_STRUCTURAL_EVIDENCE), sink=sink, strategy_id="supply")
  assert out.allowed, out.reason_code
  assert _budget_events(events) == [(STRUCTURAL, "XAU", {"strategy": "supply", "timeframe": "M5"})]


def test_a_stop_below_the_floor_is_counted_as_floor():
  events, sink = _collector()
  out = _evaluate(4143.16, 4147.06, 4141.0, reasons=(STOP_BUDGET_FLOOR_EVIDENCE,), sink=sink, strategy_id="demand")
  assert out.allowed, out.reason_code
  assert _budget_events(events) == [(FLOOR, "XAU", {"strategy": "demand", "timeframe": "M5"})]


def test_a_candidate_without_a_budget_code_is_not_counted():
  events, sink = _collector()
  out = _evaluate(4143.16, 4147.06, 4141.0, reasons=("m5_key_level_reaction",), sink=sink)
  assert out.allowed, out.reason_code
  assert _budget_events(events) == []


def test_a_failing_sink_never_changes_the_plan():
  def broken(name, symbol, dims):
    raise RuntimeError("redis is down")

  plain = _evaluate(4143.16, 4147.06, 4141.0, reasons=(STOP_BUDGET_STRUCTURAL_EVIDENCE,))
  guarded = _evaluate(4143.16, 4147.06, 4141.0, reasons=(STOP_BUDGET_STRUCTURAL_EVIDENCE,), sink=broken)
  assert guarded.allowed and guarded.measured["planned_stop_price"] == plain.measured["planned_stop_price"]


@pytest.mark.asyncio
async def test_both_counters_land_in_the_metrics_hash_with_the_strategy_dimension():
  client = redis_state.get_client()
  bucket = []
  sink = _collect_fixed_rr_metric_sink(bucket)
  _evaluate(4143.16, 4147.06, 4141.0, reasons=(STOP_BUDGET_STRUCTURAL_EVIDENCE,), sink=sink, strategy_id="key_level")
  _evaluate(4143.16, 4147.06, 4141.0, reasons=(STOP_BUDGET_FLOOR_EVIDENCE,), sink=sink, strategy_id="key_level")
  for name, symbol, dims in bucket:
    await increment_metric(client, name, symbol=symbol, dimensions=dims)
  for name in (STRUCTURAL, FLOOR):
    assert int(await client.hget("auto_trade:metrics:XAU", name)) == 1
    assert int(await client.hget(f"auto_trade:metrics:XAU:{name}", "strategy=key_level:timeframe=M5")) == 1
