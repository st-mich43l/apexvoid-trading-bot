"""Cross-service ResolvedRuntimeManifest Python tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.configuration.ctrader_option_classification import (
  classification_counts,
)
from app.configuration.runtime_manifest import (
  RuntimeManifestError,
  build_resolved_runtime_manifest,
  load_manifest_file,
  serialize_manifest_bytes,
  write_manifest_atomic,
)


pytestmark = pytest.mark.no_database

_CONFIG = (
  Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
)
_EXAMPLE_MANIFEST = (
  Path(__file__).resolve().parents[2]
  / "contracts" / "configuration" / "runtime-manifest-example.generated.json"
)


def test_classification_has_zero_unclassified():
  counts = classification_counts()
  assert sum(counts.values()) > 0
  # every property is in exactly one bucket
  assert "unclassified" not in counts


def test_manifest_deterministic_and_secret_safe(tmp_path):
  first = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  second = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  assert serialize_manifest_bytes(first) == serialize_manifest_bytes(second)
  assert (
    first["effective_configuration_fingerprint"]
    == second["effective_configuration_fingerprint"]
  )
  blob = serialize_manifest_bytes(first).decode("utf-8")
  for sentinel in (
    "DO_NOT_LEAK_CTRADER_SECRET",
    "DO_NOT_LEAK_TELEGRAM_TOKEN",
    "DO_NOT_LEAK_DATABASE_PASSWORD",
    "CTRADER_CLIENT_SECRET",
    "TELEGRAM_BOT_TOKEN",
  ):
    assert sentinel not in blob
  assert first["instruments"]["XAU"]["identity"]["rollout"] == "live"
  assert first["live_instruments"] == ["EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU"]
  out = tmp_path / "resolved-runtime.json"
  write_manifest_atomic(first, out)
  loaded = load_manifest_file(out)
  assert (
    loaded["effective_configuration_fingerprint"]
    == first["effective_configuration_fingerprint"]
  )


def test_checked_in_example_snapshots_all_effective_instruments():
  """Keep cleanup changes from altering resolved runtime settings."""
  actual = serialize_manifest_bytes(
    build_resolved_runtime_manifest(config_file=str(_CONFIG))
  )
  assert actual == _EXAMPLE_MANIFEST.read_bytes()


def test_changed_trading_value_changes_fingerprint(monkeypatch):
  base = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  monkeypatch.setenv("AUTO_TRADE_MAX_SPREAD_PIPS", "9")
  # rebuild uses os.environ via source bundle
  changed = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  assert (
    changed["effective_configuration_fingerprint"]
    != base["effective_configuration_fingerprint"]
  )
  assert changed["auto_trade"]["max_spread_pips"] == 9


def test_unsupported_version_rejected(tmp_path):
  payload = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  payload["manifest_version"] = 99
  path = tmp_path / "bad.json"
  path.write_text(json.dumps(payload), encoding="utf-8")
  with pytest.raises(RuntimeManifestError, match="unsupported"):
    load_manifest_file(path)


def test_manifest_v2_instrument_runtimes_present():
  payload = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  assert payload["manifest_version"] == 2
  assert "instrument_runtimes" in payload
  assert payload["instrument_runtimes"]["XAU"]["rollout"] == "live"
  assert payload["instrument_runtimes"]["XAU"]["manual"] == {
    "enabled": True,
    "algo_enabled": True,
    "entry_mode": "zone_ladder",
    "risk_reference": "shallow",
    "risk_multiplier": "1",
    "target_close_ratios": [],
    "target_r_multiples": [],
    "tp1_close_fraction": "0.4",
  }
  assert payload["instrument_runtimes"]["EURUSD"]["manual"] == {
    "enabled": True,
    "algo_enabled": True,
    "entry_mode": "single",
    "risk_reference": "shallow",
    "risk_multiplier": "1.5",
    "target_close_ratios": ["0.25", "0.25", "0.5"],
    "target_r_multiples": [],
    "tp1_close_fraction": None,
  }


def test_xau_units_and_targets_parity_shape():
  payload = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  units = payload["instruments"]["XAU"]["units"]
  assert units["pip_size"] == "0.1"
  assert units["price_digits"] == 2
  assert units["contract_units_per_lot"] == "100"
  assert payload["auto_trade"]["targets_pips"] == [30, 60, 90, 120, 200]
  assert payload["auto_trade"]["target_weights"] == [20, 20, 20, 20, 20]
  assert payload["feed"]["ctrader_symbol"] == "XAUUSD"
  assert payload["feed"]["redis_symbol"] == "XAU"
