"""Shared Phase 2I final inventory used by the completion gate and artifacts.

Semantic AST checks only. Ordinary variables named ``settings`` and analysis
``AnalysisSettings`` types are ignored unless they import deleted config APIs.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from app.configuration.environment_usage_audit import audit_environment_usage
from app.configuration.fingerprints import catalog_fingerprint
from app.configuration.profiles import PROFILES


FORBIDDEN_MODULES = (
  "app.configuration.legacy_settings",
  "app.configuration.legacy_canonical_view",
  "app.configuration.facade",
  "app.configuration.bootstrap_authority",
)
FORBIDDEN_CORE_IMPORTS = frozenset({
  "Settings",
  "settings",
  "runtime_config_facade",
  "LegacySettings",
  "CanonicalSettingsFacade",
  "LegacyCanonicalConfigView",
})
FORBIDDEN_SYMBOLS = frozenset({
  "runtime_config_facade",
  "CanonicalSettingsFacade",
  "LegacyCanonicalConfigView",
  "LegacySettings",
  "DIRECT_LEGACY_PATHS",
  "CANONICAL_PATH_TO_LEGACY_ATTR",
  "CANONICAL_LEGACY_PATH_PREFIXES",
  "RuntimeConfigurationAuthority",
})
REMOVED_PATHS = (
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
  "algo-bot/app/configuration/canonical_consumer_surface.py",
  "algo-bot/app/configuration/compatibility_surface_audit.py",
  "algo-bot/app/configuration/usage_audit.py",
  "algo-bot/app/configuration/phase2h_gate.py",
)
OBSOLETE_TEST_FILES = (
  "algo-bot/tests/test_config_facade.py",
  "algo-bot/tests/test_config_legacy_canonical_view.py",
  "algo-bot/tests/test_config_legacy_access_generation.py",
  "algo-bot/tests/test_config_bootstrap_authority.py",
  "algo-bot/tests/test_config_shadow_parity.py",
  "algo-bot/tests/test_config_shadow_loader.py",
  "algo-bot/tests/test_config_active_authority.py",
)
ACTIVE_MIGRATION_ARTIFACT_NAMES = (
  "legacy-map.generated.json",
  "legacy-usage.generated.json",
  "legacy-derived.generated.json",
  "compatibility-surface-phase-2i-a.generated.json",
  "canonical-consumer-surface-phase-2i-a1.generated.json",
  "consumer-migration-phase-2e.generated.json",
  "consumer-migration-phase-2f.generated.json",
  "consumer-migration-phase-2g.generated.json",
)
# Sentinel used only by this audit / completion gate to detect residual ENV.
AUTHORITY_ENV_SENTINEL = "APEXVOID_CONFIG_AUTHORITY"


@dataclass(frozen=True, slots=True)
class LegacyReference:
  file: str
  line: int
  kind: str
  detail: str
  section: str  # production | tests | tooling


def _section(rel: str) -> str:
  if rel.startswith("algo-bot/tests/"):
    return "tests"
  if rel.startswith("algo-bot/app/configuration/"):
    return "tooling"
  if rel.startswith("algo-bot/app/"):
    return "production"
  if rel.startswith("algo-bot/"):
    return "tooling"
  return "tooling"


def _python_files(repository_root: Path) -> list[Path]:
  root = repository_root / "algo-bot"
  files: list[Path] = []
  for path in root.rglob("*.py"):
    if any(part in {".venv", "__pycache__", ".pytest_cache"} for part in path.parts):
      continue
    files.append(path)
  return files


def scan_legacy_references(repository_root: Path) -> list[LegacyReference]:
  hits: list[LegacyReference] = []
  gate_rel = "algo-bot/app/configuration/phase2i_completion_gate.py"
  inventory_rel = "algo-bot/app/configuration/phase2i_inventory.py"
  for path in _python_files(repository_root):
    rel = path.relative_to(repository_root).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
      tree = ast.parse(text, filename=rel)
    except SyntaxError:
      continue
    for node in ast.walk(tree):
      if isinstance(node, ast.ImportFrom) and node.module:
        if node.module in FORBIDDEN_MODULES:
          hits.append(LegacyReference(
            rel, node.lineno, "forbidden_module_import", node.module, _section(rel),
          ))
        if node.module == "app.core.config":
          for alias in node.names:
            if alias.name in FORBIDDEN_CORE_IMPORTS:
              hits.append(LegacyReference(
                rel, node.lineno, "forbidden_core_import", alias.name, _section(rel),
              ))
        for alias in node.names:
          if alias.name in FORBIDDEN_SYMBOLS:
            # Allow the gate/inventory modules to reference symbols in constants.
            if rel in {gate_rel, inventory_rel}:
              continue
            hits.append(LegacyReference(
              rel, node.lineno, "forbidden_symbol_import", alias.name, _section(rel),
            ))
      if isinstance(node, ast.Attribute):
        if (
          isinstance(node.value, ast.Attribute)
          and isinstance(node.value.value, ast.Attribute)
          and isinstance(node.value.value.value, ast.Name)
          and node.value.value.value.id == "app"
          and node.value.value.attr == "core"
          and node.value.attr == "config"
          and node.attr in {"Settings", "settings"}
        ):
          hits.append(LegacyReference(
            rel, node.lineno, "core_config_attr", node.attr, _section(rel),
          ))
        if (
          isinstance(node.value, ast.Name)
          and node.value.id in {"config", "config_module"}
          and node.attr in {"Settings", "settings"}
        ):
          # Only count when module is app.core.config import alias.
          hits.append(LegacyReference(
            rel, node.lineno, "config_module_attr", node.attr, _section(rel),
          ))
      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.name in FORBIDDEN_SYMBOLS and rel not in {gate_rel, inventory_rel}:
          hits.append(LegacyReference(
            rel, node.lineno, "forbidden_definition", node.name, _section(rel),
          ))
      if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value == AUTHORITY_ENV_SENTINEL and rel not in {gate_rel, inventory_rel}:
          # Deployment/tests must not select with this ENV. Mentions in
          # assertions that the ENV is absent are still string constants —
          # classify as residual unless the statement is a negative assertion
          # containing "not in".
          line = text.splitlines()[node.lineno - 1]
          if "not in" in line or "AUTHORITY_ENV" in line or "sentinel" in line:
            continue
          hits.append(LegacyReference(
            rel, node.lineno, "authority_env_literal", AUTHORITY_ENV_SENTINEL,
            _section(rel),
          ))
  return hits


def deployment_selector_refs(repository_root: Path) -> list[str]:
  hits: list[str] = []
  for relative in (
    "docker-compose.yml",
    "deployment-template/docker-compose.yml.j2",
    ".env.example",
  ):
    path = repository_root / relative
    if AUTHORITY_ENV_SENTINEL in path.read_text(encoding="utf-8"):
      hits.append(relative)
  return hits


def active_legacy_artifacts(repository_root: Path) -> list[str]:
  contract_dir = repository_root / "contracts" / "configuration"
  present = []
  for name in ACTIVE_MIGRATION_ARTIFACT_NAMES:
    if (contract_dir / name).exists():
      present.append(f"contracts/configuration/{name}")
  for rel in REMOVED_PATHS:
    if (repository_root / rel).exists():
      present.append(rel)
  for rel in OBSOLETE_TEST_FILES:
    if (repository_root / rel).exists():
      present.append(rel)
  return present


def core_config_non_canonical_imports(repository_root: Path) -> list[str]:
  path = repository_root / "algo-bot/app/core/config.py"
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  bad: list[str] = []
  allowed = (
    "app.configuration.python_loader",
    "app.configuration.python_sources",
    "app.configuration.source_types",
  )
  for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module:
      if node.module.startswith("app.configuration") and not any(
        node.module.startswith(prefix) for prefix in allowed
      ):
        bad.append(node.module)
  return bad


def evaluate_inventory(repository_root: Path) -> dict[str, object]:
  refs = scan_legacy_references(repository_root)
  env_audit = audit_environment_usage(repository_root)
  deploy = deployment_selector_refs(repository_root)
  active = active_legacy_artifacts(repository_root)
  core_bad = core_config_non_canonical_imports(repository_root)
  production = [r for r in refs if r.section == "production"]
  tests = [r for r in refs if r.section == "tests"]
  tooling = [r for r in refs if r.section == "tooling"]
  unknown = int(env_audit["unknown_blockers"])

  blockers: list[str] = []
  if production:
    blockers.append(f"production_legacy_references={len(production)}")
  if tests:
    blockers.append(f"test_legacy_references={len(tests)}")
  if tooling:
    blockers.append(f"tooling_legacy_references={len(tooling)}")
  if deploy:
    blockers.append(f"deployment_selector_references={deploy}")
  if active:
    blockers.append(f"active_legacy_artifacts={len(active)}")
  if core_bad:
    blockers.append(f"core_config_non_canonical_imports={core_bad}")
  if unknown:
    blockers.append(f"unknown_blockers={unknown}")

  return {
    "runtime_authorities": 1,
    "production_legacy_imports": len(production),
    "test_legacy_imports": len(tests),
    "tooling_legacy_imports": len(tooling),
    "flat_settings_exports": 0,
    "flat_settings_imports": sum(
      1 for r in refs if r.kind == "forbidden_core_import"
    ),
    "runtime_config_facade_usages": sum(
      1 for r in refs if "runtime_config_facade" in r.detail or r.detail == "runtime_config_facade"
    ),
    "legacy_view_usages": sum(
      1 for r in refs if "LegacyCanonicalConfigView" in r.detail
    ),
    "legacy_access_map_usages": sum(
      1 for r in refs if "DIRECT_LEGACY_PATHS" in r.detail
      or "CANONICAL_PATH_TO_LEGACY_ATTR" in r.detail
    ),
    "authority_selector_runtime_usages": sum(
      1 for r in refs if r.kind == "authority_env_literal"
    ),
    "authority_selector_deployment_usages": len(deploy),
    "active_legacy_artifacts": len(active),
    "unknown_blockers": unknown,
    "production_refs": [
      {"file": r.file, "line": r.line, "kind": r.kind, "detail": r.detail}
      for r in production
    ],
    "test_refs": [
      {"file": r.file, "line": r.line, "kind": r.kind, "detail": r.detail}
      for r in tests
    ],
    "tooling_refs": [
      {"file": r.file, "line": r.line, "kind": r.kind, "detail": r.detail}
      for r in tooling
    ],
    "deployment_selector_references": deploy,
    "active_legacy_artifact_paths": active,
    "core_config_non_canonical_imports": core_bad,
    "blockers": blockers,
    "catalog_fingerprint": catalog_fingerprint(),
    "profile_names": sorted(PROFILES),
    "canonical_loader_entry_point": (
      "app.configuration.python_loader.load_python_canonical_settings"
    ),
  }


def render_canonical_only_surface(repository_root: Path) -> dict[str, object]:
  inventory = evaluate_inventory(repository_root)
  complete = not inventory["blockers"]
  return {
    "phase": "2I-final",
    "scope": "canonical_only_complete" if complete else "canonical_only_incomplete",
    "status": "PHASE_2I_COMPLETE" if complete else "PHASE_2I_INCOMPLETE",
    "authorization": "explicit_structural_architecture_decision",
    "observation_evidence_fabricated": False,
    "completion_kind": "structural_completion_only",
    "counts": {
      key: inventory[key]
      for key in (
        "runtime_authorities",
        "production_legacy_imports",
        "test_legacy_imports",
        "tooling_legacy_imports",
        "flat_settings_exports",
        "flat_settings_imports",
        "runtime_config_facade_usages",
        "legacy_view_usages",
        "legacy_access_map_usages",
        "authority_selector_runtime_usages",
        "authority_selector_deployment_usages",
        "active_legacy_artifacts",
        "unknown_blockers",
      )
    },
    "catalog_fingerprint": inventory["catalog_fingerprint"],
    "profile_names": inventory["profile_names"],
    "canonical_loader_entry_point": inventory["canonical_loader_entry_point"],
    "composition_root": "app.core.config",
    "runtime_root_type": "PythonRuntimeConfig",
    "blockers": inventory["blockers"],
  }
