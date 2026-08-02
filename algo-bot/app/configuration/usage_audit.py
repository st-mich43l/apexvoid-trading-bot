"""AST-backed audit of the repository's real legacy Settings API usage."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Iterable

from app.configuration.catalog import DERIVED_LEGACY_PROPERTIES
from app.configuration.catalog import iter_catalog_entries


class UsageClassification(StrEnum):
  FACADE_SUPPORTED = "facade_supported"
  MIGRATION_REQUIRED = "migration_required"
  TEST_ONLY_ADAPTATION = "test_only_adaptation"
  UNSAFE_MUTATION = "unsafe_mutation"
  LEGACY_INTERNAL_ONLY = "legacy_internal_only"
  ACTIVATION_BLOCKER = "activation_blocker"


@dataclass(frozen=True, slots=True)
class LegacyUsage:
  path: str
  line: int
  column: int
  operation: str
  attribute: str | None
  classification: UsageClassification
  detail: str
  dynamic_names: tuple[str, ...] = ()

  def as_dict(self) -> dict[str, object]:
    result = asdict(self)
    result["classification"] = self.classification.value
    result["dynamic_names"] = list(self.dynamic_names)
    return result


_SECTIONS = ("production", "tests", "scripts")
_BUCKETS = (
  "attribute_reads",
  "attribute_writes",
  "attribute_deletions",
  "method_calls",
  "introspection",
  "type_dependencies",
)
_INTROSPECTION = {"getattr", "hasattr", "setattr", "delattr", "vars", "dir", "repr", "isinstance", "type"}


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
  result = []
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


class _FileAuditor(ast.NodeVisitor):
  def __init__(self, *, relative: Path, source: str, supported: set[str]):
    self.relative = relative
    self.supported = supported
    self.tree = ast.parse(source, filename=relative.as_posix())
    self.parents = {
      child: parent
      for parent in ast.walk(self.tree)
      for child in ast.iter_child_nodes(parent)
    }
    self.settings_names: set[str] = set()
    self.settings_classes: set[str] = set()
    self.config_modules: set[str] = set()
    self.domains: dict[str, set[str]] = {}
    self.rows: list[tuple[str, LegacyUsage]] = []
    self._discover_imports_and_domains()

  def _discover_imports_and_domains(self) -> None:
    for node in ast.walk(self.tree):
      if isinstance(node, ast.ImportFrom):
        if node.module == "app.core.config":
          for alias in node.names:
            local = alias.asname or alias.name
            if alias.name == "settings":
              self.settings_names.add(local)
            elif alias.name == "Settings":
              self.settings_classes.add(local)
        elif node.module == "app.core" and any(
          alias.name == "config" for alias in node.names
        ):
          for alias in node.names:
            if alias.name == "config":
              self.config_modules.add(alias.asname or alias.name)
      elif isinstance(node, ast.Import):
        for alias in node.names:
          if alias.name == "app.core.config":
            self.config_modules.add(alias.asname or alias.name.split(".")[0])
      elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
          continue
        domain = self._literal_domain(value)
        for target in targets:
          if isinstance(target, ast.Name):
            if self._is_settings_ref(value) or (
              isinstance(value, ast.Call)
              and self._is_settings_constructor(value.func)
            ):
              self.settings_names.add(target.id)
            if domain:
              self.domains.setdefault(target.id, set()).update(domain)
      elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
          arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
          arguments.append(node.args.kwarg)
        for argument in arguments:
          if self._is_settings_annotation(argument.annotation):
            self.settings_names.add(argument.arg)
    changed = True
    while changed:
      changed = False
      for node in ast.walk(self.tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
          continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
          continue
        domain = self._expression_domain(value)
        for target in targets:
          if isinstance(target, ast.Name) and domain:
            known = self.domains.setdefault(target.id, set())
            if not domain <= known:
              known.update(domain)
              changed = True

  def _literal_domain(self, node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
      return {
        item.value for item in node.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
      }
    if isinstance(node, ast.Dict):
      return {
        value.value for value in node.values
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
      }
    return set()

  def _expression_domain(self, node: ast.AST) -> set[str]:
    direct = self._literal_domain(node)
    if direct:
      return direct
    if isinstance(node, ast.Name):
      return set(self.domains.get(node.id, ()))
    if (
      isinstance(node, ast.Call)
      and isinstance(node.func, ast.Attribute)
      and node.func.attr == "get"
      and isinstance(node.func.value, ast.Name)
    ):
      return set(self.domains.get(node.func.value.id, ()))
    return set()

  def _is_settings_ref(self, node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in self.settings_names:
      return True
    if isinstance(node, ast.Attribute) and node.attr == "settings":
      if isinstance(node.value, ast.Name) and node.value.id in self.config_modules:
        return True
      if (
        isinstance(node.value, ast.Attribute)
        and ast.unparse(node.value) == "app.core.config"
      ):
        return True
    return False

  def _is_settings_constructor(self, node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
      return node.id in self.settings_classes
    return (
      isinstance(node, ast.Attribute)
      and node.attr == "Settings"
      and isinstance(node.value, ast.Name)
      and node.value.id in self.config_modules
    )

  def _is_settings_annotation(self, node: ast.AST | None) -> bool:
    if isinstance(node, ast.Name):
      return node.id in self.settings_classes
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
      return node.value.rsplit(".", 1)[-1] == "Settings"
    if isinstance(node, ast.Attribute):
      return node.attr == "Settings"
    return False

  def _classification(self, *, operation: str, supported: bool) -> UsageClassification:
    section = _section(self.relative)
    if section == "tests":
      return UsageClassification.TEST_ONLY_ADAPTATION
    if self.relative.as_posix() == "algo-bot/app/core/config.py":
      return UsageClassification.LEGACY_INTERNAL_ONLY
    if self.relative.as_posix().startswith("algo-bot/app/configuration/"):
      return UsageClassification.LEGACY_INTERNAL_ONLY
    if operation in {"attribute_write", "attribute_delete", "setattr", "delattr"}:
      return UsageClassification.UNSAFE_MUTATION
    if supported:
      return UsageClassification.FACADE_SUPPORTED
    if operation in {"construct_settings"}:
      return UsageClassification.LEGACY_INTERNAL_ONLY
    return UsageClassification.ACTIVATION_BLOCKER

  def _add(
    self,
    bucket: str,
    node: ast.AST,
    *,
    operation: str,
    attribute: str | None,
    supported: bool,
    detail: str,
    dynamic_names: Iterable[str] = (),
  ) -> None:
    self.rows.append((bucket, LegacyUsage(
      path=self.relative.as_posix(),
      line=getattr(node, "lineno", 0),
      column=getattr(node, "col_offset", 0),
      operation=operation,
      attribute=attribute,
      classification=self._classification(operation=operation, supported=supported),
      detail=detail,
      dynamic_names=tuple(sorted(dynamic_names)),
    )))

  def visit_Attribute(self, node: ast.Attribute) -> None:
    if self._is_settings_ref(node.value):
      parent = self.parents.get(node)
      if isinstance(parent, ast.Call) and parent.func is node:
        self._add(
          "method_calls", node, operation=f"method:{node.attr}",
          attribute=node.attr, supported=False,
          detail="method call on legacy Settings instance",
        )
      elif isinstance(node.ctx, ast.Store):
        self._add(
          "attribute_writes", node, operation="attribute_write",
          attribute=node.attr, supported=False,
          detail="direct Settings attribute assignment",
        )
      elif isinstance(node.ctx, ast.Del):
        self._add(
          "attribute_deletions", node, operation="attribute_delete",
          attribute=node.attr, supported=False,
          detail="direct Settings attribute deletion",
        )
      else:
        self._add(
          "attribute_reads", node, operation="attribute_read",
          attribute=node.attr, supported=node.attr in self.supported,
          detail="direct Settings attribute read",
        )
    elif isinstance(node.value, ast.Name) and node.value.id in self.settings_classes:
      self._add(
        "type_dependencies", node, operation="settings_class_attribute",
        attribute=node.attr, supported=False,
        detail="legacy Settings class API dependency",
      )
    self.generic_visit(node)

  def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
      arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
      arguments.append(node.args.kwarg)
    for argument in arguments:
      if self._is_settings_annotation(argument.annotation):
        self._add(
          "type_dependencies", argument, operation="settings_parameter",
          attribute=argument.arg, supported=False,
          detail="function receives an explicitly typed legacy Settings instance",
        )
    self.generic_visit(node)

  def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
    self._visit_function(node)

  def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
    self._visit_function(node)

  def visit_Call(self, node: ast.Call) -> None:
    function_name = node.func.id if isinstance(node.func, ast.Name) else None
    if self._is_settings_constructor(node.func):
      self._add(
        "type_dependencies", node, operation="construct_settings",
        attribute=None, supported=False,
        detail="local legacy Settings construction",
      )
    if function_name in _INTROSPECTION and node.args and self._is_settings_ref(node.args[0]):
      attribute = None
      dynamic_names: set[str] = set()
      if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
        attribute = node.args[1].value
      elif len(node.args) > 1:
        dynamic_names = self._expression_domain(node.args[1])
      supported = function_name in {"getattr", "hasattr", "dir", "repr"}
      if function_name in {"getattr", "hasattr"}:
        supported = (
          attribute in self.supported if attribute is not None
          else bool(dynamic_names) and dynamic_names <= self.supported
        )
        # ``getattr(settings, "old_optional_name", fallback)`` keeps identical
        # semantics on the facade: an unknown attribute raises AttributeError
        # and the builtin returns the caller's fallback.
        if function_name == "getattr" and len(node.args) >= 3 and attribute is not None:
          supported = True
      if function_name in {"setattr", "delattr"}:
        bucket = "attribute_writes" if function_name == "setattr" else "attribute_deletions"
      elif function_name in {"isinstance", "type"}:
        bucket = "type_dependencies"
        supported = False
      else:
        bucket = "introspection"
      self._add(
        bucket, node, operation=function_name,
        attribute=attribute, supported=supported,
        detail=(
          "restricted dynamic legacy name"
          if dynamic_names else f"{function_name} on Settings"
        ),
        dynamic_names=dynamic_names,
      )
    elif (
      isinstance(node.func, ast.Attribute)
      and node.func.attr == "setattr"
      and node.args
      and self._is_settings_ref(node.args[0])
    ):
      attribute = (
        node.args[1].value
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
        else None
      )
      self._add(
        "attribute_writes", node, operation="setattr",
        attribute=attribute, supported=False,
        detail="helper or monkeypatch mutation of Settings",
      )
    elif any(self._is_settings_ref(argument) for argument in node.args) or any(
      self._is_settings_ref(keyword.value) for keyword in node.keywords
    ):
      self._add(
        "type_dependencies", node, operation="pass_settings",
        attribute=None, supported=True,
        detail="legacy Settings instance passed as a structural dependency",
      )
    self.generic_visit(node)

  def run(self) -> list[tuple[str, LegacyUsage]]:
    self.visit(self.tree)
    return self.rows


def audit_legacy_settings_usage(repository_root: Path) -> dict[str, object]:
  supported = _legacy_names()
  sections = {
    section: {bucket: [] for bucket in _BUCKETS}
    for section in _SECTIONS
  }
  blockers = []
  for path in _python_files(repository_root):
    relative = path.relative_to(repository_root)
    auditor = _FileAuditor(
      relative=relative,
      source=path.read_text(encoding="utf-8"),
      supported=supported,
    )
    section = _section(relative)
    for bucket, usage in auditor.run():
      item = usage.as_dict()
      sections[section][bucket].append(item)
      if section == "production" and usage.classification in {
        UsageClassification.ACTIVATION_BLOCKER,
        UsageClassification.MIGRATION_REQUIRED,
        UsageClassification.UNSAFE_MUTATION,
      }:
        blockers.append(item)
  for section in _SECTIONS:
    for bucket in _BUCKETS:
      sections[section][bucket].sort(key=lambda item: (
        item["path"], item["line"], item["column"], item["operation"],
      ))
  blockers.sort(key=lambda item: (
    item["path"], item["line"], item["column"], item["operation"],
  ))
  payload = {**sections, "activation_blockers": blockers}
  fingerprint = sha256(json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
  ).encode("utf-8")).hexdigest()
  return {
    "source_fingerprint": fingerprint,
    **payload,
  }
