"""Static audit of raw environment access inside ``algo-bot/app``.

Phase 2H consolidates configuration onto ``runtime_config`` and the canonical
resolver. This module performs a deterministic AST scan for the remaining raw
environment surfaces — ``os.environ`` / ``os.getenv`` reads, ``dotenv``
loading, and ``pydantic_settings`` (``BaseSettings`` / ``SettingsConfigDict``)
declarations — and classifies every site so a generated contract can assert
that production code never reaches for ambient environment values directly.

The scan is intentionally conservative and secret-safe: it records the ENV
variable *name* when it is a string literal (never its value) plus the file,
line, and API used.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


# Classification vocabulary. ``allowed`` marks the boundaries where a raw
# environment read is a reviewed, intentional exception.
BOOTSTRAP_AUTHORITY_ALLOWED = "BOOTSTRAP_AUTHORITY_ALLOWED"
CANONICAL_SOURCE_COLLECTION_ALLOWED = "CANONICAL_SOURCE_COLLECTION_ALLOWED"
LEGACY_ROLLBACK_ALLOWED = "LEGACY_ROLLBACK_ALLOWED"
SCRIPT_TOOL_ALLOWED = "SCRIPT_TOOL_ALLOWED"
TEST_FIXTURE_ALLOWED = "TEST_FIXTURE_ALLOWED"
EARLY_BOOT_ALLOWED = "EARLY_BOOT_ALLOWED"
DEPLOYMENT_OBSERVABILITY_ALLOWED = "DEPLOYMENT_OBSERVABILITY_ALLOWED"
DUPLICATE_ENV_REGISTRY = "DUPLICATE_ENV_REGISTRY"
DIRECT_PRODUCTION_ENV_FORBIDDEN = "DIRECT_PRODUCTION_ENV_FORBIDDEN"
UNKNOWN_BLOCKER = "UNKNOWN_BLOCKER"

_ALLOWED_CLASSIFICATIONS = frozenset({
  BOOTSTRAP_AUTHORITY_ALLOWED,
  CANONICAL_SOURCE_COLLECTION_ALLOWED,
  LEGACY_ROLLBACK_ALLOWED,
  SCRIPT_TOOL_ALLOWED,
  TEST_FIXTURE_ALLOWED,
  EARLY_BOOT_ALLOWED,
  DEPLOYMENT_OBSERVABILITY_ALLOWED,
})


# Per-file classification of raw environment access. Paths are POSIX-relative
# to the repository root. A file absent from this map is UNKNOWN_BLOCKER, which
# forces a reviewed decision before it can pass the contract check.
_FILE_CLASSIFICATION: dict[str, str] = {
  "algo-bot/app/configuration/python_sources.py": CANONICAL_SOURCE_COLLECTION_ALLOWED,
  "algo-bot/app/configuration/deployment_identity.py": DEPLOYMENT_OBSERVABILITY_ALLOWED,
  "algo-bot/app/configuration/environment_cli.py": SCRIPT_TOOL_ALLOWED,
  "algo-bot/app/configuration/generate.py": SCRIPT_TOOL_ALLOWED,
  "algo-bot/app/configuration/diagnostic_cli.py": SCRIPT_TOOL_ALLOWED,
  "algo-bot/app/configuration/migrate_env_to_config.py": SCRIPT_TOOL_ALLOWED,
  "algo-bot/app/configuration/phase2i_completion_gate.py": SCRIPT_TOOL_ALLOWED,
  "algo-bot/app/configuration/phase2i_inventory.py": SCRIPT_TOOL_ALLOWED,
  "algo-bot/app/configuration/runtime_manifest_boot.py": EARLY_BOOT_ALLOWED,
  "algo-bot/app/configuration/runtime_manifest_cli.py": SCRIPT_TOOL_ALLOWED,
}


@dataclass(frozen=True, slots=True)
class EnvironmentAccess:
  file: str
  line: int
  api: str
  env_name: str | None
  classification: str
  allowed: bool

  def as_dict(self) -> dict[str, object]:
    return {
      "file": self.file,
      "line": self.line,
      "api": self.api,
      "env_name": self.env_name,
      "classification": self.classification,
      "allowed": self.allowed,
    }


def _literal(node: ast.AST | None) -> str | None:
  if isinstance(node, ast.Constant) and isinstance(node.value, str):
    return node.value
  return None


def _is_os_environ(node: ast.AST) -> bool:
  return (
    isinstance(node, ast.Attribute)
    and node.attr == "environ"
    and isinstance(node.value, ast.Name)
    and node.value.id == "os"
  )


class _EnvironmentVisitor(ast.NodeVisitor):
  def __init__(self) -> None:
    self.hits: list[tuple[int, str, str | None]] = []

  def visit_ClassDef(self, node: ast.ClassDef) -> None:
    for base in node.bases:
      name = base.id if isinstance(base, ast.Name) else (
        base.attr if isinstance(base, ast.Attribute) else None
      )
      if name == "BaseSettings":
        self.hits.append((node.lineno, "BaseSettings", node.name))
    self.generic_visit(node)

  def visit_Subscript(self, node: ast.Subscript) -> None:
    if _is_os_environ(node.value):
      self.hits.append((node.lineno, "os.environ[]", _literal(node.slice)))
    self.generic_visit(node)

  def visit_Assign(self, node: ast.Assign) -> None:
    if node.value is not None and _is_os_environ(node.value):
      self.hits.append((node.lineno, "os.environ", None))
    self.generic_visit(node)

  def visit_Return(self, node: ast.Return) -> None:
    if node.value is not None and _is_os_environ(node.value):
      self.hits.append((node.lineno, "os.environ", None))
    self.generic_visit(node)

  def visit_Call(self, node: ast.Call) -> None:
    func = node.func
    first = node.args[0] if node.args else None
    # Bare ``os.environ`` passed as a mapping argument (bootstrap authority,
    # ``dict(os.environ)`` source collection) is an ambient read.
    for arg in node.args:
      if _is_os_environ(arg):
        self.hits.append((node.lineno, "os.environ", None))
    for keyword in node.keywords:
      if keyword.value is not None and _is_os_environ(keyword.value):
        self.hits.append((node.lineno, "os.environ", None))
    if isinstance(func, ast.Attribute):
      # os.getenv(...) / os.environ.get(...)
      if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
        self.hits.append((node.lineno, "os.getenv", _literal(first)))
      elif func.attr == "get" and _is_os_environ(func.value):
        self.hits.append((node.lineno, "os.environ.get", _literal(first)))
      elif func.attr == "putenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
        self.hits.append((node.lineno, "os.putenv", _literal(first)))
    elif isinstance(func, ast.Name):
      if func.id == "getenv":
        self.hits.append((node.lineno, "os.getenv", _literal(first)))
      elif func.id == "dotenv_values":
        self.hits.append((node.lineno, "dotenv_values", None))
      elif func.id == "load_dotenv":
        self.hits.append((node.lineno, "load_dotenv", None))
      elif func.id == "SettingsConfigDict":
        self.hits.append((node.lineno, "SettingsConfigDict", None))
    self.generic_visit(node)


def _classify(rel_path: str) -> str:
  return _FILE_CLASSIFICATION.get(rel_path, UNKNOWN_BLOCKER)


def audit_environment_usage(repository_root: Path) -> dict[str, object]:
  """Scan ``algo-bot/app`` and classify every raw environment access site."""
  app_root = repository_root / "algo-bot" / "app"
  accesses: list[EnvironmentAccess] = []
  for path in sorted(app_root.rglob("*.py")):
    if not path.is_file():
      continue
    rel = path.relative_to(repository_root).as_posix()
    if "/generated/" in rel:
      continue
    visitor = _EnvironmentVisitor()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
    if not visitor.hits:
      continue
    classification = _classify(rel)
    allowed = classification in _ALLOWED_CLASSIFICATIONS
    for line, api, env_name in visitor.hits:
      accesses.append(EnvironmentAccess(
        file=rel,
        line=line,
        api=api,
        env_name=env_name,
        classification=classification,
        allowed=allowed,
      ))
  accesses.sort(key=lambda item: (item.file, item.line, item.api))
  counts: dict[str, int] = {}
  for access in accesses:
    counts[access.classification] = counts.get(access.classification, 0) + 1
  forbidden = sum(
    1 for access in accesses
    if access.classification == DIRECT_PRODUCTION_ENV_FORBIDDEN
  )
  unknown = sum(
    1 for access in accesses
    if access.classification == UNKNOWN_BLOCKER
  )
  return {
    "counts": dict(sorted(counts.items())),
    "total_access_sites": len(accesses),
    "allowed_sites": sum(1 for access in accesses if access.allowed),
    "direct_production_env_forbidden": forbidden,
    "unknown_blockers": unknown,
    "accesses": [access.as_dict() for access in accesses],
  }
