"""Tests for instrument pack merge and live symbol helpers."""

from pathlib import Path

import pytest
import yaml

from app.configuration.config_file import load_config_file
from app.configuration.instrument_packs import (
  expand_instrument_declarations,
  live_instrument_symbol_csv,
)
from app.configuration.models.instruments import resolve_manual_profile


pytestmark = pytest.mark.no_database

_CONFIG_FILE = (
  Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
)


def test_expand_instrument_pack_merges_defaults_and_overrides():
  packs = {
    "fx_usd_major_v1": {
      "policy": "fx_fixed_2r_v1",
      "reaction_session": "london_ny",
      "targeting": {
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": [1.0, 2.0],
        "close_ratios": [0.5, 0.5],
        "breakeven_after_r": 1.0,
        "entry_clips": 2,
      },
      "manual": {
        "enabled": True,
        "algo_enabled": True,
        "entry_mode": "single",
        "risk_reference": "shallow",
        "risk_multiplier": 1.5,
        "target_close_ratios": [0.25, 0.25, 0.50],
      },
      "stop_envelope": {"min_pips": 10, "max_pips": 18, "sl_distance": 0.0018},
      "activation": {
        "require_sweep_body": True,
        "trigger_maximum_age_bars": 3,
        "max_spread_pips": 1,
      },
      "price_scale": {
        "round_step": 0.001,
        "market_map": {
          "change_min": 0.0001,
          "fallback_radius_price": 0.01,
          "scalp_radius_price": 0.006,
        },
        "zone_merge_gap_price": 0.0005,
        "zone_merge_max_width": 0.0015,
        "opposing_minimum_separation_price": 0.0015,
        "fvg_entry_max_width_price": 0.0015,
      },
      "contract": {
        "contract_units_per_lot": 100000.0,
        "max_lots": 10.0,
        "pip_size": 0.0001,
        "price_digits": 5,
        "volume_units_per_lot": 10000000,
      },
      "analysis": {
        "zones": {
          "major_maximum_width_price": 0.003,
          "minimum_width_price": 0.0006,
          "preferred_maximum_width_price": 0.0015,
          "preferred_minimum_width_price": 0.0008,
        },
      },
      "market_data": {
        "lookbacks": {
          "h1_bars": 400,
          "m15_bars": 250,
          "m1_bars": 150,
          "m5_bars": 150,
        },
      },
      "timeframes": ["H1", "M15", "M5", "M1"],
    },
  }
  expanded = expand_instrument_declarations(
    {
      "TESTUSD": {
        "pack": "fx_usd_major_v1",
        "broker_symbol": "TESTUSD",
        "canonical_symbol": "TESTUSD",
        "rollout": "live",
      },
    },
    packs,
  )
  body = expanded["TESTUSD"]
  assert body["policy"] == "fx_fixed_2r_v1"
  assert body["broker_symbol"] == "TESTUSD"
  assert body["stop_envelope"]["min_pips"] == 10
  assert body["manual"] == {
    "enabled": True,
    "algo_enabled": True,
    "entry_mode": "single",
    "risk_reference": "shallow",
    "risk_multiplier": 1.5,
    "target_close_ratios": [0.25, 0.25, 0.50],
  }
  assert "pack" not in body


@pytest.mark.parametrize(
  "field,value",
  [
    ("rollout", "live"),
    ("enabled", True),
    ("canonical_symbol", "EURUSD"),
    ("broker_symbol", "EURUSD"),
    ("aliases", ["EUR/USD"]),
  ],
)
def test_instrument_pack_rejects_identity_and_rollout_fields(field, value):
  with pytest.raises(ValueError, match="per-instrument fields"):
    expand_instrument_declarations(
      {
        "TESTUSD": {
          "pack": "unsafe",
          "rollout": "disabled",
          "canonical_symbol": "TESTUSD",
          "broker_symbol": "TESTUSD",
        },
      },
      {"unsafe": {field: value}},
    )


@pytest.mark.parametrize(
  "declaration",
  [
    {
      "pack": "fx",
      "canonical_symbol": "AUDUSD",
      "broker_symbol": "AUDUSD",
    },
    {
      "pack": "fx",
      "rollout": None,
      "canonical_symbol": "AUDUSD",
      "broker_symbol": "AUDUSD",
    },
  ],
  ids=("missing", "null"),
)
def test_packed_instrument_requires_explicit_rollout(declaration):
  with pytest.raises(ValueError, match="has no explicit rollout"):
    expand_instrument_declarations(
      {"AUDUSD": declaration},
      {"fx": {"policy": "fx_fixed_2r_v1"}},
    )


def test_production_config_instrument_packs_parse():
  loaded = load_config_file(_CONFIG_FILE)
  assert loaded.instruments.get("EURUSD") is not None
  assert loaded.instruments.get("GBPUSD") is not None


def test_live_instrument_symbol_csv_includes_xau():
  csv = live_instrument_symbol_csv(
    {
      "EURUSD": {"enabled": True, "rollout": "live", "canonical_symbol": "EURUSD",
                 "broker_symbol": "EURUSD", "contract": {"pip_size": 0.0001,
                 "contract_units_per_lot": 100000, "price_digits": 5,
                 "volume_units_per_lot": 10000000}},
    }
  )
  assert "EURUSD" in csv.split(",")
  assert csv.split(",")[0] == "XAU"


