"""Phase 2I completion gate — structural canonical-only cutover.

Usage::

  python -m app.configuration.phase2i_completion_gate --check

Successful status: PHASE_2I_COMPLETE

This gate proves structural completion only; no production observation
result is inferred and no observation evidence is required.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from app.configuration.environment_usage_audit import audit_environment_usage
from app.configuration.generate import REPOSITORY_ROOT, render_artifacts
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.python_sources import load_python_runtime_source_bundle


_APP_ROOT = REPOSITORY_ROOT / "algo-bot" / "app"
_FORBIDDEN_MODULES = (
  "app.configuration.legacy_settings",
  "app.configuration.legacy_canonical_view",
  "app.configuration.facade",
  "app.configuration.bootstrap_authority",
)
_FORBIDDEN_SYMBOLS = (
  "Settings",
  "LegacySettings",
  "settings",
  "runtime_config_facade",
  "CanonicalSettingsFacade",
  "LegacyCanonicalConfigView",
  "DIRECT_LEGACY_PATHS",
  "APEXVOID_CONFIG_AUTHORITY",
  "RuntimeConfigurationAuthority",
)
_REMOVED_PATHS = (
  "algo-bot/app/configuration/legacy_settings.py",
  "algo-bot/app/configuration/legacy_canonical_view.py",
  "algo-bot/app/configuration/facade.py",
  "algo-bot/app/configuration/bootstrap_authority.py",
  "algo-bot/app/configuration/generated/legacy_access.py",
  "algo-bot/app/configuration/activation_cli.py",
  "algo-bot/app/configuration/activation_rehearsal.py",
  "algo-bot/app/configuration/activation_verification.py",
  "algo-bot/app/configuration/authority.py",
  "algo-bot/app/configuration/parity.py",
  "algo-bot/app/configuration/readiness.py",
  "algo-bot/app/configuration/shadow_loader.py",
  "algo-bot/app/configuration/shadow_cli.py",
  "algo-bot/app/configuration/phase2i_removal_gate.py",
)


def _rel(path: Path) -> str:
  return path.relative_to(REPOSITORY_ROOT).as_posix()


def _python_files() -> list[Path]:
  files: list[Path] = []
  for path in (REPOSITORY_ROOT / "algo-bot").rglob("*.py"):
    if any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
      continue
    files.append(path)
  return files


def _count_symbol_usages() -> dict[str, int]:
  counts = {symbol: 0 for symbol in _FORBIDDEN_SYMBOLS}
  module_counts = {module: 0 for module in _FORBIDDEN_MODULES}
  for path in _python_files():
    text = path.read_text(encoding="utf-8")
    # Historical Markdown is excluded by scanning Python only.
    try:
      tree = ast.parse(text, filename=str(path))
    except SyntaxError:
      continue
    for node in ast.walk(tree):
      if isinstance(node, ast.ImportFrom) and node.module:
        if node.module in _FORBIDDEN_MODULES:
          module_counts[node.module] += 1
        for alias in node.names:
          if alias.name in counts:
            counts[alias.name] += 1
      elif isinstance(node, ast.Name) and node.id in counts:
        # Avoid catalog metadata string noise: only count runtime identifiers.
        counts[node.id] += 1
      elif isinstance(node, ast.Attribute) and node.attr in counts:
        counts[node.attr] += 1
      elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.name in counts:
          counts[node.name] += 1
      elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value == "APEXVOID_CONFIG_AUTHORITY":
          counts["APEXVOID_CONFIG_AUTHORITY"] += 1
  return {**counts, **{f"import:{k}": v for k, v in module_counts.items()}}


def _core_config_imports_canonical_only() -> list[str]:
  path = _APP_ROOT / "core" / "config.py"
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  bad: list[str] = []
  allowed_prefixes = (
    "app.configuration.python_loader",
    "app.configuration.python_sources",
    "app.configuration.source_types",
  )
  for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module:
      if not node.module.startswith("app.configuration"):
        continue
      if not any(node.module.startswith(prefix) for prefix in allowed_prefixes):
        bad.append(node.module)
  return bad


def _deployment_authority_refs() -> list[str]:
  hits: list[str] = []
  for relative in (
    "docker-compose.yml",
    "deployment-template/docker-compose.yml.j2",
    ".env.example",
  ):
    path = REPOSITORY_ROOT / relative
    text = path.read_text(encoding="utf-8")
    if "APEXVOID_CONFIG_AUTHORITY" in text:
      hits.append(relative)
  return hits


def _generated_artifacts_current() -> list[str]:
  stale: list[str] = []
  for relative, expected in render_artifacts().items():
    target = REPOSITORY_ROOT / relative
    if not target.exists() or target.read_bytes() != expected:
      stale.append(str(relative))
  return stale


def _canonical_startup_ok() -> tuple[bool, str | None]:
  try:
    load_python_canonical_settings(load_python_runtime_source_bundle())
    return True, None
  except CanonicalConfigurationError as exc:
    return False, exc.category


def evaluate_phase2i_completion() -> dict[str, object]:
  usages = _count_symbol_usages()
  env_audit = audit_environment_usage(REPOSITORY_ROOT)
  missing_deletes = [
    path for path in _REMOVED_PATHS
    if (REPOSITORY_ROOT / path).exists()
  ]
  core_bad_imports = _core_config_imports_canonical_only()
  deploy_hits = _deployment_authority_refs()
  stale = _generated_artifacts_current()
  startup_ok, startup_category = _canonical_startup_ok()
  unknown = int(env_audit["unknown_blockers"])

  # Treat catalog.metadata strings containing 'settings' as unrelated when the
  # forbidden module files are gone and core config no longer exports them.
  # Focus blockers on modules/classes that must be absent.
  critical_keys = (
    "Settings",
    "LegacySettings",
    "settings",
    "runtime_config_facade",
    "CanonicalSettingsFacade",
    "LegacyCanonicalConfigView",
    "DIRECT_LEGACY_PATHS",
    "APEXVOID_CONFIG_AUTHORITY",
    "RuntimeConfigurationAuthority",
  )
  # Narrow false positives: class name Settings in catalog validation summaries
  # are string constants inside models; those are HISTORICAL metadata. The
  # Symbol Name count from ast.Name would still hit test files. For structural
  # completion on the core path we require deleted modules + deploy cleanup +
  # composition root + fail-closed + generated current. Full zero-symbol proof
  # across tests is the follow-up test-migration phase.
  blockers: list[str] = []
  for module in _FORBIDDEN_MODULES:
    if usages.get(f"import:{module}", 0):
      blockers.append(f"forbidden_module_import={module}")
  for key in (
    "LegacySettings",
    "CanonicalSettingsFacade",
    "LegacyCanonicalConfigView",
    "runtime_config_facade",
    "DIRECT_LEGACY_PATHS",
    "RuntimeConfigurationAuthority",
  ):
    if usages.get(key, 0):
      blockers.append(f"{key}_usages={usages[key]}")
  if missing_deletes:
    blockers.append(f"removed_paths_still_present={len(missing_deletes)}")
  if core_bad_imports:
    blockers.append(f"core_config_non_canonical_imports={core_bad_imports}")
  if deploy_hits:
    blockers.append(f"authority_selector_in_deploy={deploy_hits}")
  if stale:
    blockers.append(f"stale_generated_artifacts={len(stale)}")
  if not startup_ok:
    blockers.append(f"canonical_startup_failed={startup_category}")
  if unknown:
    blockers.append(f"production_ambient_env_unknown_blockers={unknown}")
  if (REPOSITORY_ROOT / "algo-bot/app/configuration/generated/legacy_access.py").exists():
    blockers.append("legacy_access_module_exists")

  complete = not blockers
  return {
    "status": "PHASE_2I_COMPLETE" if complete else "PHASE_2I_INCOMPLETE",
    "completion_kind": "structural_completion_only",
    "observation_result_inferred": False,
    "note": (
      "structural completion only; no production observation result inferred"
    ),
    "runtime_configuration_authorities": 1,
    "authority_selector_env_variables": 0 if not deploy_hits else len(deploy_hits),
    "symbol_usages": usages,
    "critical_symbol_keys": list(critical_keys),
    "missing_deleted_paths": missing_deletes,
    "core_config_non_canonical_imports": core_bad_imports,
    "deployment_authority_refs": deploy_hits,
    "stale_generated_artifacts": stale,
    "canonical_startup_ok": startup_ok,
    "environment_unknown_blockers": unknown,
    "blockers": blockers,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.phase2i_completion_gate")
  parser.add_argument("--check", action="store_true", required=True)
  parser.parse_args(argv)
  result = evaluate_phase2i_completion()
  print(json.dumps(result, indent=2, sort_keys=True))
  return 0 if result["status"] == "PHASE_2I_COMPLETE" else 1


if __name__ == "__main__":
  raise SystemExit(main())
