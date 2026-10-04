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


def test_decodes_post_bootstrap_recovery_marker():
  event = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity(
    payload={**_opportunity()["payload"], "recovered_at": 200},
  )))
  assert event.payload.recovered_at == 200


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


# ---- technical_context (additive V1 block) ---------------------------------------

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


def test_go_candle_evidence_extension_decodes_strictly():
  event = _opportunity()
  event["payload"]["technical_context"] = {
    "atr": 3.6,
    "reference_price": 4353.0,
    "reference_time": 100,
  }
  event["payload"]["technical_context"]["candle_evidence"] = {
    "version": 2,
    "direction": "BUY",
    "rejection": {
      "score": 0.6,
      "patterns": ["sweep_reclaim"],
      "wick_fraction": 0.2,
      "body_fraction": 0.7,
      "close_location": 0.9,
      "sweep": True,
      "sweep_penetration_atr": 0.4,
      "reclaim": True,
      "reclaim_depth_atr": 0.3,
    },
    "displacement": {
      "score": 0.8,
      "patterns": ["strong_close"],
      "body_atr": 1.1,
      "range_atr": 1.4,
      "body_dominance": 0.75,
      "close_location": 0.9,
      "reclaim": True,
      "reclaim_depth_atr": 0.2,
      "engulfing": False,
    },
    "indecision": {
      "doji": False,
      "spinning_top": False,
      "inside_bar": False,
      "body_fraction": 0.7,
      "compression_score": 0.1,
    },
    "base_score": 0.72,
    "synergy_bonus": 0.08,
    "final_score": 0.8,
    "primary_pattern": "sweep_reclaim",
    "all_patterns": ["sweep_reclaim", "strong_close"],
  }
  parsed = parse_analysis_event(OpportunityTopic, json.dumps(event)).payload.technical_context
  assert parsed is not None and parsed.candle_evidence is not None
  assert parsed.candle_evidence.version == 2
  assert parsed.candle_evidence.primary_pattern == "sweep_reclaim"
  assert parsed.candle_evidence.rejection is not None


@pytest.mark.no_database
def test_go_mad_telemetry_extension_decodes_without_becoming_policy_gate():
  event = _opportunity()
  event["payload"]["technical_context"] = {
    "atr": 3.6,
    "reference_price": 4353.0,
    "reference_time": 100,
    "mad": {
      "version": 2,
      "phase": "manip",
      "confidence": 0.91,
      "affinity": 0.86,
      "direction": "SELL",
      "sweep_side": "high",
      "reclaim": True,
      "range_quality_atr": 2.1,
      "acceptance_closes": 0,
      "reason_code": "asia_sweep_reclaim",
    },
  }
  parsed = parse_analysis_event(OpportunityTopic, json.dumps(event)).payload.technical_context
  assert parsed is not None and parsed.mad is not None
  assert parsed.mad.phase == "manip"
  assert parsed.mad.affinity == 0.86


@pytest.mark.no_database
def test_go_confluence_extension_decodes_as_engine_owned_facts():
  event = _opportunity()
  event["payload"]["technical_context"] = {
    "atr": 3.6,
    "reference_price": 4353.0,
    "reference_time": 100,
    "confluence": {
      "version": "v1",
      "selected_stars": 2,
      "v1_stars": 2,
      "v2_stars": 3,
      "v2_raw": 18.5,
      "raw_factor_score": 15.0,
      "zone_quality_score": 2.0,
      "mad_bonus": 0.75,
      "factors": {
        "htf_aligned": True,
        "touches": 1,
        "wick_rejection": True,
        "displacement_grade": False,
        "session_context": True,
        "structural_agreement": True,
        "fib_touch": False,
        "choch": False,
      },
    },
  }
  parsed = parse_analysis_event(OpportunityTopic, json.dumps(event)).payload.technical_context
  assert parsed is not None and parsed.confluence is not None
  assert parsed.confluence.selected_stars == 2
  assert parsed.confluence.factors.touches == 1


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
