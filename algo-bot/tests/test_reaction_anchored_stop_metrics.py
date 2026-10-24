"""A legacy-family invalidation anchored beyond the reaction's own extreme is counted, and the extra evidence code never blocks a plan.

Go marks such an opportunity with the evidence code ``invalidation_beyond_reaction_extreme``. The adapter accepts it like any other code; the
execution policy counts the plans that carry it and those whose now wider stop exceeds the instrument envelope.
"""

from __future__ import annotations

import json

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.provenance import GO_ORIGIN_TAG, REACTION_EXTREME_EVIDENCE
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.lifecycle import increment_metric
from app.autotrade.worker import _collect_fixed_rr_metric_sink
from app.persistence import redis_state
from app.autotrade.execution_policy import _emit_metric, evaluate_execution_policy
from tests.test_go_opportunity_policy import golden
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database

ANCHORED = "go_invalidation_reaction_anchored"
TOO_WIDE = "go_invalidation_reaction_anchored_too_wide"


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


def test_the_adapter_accepts_the_reaction_extreme_evidence_code():
  raw = golden()
  raw["payload"]["evidence"] = [{"code": "m5_supply_zone_fresh"}, {"code": REACTION_EXTREME_EVIDENCE}]
  event = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["supply"], now=raw["payload"]["created_at"] + 1)
  assert match is not None
  assert REACTION_EXTREME_EVIDENCE in match.reasons


def test_an_anchored_plan_inside_the_envelope_is_counted_once_per_strategy():
  events, sink = _collector()
  out = _evaluate(4143.16, 4147.06, 4141.0, reasons=("m5_key_level_reaction", REACTION_EXTREME_EVIDENCE), sink=sink)
  assert out.allowed, out.reason_code
  assert [e for e in events if e[0] in (ANCHORED, TOO_WIDE)] == [(ANCHORED, "XAU", {"strategy": "key_level", "timeframe": "M5"})]


def test_a_plan_without_the_evidence_code_is_not_counted():
  events, sink = _collector()
  out = _evaluate(4143.16, 4147.06, 4141.0, reasons=("m5_key_level_reaction",), sink=sink)
  assert out.allowed, out.reason_code
  assert not [e for e in events if e[0] in (ANCHORED, TOO_WIDE)]


def test_an_anchored_stop_beyond_the_envelope_maximum_is_rejected_and_counted_as_too_wide():
  events, sink = _collector()
  out = _evaluate(4140.0, 4147.0, 4139.0, reasons=(REACTION_EXTREME_EVIDENCE,), sink=sink, strategy_id="demand")
  assert not out.allowed
  assert out.reason_code == "stop_exceeds_go_invalidation_envelope"
  counted = [e for e in events if e[0] in (ANCHORED, TOO_WIDE)]
  assert counted == [(ANCHORED, "XAU", {"strategy": "demand", "timeframe": "M5"}), (TOO_WIDE, "XAU", {"strategy": "demand", "timeframe": "M5"})]


def test_a_wide_stop_without_the_evidence_code_is_rejected_but_not_counted_as_anchored():
  events, sink = _collector()
  out = _evaluate(4140.0, 4147.0, 4139.0, sink=sink)
  assert out.reason_code == "stop_exceeds_go_invalidation_envelope"
  assert not [e for e in events if e[0] in (ANCHORED, TOO_WIDE)]


def test_a_failing_counter_sink_never_changes_the_plan():
  def broken(name, symbol, dims):
    raise RuntimeError("redis is down")

  plain = _evaluate(4143.16, 4147.06, 4141.0, reasons=(REACTION_EXTREME_EVIDENCE,))
  guarded = _evaluate(4143.16, 4147.06, 4141.0, reasons=(REACTION_EXTREME_EVIDENCE,), sink=broken)
  assert guarded.allowed and guarded.measured["planned_stop_price"] == plain.measured["planned_stop_price"]
  _emit_metric(broken, ANCHORED, "XAU", {"strategy": "key_level"})   # does not raise
  _emit_metric(None, ANCHORED, "XAU", {})


@pytest.mark.asyncio
async def test_both_counters_land_in_the_metrics_hash_with_the_strategy_dimension():
  client = redis_state.get_client()
  bucket = []
  out = _evaluate(4140.0, 4147.0, 4139.0, reasons=(REACTION_EXTREME_EVIDENCE,), strategy_id="demand", sink=_collect_fixed_rr_metric_sink(bucket))
  assert out.reason_code == "stop_exceeds_go_invalidation_envelope"
  for name, symbol, dims in bucket:
    await increment_metric(client, name, symbol=symbol, dimensions=dims)
  for name in (ANCHORED, TOO_WIDE):
    assert int(await client.hget("auto_trade:metrics:XAU", name)) == 1
    assert int(await client.hget(f"auto_trade:metrics:XAU:{name}", "strategy=demand:timeframe=M5")) == 1
