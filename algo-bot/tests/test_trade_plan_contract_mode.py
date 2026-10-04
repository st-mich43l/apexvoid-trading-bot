"""Current TradePlan contract-mode configuration health.

Python and C# must agree on contract mode, TradePlan version, and the
execution stream name before either side trusts a TradePlan plan; a mismatch is
a fatal config-health finding, not a warning.
"""

from __future__ import annotations

import pytest

from app.autotrade.config_health import CONTRACT_MODES, compare_manifests, python_manifest
from app.autotrade.trade_plan import TRADE_PLAN_VERSION
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle
from app.core.config import runtime_config

pytestmark = pytest.mark.no_database

_SAFE = {
  "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "apexvoid",
}


def _load(**extra):
  return load_python_canonical_settings(ConfigurationSourceBundle(
    process_environment={**_SAFE, **extra},
  ))


def test_default_contract_mode_is_v8_only():
  assert runtime_config.contract.mode == "v8_only"


def test_contract_mode_rejects_unknown_value():
  with pytest.raises(CanonicalConfigurationError):
    _load(AUTO_TRADE_CONTRACT_MODE="definitely_not_a_mode")


def test_legacy_v6_mode_value_is_rejected_not_silently_ignored():
  with pytest.raises(CanonicalConfigurationError):
    _load(AUTO_TRADE_CONTRACT_MODE="legacy_v6")


@pytest.mark.parametrize("mode", CONTRACT_MODES)
def test_every_documented_mode_is_accepted(mode):
  result = _load(AUTO_TRADE_CONTRACT_MODE=mode)
  assert result.config.contract.mode == mode


def test_manifest_carries_trade_plan_version_and_contract_mode():
  manifest = python_manifest()
  assert manifest["trade_plan_version"] == TRADE_PLAN_VERSION
  assert manifest["contract_mode"] in CONTRACT_MODES
  assert manifest["trade_plan_stream"] == "execution:trade_plans"

def test_contract_mode_mismatch_is_fatal_not_a_warning():
  python = python_manifest()
  ctrader = dict(python)
  ctrader["contract_mode"] = "v8_only"
  python["contract_mode"] = "legacy_v6"

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "contract_mode" in health["fatal"]


def test_trade_plan_version_mismatch_is_fatal():
  python = python_manifest()
  ctrader = dict(python)
  ctrader["trade_plan_version"] = 6

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "trade_plan_version" in health["fatal"]


def test_trade_plan_stream_mismatch_is_fatal():
  python = python_manifest()
  ctrader = dict(python)
  ctrader["trade_plan_stream"] = "execution:trade_plans_v2"

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "trade_plan_stream" in health["fatal"]


def test_matching_contract_fields_do_not_trigger_v8_fatal_reasons():
  # required_strategy_key_missing entries are a pre-existing, unrelated
  # fatal reason in a bare test environment (no strategy-enable env vars
  # set) - this test only asserts the *new* TradePlan fields stay quiet when both
  # sides agree, not that the whole manifest comparison is clean.
  python = python_manifest()
  ctrader = dict(python)

  health = compare_manifests(python, ctrader)

  assert "contract_mode" not in health["fatal"]
  assert "trade_plan_version" not in health["fatal"]
  assert "trade_plan_stream" not in health["fatal"]
