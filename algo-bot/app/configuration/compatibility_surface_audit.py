"""AST-backed audit of the Phase 2I compatibility surface.

Phase 2I-A removed every *production* ``runtime_config_facade()`` call and left
a deliberately scoped compatibility surface behind: the legacy authority, its
rollback types, the flat ``CanonicalSettingsFacade`` (still used by tests and
tooling), and the generated legacy-access maps. This audit classifies every
reference to that surface so the removal gate can assert there are zero
*unknown* blockers before the canonical observation window opens.

Tracked symbols: ``Settings``, ``LegacySettings``, ``settings``,
``CanonicalSettingsFacade``, ``LegacyCanonicalConfigView``,
``runtime_config_facade``, ``DIRECT_LEGACY_PATHS``, ``DERIVED_LEGACY_PROPERTIES``,
``getattr`` with a known legacy config name, and ``SimpleNamespace`` config
fixtures (namespaces whose keyword arguments are known legacy field names).

Classifications:

* ``PRODUCTION_REMOVE_2I_A`` -- a production ``runtime_config_facade`` call that
  should have been removed in this phase (target: zero).
* ``TEST_COMPATIBILITY_RETAIN_2I_A`` -- test-suite compatibility usage kept so
  flat ``SimpleNamespace``/``Settings`` overrides keep working.
* ``LEGACY_ROLLBACK_RETAIN_2I_A`` -- legacy authority + rollback types wired in
  the composition root and configuration package.
* ``TOOLING_RETAIN_2I_A`` -- generators, audits, and the narrow-projection
  builder that legitimately read the generated maps / build namespaces.
* ``REMOVE_2I_B`` -- the ``runtime_config_facade`` definition/export itself,
  retained for tests and tooling until the Phase 2I-B deletion.
* ``UNKNOWN_BLOCKER`` -- any production reference to a removal-target symbol
  that this audit cannot account for (target: zero).
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path

from app.configuration.catalog import DERIVED_LEGACY_PROPERTIES
from app.configuration.catalog import iter_catalog_entries


CLASSIFICATIONS = (
  "PRODUCTION_REMOVE_2I_A",
  "TEST_COMPATIBILITY_RETAIN_2I_A",
  "LEGACY_ROLLBACK_RETAIN_2I_A",
  "TOOLING_RETAIN_2I_A",
  "REMOVE_2I_B",
  "UNKNOWN_BLOCKER",
)

_COMPATIBILITY_SYMBOLS = frozenset({
  "Settings",
  "LegacySettings",
  "settings",
  "CanonicalSettingsFacade",
  "LegacyCanonicalConfigView",
  "runtime_config_facade",
  "DIRECT_LEGACY_PATHS",
  "DERIVED_LEGACY_PROPERTIES",
})

# Configuration-package modules that legitimately own the compatibility surface.
_CORE_CONFIG = "algo-bot/app/core/config.py"
_CONFIG_PREFIX = "algo-bot/app/configuration/"


@dataclass(frozen=True, slots=True)
class CompatibilityUsage:
  path: str
  line: int
  column: int
  symbol: str
  kind: str
  classification: str
  detail: str

  def as_dict(self) -> dict[str, object]:
    return asdict(self)


def _legacy_names() -> set[str]:
  return {
    entry.legacy_attr
    for entry in iter_catalog_entries()
    if entry.legacy_attr is not None
  } | {item.property_name for item in DERIVED_LEGACY_PROPERTIES}


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


def _classify(*, section: str, symbol: str, kind: str, relative: str) -> str:
  is_core_config = relative == _CORE_CONFIG
  is_config_pkg = relative.startswith(_CONFIG_PREFIX)

  if section == "tests":
    return "TEST_COMPATIBILITY_RETAIN_2I_A"
  if section == "scripts":
    return "TOOLING_RETAIN_2I_A"

  # ---- production section (algo-bot/app/**) ----
  if symbol == "runtime_config_facade":
    if is_core_config:
      # Definition + __all__ export, retained for tests/tooling until 2I-B.
      return "REMOVE_2I_B"
    if is_config_pkg:
      return "TOOLING_RETAIN_2I_A"
    # A production autotrade/analysis facade call is exactly what 2I-A removed.
    return "PRODUCTION_REMOVE_2I_A"
  if symbol in {"CanonicalSettingsFacade", "LegacyCanonicalConfigView"}:
    if is_core_config or is_config_pkg:
      return "LEGACY_ROLLBACK_RETAIN_2I_A"
    return "UNKNOWN_BLOCKER"
  if symbol in {"Settings", "LegacySettings", "settings"}:
    if is_core_config or is_config_pkg:
      return "LEGACY_ROLLBACK_RETAIN_2I_A"
    # Production flat Settings usage outside the root is a phase-2H regression.
    return "UNKNOWN_BLOCKER"
  if symbol in {"DIRECT_LEGACY_PATHS", "DERIVED_LEGACY_PROPERTIES"}:
    return "TOOLING_RETAIN_2I_A"
  if symbol == "getattr_legacy_name":
    # Retained flat-attribute reads on the ``cfg`` parameter that keep test
    # SimpleNamespace overrides working; production defaults now build a narrow
    # canonical projection instead of a facade.
    return "TEST_COMPATIBILITY_RETAIN_2I_A"
  if symbol == "settings_global_getattr":
    # Dynamic flat lookup on the process settings/runtime_config singleton.
    return "UNKNOWN_BLOCKER"
  if symbol == "simplenamespace_config_fixture":
    return "TOOLING_RETAIN_2I_A"
  return "UNKNOWN_BLOCKER"


class _FileAuditor(ast.NodeVisitor):
  def __init__(self, *, relative: Path, source: str, legacy_names: set[str]):
    self.relative = relative
    self.relative_posix = relative.as_posix()
    self.section = _section(relative)
    self.legacy_names = legacy_names
    self.tree = ast.parse(source, filename=self.relative_posix)
    # Local names bound to tracked symbols and to the flat settings singleton.
    self.symbol_bindings: dict[str, str] = {}
    self.settings_globals: set[str] = set()
    self.simplenamespace_names: set[str] = set()
    self.rows: list[CompatibilityUsage] = []
    self._discover_imports()

  def _discover_imports(self) -> None:
    for node in ast.walk(self.tree):
      if not isinstance(node, ast.ImportFrom):
        continue
      for alias in node.names:
        local = alias.asname or alias.name
        if alias.name in _COMPATIBILITY_SYMBOLS:
          self.symbol_bindings[local] = alias.name
          if alias.name in {"settings"}:
            self.settings_globals.add(local)
          self._record(node, alias.name, "import", f"import {alias.name}")
        if (
          node.module == "app.core.config"
          and alias.name in {"settings", "runtime_config", "active_config"}
        ):
          self.settings_globals.add(local)
        if node.module == "types" and alias.name == "SimpleNamespace":
          self.simplenamespace_names.add(local)

  def _record(self, node: ast.AST, symbol: str, kind: str, detail: str) -> None:
    classification = _classify(
      section=self.section,
      symbol=symbol,
      kind=kind,
      relative=self.relative_posix,
    )
    self.rows.append(CompatibilityUsage(
      path=self.relative_posix,
      line=getattr(node, "lineno", 0),
      column=getattr(node, "col_offset", 0),
      symbol=symbol,
      kind=kind,
      classification=classification,
      detail=detail,
    ))

  def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
    if node.name == "runtime_config_facade":
      self._record(
        node, "runtime_config_facade", "definition", "facade definition",
      )
    self.generic_visit(node)

  def visit_Call(self, node: ast.Call) -> None:
    func = node.func
    # runtime_config_facade() calls.
    if isinstance(func, ast.Name) and self.symbol_bindings.get(func.id) == (
      "runtime_config_facade"
    ):
      self._record(node, "runtime_config_facade", "call", "facade call")
    if isinstance(func, ast.Attribute) and func.attr == "runtime_config_facade":
      self._record(node, "runtime_config_facade", "call", "facade call")
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
      on_settings_global = (
        isinstance(target, ast.Name) and target.id in self.settings_globals
      )
      symbol = (
        "settings_global_getattr" if on_settings_global else "getattr_legacy_name"
      )
      self._record(
        node,
        symbol,
        "getattr",
        f"getattr(..., {node.args[1].value!r})",
      )
    # SimpleNamespace(...) config fixtures: keyword args are known legacy names.
    if isinstance(func, ast.Name) and func.id in self.simplenamespace_names:
      keyword_names = {
        keyword.arg for keyword in node.keywords if keyword.arg is not None
      }
      if keyword_names and keyword_names & self.legacy_names:
        self._record(
          node,
          "simplenamespace_config_fixture",
          "namespace",
          "SimpleNamespace config fixture",
        )
    self.generic_visit(node)

  def visit_Name(self, node: ast.Name) -> None:
    symbol = self.symbol_bindings.get(node.id)
    if symbol is not None and symbol in {
      "CanonicalSettingsFacade",
      "LegacyCanonicalConfigView",
      "Settings",
      "LegacySettings",
      "DIRECT_LEGACY_PATHS",
      "DERIVED_LEGACY_PROPERTIES",
    } and isinstance(node.ctx, ast.Load):
      self._record(node, symbol, "reference", f"reference {symbol}")
    self.generic_visit(node)

  def run(self) -> list[CompatibilityUsage]:
    self.visit(self.tree)
    return self.rows


def audit_compatibility_surface(repository_root: Path) -> dict[str, object]:
  """Return the deterministic compatibility-surface classification payload."""
  legacy_names = _legacy_names()
  usages: list[dict[str, object]] = []
  by_classification = {name: 0 for name in CLASSIFICATIONS}
  by_symbol: dict[str, int] = {}
  for path in _python_files(repository_root):
    relative = path.relative_to(repository_root)
    auditor = _FileAuditor(
      relative=relative,
      source=path.read_text(encoding="utf-8"),
      legacy_names=legacy_names,
    )
    for usage in auditor.run():
      item = usage.as_dict()
      usages.append(item)
      by_classification[usage.classification] += 1
      by_symbol[usage.symbol] = by_symbol.get(usage.symbol, 0) + 1
  usages.sort(key=lambda item: (
    item["path"], item["line"], item["column"], item["symbol"],
  ))
  unknown = [item for item in usages if item["classification"] == "UNKNOWN_BLOCKER"]
  production_facade = [
    item for item in usages
    if item["classification"] == "PRODUCTION_REMOVE_2I_A"
  ]
  payload = {
    "phase": "2I-A",
    "classifications": list(CLASSIFICATIONS),
    "counts": {
      "total": len(usages),
      "unknown_blockers": len(unknown),
      "production_facade_calls": len(production_facade),
      "by_classification": by_classification,
      "by_symbol": dict(sorted(by_symbol.items())),
    },
    "unknown_blockers": unknown,
    "usages": usages,
  }
  fingerprint = sha256(json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
  ).encode("utf-8")).hexdigest()
  return {"source_fingerprint": fingerprint, **payload}
