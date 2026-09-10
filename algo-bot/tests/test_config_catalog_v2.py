"""Catalog V2 and evergreen configuration governance tests."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")
os.environ.setdefault("POSTGRES_PASSWORD", "apexvoid")
os.environ.setdefault(
  "DATABASE_URL",
  "postgresql://apexvoid:apexvoid@localhost:55432/signals",
)

from app.configuration.catalog import iter_catalog_entries, infer_ctrader_type
from app.configuration.catalog_validation import validate_active_catalog
from app.configuration.configuration_integrity_gate import (
  evaluate_configuration_integrity,
)
from app.configuration.fingerprints import (
  catalog_fingerprint,
  configuration_contract_fingerprint,
  configuration_document_fingerprint,
)
from app.configuration.generate import CATALOG_VERSION, REPOSITORY_ROOT, render_artifacts
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle
from app.core.config import runtime_config


pytestmark = pytest.mark.no_database


BASELINE = {
  # 2026-09 Candle Confirmation V2: +33 new leaf fields under
  # analysis.candle_confirmation.* (enabled, version, rejection.*,
  # displacement.*, sequences.*, synergy.*), all ConfigOwner.PYTHON,
  # ConfigKind.ALGORITHM_CONSTANT (canonical_env=None, shadow-only, no
  # new environment surface), none deprecated-alias'd.
  # 2026-09 MAD v2: +7 new leaf fields (execution.mad.expand.break_atr,
  # .displacement_atr, .accept_closes, execution.mad.manip.min_penetration_atr,
  # .min_reclaim_atr, execution.mad.accum.minimum_rq, .maximum_rq), all
  # ConfigOwner.PYTHON, none deprecated-alias'd.
  "entries": 620,
  "configurable": 520,
  "protocol": 10,
  "algorithm": 90,
  "owners": {"python": 475, "shared": 96, "ctrader": 49},
  "projection": 571,
  "env": 520,
  "deprecated_aliases": 21,
}

# Paths removed from the live catalog after the v1 parity snapshot was frozen.
# Historical leaf_types still list them; skip rather than rewriting history.
_INTENTIONAL_POST_V1_REMOVED_PATHS = frozenset({
  "analysis.measurements.regime_chop_alert_share",
  "strategies.trend.pullback_enabled",
})


def _load(**env: str):
  process = {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "POSTGRES_PASSWORD": "apexvoid",
    "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:55432/signals",
    **env,
  }
  return load_python_canonical_settings(
    ConfigurationSourceBundle(
      process_environment=process,
      dotenv_values={},
      file_secret_values={},
      init_values={},
    )
  )


def test_catalog_v2_uses_canonical_paths_as_identity():
  entries = iter_catalog_entries()
  assert all(entry.path for entry in entries)
  assert len({entry.path for entry in entries}) == len(entries)
  assert all(entry.display_id == f"config:{entry.path}" for entry in entries)


def test_catalog_v2_has_no_item_id():
  for entry in iter_catalog_entries():
    assert "item_id" not in entry.__dataclass_fields__
    assert "item_id" not in entry.as_dict()


def test_catalog_v2_has_no_legacy_attr():
  for entry in iter_catalog_entries():
    assert "legacy_attr" not in entry.__dataclass_fields__
    assert "legacy_attr" not in entry.as_dict()


def test_catalog_v2_has_no_derived_legacy_properties():
  import app.configuration.catalog as catalog
  assert not hasattr(catalog, "DERIVED_LEGACY_PROPERTIES")
  assert not hasattr(catalog, "DerivedLegacyProperty")


def test_active_descriptions_have_no_migration_language():
  validate_active_catalog()


def test_type_inference_does_not_use_legacy_ids():
  source = Path(
    REPOSITORY_ROOT / "algo-bot/app/configuration/catalog.py"
  ).read_text()
  assert "python.settings." not in source
  assert "ctrader.env." not in source
  assert "hardcoded." not in source


def test_catalog_entry_count_unchanged():
  assert len(iter_catalog_entries()) == BASELINE["entries"]


def test_configurable_count_unchanged():
  assert sum(1 for e in iter_catalog_entries() if e.configurable) == BASELINE["configurable"]


def test_owner_counts_unchanged():
  from collections import Counter
  counts = Counter(e.owner for e in iter_catalog_entries())
  assert dict(counts) == BASELINE["owners"]


def test_python_projection_count_unchanged():
  assert len(iter_catalog_entries(PythonRuntimeConfig)) == BASELINE["projection"]


def test_environment_contract_count_unchanged():
  from app.configuration.environment_contract import iter_environment_contract_entries
  assert len(iter_environment_contract_entries()) == BASELINE["env"]


def test_deprecated_alias_count_unchanged():
  total = sum(len(e.deprecated_aliases) for e in iter_catalog_entries())
  assert total == BASELINE["deprecated_aliases"]


def test_profile_assignments_unchanged():
  from app.configuration.profiles import PROFILES
  assert set(PROFILES) == {"conservative", "demo_eval"}
  assert len(PROFILES["conservative"].assignments) == 0
  assert len(PROFILES["demo_eval"].assignments) == 48


def test_all_resolved_values_unchanged():
  first = _load().config.model_dump()
  second = _load().config.model_dump()
  assert first == second
  hist = json.loads((
    REPOSITORY_ROOT
    / "docs/configuration/history/artifacts"
    / "catalog-v1-parity-before-v2.historical.json"
  ).read_text())
  for path, expected_type in hist["leaf_types"].items():
    if path in _INTENTIONAL_POST_V1_REMOVED_PATHS:
      continue
    cur = _load().config
    for part in path.split("."):
      cur = getattr(cur, part)
    assert type(cur).__name__ == expected_type, path


def test_all_resolved_types_unchanged():
  hist = json.loads((
    REPOSITORY_ROOT
    / "docs/configuration/history/artifacts"
    / "catalog-v1-parity-before-v2.historical.json"
  ).read_text())
  result = _load()
  for path, expected in hist["leaf_types"].items():
    if path in _INTENTIONAL_POST_V1_REMOVED_PATHS:
      continue
    cur = result.config
    for part in path.split("."):
      cur = getattr(cur, part)
    assert type(cur).__name__ == expected, path


def test_source_precedence_unchanged():
  dotenv = _load()
  assert dotenv.config.bootstrap.logging.level == "INFO"
  process = _load(LOG_LEVEL="WARNING")
  assert process.config.bootstrap.logging.level == "WARNING"


def test_alias_warning_behavior_unchanged():
  result = _load(
    AUTO_TRADE_TARGET_PLANS_PIPS="15,30,45",
    AUTO_TRADE_TP_PIPS="15,30,45",
  )
  assert result.success
  assert any(w.code == "deprecated_alias" for w in result.warnings)


def test_alias_conflict_behavior_unchanged():
  with pytest.raises(CanonicalConfigurationError):
    _load(
      AUTO_TRADE_TARGET_PLANS_PIPS="15,30,45",
      AUTO_TRADE_TP_PIPS="20,40,60",
    )


def test_required_input_behavior_unchanged():
  with pytest.raises(CanonicalConfigurationError) as exc:
    load_python_canonical_settings(ConfigurationSourceBundle(
      process_environment={},
      dotenv_values={},
    ))
  assert exc.value.category == "missing_required_input"


def test_validation_categories_unchanged():
  with pytest.raises(CanonicalConfigurationError) as exc:
    _load(AUTO_TRADE_PROFILE="not-a-real-profile")
  assert exc.value.category in {
    "missing_required_input",
    "validation_error",
    "source_conflict",
    "unsupported_profile",
    "source_parse_error",
  }


def test_generator_contains_no_phase_migration_builders():
  source = (
    REPOSITORY_ROOT / "algo-bot/app/configuration/generate.py"
  ).read_text()
  for symbol in (
    "_legacy_artifact",
    "_consumer_migration_artifact",
    "PHASE_2E_ROOTS",
    "DERIVED_LEGACY_PROPERTIES",
    "phase2i",
  ):
    assert symbol not in source


def test_generator_emits_only_evergreen_artifacts():
  rendered = {str(path) for path in render_artifacts()}
  assert "contracts/configuration/configuration-architecture.generated.json" in rendered
  assert not any("phase-2i" in path for path in rendered)
  assert not any("legacy-map" in path for path in rendered)


def test_duplicate_phase2i_artifacts_are_not_generated():
  rendered = {str(path) for path in render_artifacts()}
  assert "contracts/configuration/canonical-only-surface-phase-2i-b.generated.json" not in rendered
  assert "contracts/configuration/canonical-only-surface-phase-2i-final.generated.json" not in rendered


def test_historical_artifacts_are_not_runtime_dependencies():
  for path in (REPOSITORY_ROOT / "algo-bot/app").rglob("*.py"):
    text = path.read_text()
    assert "docs/configuration/history/artifacts" not in text


def test_active_catalog_validation_reports_success():
  validate_active_catalog()


def test_configuration_integrity_gate_reports_ok():
  result = evaluate_configuration_integrity(REPOSITORY_ROOT)
  assert result["status"] == "CONFIGURATION_INTEGRITY_OK", result["blockers"]


def test_generated_artifacts_are_current():
  for relative, expected in render_artifacts().items():
    actual = (REPOSITORY_ROOT / relative).read_bytes()
    assert actual == expected, relative


def test_runtime_root_remains_python_runtime_config():
  assert type(runtime_config) is PythonRuntimeConfig


def test_canonical_startup_remains_fail_closed():
  with pytest.raises(CanonicalConfigurationError):
    load_python_canonical_settings(ConfigurationSourceBundle(process_environment={}))
  assert _load().success is True


def test_catalog_version_is_two():
  assert CATALOG_VERSION == 2
  assert all(e.catalog_version == 2 for e in iter_catalog_entries())


def test_contract_and_document_fingerprints_differ():
  contract = configuration_contract_fingerprint()
  document = configuration_document_fingerprint()
  assert contract == catalog_fingerprint()
  assert contract != document


# Defaults deliberately changed after the v1 snapshot was frozen - each one
# is a documented, evidenced behavior fix, not a silent v1->v2 migration
# drift. The frozen historical file is never edited to match; this table is
# the record of intentional post-v1 divergence.
_INTENTIONAL_POST_V1_DEFAULT_CHANGES = {
  # 04 Aug 2026 incident: a major breaker/flip demand zone still being
  # actively retested lost its opposing-barrier status after its 2nd
  # touch (max_touches=2), letting a SELL through with no real room-check
  # against it - see analysis.py's max_touches config_field description.
  "analysis.market_map.max_touches",
  # Contract surface moved to V8-only after TradePlan V8 cutover.
  "contract.mode",
  "contract.versions.trade_plan",
  # Pre-existing live defaults already shipped before HFS quality work.
  "execution.entry.poll_ms",
  "execution.reaction.market_fraction",
  "execution.reaction.scale_fraction",
  "execution.stops.reaction.room_floor_pips",
  "execution.zone_scaling.first_leg_fraction",
  "risk.tiers.b_multiplier",
  # The range risk cap was reduced to the current conservative value after
  # the v1 parity snapshot was frozen.
  "risk.sizing.range_max_risk_multiplier",
  # 12 Aug 2026 HFS quality dig: Impulse bleed on late chase / wide stops /
  # mid-range location; tighten chase and pullback location gates.
  "strategies.scalping.activation.maximum_chase_pips",
  "strategies.scalping.stop.maximum_pips",
  "strategies.scalping.location.pullback_buy_maximum_position",
  "strategies.scalping.location.pullback_sell_minimum_position",
}


def test_entry_behavior_types_match_v1_parity():
  hist = {
    e["path"]: e
    for e in json.loads((
      REPOSITORY_ROOT
      / "docs/configuration/history/artifacts"
      / "catalog-v1-parity-before-v2.historical.json"
    ).read_text())["entry_behavior"]
  }
  for entry in iter_catalog_entries():
    prior = hist.get(entry.path)
    if prior is None:
      # Entry introduced after the v1 snapshot was frozen - nothing to
      # compare parity against.
      continue
    assert entry.type == prior["type"], entry.path
    assert entry.canonical_env == prior["canonical_env"], entry.path
    assert list(entry.deprecated_aliases) == prior["deprecated_aliases"]
    if entry.path not in _INTENTIONAL_POST_V1_DEFAULT_CHANGES:
      assert entry.default == prior["default"]
    assert infer_ctrader_type(entry) == prior["ctrader_type"]
