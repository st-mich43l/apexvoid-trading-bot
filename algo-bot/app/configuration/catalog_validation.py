"""Validation CLI for the normalized, inactive Phase 2A catalog."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification


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

NUMERIC_TYPE_RE = re.compile(r"int|float|decimal", re.IGNORECASE)
TRADING_ROOTS = {
  "analysis",
  "strategies",
  "actionability",
  "execution",
  "risk",
}
SUFFIX_UNITS = {
  "seconds": ConfigUnit.SECONDS.value,
  "minutes": ConfigUnit.MINUTES.value,
  "hours": ConfigUnit.HOURS.value,
  "days": ConfigUnit.DAYS.value,
  "bars": ConfigUnit.BARS.value,
  "pips": ConfigUnit.PIPS.value,
  "atr": ConfigUnit.ATR.value,
  "ticks": ConfigUnit.TICKS.value,
  "pct": ConfigUnit.PERCENT.value,
  "percent": ConfigUnit.PERCENT.value,
  "fraction": ConfigUnit.FRACTION.value,
}


def _duplicates(values: Iterable[str]) -> list[str]:
  counts = Counter(values)
  return sorted(value for value, count in counts.items() if count > 1)


def _suffix_error(item: dict[str, Any]) -> str | None:
  if item.get("unit") == ConfigUnit.BOOLEAN.value:
    # Boolean presentation switches such as public_show_pips describe what is
    # displayed; their suffix is not the boolean value's physical unit.
    return None
  names = [
    str(value).lower()
    for value in (
      item.get("legacy_attr"),
      item.get("canonical_env"),
      item.get("proposed_path", "").split(".")[-1],
    )
    if value
  ]
  expected = {
    unit
    for suffix, unit in SUFFIX_UNITS.items()
    if any(name.endswith(f"_{suffix}") or name == suffix for name in names)
  }
  if not expected or item["unit"] in expected:
    return None
  return (
    f"{item['item_id']}: suffix requires {sorted(expected)}, "
    f"found {item['unit']}"
  )


def validate_catalog(catalog: dict[str, Any]) -> None:
  errors: list[str] = []
  items = catalog.get("items")
  if not isinstance(items, list):
    raise CatalogValidationError(("catalog.items must be a list",))
  if catalog.get("catalog_version") != 1:
    errors.append("catalog_version must be 1")
  if catalog.get("introduced_in") != "config-catalog-v1":
    errors.append("introduced_in must be config-catalog-v1")

  identifiers = [str(item.get("item_id")) for item in items]
  paths = [str(item.get("proposed_path")) for item in items]
  legacy = [str(item["legacy_attr"]) for item in items if item.get("legacy_attr")]
  envs = [str(item["canonical_env"]) for item in items if item.get("canonical_env")]
  aliases = [
    str(alias)
    for item in items
    for alias in item.get("deprecated_aliases", [])
  ]
  for label, values in (
    ("item_id", identifiers),
    ("proposed_path", paths),
    ("legacy_attr", legacy),
    ("canonical_env", envs),
    ("deprecated_alias", aliases),
  ):
    duplicates = _duplicates(values)
    if duplicates:
      errors.append(f"duplicate {label}: {duplicates}")
  collisions = sorted(set(aliases) & set(envs))
  if collisions:
    errors.append(f"aliases collide with canonical ENV: {collisions}")

  allowed_owner = {value.value for value in ConfigOwner}
  allowed_reload = {value.value for value in ReloadPolicy}
  allowed_unit = {value.value for value in ConfigUnit}
  allowed_kind = {value.value for value in ConfigKind}
  allowed_mismatch = {value.value for value in MismatchPolicy}
  allowed_risk = {value.value for value in RiskClassification}
  for item in items:
    item_id = item.get("item_id", "<missing>")
    kind = item.get("kind")
    if item.get("owner") not in allowed_owner:
      errors.append(f"{item_id}: invalid owner {item.get('owner')!r}")
    if item.get("reload_policy") not in allowed_reload:
      errors.append(
        f"{item_id}: invalid reload policy {item.get('reload_policy')!r}"
      )
    if item.get("unit") not in allowed_unit:
      errors.append(f"{item_id}: invalid unit {item.get('unit')!r}")
    if kind not in allowed_kind:
      errors.append(f"{item_id}: invalid kind {kind!r}")
    if item.get("mismatch_policy") not in allowed_mismatch:
      errors.append(f"{item_id}: invalid mismatch policy")
    if item.get("risk_classification") not in allowed_risk:
      errors.append(f"{item_id}: invalid risk classification")
    if item.get("catalog_version") != catalog.get("catalog_version"):
      errors.append(f"{item_id}: catalog_version mismatch")
    if item.get("introduced_in") != catalog.get("introduced_in"):
      errors.append(f"{item_id}: introduced_in mismatch")
    flags = (
      bool(item.get("configurable")),
      bool(item.get("protocol_constant")),
      bool(item.get("algorithm_constant")),
    )
    expected_flags = {
      ConfigKind.CONFIGURABLE.value: (True, False, False),
      ConfigKind.PROTOCOL_CONSTANT.value: (False, True, False),
      ConfigKind.ALGORITHM_CONSTANT.value: (False, False, True),
    }.get(kind)
    if expected_flags is not None and flags != expected_flags:
      errors.append(f"{item_id}: kind flags {flags} != {expected_flags}")
    if kind in {
      ConfigKind.PROTOCOL_CONSTANT.value,
      ConfigKind.ALGORITHM_CONSTANT.value,
    }:
      if item.get("canonical_env") is not None:
        errors.append(f"{item_id}: constant has canonical ENV")
      if item.get("deprecated_aliases"):
        errors.append(f"{item_id}: constant has ENV aliases")
      if item.get("reload_policy") != ReloadPolicy.CODE_RELEASE.value:
        errors.append(f"{item_id}: constant must reload on code release")
    if item.get("canonical_env") in SECRET_ENV_NAMES and not item.get("secret"):
      errors.append(f"{item_id}: known secret is not classified")
    if item.get("secret") and item.get("default") not in {None, "<redacted>"}:
      errors.append(f"{item_id}: secret default is not redacted")
    path_root = str(item.get("proposed_path", "")).split(".")[0]
    numeric = NUMERIC_TYPE_RE.search(str(item.get("type", ""))) is not None
    if numeric and path_root in TRADING_ROOTS and item.get("unit") == "string":
      errors.append(f"{item_id}: numeric trading field uses string unit")
    suffix_error = _suffix_error(item)
    if suffix_error:
      errors.append(suffix_error)
    if item.get("shared_with_ctrader") and (
      item.get("mismatch_policy") not in allowed_mismatch
    ):
      errors.append(f"{item_id}: shared field lacks mismatch policy")
    if item.get("deprecated") and not (
      item.get("replacement_path")
      or item.get("terminal_deprecation_reason")
    ):
      errors.append(f"{item_id}: deprecated path lacks disposition")
    if item.get("replacement_path") == item.get("proposed_path"):
      errors.append(f"{item_id}: replacement path cannot equal current path")

  runtime_controls = {
    "auto_trade_profile",
    "auto_trade_enabled",
    "auto_trade_dry_run",
    "scanner_enabled",
  }
  for item in items:
    if item.get("legacy_attr") in runtime_controls and str(
      item.get("proposed_path", "")
    ).startswith("contract."):
      errors.append(f"{item['item_id']}: runtime control is under contract")
    env = str(item.get("canonical_env") or "")
    if env.startswith("CTRADER_") and any(
      token in env for token in (
        "HOST", "PORT", "REQUEST_TIMEOUT", "CLIENT_ID", "CLIENT_SECRET",
        "ACCESS_TOKEN", "REFRESH_TOKEN", "ACCOUNT_ID",
      )
    ) and str(item.get("proposed_path", "")).startswith("analysis."):
      errors.append(f"{item['item_id']}: cTrader bootstrap field under analysis")

  expected_total = catalog.get("counts", {}).get("total_items")
  if expected_total != len(items):
    errors.append(f"counts.total_items={expected_total} but found {len(items)}")
  if errors:
    raise CatalogValidationError(errors)


def load_catalog(path: str | Path) -> dict[str, Any]:
  candidate = Path(path)
  if not candidate.exists():
    repo_candidate = Path(__file__).resolve().parents[3] / candidate
    if repo_candidate.exists():
      candidate = repo_candidate
  return json.loads(candidate.read_text())


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("catalog", type=Path)
  args = parser.parse_args(argv)
  try:
    catalog = load_catalog(args.catalog)
    validate_catalog(catalog)
  except (CatalogValidationError, OSError, json.JSONDecodeError) as exc:
    print(f"catalog validation failed: {exc}", file=sys.stderr)
    return 1
  print(
    "catalog validation passed: "
    f"{len(catalog['items'])} items, version {catalog['catalog_version']}"
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
