"""CONFIG_FILE source, instrument registry, and XAU projection tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.configuration.config_file import ConfigFileError
from app.configuration.config_file import load_config_file
from app.configuration.deployment_contract import deployment_contract_document
from app.configuration.fingerprints import configuration_contract_fingerprint
from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.instruments import InstrumentsConfig
from app.configuration.resolver import resolve_configuration
from app.configuration.source_types import SourceKind


pytestmark = pytest.mark.no_database


_XAU_BLOCK = {
  "enabled": True,
  "canonical_symbol": "XAU",
  "broker_symbol": "XAUUSD",
  "timeframes": ["H1", "M15", "M5", "M1"],
  "contract": {
    "pip_size": 0.1,
    "contract_units_per_lot": 100.0,
    "price_digits": 2,
  },
  "market_data": {
    "lookbacks": {
      "h1_bars": 400,
      "m15_bars": 250,
      "m5_bars": 150,
      "m1_bars": 150,
    },
  },
  "analysis": {
    "zones": {
      "minimum_width_price": 3.0,
      "preferred_minimum_width_price": 3.0,
      "preferred_maximum_width_price": 6.0,
      "major_maximum_width_price": 10.0,
    },
  },
}


def _write_yaml(tmp_path: Path, payload: dict) -> Path:
  path = tmp_path / "trading-bot.yml"
  path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
  return path


def _resolve(**overrides):
  values = {
    "init_values": {},
    "process_environment": {},
    "dotenv_values": {},
    "file_secret_values": {},
    "config_file_values": {},
    "instruments": {},
  }
  values.update(overrides)
  return resolve_configuration(**values)


def test_env_only_xau_parity_hydrates_instruments():
  result = _resolve()
  assert result.flat_values["contract.instrument.pip_size"] == 0.1
  assert result.flat_values["market_data.lookbacks.h1_bars"] == 400
  assert result.flat_values["analysis.zones.symbol_contract.major_maximum_width_price"] == 10.0
  assert "XAU" in result.instruments
  assert result.instruments["XAU"].contract.pip_size == 0.1


def test_valid_yaml_projects_xau_leaves(tmp_path: Path):
  path = _write_yaml(tmp_path, {
    "version": 1,
    "instruments": {
      "XAU": {**_XAU_BLOCK, "contract": {**_XAU_BLOCK["contract"], "pip_size": 0.05}},
      "EUR": {
        "enabled": False,
        "canonical_symbol": "EUR",
        "broker_symbol": "EURUSD",
      },
    },
  })
  loaded = load_config_file(path, missing_ok=False)
  assert loaded.flat_values["contract.instrument.pip_size"] == 0.05
  assert set(loaded.instruments.root) == {"XAU", "EUR"}
  result = _resolve(
    config_file_values=loaded.flat_values,
    instruments=loaded.instruments.root,
  )
  assert result.flat_values["contract.instrument.pip_size"] == 0.05
  source = result.trace.by_path()["contract.instrument.pip_size"]
  assert source.source_kind is SourceKind.CONFIG_FILE
  assert result.instruments["EUR"].enabled is False


def test_unknown_top_level_key_fails(tmp_path: Path):
  path = _write_yaml(tmp_path, {"version": 1, "not_a_group": {}})
  with pytest.raises(ConfigFileError, match="unknown top-level"):
    load_config_file(path, missing_ok=False)


def test_unknown_nested_path_fails(tmp_path: Path):
  path = _write_yaml(tmp_path, {"runtime": {"not_a_field": True}})
  with pytest.raises(ConfigFileError, match="unknown configuration path"):
    load_config_file(path, missing_ok=False)


def test_secret_leaf_in_yaml_rejected(tmp_path: Path):
  path = _write_yaml(tmp_path, {
    "bootstrap": {"telegram": {"bot_token": "secret-token"}},
  })
  with pytest.raises(ConfigFileError, match="secret leaf"):
    load_config_file(path, missing_ok=False)


def test_malformed_yaml_fails_closed(tmp_path: Path):
  path = tmp_path / "bad.yml"
  path.write_text(":\n  - not yaml\n  [[", encoding="utf-8")
  with pytest.raises(ConfigFileError, match="malformed YAML"):
    load_config_file(path, missing_ok=False)


def test_missing_explicit_config_file_fails(tmp_path: Path):
  with pytest.raises(ConfigFileError, match="does not exist"):
    load_config_file(tmp_path / "missing.yml", missing_ok=False)


def test_absent_config_file_is_empty_layer():
  loaded = load_config_file(None)
  assert loaded.flat_values == {}
  assert loaded.instruments.root == {}


def test_process_env_overrides_yaml_instrument_projection(tmp_path: Path):
  path = _write_yaml(tmp_path, {"version": 1, "instruments": {"XAU": _XAU_BLOCK}})
  loaded = load_config_file(path, missing_ok=False)
  result = _resolve(
    config_file_values=loaded.flat_values,
    instruments=loaded.instruments.root,
    process_environment={"AUTO_TRADE_XAU_PIP_SIZE": "0.2"},
  )
  assert result.flat_values["contract.instrument.pip_size"] == 0.2
  assert result.trace.by_path()["contract.instrument.pip_size"].source_kind is (
    SourceKind.PROCESS_ENV
  )
  assert any(
    warning.code == "instrument_registry_leaf_conflict"
    for warning in result.warnings
  )

def test_init_overrides_env_and_yaml(tmp_path: Path):
  path = _write_yaml(tmp_path, {"version": 1, "instruments": {"XAU": _XAU_BLOCK}})
  loaded = load_config_file(path, missing_ok=False)
  result = _resolve(
    config_file_values=loaded.flat_values,
    instruments=loaded.instruments.root,
    process_environment={"AUTO_TRADE_XAU_PIP_SIZE": "0.2"},
    init_values={"contract.instrument.pip_size": 0.3},
  )
  assert result.flat_values["contract.instrument.pip_size"] == 0.3
  assert result.trace.by_path()["contract.instrument.pip_size"].source_kind is (
    SourceKind.INIT_VALUE
  )


def test_profile_assignment_still_applies_with_config_file():
  result = _resolve(
    dotenv_values={"AUTO_TRADE_PROFILE": "demo_eval"},
    config_file_values={"runtime.auto_trade.enabled": False},
  )
  assert result.profile == "demo_eval"
  # profile assignments may set enabled; config_file can override profile non-explicit
  assert result.trace.by_path()["runtime.auto_trade.enabled"].source_kind in {
    SourceKind.CONFIG_FILE,
    SourceKind.PROFILE,
  }


def test_deprecated_xau_env_aliases_still_resolve():
  result = _resolve(process_environment={
    "AUTO_TRADE_PIP_SIZE": "0.02",
    "XAU_LOOKBACK_H1_BARS": "420",
  })
  assert result.flat_values["contract.instrument.pip_size"] == 0.02
  assert result.flat_values["market_data.lookbacks.h1_bars"] == 420
  assert any(warning.code == "deprecated_alias" for warning in result.warnings)


def test_duplicate_broker_symbols_fail():
  with pytest.raises(Exception, match="duplicate broker_symbol"):
    InstrumentsConfig.model_validate({
      "XAU": {**_XAU_BLOCK, "broker_symbol": "SAME"},
      "EUR": {
        "enabled": True,
        "canonical_symbol": "EUR",
        "broker_symbol": "SAME",
        "contract": _XAU_BLOCK["contract"],
      },
    })


def test_enabled_instrument_requires_contract():
  with pytest.raises(Exception, match="require contract"):
    InstrumentsConfig.model_validate({
      "XAU": {
        "enabled": True,
        "canonical_symbol": "XAU",
        "broker_symbol": "XAUUSD",
      },
    })


def test_unsupported_timeframe_rejected():
  with pytest.raises(Exception, match="unsupported instrument timeframes"):
    InstrumentsConfig.model_validate({
      "XAU": {**_XAU_BLOCK, "timeframes": ["H1", "W1"]},
    })


def test_yaml_vs_env_only_leaf_parity_for_defaults(tmp_path: Path):
  path = _write_yaml(tmp_path, {"version": 1, "instruments": {"XAU": _XAU_BLOCK}})
  loaded = load_config_file(path, missing_ok=False)
  from_yaml = _resolve(
    config_file_values=loaded.flat_values,
    instruments=loaded.instruments.root,
  )
  from_env = _resolve()
  for leaf in (
    "contract.instrument.pip_size",
    "contract.instrument.contract_units_per_lot",
    "contract.instrument.price_digits",
    "market_data.lookbacks.h1_bars",
    "market_data.lookbacks.m15_bars",
    "market_data.lookbacks.m5_bars",
    "market_data.lookbacks.m1_bars",
    "analysis.zones.symbol_contract.minimum_width_price",
    "analysis.zones.symbol_contract.preferred_minimum_width_price",
    "analysis.zones.symbol_contract.preferred_maximum_width_price",
    "analysis.zones.symbol_contract.major_maximum_width_price",
  ):
    assert from_yaml.flat_values[leaf] == from_env.flat_values[leaf]


def test_deployment_contract_is_deterministic_and_secret_safe():
  entries = iter_catalog_entries()
  fingerprint = configuration_contract_fingerprint(entries)
  first = deployment_contract_document(entries, contract_fingerprint=fingerprint)
  second = deployment_contract_document(entries, contract_fingerprint=fingerprint)
  assert first == second
  assert first["contract_version"] == 1
  assert "TELEGRAM_BOT_TOKEN" in first["secret_environment"]
  assert "APEXVOID_CONFIG_FILE" in first["bootstrap_environment"]
  assert "config_file" in first["source_precedence"]
  assert first["deprecated_environment_aliases"]["AUTO_TRADE_XAU_PIP_SIZE"].startswith(
    "instruments.XAU"
  )
