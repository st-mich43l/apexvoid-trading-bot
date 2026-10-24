"""Zone strategies find their setup on M15/H1 structure and confirm and execute on M5: the structure timeframe is a dimension of the Go counters.

Go marks a higher-timeframe candidate with the typed ``structure_timeframe`` and the evidence ``htf_zone_<tf>``; the adapter turns the typed field into the
tag ``go_structure_tf:<TF>``. A candidate without it was found on M5.
"""

from __future__ import annotations

import json

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.provenance import GO_ORIGIN_TAG, STOP_BUDGET_STRUCTURAL_EVIDENCE
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.go_containment import STRUCTURE_TIMEFRAME_TAG
from app.autotrade.lifecycle import increment_metric
from app.autotrade.worker import _collect_fixed_rr_metric_sink
from app.persistence import redis_state
from tests.test_go_opportunity_policy import golden
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database

COUNTER = "go_structure_timeframe"


def _evaluate(strategy_id, structure_tf=None, sink=None):
  tags = (GO_ORIGIN_TAG, f"catalog:{strategy_id}") + ((f"{STRUCTURE_TIMEFRAME_TAG}{structure_tf}",) if structure_tf else ())
  return evaluate_execution_policy(
    _policy_match(
      strategy="Order Block", direction="BUY", entry_low=4143.16, entry_high=4147.06, current_price=4147.06, atr=3.0,
      structure_swing=4141.0, go_invalidation_price=4141.0, reasons=(STOP_BUDGET_STRUCTURAL_EVIDENCE,), tags=tags,
      structural_kind=strategy_id, targets_pips=(56, 112, 168, 224),
    ),
    spot_price=4147.06, executable_quote=4147.06, regime="trend", pip_size=0.1, cfg=_cfg(), metric_sink=sink,
  )


def _events(sink_events):
  return [e for e in sink_events if e[0] == COUNTER]


@pytest.mark.parametrize("strategy,timeframe", [("order_block", "M15"), ("fvg", "H1"), ("demand", "M15")])
def test_a_higher_timeframe_zone_is_counted_with_its_structure_timeframe(strategy, timeframe):
  events = []
  out = _evaluate(strategy, timeframe, lambda name, symbol, dims: events.append((name, symbol, dims)))
  assert out.allowed, out.reason_code
  assert _events(events) == [(COUNTER, "XAU", {"strategy": strategy, "timeframe": timeframe})]


def test_a_candidate_without_a_structure_timeframe_was_found_on_m5():
  events = []
  _evaluate("fvg", None, lambda name, symbol, dims: events.append((name, symbol, dims)))
  assert _events(events) == [(COUNTER, "XAU", {"strategy": "fvg", "timeframe": "M5"})]


@pytest.mark.parametrize("strategy,timeframe,evidence", [("order_block", "M15", "m5_order_block_confirmed"), ("fvg", "H1", "m5_fvg_zone_confirmed")])
def test_the_adapter_accepts_a_higher_timeframe_order_block_or_fvg_envelope(strategy, timeframe, evidence):
  raw = golden()
  payload = raw["payload"]
  payload.update({
    "strategy": strategy, "direction": "BUY", "timeframe": "M5", "structure_timeframe": timeframe,
    "entry": {"low": 4330.0, "high": 4333.5}, "invalidation": {"price": 4327.0}, "targets": [{"price": {"price": 4341.0}}],
    "evidence": [{"code": evidence}, {"code": f"htf_zone_{timeframe.lower()}"}],
  })
  event = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES[strategy], now=payload["created_at"] + 1)
  assert match is not None
  assert f"{STRUCTURE_TIMEFRAME_TAG}{timeframe}" in match.tags
  assert f"htf_zone_{timeframe.lower()}" in match.reasons


@pytest.mark.asyncio
async def test_the_counter_lands_in_the_metrics_hash_with_strategy_and_timeframe():
  client = redis_state.get_client()
  bucket = []
  _evaluate("order_block", "H1", _collect_fixed_rr_metric_sink(bucket))
  for name, symbol, dims in bucket:
    await increment_metric(client, name, symbol=symbol, dimensions=dims)
  assert int(await client.hget("auto_trade:metrics:XAU", COUNTER)) == 1
  assert int(await client.hget(f"auto_trade:metrics:XAU:{COUNTER}", "strategy=order_block:timeframe=H1")) == 1


@pytest.mark.parametrize("symbol", ["XAU", "EURUSD", "GBPUSD", "GBPJPY", "USDJPY"])
@pytest.mark.parametrize("strategy", ["supply", "demand", "order_block", "fvg"])
@pytest.mark.parametrize("timeframe", ["M15", "H1"])
def test_containment_stays_empty_for_higher_timeframe_zone_strategies(symbol, strategy, timeframe):
  from app.autotrade.go_containment import containment_reason

  assert containment_reason(symbol, strategy, timeframe) is None
