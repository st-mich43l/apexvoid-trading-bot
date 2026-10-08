"""Real Go opportunity payloads, one per strategy, through Algo Bot's adapter.

``fixtures/go_payloads_by_strategy.json`` holds the first Kafka opportunity payload
each strategy produced replaying the committed XAU captures through the Go engine
(``PAYLOAD_DUMP=... go test ./test/legacyparity -run TestDumpPayloads``; the M1 scalps
from the seeded synthetic capture). The adapter must translate every one of them
into a StrategyMatch for its own reviewed scope: a strategy whose evidence, geometry
or reaction the adapter cannot read would otherwise fail only in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from tests.test_go_opportunity_policy import golden

PAYLOADS = json.loads((Path(__file__).parent / "fixtures" / "go_payloads_by_strategy.json").read_text())

pytestmark = pytest.mark.no_database


@pytest.mark.parametrize("strategy", sorted(PAYLOADS))
def test_a_real_go_payload_is_admitted_by_its_reviewed_adapter(strategy):
  raw = golden()
  raw["payload"] = PAYLOADS[strategy]
  event = parse_analysis_event(OpportunityTopic, json.dumps(raw))
  payload = event.payload
  profile = pol.REVIEWED_SCOPES[strategy]
  match = pol.build_strategy_match(event, profile=profile, now=int(payload.created_at) + 1)
  assert match.symbol == "XAU"
  assert match.direction == payload.direction
  assert match.go_invalidation_price == payload.invalidation.price
  assert f"catalog:{strategy}" in match.tags
