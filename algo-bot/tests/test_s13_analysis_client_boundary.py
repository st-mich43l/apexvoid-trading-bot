"""Protect the Go consumer from re-importing Python technical detectors."""

from __future__ import annotations

import ast
from pathlib import Path

from s13_legacy_dependency_audit import (
  _imported_modules,
  _legacy_package,
  inventory_legacy_dependencies,
)


def test_analysis_client_has_no_direct_legacy_detector_imports():
  root = Path(__file__).resolve().parents[1]
  client = root / "app" / "analysis_client"
  violations: list[str] = []
  for path in sorted(client.glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = ("app", "analysis_client")
    for node in ast.walk(tree):
      for imported in _imported_modules(node, package_parts):
        if _legacy_package(imported) is not None:
          violations.append(f"{path.name}: {imported}")
  assert violations == [], (
    "Go Analysis Client must consume technical facts rather than importing "
    "legacy Python detectors: " + ", ".join(violations)
  )


def test_legacy_audit_catches_absolute_and_relative_imports(tmp_path):
  path = tmp_path / "app" / "analysis_client" / "sample.py"
  path.parent.mkdir(parents=True)
  path.write_text(
    "from app.analysis.detectors import build_context\n"
    "from ..scalping import runtime\n"
    "from app import analysis\n"
    "import app.scalping.risk\n",
    encoding="utf-8",
  )
  report = inventory_legacy_dependencies(tmp_path)
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
  report = inventory_legacy_dependencies(tmp_path)
  assert report["importers"] == {}
