import json
import fakeredis
import pytest

from app.autotrade.config_health import canonicalize_account_mode
from app.autotrade.config_health import canonicalize_broker
from app.autotrade.config_health import compare_manifests
from app.autotrade.config_health import python_manifest
from app.autotrade.config_health import publish_python_manifest
from app.autotrade.range_types import AutoScalpDecision
from app.autotrade.lifecycle import emit_lifecycle
from app.autotrade.lifecycle import parse_lifecycle_state
from app.autotrade.range_context import (
  RangeBarrier,
  RangeContext,
  resolve_range_context,
)
from app.autotrade import config_health, worker
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf


def _settings(monkeypatch, **env):
  values = {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
    "POSTGRES_PASSWORD": "apexvoid",
    "AUTO_TRADE_PROFILE": "demo_eval",
  }
  for key, value in env.items():
    values[key] = str(value)
  for key, value in values.items():
    monkeypatch.setenv(key, value)
  result = load_python_canonical_settings(
    ConfigurationSourceBundle(process_environment=values),
  )
  return result.config


def test_demo_profile_resolves_execution_defaults(monkeypatch):
  cfg = _settings(monkeypatch)
  assert leaf(cfg, "auto_trade_profile") == "demo_eval"
  assert leaf(cfg, "auto_trade_require_demo_account")
  assert leaf(cfg, "auto_trade_allow_concurrent_strategies")
  assert leaf(cfg, "auto_trade_allow_hedged_xau")
  assert not leaf(cfg, "auto_trade_require_flat_for_range")
  assert leaf(cfg, "auto_trade_range_two_sided_enabled")
  assert leaf(cfg, "auto_trade_range_flip_enabled")
  assert leaf(cfg, "auto_trade_trend_enabled")
  assert leaf(cfg, "auto_trade_range_enabled")
  assert leaf(cfg, "auto_trade_mapped_zone_enabled")
  assert leaf(cfg, "auto_trade_market_map_guard_enabled")
  assert leaf(cfg, "auto_trade_strategy_match_enabled")
  assert leaf(cfg, "auto_trade_breakout_enabled")
  assert leaf(cfg, "auto_trade_retest_enabled")
  assert leaf(cfg, "auto_trade_reaction_enabled")
  assert leaf(cfg, "auto_trade_liquidity_reversal_enabled")
  assert leaf(cfg, "auto_trade_allow_counter_bias")
  assert leaf(cfg, "auto_trade_multi_match_enabled")
  assert leaf(cfg, "auto_trade_track_all_structural_matches")
  assert leaf(cfg, "auto_trade_enabled")
  assert not leaf(cfg, "auto_trade_dry_run")
  assert leaf(cfg, "auto_trade_candidate_contract_version") == 6
  assert leaf(cfg, "auto_trade_candidate_max_age_seconds") == 420
  assert leaf(cfg, "auto_trade_candidate_ttl") == 604800
  assert leaf(cfg, "auto_trade_spot_max_age") == 5
  assert leaf(cfg, "auto_trade_zone_fill_enabled")
  assert leaf(cfg, "auto_trade_non_hedged_opposite_policy") == "broker_netting"
  assert leaf(cfg, "auto_trade_structural_guard_mode") == "observe"
  assert not leaf(cfg, "auto_trade_opposing_barrier_veto_enabled")
  assert not leaf(cfg, "auto_trade_overlap_veto_enabled")
  assert not leaf(cfg, "auto_trade_zone_cooldown_enabled")
  assert leaf(cfg, "auto_trade_zone_reconcile_mode") == "shadow"
  assert leaf(cfg, "auto_trade_range_min_entry_drift_pips") == 10
  assert leaf(cfg, "auto_trade_map_min_entry_drift_pips") == 10
  assert leaf(cfg, "auto_trade_trend_min_entry_drift_pips") == 15
  assert leaf(cfg, "auto_trade_range_max_entry_drift_atr") == 1.0
  assert leaf(cfg, "auto_trade_map_max_entry_drift_atr") == 1.0
  assert leaf(cfg, "auto_trade_trend_max_entry_drift_atr") == 1.5
  assert leaf(cfg, "auto_trade_range_hard_entry_drift_pips") == 20
  assert leaf(cfg, "auto_trade_map_hard_entry_drift_pips") == 20
  assert leaf(cfg, "auto_trade_trend_hard_entry_drift_pips") == 30
  assert leaf(cfg, "scanner_top_n") == 0
  assert leaf(cfg, "scanner_card_top_n") == 2
  assert not leaf(cfg, "scanner_gate_require_structural_anchor")
  assert leaf(cfg, "scanner_gate_max_source_touches") == 0
  assert not leaf(cfg, "scanner_gate_suppress_counter_bias_in_range")
  assert leaf(cfg, "scanner_gate_counter_bias_min_confluence") == 3
  assert leaf(cfg, "auto_trade_max_tracked_candidates") == 0


