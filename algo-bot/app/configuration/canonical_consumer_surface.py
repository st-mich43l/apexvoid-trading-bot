"""AST-backed inventory of the Phase 2I-A.1 canonical-consumer surface.

Phase 2I-A.1 replaces the residual ``project_runtime_config`` /
``_RUNTIME_*_CFG_FIELDS`` / SimpleNamespace projection surface left behind by
Phase 2I-A with typed canonical domain injection. This audit enumerates every
production reference to that residual surface so the migration ledger and the
Phase 2I-A.1 architecture guards can assert a zero end-state.

Tracked call shapes:

* ``project_runtime_config(...)`` calls (via ``app.core.runtime_projection``).
* ``project_from(...)`` calls (via ``app.configuration.runtime_projection``).
* ``_RUNTIME_*_CFG_FIELDS`` tuple *definitions* -- each tuple that enumerates
  legacy attribute names for the retired narrow projection helper.
* ``DIRECT_LEGACY_PATHS`` imports outside the sanctioned tooling paths (kept in
  ``facade.py``, ``activation_rehearsal.py``, ``activation_cli.py``, and the
  generator itself for reverse-mapping purposes).
* ``getattr(cfg_like, "<legacy_name>", ...)`` reads on ``cfg``/``config`` in
  production modules.
* ``SimpleNamespace(**legacy_name=...)`` fixtures in production.

Classifications:

* ``PHASE_2I_A_1_MIGRATE`` -- production usage that must be migrated to a typed
  canonical read in Phase 2I-A.1.
* ``PHASE_2I_A_1_MIGRATED`` -- production usage already converted to a typed
  canonical read (accounted for once in inventory diff).
* ``TOOLING_RETAIN_2I_A_1`` -- generators, audits, and rollback plumbing that
  legitimately continue to build/lookup projections.
* ``TEST_COMPATIBILITY_RETAIN_2I_A_1`` -- test-suite usage that is being
  updated to canonical fixtures but remains compatible during rollout.
* ``UNKNOWN_BLOCKER`` -- any production reference that cannot be accounted for
  and blocks Phase 2I-A.1 completion.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Iterable

from app.configuration.catalog import DERIVED_LEGACY_PROPERTIES
from app.configuration.catalog import iter_catalog_entries


CLASSIFICATIONS = (
  "PHASE_2I_A_1_MIGRATE",
  "PHASE_2I_A_1_MIGRATED",
  "TOOLING_RETAIN_2I_A_1",
  "TEST_COMPATIBILITY_RETAIN_2I_A_1",
  "UNKNOWN_BLOCKER",
)

_TARGET_SYMBOLS = frozenset({
  "project_runtime_config",
  "project_from",
})

# Config-package modules that historically owned the reverse-mapping surface.
# Phase 2I-B removed facade/activation tooling; only the generator/audits remain
# as historical scanners until their dependency on DIRECT_LEGACY_PATHS is gone.
_TOOLING_ALLOWED_DIRECT_LEGACY_PATHS_IMPORTS = frozenset({
  "algo-bot/app/configuration/generate.py",
  "algo-bot/app/configuration/compatibility_surface_audit.py",
  "algo-bot/app/configuration/canonical_consumer_surface.py",
})

_PRODUCTION_ROOT = "algo-bot/app/"


@dataclass(frozen=True, slots=True)
class ConsumerUsage:
  path: str
  line: int
  column: int
  function: str
  mechanism: str
  legacy_field: str | None
  canonical_path: str | None
  classification: str
  status: str
  test_coverage: str
  detail: str

  def as_dict(self) -> dict[str, object]:
    return asdict(self)


def _legacy_names() -> set[str]:
  return {
    entry.legacy_attr
    for entry in iter_catalog_entries()
    if entry.legacy_attr is not None
  } | {item.property_name for item in DERIVED_LEGACY_PROPERTIES}


def _legacy_path_map() -> dict[str, str]:
  paths: dict[str, str] = {}
  for entry in iter_catalog_entries():
    if entry.legacy_attr is not None:
      paths[entry.legacy_attr] = entry.path
  for item in DERIVED_LEGACY_PROPERTIES:
    if item.source_path is not None and item.property_name not in paths:
      paths[item.property_name] = item.source_path
  return paths


def _section(relative: Path) -> str:
  parts = relative.parts
  if "tests" in parts:
    return "tests"
  if len(parts) >= 2 and parts[0] == "algo-bot" and parts[1] == "app":
    return "production"
  return "scripts"


def _python_files(repository_root: Path) -> tuple[Path, ...]:
  result: list[Path] = []
  excluded = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
  for directory, child_directories, filenames in os.walk(repository_root):
    child_directories[:] = sorted(
      name for name in child_directories
      if name not in excluded and not name.startswith(".venv")
    )
    for filename in sorted(filenames):
      if filename.endswith(".py"):
        result.append(Path(directory) / filename)
  return tuple(sorted(result, key=lambda item: item.as_posix()))


_MIGRATION_TARGETS = frozenset({
  "algo-bot/app/analysis/actionability.py",
  "algo-bot/app/analysis/m1_trigger.py",
  "algo-bot/app/analysis/market_map.py",
  "algo-bot/app/analysis/detectors.py",
  "algo-bot/app/analysis/regime.py",
  "algo-bot/app/analysis/scalp_ranges.py",
  "algo-bot/app/analysis/session_liquidity.py",
  "algo-bot/app/analysis/trendlines.py",
  "algo-bot/app/autotrade/map_strategy.py",
  "algo-bot/app/autotrade/scale_context.py",
  "algo-bot/app/autotrade/trend.py",
  "algo-bot/app/autotrade/execution_policy.py",
  "algo-bot/app/autotrade/protective_stop.py",
  "algo-bot/app/autotrade/trade_plan_builder.py",
  "algo-bot/app/autotrade/worker.py",
})


_TEST_COVERAGE_BY_FILE = {
  "algo-bot/app/analysis/actionability.py": (
    "test_scanner_actionability.py; test_worker_veto_regression_replay.py"
  ),
  "algo-bot/app/analysis/m1_trigger.py": "test_m1_trigger.py",
  "algo-bot/app/analysis/market_map.py": "test_market_map.py",
  "algo-bot/app/analysis/detectors.py": "test_detectors.py; test_scanner.py",
  "algo-bot/app/analysis/regime.py": "test_scanner.py; test_detectors.py",
  "algo-bot/app/analysis/scalp_ranges.py": "test_scalp_ranges.py",
  "algo-bot/app/analysis/session_liquidity.py": "test_scanner.py",
  "algo-bot/app/analysis/trendlines.py": "test_scanner.py",
  "algo-bot/app/autotrade/map_strategy.py": (
    "test_auto_map_strategy.py; test_map_reaction_range_retirement.py"
  ),
  "algo-bot/app/autotrade/scale_context.py": "test_auto_scale_context.py",
  "algo-bot/app/autotrade/trend.py": "test_trend.py",
  "algo-bot/app/autotrade/execution_policy.py": (
    "test_execution_pipeline_integrity.py; test_protective_stop.py; "
    "test_trade_plan_builder.py"
  ),
  "algo-bot/app/autotrade/protective_stop.py": "test_protective_stop.py",
  "algo-bot/app/autotrade/trade_plan_builder.py": "test_trade_plan_builder.py",
  "algo-bot/app/autotrade/worker.py": (
    "test_structure_aware_autotrade.py; test_execution_pipeline_integrity.py"
  ),
}


def _classify_production(
  *,
  relative: str,
  mechanism: str,
  status_migrated: bool,
) -> tuple[str, str]:
  """Return (classification, status)."""
  if relative in _TOOLING_ALLOWED_DIRECT_LEGACY_PATHS_IMPORTS:
    return "TOOLING_RETAIN_2I_A_1", "retained"
  if relative in _MIGRATION_TARGETS:
    if status_migrated:
      return "PHASE_2I_A_1_MIGRATED", "migrated"
    return "PHASE_2I_A_1_MIGRATE", "pending"
  # An unaccounted production use of the residual surface is a blocker.
  return "UNKNOWN_BLOCKER", "blocked"


class _FileAuditor(ast.NodeVisitor):
  def __init__(
    self,
    *,
    relative: Path,
    source: str,
    legacy_names: set[str],
    legacy_paths: dict[str, str],
  ):
    self.relative = relative
    self.relative_posix = relative.as_posix()
    self.section = _section(relative)
    self.legacy_names = legacy_names
    self.legacy_paths = legacy_paths
    self.tree = ast.parse(source, filename=self.relative_posix)
    self.rows: list[ConsumerUsage] = []
    self._function_stack: list[str] = []
    self._imports_project_runtime_config = False
    self._imports_project_from = False
    self._imports_direct_legacy_paths = False
    self._simplenamespace_names: set[str] = set()
    self._discover_imports()

  def _discover_imports(self) -> None:
    for node in ast.walk(self.tree):
      if isinstance(node, ast.ImportFrom):
        for alias in node.names:
          local = alias.asname or alias.name
          if alias.name == "project_runtime_config":
            self._imports_project_runtime_config = True
          if alias.name == "project_from":
            self._imports_project_from = True
          if alias.name == "DIRECT_LEGACY_PATHS":
            self._imports_direct_legacy_paths = True
            self._record_import(
              node=node,
              mechanism="DIRECT_LEGACY_PATHS_import",
              symbol=alias.name,
            )
          if node.module == "types" and alias.name == "SimpleNamespace":
            self._simplenamespace_names.add(local)

  def _current_function(self) -> str:
    return self._function_stack[-1] if self._function_stack else "<module>"

  def _record_import(
    self,
    *,
    node: ast.AST,
    mechanism: str,
    symbol: str,
  ) -> None:
    if self.section != "production":
      classification, status = ("TOOLING_RETAIN_2I_A_1", "retained")
    else:
      classification, status = _classify_production(
        relative=self.relative_posix,
        mechanism=mechanism,
        status_migrated=False,
      )
    self.rows.append(ConsumerUsage(
      path=self.relative_posix,
      line=getattr(node, "lineno", 0),
      column=getattr(node, "col_offset", 0),
      function=self._current_function(),
      mechanism=mechanism,
      legacy_field=symbol,
      canonical_path=None,
      classification=classification,
      status=status,
      test_coverage=_TEST_COVERAGE_BY_FILE.get(self.relative_posix, ""),
      detail=f"import {symbol}",
    ))

  def _record(
    self,
    *,
    node: ast.AST,
    mechanism: str,
    legacy_field: str | None,
    detail: str,
  ) -> None:
    canonical_path = (
      self.legacy_paths.get(legacy_field) if legacy_field else None
    )
    if self.section == "tests":
      classification, status = ("TEST_COMPATIBILITY_RETAIN_2I_A_1", "retained")
    elif self.section == "scripts":
      classification, status = ("TOOLING_RETAIN_2I_A_1", "retained")
    else:
      classification, status = _classify_production(
        relative=self.relative_posix,
        mechanism=mechanism,
        status_migrated=False,
      )
    self.rows.append(ConsumerUsage(
      path=self.relative_posix,
      line=getattr(node, "lineno", 0),
      column=getattr(node, "col_offset", 0),
      function=self._current_function(),
      mechanism=mechanism,
      legacy_field=legacy_field,
      canonical_path=canonical_path,
      classification=classification,
      status=status,
      test_coverage=_TEST_COVERAGE_BY_FILE.get(self.relative_posix, ""),
      detail=detail,
    ))

  def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
    self._function_stack.append(node.name)
    try:
      self.generic_visit(node)
    finally:
      self._function_stack.pop()

  visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

  def visit_Assign(self, node: ast.Assign) -> None:
    # Record any tuple assignment matching the retired _RUNTIME_*_CFG_FIELDS
    # pattern -- one row per definition, so removals show up in the ledger.
    for target in node.targets:
      if not isinstance(target, ast.Name):
        continue
      name = target.id
      if name.startswith("_RUNTIME_") and name.endswith("_CFG_FIELDS"):
        legacy_fields: list[str] = []
        if isinstance(node.value, (ast.Tuple, ast.List)):
          for element in node.value.elts:
            if (
              isinstance(element, ast.Constant)
              and isinstance(element.value, str)
            ):
              legacy_fields.append(element.value)
        for field in legacy_fields:
          self._record(
            node=node,
            mechanism="RUNTIME_CFG_FIELDS_definition",
            legacy_field=field,
            detail=f"{name}[{field!r}]",
          )
        if not legacy_fields:
          self._record(
            node=node,
            mechanism="RUNTIME_CFG_FIELDS_definition",
            legacy_field=None,
            detail=name,
          )
    self.generic_visit(node)

  def visit_Call(self, node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _TARGET_SYMBOLS:
      mechanism = (
        "project_runtime_config_call"
        if func.id == "project_runtime_config"
        else "project_from_call"
      )
      self._record(
        node=node,
        mechanism=mechanism,
        legacy_field=None,
        detail=f"{func.id}()",
      )
    if isinstance(func, ast.Attribute) and func.attr in _TARGET_SYMBOLS:
      mechanism = (
        "project_runtime_config_call"
        if func.attr == "project_runtime_config"
        else "project_from_call"
      )
      self._record(
        node=node,
        mechanism=mechanism,
        legacy_field=None,
        detail=f"...{func.attr}()",
      )
    # getattr(target, "<legacy_name>", ...) with a known legacy field literal.
    if (
      isinstance(func, ast.Name)
      and func.id == "getattr"
      and len(node.args) >= 2
      and isinstance(node.args[1], ast.Constant)
      and isinstance(node.args[1].value, str)
      and node.args[1].value in self.legacy_names
    ):
      target = node.args[0]
      target_name = (
        target.id if isinstance(target, ast.Name) else "<expr>"
      )
      # Only track getattr on cfg/config/settings-like names in production
      # (this is the "dynamic legacy getattr on cfg" the guard forbids).
      if (
        self.section == "production"
        and target_name in {"cfg", "config", "settings", "runtime_config"}
      ):
        self._record(
          node=node,
          mechanism="legacy_getattr_on_cfg",
          legacy_field=node.args[1].value,
          detail=f"getattr({target_name}, {node.args[1].value!r})",
        )
    # SimpleNamespace(...) config fixtures: keyword args are known legacy names.
    if isinstance(func, ast.Name) and func.id in self._simplenamespace_names:
      keyword_names = {
        keyword.arg for keyword in node.keywords if keyword.arg is not None
      }
      if keyword_names and keyword_names & self.legacy_names:
        for name in sorted(keyword_names & self.legacy_names):
          self._record(
            node=node,
            mechanism="simplenamespace_legacy_fixture",
            legacy_field=name,
            detail=f"SimpleNamespace(..., {name}=...)",
          )
    self.generic_visit(node)

  def run(self) -> list[ConsumerUsage]:
    self.visit(self.tree)
    return self.rows


def audit_canonical_consumer_surface(repository_root: Path) -> dict[str, object]:
  """Return the deterministic Phase 2I-A.1 canonical-consumer inventory."""
  legacy_names = _legacy_names()
  legacy_paths = _legacy_path_map()
  usages: list[dict[str, object]] = []
  by_classification = {name: 0 for name in CLASSIFICATIONS}
  by_mechanism: dict[str, int] = {}
  by_file_pending: dict[str, int] = {}
  for path in _python_files(repository_root):
    relative = path.relative_to(repository_root)
    auditor = _FileAuditor(
      relative=relative,
      source=path.read_text(encoding="utf-8"),
      legacy_names=legacy_names,
      legacy_paths=legacy_paths,
    )
    for usage in auditor.run():
      item = usage.as_dict()
      usages.append(item)
      by_classification[usage.classification] += 1
      by_mechanism[usage.mechanism] = by_mechanism.get(usage.mechanism, 0) + 1
      if usage.status == "pending":
        by_file_pending[usage.path] = by_file_pending.get(usage.path, 0) + 1
  usages.sort(key=lambda item: (
    item["path"], item["line"], item["column"], item["mechanism"],
    item["legacy_field"] or "",
  ))
  unknown = [item for item in usages if item["classification"] == "UNKNOWN_BLOCKER"]
  production_pending = [
    item for item in usages
    if item["classification"] == "PHASE_2I_A_1_MIGRATE"
    and item["status"] == "pending"
  ]
  payload = {
    "phase": "2I-A.1",
    "classifications": list(CLASSIFICATIONS),
    "candidate_files": sorted(_MIGRATION_TARGETS),
    "counts": {
      "total": len(usages),
      "unknown_blockers": len(unknown),
      "production_pending": len(production_pending),
      "by_classification": by_classification,
      "by_mechanism": dict(sorted(by_mechanism.items())),
      "by_file_pending": dict(sorted(by_file_pending.items())),
    },
    "unknown_blockers": unknown,
    "usages": usages,
  }
  fingerprint = sha256(json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
  ).encode("utf-8")).hexdigest()
  return {"source_fingerprint": fingerprint, **payload}


__all__ = [
  "CLASSIFICATIONS",
  "ConsumerUsage",
  "audit_canonical_consumer_surface",
]
