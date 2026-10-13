"""Confluence Zone v3 through Algo Bot's adapter.

The payload in ``fixtures/go_payloads_by_strategy.json`` is a real v3 opportunity the Go engine
published replaying a committed production capture. The adapter must keep treating Confluence
Zone as reaction-only (a resting overlap can never execute), key the thesis on the Go
confluence episode (so a re-confirmation is the same thesis), and read none of the v3 quality
components as a decision: they are Go-owned facts for the journal.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from tests.test_go_opportunity_policy import golden

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "go_payloads_by_strategy.json").read_text())["confluence_zone"]

pytestmark = pytest.mark.no_database


def _event(payload):
  raw = golden()
  raw["payload"] = payload
  return parse_analysis_event(OpportunityTopic, json.dumps(raw))


def _match(payload):
  event = _event(payload)
  return pol.build_strategy_match(
    event, profile=pol.REVIEWED_SCOPES["confluence_zone"], now=int(event.payload.created_at) + 1
  )


def test_a_real_v3_payload_is_a_confirmed_reaction_with_an_episode_identity():
  assert PAYLOAD["technical_context"]["confirmation"]["zone_id"].startswith("cz3:")
  assert PAYLOAD["strategy"] == "confluence_zone"
  codes = {e["code"] for e in PAYLOAD["evidence"]}
  assert {"m5_distinct_zone_overlap", "m5_confluence_independent_facts", "m5_confluence_reaction"} <= codes
  match = _match(PAYLOAD)
  assert match.direction == PAYLOAD["direction"]
  assert match.go_invalidation_price == PAYLOAD["invalidation"]["price"]
  assert "catalog:confluence_zone" in match.tags


def test_a_resting_overlap_is_never_executable():
  resting = copy.deepcopy(PAYLOAD)
  del resting["technical_context"]["confirmation"]
  resting["evidence"] = [{"code": "m5_distinct_zone_overlap"}, {"code": "m5_confluence_independent_facts"}]
  with pytest.raises(pol.AdapterRejection) as raised:
    _match(resting)
  assert raised.value.code == "reaction_confirmation_unavailable"


def test_a_reconfirmation_of_one_episode_is_one_thesis():
  first = copy.deepcopy(PAYLOAD)
  again = copy.deepcopy(PAYLOAD)
  again["id"] = "opp_" + "0" * 64  # a new opportunity id for a later confirmation of the same episode
  again["technical_context"]["confirmation"]["confirmation_bar_time"] += 0
  assert _match(first).thesis_id == _match(again).thesis_id
  other = copy.deepcopy(PAYLOAD)
  other["id"] = "opp_" + "1" * 64
  other["technical_context"]["confirmation"]["zone_id"] = "cz3:buy:" + "f" * 16
  assert _match(first).thesis_id != _match(other).thesis_id


def test_the_adapter_requires_the_strategys_own_evidence():
  foreign = copy.deepcopy(PAYLOAD)
  foreign["evidence"] = [{"code": "m5_fvg_gap"}]
  with pytest.raises(pol.AdapterRejection) as raised:
    _match(foreign)
  assert raised.value.code == "strategy_evidence_mismatch"


def test_v3_quality_components_are_not_read_as_a_decision():
  stripped = copy.deepcopy(PAYLOAD)
  stripped["quality"]["components"] = {"confluence_stars": 2}
  assert _match(stripped).thesis_id == _match(PAYLOAD).thesis_id