def test_demo_profile_does_not_override_explicit_environment(monkeypatch):
  cfg = _settings(
    monkeypatch,
    AUTO_TRADE_RANGE_FLIP_ENABLED="false",
    AUTO_TRADE_ALLOW_HEDGED_XAU="false",
    AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS="123",
    AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS="456",
    SCANNER_TOP_N="7",
    SCANNER_CARD_TOP_N="4",
    SCANNER_GATE_REQUIRE_STRUCTURAL_ANCHOR="true",
    SCANNER_GATE_MAX_SOURCE_TOUCHES="6",
    SCANNER_GATE_SUPPRESS_COUNTER_BIAS_IN_RANGE="true",
    SCANNER_GATE_COUNTER_BIAS_MIN_CONFLUENCE="5",
  )
  assert not leaf(cfg, "auto_trade_range_flip_enabled")
  assert not leaf(cfg, "auto_trade_allow_hedged_xau")
  assert leaf(cfg, "auto_trade_candidate_max_age_seconds") == 123
  assert leaf(cfg, "auto_trade_candidate_ttl") == 456
  assert leaf(cfg, "scanner_top_n") == 7
  assert leaf(cfg, "scanner_card_top_n") == 4
  assert leaf(cfg, "scanner_gate_require_structural_anchor")
  assert leaf(cfg, "scanner_gate_max_source_touches") == 6
  assert leaf(cfg, "scanner_gate_suppress_counter_bias_in_range")
  assert leaf(cfg, "scanner_gate_counter_bias_min_confluence") == 5


def test_market_map_guard_follows_execution_flag_unless_explicit(monkeypatch):
  neutral = _settings(
    monkeypatch,
    AUTO_TRADE_MAPPED_ZONE_ENABLED="false",
  )
  guarded = _settings(
    monkeypatch,
    AUTO_TRADE_MAPPED_ZONE_ENABLED="false",
    AUTO_TRADE_MARKET_MAP_GUARD_ENABLED="true",
  )

  assert not leaf(neutral, "auto_trade_mapped_zone_enabled")
  assert not leaf(neutral, "auto_trade_market_map_guard_enabled")
  assert not leaf(guarded, "auto_trade_mapped_zone_enabled")
  assert leaf(guarded, "auto_trade_market_map_guard_enabled")


def test_demo_profile_cannot_disable_demo_account_guard(monkeypatch):
  with pytest.raises(CanonicalConfigurationError) as excinfo:
    _settings(
      monkeypatch,
      AUTO_TRADE_REQUIRE_DEMO_ACCOUNT="false",
    )
  assert excinfo.value.category == "demo_account_requirement"


def _context(source, lower, upper, quality=5.0):
  return RangeContext(
    version=1,
    range_id=f"{source}-{lower}-{upper}",
    symbol="XAU",
    state="confirmed",
    source=source,
    execution_timeframe="M1",
    context_timeframes=("M1", "M5"),
    lower=lower,
    upper=upper,
    equilibrium=(lower + upper) / 2,
    width_price=upper - lower,
    width_pips=(upper - lower) / 0.1,
    width_atr=(upper - lower) / 2,
    lower_barrier=RangeBarrier(
      lower, lower - 0.1, lower + 0.1, 3, 2,
    ),
    upper_barrier=RangeBarrier(
      upper, upper - 0.1, upper + 0.1, 3, 2,
    ),
    supports=(RangeBarrier(lower, lower, lower, 3, 2),),
    resistances=(RangeBarrier(upper, upper, upper, 3, 2),),
    inside_close_count=12,
    quality=quality,
    generated_at=100,
    expires_at=1000,
  )


def test_compatible_scanner_and_private_ranges_merge():
  resolved, comparison = resolve_range_context(
    _context("scanner", 4000, 4010),
    _context("private", 4000.5, 4009.5),
    now=200,
  )
  assert resolved is not None
  assert resolved.source == "merged"
  assert comparison["resolution"] == "merged"
  assert not comparison["disagreement"]


def test_material_range_disagreement_is_recorded_deterministically():
  scanner = _context("scanner", 4000, 4010, quality=8)
  private = _context("private", 4020, 4030, quality=3)
  resolved, comparison = resolve_range_context(scanner, private, now=200)
  assert resolved == scanner
  assert comparison["disagreement"]
  assert comparison["reason"] == "materially_incompatible_geometry"


