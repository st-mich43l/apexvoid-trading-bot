"""Active Catalog V2 validation CLI.

Usage::

  python -m app.configuration.catalog_validation --check
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from typing import Iterable

from app.configuration.catalog import iter_catalog_entries
from app.configuration.generate import CATALOG_VERSION
from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.models.python_runtime import PythonRuntimeConfig


class CatalogValidationError(ValueError):
  def __init__(self, errors: Iterable[str]):
    self.errors = tuple(errors)
    super().__init__("; ".join(self.errors))


SECRET_ENV_NAMES = {
  "ANTHROPIC_API_KEY",
  "CTRADER_ACCESS_TOKEN",
  "CTRADER_CLIENT_SECRET",
  "CTRADER_REFRESH_TOKEN",
  "DATABASE_URL",
  "POSTGRES_PASSWORD",
  "SCANNER_TELEGRAM_BOT_TOKEN",
  "TELEGRAM_BOT_TOKEN",
  "TIINGO_API_KEY",
}

MIGRATION_LANGUAGE = re.compile(
  r"\b(Legacy|Settings|migration|mapped to|Phase\s*2|inactive schema)\b",
  re.IGNORECASE,
)
V1_ID_PREFIXES = (
  "python.settings.",
  "ctrader.env.",
  "hardcoded.",
)


def _duplicates(values: Iterable[str]) -> list[str]:
  counts = Counter(values)
  return sorted(value for value, count in counts.items() if count > 1)


def validate_active_catalog() -> None:
  entries = iter_catalog_entries()
  errors: list[str] = []
  if not entries:
    raise CatalogValidationError(["catalog is empty"])

  paths = [entry.path for entry in entries]
  path_dupes = _duplicates(paths)
  if path_dupes:
    errors.append(f"duplicate paths: {path_dupes}")

  canonical_envs = [
    entry.canonical_env for entry in entries if entry.canonical_env
  ]
  env_dupes = _duplicates(canonical_envs)
  if env_dupes:
    errors.append(f"duplicate canonical ENV names: {env_dupes}")

  aliases: list[str] = []
  for entry in entries:
    aliases.extend(entry.deprecated_aliases)
  alias_dupes = _duplicates(aliases)
  if alias_dupes:
    errors.append(f"duplicate deprecated aliases: {alias_dupes}")

  canonical_set = set(canonical_envs)
  collisions = sorted(set(aliases) & canonical_set)
  if collisions:
    errors.append(f"alias/canonical collisions: {collisions}")

  owners = {item.value for item in ConfigOwner}
  units = {item.value for item in ConfigUnit}
  kinds = {item.value for item in ConfigKind}
  risks = {item.value for item in RiskClassification}
  reloads = {item.value for item in ReloadPolicy}
  mismatches = {item.value for item in MismatchPolicy}

  for entry in entries:
    label = entry.path
    if entry.catalog_version != CATALOG_VERSION:
      errors.append(f"{label}: catalog_version {entry.catalog_version}")
    if entry.owner not in owners:
      errors.append(f"{label}: invalid owner {entry.owner!r}")
    if entry.unit not in units:
      errors.append(f"{label}: invalid unit {entry.unit!r}")
    if entry.kind not in kinds:
      errors.append(f"{label}: invalid kind {entry.kind!r}")
    if entry.risk_classification not in risks:
      errors.append(f"{label}: invalid risk {entry.risk_classification!r}")
    if entry.reload_policy not in reloads:
      errors.append(f"{label}: invalid reload {entry.reload_policy!r}")
    if entry.runtime_reload_policy not in reloads:
      errors.append(
        f"{label}: invalid runtime reload {entry.runtime_reload_policy!r}"
      )
    if entry.mismatch_policy not in mismatches:
      errors.append(f"{label}: invalid mismatch policy")
    if MIGRATION_LANGUAGE.search(entry.description or ""):
      errors.append(f"{label}: migration terminology in description")
    if any(entry.description.startswith(prefix) for prefix in V1_ID_PREFIXES):
      errors.append(f"{label}: V1 item id leak in description")
    if entry.kind != ConfigKind.CONFIGURABLE.value and entry.canonical_env:
      errors.append(f"{label}: constant has ENV binding")
    if entry.secret and entry.default != "<redacted>":
      errors.append(f"{label}: secret default is not redacted")
    if entry.canonical_env in SECRET_ENV_NAMES and not entry.secret:
      errors.append(f"{label}: known secret ENV is not classified secret")
    if entry.shared_with_ctrader and entry.mismatch_policy == (
      MismatchPolicy.NOT_REPORTED.value
    ):
      # Shared fields may be not_reported; keep soft signal only when fatal-required.
      pass
    if entry.deprecated and not (
      entry.replacement_path or entry.terminal_deprecation_reason
    ):
      errors.append(f"{label}: deprecated without disposition")
    payload = entry.as_dict()
    if "item_id" in payload or "legacy_attr" in payload:
      errors.append(f"{label}: legacy identity metadata still present")

  projected = {entry.path for entry in iter_catalog_entries(PythonRuntimeConfig)}
  for entry in entries:
    if entry.owner == "ctrader" and not entry.shared_with_ctrader:
      if entry.path in projected:
        errors.append(f"{entry.path}: cTrader-only leaf projected into Python")
    elif entry.path not in projected and entry.owner != "ctrader":
      errors.append(f"{entry.path}: Python/shared leaf missing from projection")

  if errors:
    raise CatalogValidationError(errors)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.catalog_validation")
  parser.add_argument("--check", action="store_true", required=True)
  parser.parse_args(argv)
  try:
    validate_active_catalog()
  except CatalogValidationError as exc:
    for error in exc.errors:
      print(error, file=sys.stderr)
    return 1
  entries = iter_catalog_entries()
  print(
    f"catalog validation passed: {len(entries)} items, "
    f"version {CATALOG_VERSION}"
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
