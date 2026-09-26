import json

import pytest

from app.analysis_client.models import (
  AnalysisContractError,
  InvalidationTopic,
  OpportunityTopic,
  parse_analysis_event,
)


def _opportunity(**overrides):
  payload = {
    "event_id": "evt-create-1",
    "event_type": OpportunityTopic,
    "event_version": 1,
    "occurred_at": 100,
    "produced_at": 101,
    "producer": "apexvoid-analysis-engine",
    "correlation_id": "corr-1",
    "payload": {
      "id": "opp-1", "strategy": "fvg", "symbol": "XAU", "timeframe": "M5",
      "direction": "SELL", "entry": {"low": 4353.6, "high": 4358.6},
      "invalidation": {"price": 4360.62},
      "targets": [{"price": {"price": 4349.42}}],
      "evidence": [{"code": "fvg_present"}],
      "quality": {"overall": 0.57, "components": {"reaction": 0.6}},
      "algorithm_version": {"structure": "v2", "liquidity": "v1"},
      "formed_at": 99, "created_at": 100, "expires_at": 3700,
    },
  }
  payload.update(overrides)
  return payload


def _invalidated(**overrides):
  event = {
    "event_id": "evt-terminal-1", "event_type": InvalidationTopic,
    "event_version": 1, "occurred_at": 102, "produced_at": 103,
    "producer": "apexvoid-analysis-engine", "correlation_id": "corr-1",
    "payload": {"opportunity_id": "opp-1", "symbol": "XAU", "strategy": "fvg", "reason_code": "SETUP_EXPIRED", "invalidated_at": 102},
  }
  event.update(overrides)
  return event


def test_decodes_creation_and_additive_v1_fields():
  event = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity()))
  assert event.payload.id == "opp-1"
  assert event.payload.timeframe == "M5"
  assert event.payload.formed_at == 99


def test_decodes_terminal_event():
  event = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated()))
  assert event.payload.reason_code == "SETUP_EXPIRED"


@pytest.mark.parametrize(
  ("topic", "event", "fragment"),
  [
    (OpportunityTopic, _opportunity(event_type=InvalidationTopic), "event_type"),
    (OpportunityTopic, _opportunity(producer="other-service"), "unexpected producer"),
    (OpportunityTopic, _opportunity(produced_at=99), "produced_at"),
    (OpportunityTopic, _opportunity(payload={**_opportunity()["payload"], "targets": [{"price": {"price": 4354.0}}]}), "SELL targets"),
  ],
)
def test_rejects_semantically_invalid_events(topic, event, fragment):
  with pytest.raises(AnalysisContractError, match=fragment):
    parse_analysis_event(topic, json.dumps(event))


def test_rejects_unknown_topic_and_bad_json():
  with pytest.raises(AnalysisContractError, match="unexpected analysis topic"):
    parse_analysis_event("other.topic", "{}")
  with pytest.raises(AnalysisContractError, match="invalid JSON"):
    parse_analysis_event(OpportunityTopic, "not-json")


# ---- S13B technical_context (additive V1 block) ---------------------------------

GOLDEN = "contracts/analysis/examples/opportunity-v1-technical-context.json"


def _golden_text():
  from pathlib import Path
  return (Path(__file__).resolve().parents[2] / GOLDEN).read_text()


def test_go_golden_fixture_decodes_with_its_technical_context():
  """The bytes are produced and pinned by the Go encoder's contract test."""
  event = parse_analysis_event(OpportunityTopic, _golden_text())
  tech = event.payload.technical_context
  assert tech is not None
  assert (tech.atr, tech.reference_price, tech.reference_time) == (3.61, 4351.9, 1789961100)
  assert tech.bias is not None and (tech.bias.direction, tech.bias.layer) == ("SELL", "internal")
  assert event.payload.timeframe == "M5" and event.payload.strategy == "supply"


def test_technical_context_is_optional_for_retained_events():
  assert parse_analysis_event(OpportunityTopic, json.dumps(_opportunity())).payload.technical_context is None


@pytest.mark.parametrize("mutation,needle", [
  ({"atr": 0}, "atr"),
  ({"atr": -1.5}, "atr"),
  ({"reference_price": 0}, "reference_price"),
  ({"atr": float("nan")}, "atr"),
  ({"spread": 0.1}, "spread"),                 # a quote/spread must never ride along
  ({"account_balance": 1000}, "account_balance"),
  ({"reference_time": 101}, "reference_time"),  # look-ahead: after created_at=100
  ({"bias": {"direction": "NEUTRAL", "layer": "internal"}}, "bias"),
])
def test_technical_context_is_strictly_validated(mutation, needle):
  event = _opportunity()
  event["payload"]["technical_context"] = {"atr": 3.6, "reference_price": 4353.0, "reference_time": 100, **mutation}
  with pytest.raises(AnalysisContractError) as exc:
    parse_analysis_event(OpportunityTopic, json.dumps(event))
  assert needle in str(exc.value)
