"""Enforces the one hard constraint on StructuralBarrierBook: it must never
become a live Market Map dependency again (mirrors the C# engine's own
TradePlanExecutionEngineDependencyTests.cs pattern for a similar boundary).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database

_MODULE_PATH = (
  Path(__file__).resolve().parent.parent
  / "app" / "autotrade" / "structural_barriers.py"
)
_FORBIDDEN_MODULES = {
  "app.analysis.market_map",
  "app.analysis.map_strategy",
  "app.autotrade.map_strategy",
}
_FORBIDDEN_NAMES = {"MarketMap", "MapEntry", "market_map_key", "market_map_display_key"}


def _imported_module_names(tree: ast.Module) -> set[str]:
  modules: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      modules.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      modules.add(node.module)
  return modules


def _referenced_names(tree: ast.Module) -> set[str]:
  return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def test_structural_barriers_never_imports_market_map():
  source = _MODULE_PATH.read_text()
  tree = ast.parse(source, filename=str(_MODULE_PATH))
  modules = _imported_module_names(tree)
  offending_modules = modules & _FORBIDDEN_MODULES
  assert not offending_modules, (
    f"structural_barriers.py must never import Market Map: {offending_modules}"
  )
  # Also fail on any bare `import x.y.market_map` style partial matches.
  assert not any("market_map" in module for module in modules), (
    f"structural_barriers.py imports something market_map-shaped: {modules}"
  )
  names = _referenced_names(tree)
  offending_names = names & _FORBIDDEN_NAMES
  assert not offending_names, (
    f"structural_barriers.py must never reference Market Map types: {offending_names}"
  )
