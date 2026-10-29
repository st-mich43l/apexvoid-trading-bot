"""Round Number, Asia Range and Shock Retest: the adapter, the catalog and the names the Algo Bot knows them by.

Each is a Go-native XAU strategy that publishes a confirmed reaction; the adapter requires its own evidence prefix, accepts the extra codes the engine
appends (the stop budget, the session momentum), and the catalog row gives it the CRT execution profile.
"""

from __future__ import annotations

import json

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.provenance import CATALOG_STRATEGY_IDS
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.strategy_catalog import STRATEGY_BY_DETECTOR_KEY, STRATEGY_BY_NAME, lookup_profile, resolve_strategy_enabled
from app.autotrade.strategy_names import resolve_strategy
from tests.test_go_opportunity_policy import golden

pytestmark = pytest.mark.no_database

NEW = {
  "round_number": ("Round Number", "round_number_reaction", "m5_round_number_sweep"),
  "asia_range": ("Asia Range", "asia_range_reaction", "m5_asia_range_sweep"),
  "shock_retest": ("Shock Retest", "shock_retest_reaction", "m5_volatility_shock"),
}


def _envelope(scope: str, evidence: list[str]):
  raw = golden()
  raw["payload"]["technical_context"]["confluence"] = {
    "version": "v1", "selected_stars": 3, "v1_stars": 3, "v2_stars": 2, "v2_raw": 14.0, "raw_factor_score": 12.0, "zone_quality_score": 2.0, "mad_bonus": 0.0,
    "factors": {"htf_aligned": True, "touches": 0, "wick_rejection": True, "displacement_grade": True, "session_context": False,
                "structural_agreement": True, "fib_touch": False, "choch": False},
  }
  payload = raw["payload"]
  payload.update({
    "strategy": scope, "direction": "BUY", "timeframe": "M5",
    "entry": {"low": 4330.0, "high": 4333.5}, "invalidation": {"price": 4327.0}, "targets": [{"price": {"price": 4341.0}}],
    "evidence": [{"code": code} for code in evidence],
  })
  return raw, parse_analysis_event(OpportunityTopic, json.dumps(raw))


@pytest.mark.parametrize("scope", sorted(NEW))
def test_the_adapter_accepts_each_strategy_with_its_own_evidence_and_the_extra_codes(scope):
  _, code = NEW[scope][0], NEW[scope][2]
  extras = ["stop_budget_floor", "session_momentum_aligned", "invalidation_beyond_reaction_extreme"]
  raw, event = _envelope(scope, [code, *extras])
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES[scope], now=raw["payload"]["created_at"] + 1)
  assert match is not None
  assert match.structural_source == f"go:{scope}"
  assert set(extras) <= set(match.reasons)
  assert match.confluence == 3          # Go's selected stars, not the evidence-code count (four codes are present)


@pytest.mark.parametrize("scope", sorted(NEW))
def test_the_adapter_refuses_an_envelope_without_the_strategys_own_evidence(scope):
  raw, event = _envelope(scope, ["stop_budget_structural", "session_momentum_opposed"])
  with pytest.raises(pol.AdapterRejection) as info:
    pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES[scope], now=raw["payload"]["created_at"] + 1)
  assert info.value.code == "strategy_evidence_mismatch"


def test_the_catalog_ids_and_the_adapter_registry_agree_on_the_new_strategies():
  assert set(NEW) <= CATALOG_STRATEGY_IDS
  assert set(NEW) <= set(pol.REVIEWED_SCOPES)


@pytest.mark.parametrize("scope", sorted(NEW))
def test_the_catalog_row_is_the_crt_execution_profile_and_is_enabled(scope):
  from app.core.config import runtime_config

  name, detector_key, _ = NEW[scope]
  profile = lookup_profile(name)
  assert profile is not None and profile.name == name
  assert profile.legacy_family_label == scope
  assert profile.detector_key == detector_key
  assert profile.enable_setting == f"auto_algo.strategies.technique.{scope}.enabled"
  assert resolve_strategy_enabled(profile, runtime_config) is True
  crt = STRATEGY_BY_NAME["CRT"]
  assert profile.execution == crt.execution
  assert (profile.m5_authoritative, profile.is_scalp, profile.is_technique, profile.zone) == (True, False, True, True)


@pytest.mark.parametrize("scope", sorted(NEW))
def test_the_display_names_resolve_to_themselves(scope):
  name, detector_key, _ = NEW[scope]
  assert resolve_strategy(name).canonical == name
  assert resolve_strategy(f"{name.lower()} reaction").canonical == name
  assert STRATEGY_BY_DETECTOR_KEY[detector_key].name == name
