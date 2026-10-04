"""Protect the execution service from reintroducing Python analysis authority."""

from __future__ import annotations

import ast
from pathlib import Path


_ANALYSIS_PACKAGES = ("app.analysis", "app.scalping")


def _analysis_package(module: str) -> str | None:
  for package in _ANALYSIS_PACKAGES:
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
  names = [module]
  names.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
  return tuple(names)


def _inventory_analysis_dependencies(root: Path) -> dict[str, object]:
  """Return deterministic static import edges without importing app modules."""
  app = root / "app"
  importers: dict[str, list[str]] = {}
  for path in sorted(app.rglob("*.py")):
    package_parts = ("app", *path.parent.relative_to(app).parts)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    edges: set[str] = set()
    for node in ast.walk(tree):
      for imported in _imported_modules(node, package_parts):
        if _analysis_package(imported) is not None:
          edges.add(imported)
    if edges:
      importers[path.relative_to(root).as_posix()] = sorted(edges)
  return {"importers": importers}


def test_analysis_client_has_no_python_analysis_imports():
  root = Path(__file__).resolve().parents[1]
  client = root / "app" / "analysis_client"
  violations: list[str] = []
  for path in sorted(client.glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = ("app", "analysis_client")
    for node in ast.walk(tree):
      for imported in _imported_modules(node, package_parts):
        if _analysis_package(imported) is not None:
          violations.append(f"{path.name}: {imported}")
  assert violations == [], (
    "Analysis Client must consume Go technical facts rather than importing "
    "Python analysis modules: " + ", ".join(violations)
  )


def test_boundary_check_catches_absolute_and_relative_imports(tmp_path):
  path = tmp_path / "app" / "analysis_client" / "sample.py"
  path.parent.mkdir(parents=True)
  path.write_text(
    "from app.analysis.detectors import build_context\n"
    "from ..scalping import runtime\n"
    "from app import analysis\n"
    "import app.scalping.risk\n",
    encoding="utf-8",
  )
  report = _inventory_analysis_dependencies(tmp_path)
  found = report["importers"]["app/analysis_client/sample.py"]
  assert "app.analysis.detectors" in found
  assert "app.scalping" in found
  assert "app.analysis" in found
  assert "app.scalping.risk" in found


def test_analysis_client_name_is_not_mistaken_for_analysis_package(tmp_path):
  path = tmp_path / "app" / "analysis_client" / "sample.py"
  path.parent.mkdir(parents=True)
  path.write_text(
    "from app.analysis_client.models import OpportunityEnvelope\n",
    encoding="utf-8",
  )
  report = _inventory_analysis_dependencies(tmp_path)
  assert report["importers"] == {}


def test_execution_strategy_registry_does_not_load_python_analysis_graph():
  root = Path(__file__).resolve().parents[1]
  path = root / "app" / "autotrade" / "strategy_registry.py"
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  imported = set()
  for node in ast.walk(tree):
    for module in _imported_modules(node, ("app", "autotrade")):
      imported.add(module)
  assert not any(
    module == "app.analysis.detectors"
    or module.startswith("app.analysis.detectors.")
    for module in imported
  ), "execution strategy policy must not import retired Python detectors"