def test_load_config_file_syncs_legacy_symbol_csvs_from_live_rollout(tmp_path):
  """Go-live is instruments.*.rollout=live; stale CSV leaves must not win."""
  path = tmp_path / "trading-bot.yml"
  path.write_text(
    """
version: 1
contract:
  instrument:
    symbols: "XAU"
market_data:
  scanner:
    symbols: "XAU"
instruments:
  XAU:
    enabled: true
    rollout: live
    canonical_symbol: XAU
    broker_symbol: XAUUSD
    contract:
      pip_size: 0.1
      contract_units_per_lot: 100.0
      price_digits: 2
  EURUSD:
    enabled: true
    rollout: live
    canonical_symbol: EURUSD
    broker_symbol: EURUSD
    policy: fx_fixed_2r_v1
    reaction_session: london_ny
    targeting:
      mode: fixed_rr
      reward_risk: 2.0
      target_r_multiples: [1.0, 2.0]
      close_ratios: [0.5, 0.5]
      breakeven_after_r: 1.0
      entry_clips: 2
    stop_envelope: {min_pips: 10, max_pips: 18, sl_distance: 0.0018}
    activation:
      require_sweep_body: true
      trigger_maximum_age_bars: 3
      max_spread_pips: 1
    price_scale:
      round_step: 0.001
      market_map:
        change_min: 0.0001
        fallback_radius_price: 0.01
        scalp_radius_price: 0.006
      zone_merge_gap_price: 0.0005
      zone_merge_max_width: 0.0015
      opposing_minimum_separation_price: 0.0015
      fvg_entry_max_width_price: 0.0015
    contract:
      pip_size: 0.0001
      contract_units_per_lot: 100000.0
      price_digits: 5
      volume_units_per_lot: 10000000
""",
    encoding="utf-8",
  )
  loaded = load_config_file(path, missing_ok=False)
  symbols = loaded.flat_values["contract.instrument.symbols"].split(",")
  scanner = loaded.flat_values["market_data.scanner.symbols"].split(",")
  assert "EURUSD" in symbols
  assert "XAU" in symbols
  assert symbols == scanner


def test_synthetic_audusd_onboarding_inherits_manual_profile_and_symbols(
  tmp_path,
  monkeypatch,
):
  """A new FX major is one declaration plus the reusable instrument pack."""
  raw = yaml.safe_load(_CONFIG_FILE.read_text(encoding="utf-8"))
  raw["instruments"]["AUDUSD"] = {
    "pack": "fx_usd_major_fixed_2r_v1",
    "rollout": "live",
    "broker_symbol": "AUDUSD",
    "canonical_symbol": "AUDUSD",
    "aliases": ["AUD/USD"],
  }
  path = tmp_path / "trading-bot.yml"
  path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

  loaded = load_config_file(path, missing_ok=False)
  audusd = loaded.instruments.get("AUDUSD")
  assert audusd is not None
  manual = resolve_manual_profile("AUDUSD", audusd)
  assert manual.enabled is True
  assert manual.algo_enabled is True
  assert manual.entry_mode.value == "single"
  assert manual.risk_reference.value == "shallow"
  assert manual.risk_multiplier == 1.5
  assert manual.target_close_ratios == (0.25, 0.25, 0.50)
  assert "AUDUSD" in loaded.flat_values[
    "contract.instrument.symbols"
  ].split(",")
  assert "AUDUSD" in loaded.flat_values[
    "market_data.scanner.symbols"
  ].split(",")

  # The same one-block declaration must reach the owner command/parser and
  # candidate contract; onboarding is not complete if it only changes CSVs.
  from app.configuration.python_loader import load_python_canonical_settings
  from app.configuration.python_sources import load_python_runtime_source_bundle
  from app.configuration.source_policy import PythonConfigurationSourcePolicy
  from app.core import symbols as symbol_module
  from app.signals import fx_manual_algo, manual_execution, parsing
  from app.signals.manual_intent import ManualTradeIntent

  config = load_python_canonical_settings(
    load_python_runtime_source_bundle(
      policy=PythonConfigurationSourcePolicy(config_file=str(path)),
    ),
  ).config
  for module in (
    symbol_module,
    fx_manual_algo,
    manual_execution,
    parsing,
  ):
    monkeypatch.setattr(module, "runtime_config", config)

  assert symbol_module.resolve_command_symbol("AUD/USD") == "AUDUSD"
  parsed = parsing._parse_manual("AUDUSD buy 0.65000 / algo")
  assert parsed is not None
  assert parsed["symbol"] == "AUDUSD"
  assert parsed["execution_mode"] == "algo"
  intent = ManualTradeIntent(
    intent_id="manual:999:0",
    manual_signal_id=999,
    revision=0,
    direction=parsed["action"],
    symbol=parsed["symbol"],
    entry_low=parsed["entry"],
    entry_high=parsed["entry_end"],
    sl=parsed["sl"],
    tps=tuple(parsed["tps"]),
    created_at=1_800_000_000,
    expires_at=None,
    setup_type=parsed["setup_type"],
    confluence=parsed["confluence"],
    execution_mode=parsed["execution_mode"],
  )
  candidate = manual_execution._intent_to_candidate_payload(intent)
  assert candidate["symbol"] == "AUDUSD"
  assert candidate["manual_single_entry"] is True
  # Default FX /algo TPs follow targeting.target_r_multiples (1R/2R → two
  # TPs). Explicit manual.target_close_ratios still applies when TP count
  # matches; with two TPs the equal split is used.
  assert candidate["manual_target_weights"] == [50, 50]
  assert candidate["risk_multiplier"] == 1.5
