"""Read-only S13 inventory of imports that still depend on Python detectors.

Run from algo-bot: python s13_legacy_dependency_audit.py
Only static Python imports are discovered. Dynamic imports and runtime
configuration must be verified separately before deleting any module.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

LEGACY_PACKAGES = ("app.analysis", "app.scalping")


def _legacy_package(module: str) -> str | None:
  for package in LEGACY_PACKAGES:
    if module == package or module.startswith(f"{package}."):
      return package
  return None


def _imported_modules(node: ast.AST, package_parts: tuple[str, ...]) -> tuple[str, ...]:
  if isinstance(node, ast.Import):
    return tuple(alias.name for alias in node.names)
  if not isinstance(node, ast.ImportFrom):
    return ()
  if node.level:
    if node.level > len(package_parts):
      return ()
    base = package_parts[:len(package_parts) - node.level + 1]
    module = ".".join((*base, *(node.module or "").split("."))) if node.module else ".".join(base)
  else:
    module = node.module or ""
  # "from app import analysis" and "from .. import analysis" also import
  # the legacy package; checking the source module alone would miss them.
  names = [module]
  names.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
  return tuple(names)


def inventory_legacy_dependencies(root: Path) -> dict[str, object]:
  """Return deterministic static import edges; never import project modules."""
  app = root / "app"
  importers: dict[str, list[str]] = {}
  for path in sorted(app.rglob("*.py")):
    package_parts = ("app", *path.parent.relative_to(app).parts)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    edges: set[str] = set()
    for node in ast.walk(tree):
      for imported in _imported_modules(node, package_parts):
        if _legacy_package(imported) is not None:
          edges.add(imported)
    if edges:
      importers[path.relative_to(root).as_posix()] = sorted(edges)
  return {
    "scope": "static imports under algo-bot/app; not proof of runtime use",
    "legacy_packages": list(LEGACY_PACKAGES),
    "importer_count": len(importers),
    "dependency_count": sum(len(edges) for edges in importers.values()),
    "importers": importers,
  }


if __name__ == "__main__":
  print(json.dumps(
    inventory_legacy_dependencies(Path(__file__).resolve().parent),
    indent=2,
    sort_keys=True,
  ))