def test_accepted_breakout_retires_range_over_stale_active_source():
  scanner = _context("scanner", 4000, 4010, quality=8)
  broken = RangeContext(
    **{
      **scanner.__dict__,
      "range_id": "private-broken",
      "source": "private",
      "state": "broken",
      "breakout_state": "accepted",
      "invalidation_reason": "accepted_structural_breakout",
      "generated_at": 150,
    }
  )
  resolved, comparison = resolve_range_context(
    scanner,
    broken,
    now=200,
  )
  assert resolved == broken
  assert comparison["resolution"] == "accepted_structural_breakout"
  assert comparison["disagreement"]


@pytest.mark.asyncio
async def test_lifecycle_keeps_history_not_only_latest(monkeypatch):
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_profile": "demo_eval"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_candidate_ttl": 86400})
  await emit_lifecycle(
    client, "detected", symbol="XAU", candidate_id="candidate-1",
  )
  await emit_lifecycle(
    client, "auto_ready", symbol="XAU", candidate_id="candidate-1",
  )
  history = await client.lrange(
    "auto_trade:lifecycle:candidate-1", 0, -1,
  )
  assert [json.loads(item)["state"] for item in history] == [
    "detected", "auto_ready",
  ]


def test_parse_lifecycle_state_supports_plain_and_structured_records():
  assert parse_lifecycle_state("managing") == "managing"
  assert parse_lifecycle_state(
    json.dumps({"owner_id": "abc", "state": "order_filled", "version": 1})
  ) == "order_filled"
  assert parse_lifecycle_state(None) is None


@pytest.mark.asyncio
async def test_python_config_manifest_is_published(monkeypatch):
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  cfg = _settings(monkeypatch)
  monkeypatch.setattr(config_health, "runtime_config", cfg)

  health = await publish_python_manifest(client)

  manifest = json.loads(
    await client.get("auto_trade:config_manifest:python")
  )
  assert manifest["profile"] == "demo_eval"
  assert manifest["two_sided_range"]
  assert manifest["concurrent_strategies"]
  assert manifest["structural_guard_mode"] == "observe"
  assert manifest["zone_cooldown_enabled"] is False
  assert manifest["zone_reconcile_mode"] == "shadow"
  assert health["state"] == "warning"
  assert health["warnings"] == ["ctrader_manifest_missing"]


def test_config_health_detects_fatal_contract_mismatch():
  python = {
    "candidate_stream": "auto_trade:candidates",
    "redis_database": 0,
    "redis_fingerprint": "same",
    "canonical_symbol": "XAU",
    "pip_size": 0.1,
    "candidate_contract_version": 4,
    "target_plans": [30, 60],
    "range_target_plans": [20, 30],
  }
  ctrader = {
    **python,
    "pip_size": 0.01,
  }
  health = compare_manifests(python, ctrader)
  assert health["state"] == "fatal"
  assert "pip_size" in health["fatal"]


def test_config_health_detects_dry_run_split_brain():
  python = {
    "auto_trade_enabled": True,
    "dry_run": True,
    "manual_algo_enabled": True,
    "manual_algo_dry_run": True,
    "candidate_stream": "auto_trade:candidates",
    "event_stream": "auto_trade:events",
    "redis_database": 0,
    "redis_fingerprint": "same",
    "canonical_symbol": "XAU",
    "pip_size": 0.1,
    "candidate_contract_version": 4,
    "target_plans": [30, 60],
    "range_target_plans": [20, 30],
  }
  ctrader = {**python, "dry_run": False, "manual_algo_dry_run": False}

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "dry_run" in health["fatal"]
  assert "manual_algo_dry_run" in health["warnings"]


def _contract_manifest(**overrides):
  base = {
    "config_manifest_version": 2,
    "auto_trade_enabled": True,
    "dry_run": False,
    "candidate_stream": "auto_trade:candidates",
    "event_stream": "auto_trade:events",
    "redis_database": 0,
    "redis_fingerprint": "same",
    "symbols": ["XAU", "EURUSD"],
    "canonical_symbol": "XAU",
    "pip_size": 0.1,
    "contract_size": 100,
    "candidate_contract_version": 6,
    "target_plans": [30, 60, 90, 120, 200],
    "range_target_plans": [20, 30, 40, 50, 70],
    "range_tp_buffer": 3,
    "candidate_execution_max_age_seconds": 420,
    "candidate_storage_ttl_seconds": 604800,
    "spot_max_age_seconds": 5,
    "require_demo_account": True,
    "account_mode": "demo",
    "broker": "fpmarkets",
    "broker_hedging_capability": True,
    "profile": "demo_eval",
  }
  return {**base, **overrides}


