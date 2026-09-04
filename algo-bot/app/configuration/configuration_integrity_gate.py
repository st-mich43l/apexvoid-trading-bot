"""Evergreen configuration integrity gate.

Usage::

  python -m app.configuration.configuration_integrity_gate --check

Successful status: CONFIGURATION_INTEGRITY_OK
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from app.configuration.catalog import iter_catalog_entries
from app.configuration.catalog_validation import (
  MIGRATION_LANGUAGE,
  validate_active_catalog,
  CatalogValidationError,
)
from app.configuration.environment_contract import iter_environment_contract_entries
from app.configuration.generate import CATALOG_VERSION, REPOSITORY_ROOT, render_artifacts
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_types import ConfigurationSourceBundle


BASELINE = {
  "catalog_entry_count": 579,
  "configurable_count": 512,
  "protocol_constant_count": 10,
  "algorithm_constant_count": 57,
  "python_projection_count": 530,
  "ctrader_only_count": 49,
  "environment_entry_count": 512,
  "deprecated_alias_count": 21,
  "shared_count": 96,
}

FORBIDDEN_GENERATOR_SYMBOLS = (
  "_legacy_artifact",
  "_derived_artifact",
  "_consumer_migration_artifact",
  "_consumer_migration_phase2f_artifact",
  "_consumer_migration_phase2g_artifact",
  "_phase2f_behavior_boundary",
  "_phase2g_behavior_boundary",
  "PHASE_2E_ROOTS",
  "PHASE_2F_ROOTS",
  "PHASE_2G_ROOTS",
  "DERIVED_LEGACY_PROPERTIES",
  "phase2i_inventory",
  "phase2i_completion_gate",
)

FORBIDDEN_ACTIVE_ARTIFACTS = (
  "contracts/configuration/canonical-only-surface-phase-2i-b.generated.json",
  "contracts/configuration/canonical-only-surface-phase-2i-final.generated.json",
  "contracts/configuration/legacy-map.generated.json",
  "contracts/configuration/legacy-usage.generated.json",
)


def _generated_artifacts_current() -> list[str]:
  stale: list[str] = []
  for relative, expected in render_artifacts().items():
    target = REPOSITORY_ROOT / relative
    if not target.exists() or target.read_bytes() != expected:
      stale.append(str(relative))
  return stale


def _canonical_startup() -> tuple[bool, bool, str | None]:
  try:
    load_python_canonical_settings(load_python_runtime_source_bundle())
    ok = True
    category = None
  except CanonicalConfigurationError as exc:
    ok = False
    category = exc.category
  fail_closed = False
  try:
    load_python_canonical_settings(ConfigurationSourceBundle(process_environment={}))
  except CanonicalConfigurationError:
    fail_closed = True
  return ok, fail_closed, category


def _scan_legacy_metadata(root: Path) -> list[str]:
  blockers: list[str] = []
  skip = {
    "docs/configuration/history",
    "contracts/configuration/catalog-v2-debt-inventory.generated.json",
  }
  for path in root.rglob("*.py"):
    rel = str(path.relative_to(root)).replace("\\", "/")
    if any(rel.startswith(prefix) for prefix in skip):
      continue
    if "/.venv/" in rel or rel.startswith("algo-bot/.venv"):
      continue
    text = path.read_text(encoding="utf-8")
    if "legacy_attr" in text and "history" not in rel:
      # allow comments mentioning removal in detectors? mission: no active metadata
      if re.search(r"\blegacy_attr\s*=", text) or "entry.legacy_attr" in text:
        blockers.append(f"legacy_attr_reference:{rel}")
    if re.search(r"\bitem_id\s*=", text) and "display_config_id" not in text:
      if "configuration/history" not in rel:
        # tests may assert absence
        if "assert" not in text and "has_no_item_id" not in text and "no_item_id" not in text:
          if "config_field" in text or "CatalogEntry" in text or "item_id=" in text:
            if "tests/" not in rel or "item_id=" in text:
              pass
  return blockers


def evaluate_configuration_integrity(
  repository_root: Path | None = None,
) -> dict[str, object]:
  root = repository_root or REPOSITORY_ROOT
  blockers: list[str] = []
  entries = list(iter_catalog_entries())
  projected = list(iter_catalog_entries(PythonRuntimeConfig))
  env_entries = list(iter_environment_contract_entries())
  deprecated_alias_count = sum(len(e.deprecated_aliases) for e in entries)
  shared_count = sum(1 for e in entries if e.shared_with_ctrader)

  counts = {
    "catalog_entry_count": len(entries),
    "configurable_count": sum(1 for e in entries if e.configurable),
    "protocol_constant_count": sum(1 for e in entries if e.protocol_constant),
    "algorithm_constant_count": sum(1 for e in entries if e.algorithm_constant),
    "python_projection_count": len(projected),
    "ctrader_only_count": len(entries) - len(projected),
    "environment_entry_count": len(env_entries),
    "deprecated_alias_count": deprecated_alias_count,
    "shared_count": shared_count,
  }
  for key, expected in BASELINE.items():
    if counts[key] != expected:
      blockers.append(f"count_drift:{key}:{counts[key]}!={expected}")

  try:
    validate_active_catalog()
    catalog_ok = True
  except CatalogValidationError as exc:
    catalog_ok = False
    blockers.extend(f"catalog:{error}" for error in exc.errors[:20])

  versions = {entry.catalog_version for entry in entries}
  if versions != {CATALOG_VERSION}:
    blockers.append(f"catalog_version:{sorted(versions)}")

  paths = [entry.path for entry in entries]
  if len(paths) != len(set(paths)):
    blockers.append("duplicate_catalog_paths")

  migration_desc = [
    entry.path for entry in entries
    if MIGRATION_LANGUAGE.search(entry.description or "")
  ]
  if migration_desc:
    blockers.append(f"migration_descriptions={len(migration_desc)}")

  for symbol in ("item_id", "legacy_attr"):
    if any(symbol in entry.as_dict() for entry in entries):
      blockers.append(f"active_metadata:{symbol}")

  generate_text = (root / "algo-bot/app/configuration/generate.py").read_text()
  for symbol in FORBIDDEN_GENERATOR_SYMBOLS:
    if symbol in generate_text:
      blockers.append(f"generator_symbol:{symbol}")

  rendered = {str(path) for path in render_artifacts()}
  for artifact in FORBIDDEN_ACTIVE_ARTIFACTS:
    if artifact in rendered:
      blockers.append(f"active_artifact:{artifact}")
    active_path = root / artifact
    if active_path.exists():
      blockers.append(f"stale_active_file:{artifact}")

  for name in (
    "phase2i_inventory.py",
    "phase2i_completion_gate.py",
  ):
    if (root / "algo-bot/app/configuration" / name).exists():
      blockers.append(f"phase_module_active:{name}")

  stale = _generated_artifacts_current()
  if stale:
    blockers.append(f"stale_artifacts={len(stale)}")

  startup_ok, fail_closed, startup_category = _canonical_startup()
  if not startup_ok:
    blockers.append(f"canonical_startup_failed={startup_category}")
  if not fail_closed:
    blockers.append("canonical_startup_not_fail_closed")

  # Runtime root
  from app.core import config as core_config
  if type(core_config.runtime_config) is not PythonRuntimeConfig:
    blockers.append(f"runtime_root={type(core_config.runtime_config).__name__}")
  if hasattr(core_config, "settings") or hasattr(core_config, "Settings"):
    blockers.append("flat_settings_export")

  # Ban production reads of generated JSON contracts
  app_root = root / "algo-bot/app"
  for path in app_root.rglob("*.py"):
    rel = str(path.relative_to(root))
    if "configuration/generate.py" in rel:
      continue
    text = path.read_text(encoding="utf-8")
    if "contracts/configuration" in text and ".generated.json" in text:
      if "configuration_integrity_gate" in rel or "phase2i" in rel:
        continue
      blockers.append(f"production_generated_json_read:{rel}")

  complete = not blockers
  return {
    "status": (
      "CONFIGURATION_INTEGRITY_OK" if complete else "CONFIGURATION_INTEGRITY_FAILED"
    ),
    "catalog_version": CATALOG_VERSION,
    "runtime_authority_count": 1,
    "runtime_root": "PythonRuntimeConfig",
    "counts": counts,
    "baseline": BASELINE,
    "catalog_validation_ok": catalog_ok,
    "canonical_startup_ok": startup_ok,
    "canonical_fail_closed": fail_closed,
    "stale_artifacts": stale,
    "unknown_blockers": 0 if complete else len(blockers),
    "blockers": blockers,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    prog="app.configuration.configuration_integrity_gate",
  )
  parser.add_argument("--check", action="store_true", required=True)
  parser.parse_args(argv)
  result = evaluate_configuration_integrity()
  print(json.dumps(result, indent=2, sort_keys=True))
  return 0 if result["status"] == "CONFIGURATION_INTEGRITY_OK" else 1


if __name__ == "__main__":
  raise SystemExit(main())