def test_config_contract_normalizes_order_aliases_and_numeric_tokens():
  python = _contract_manifest(
    symbols=["XAU", "EURUSD"],
    range_target_plans=[70, 50, 40, 30, 20, 20],
    range_tp_buffer=3.0,
    contract_size=100.0,
    broker="fpmarkets",
    account_mode="demo_required",
  )
  ctrader = _contract_manifest(
    symbols=["EURUSD", "XAU"],
    range_target_plans=[20, 30, 40, 50, 70],
    range_tp_buffer=3,
    contract_size=100,
    broker="fpmarketssc",
    account_mode="demo",
  )

  health = compare_manifests(python, ctrader)

  assert health["state"] == "healthy"
  assert health["fatal"] == []


def test_config_contract_keeps_genuine_target_difference_fatal():
  python = _contract_manifest(
    range_target_plans=[20, 30, 40, 50, 70],
  )
  ctrader = _contract_manifest(
    range_target_plans=[20, 30, 50, 70],
  )

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "range_target_plans" in health["fatal"]


def test_storage_ttl_is_warning_but_candidate_age_is_fatal():
  python = _contract_manifest(candidate_storage_ttl_seconds=86400)
  ctrader = _contract_manifest(candidate_storage_ttl_seconds=604800)
  health = compare_manifests(python, ctrader)
  assert health["state"] == "healthy"
  assert health["fatal"] == []
  assert "candidate_storage_ttl_seconds" in health["warnings"]

  ctrader["candidate_execution_max_age_seconds"] = 90
  health = compare_manifests(python, ctrader)
  assert health["state"] == "fatal"
  assert "candidate_execution_max_age_seconds" in health["fatal"]


def test_broker_and_account_aliases_are_canonical():
  assert canonicalize_broker("fpmarkets-sc") == "fpmarkets"
  assert canonicalize_broker("FP Markets SC") == "fpmarkets"
  assert canonicalize_account_mode("demo_required") == "demo"
  assert canonicalize_account_mode("demo-only") == "demo"


def test_manifest_canonicalizes_descending_runtime_targets(monkeypatch):
  cfg = _settings(
    monkeypatch,
    AUTO_TRADE_RANGE_TARGETS_PIPS="70,50,40,30,20,20",
  )
  monkeypatch.setattr(config_health, "runtime_config", cfg)
  monkeypatch.setattr(
    config_health,
    "configured_range_targets",
    lambda: (70, 50, 40, 30, 20),
  )

  manifest = python_manifest()

  assert manifest["range_target_plans"] == [20, 30, 40, 50, 70]
  assert manifest["candidate_contract_version"] == 6
  assert manifest["config_manifest_version"] == 2


def test_canonical_environment_agrees_with_legacy_alias(monkeypatch):
  # Canonical + its deprecated alias set to the SAME value resolves cleanly -
  # source_alias_conflict only fires when they actually disagree (see the
  # conflict test below), so an operator mid-migration who sets both to the
  # same value isn't blocked.
  cfg = _settings(
    monkeypatch,
    AUTO_TRADE_CANDIDATE_STREAM="auto_trade:candidates",
    AUTO_TRADE_STREAM="auto_trade:candidates",
    AUTO_TRADE_TARGET_PLANS_PIPS="30,60,90,120,200",
    AUTO_TRADE_TP_PIPS="30,60,90,120,200",
  )

  assert leaf(cfg, "auto_trade_stream") == "auto_trade:candidates"
  assert leaf(cfg, "auto_trade_tp_pips") == "30,60,90,120,200"


def test_canonical_environment_conflicting_with_legacy_alias_fails_closed(monkeypatch):
  # Canonical config catalog hardening: a canonical env var and its own
  # deprecated alias set to DIFFERENT values is an ambiguous operator
  # mistake now made explicit at startup, not silently resolved in
  # canonical's favor.
  with pytest.raises(CanonicalConfigurationError) as excinfo:
    _settings(
      monkeypatch,
      AUTO_TRADE_CANDIDATE_STREAM="canonical:candidates",
      AUTO_TRADE_STREAM="legacy:candidates",
    )
  assert excinfo.value.category == "source_alias_conflict"


@pytest.mark.parametrize(
  ("configured", "expected"),
  [
    ("true", True),
    ("TRUE", True),
    ("1", True),
    ("yes", True),
    ("false", False),
    ("0", False),
    ("NO", False),
  ],
)
def test_python_boolean_parser_accepts_documented_forms(
  monkeypatch,
  configured,
  expected,
):
  cfg = _settings(monkeypatch, AUTO_TRADE_RANGE_FLIP_ENABLED=configured)
  assert leaf(cfg, "auto_trade_range_flip_enabled") is expected


def test_python_boolean_parser_rejects_unknown_value(monkeypatch):
  with pytest.raises(CanonicalConfigurationError) as excinfo:
    _settings(
      monkeypatch,
      AUTO_TRADE_RANGE_FLIP_ENABLED="enable-ish",
    )
  assert excinfo.value.category == "source_parse_error"
