"""Migrate flat ENV / .env values into trading-bot.yml + trading-bot.env.

Usage::

  python -m app.configuration.migrate_env_to_config \\
    --env-file .env \\
    --output-dir ./config \\
    --report ./config/migration-report.json

Fails closed when unknown keys remain unclassified.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

import yaml
from dotenv import dotenv_values

from app.configuration.catalog import iter_catalog_entries
from app.configuration.config_file import CONFIG_FILE_ENV
from app.configuration.models.instruments import DEPRECATED_XAU_ENV_ALIASES


_BOOTSTRAP_ENV = frozenset({
  CONFIG_FILE_ENV,
  "AUTO_TRADE_PROFILE",
  "REDIS_URL",
  "LOG_LEVEL",
  "LOG_DIR",
  "LOG_FILE_ENABLED",
  "LOG_RETENTION_DAYS",
  "LOG_FILE_NAME",
  "SERVICE_VERSION",
  "GIT_SHA",
  "HOSTNAME",
  "POSTGRES_DB",
  "POSTGRES_USER",
})


def _env_index() -> dict[str, object]:
  index: dict[str, object] = {}
  for entry in iter_catalog_entries():
    if entry.canonical_env:
      index[entry.canonical_env] = entry
    for alias in entry.deprecated_aliases:
      index[alias] = entry
  return index


def _nested_set(root: dict, path: str, value: object) -> None:
  cursor = root
  parts = path.split(".")
  for part in parts[:-1]:
    child = cursor.setdefault(part, {})
    if not isinstance(child, dict):
      raise ValueError(f"path collision at {path}")
    cursor = child
  cursor[parts[-1]] = value


def _coerce_scalar(raw: str, entry) -> object:
  text = raw.strip()
  declared = entry.type
  if declared == "bool":
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on"}:
      return True
    if lowered in {"0", "false", "no", "off"}:
      return False
    return text
  if declared in {"int", "long"}:
    try:
      return int(text)
    except ValueError:
      return text
  if declared in {"float", "decimal"}:
    try:
      return float(text)
    except ValueError:
      return text
  if declared.startswith("list["):
    return [] if not text else [item.strip() for item in text.split(",")]
  return text


def classify_and_migrate(
  env_values: Mapping[str, str | None],
) -> dict[str, object]:
  """Classify ENV keys and build YAML + secret env payloads."""
  index = _env_index()
  yaml_root: dict[str, object] = {"version": 1, "instruments": {"XAU": {}}}
  secret_env: dict[str, str] = {}
  bootstrap_env: dict[str, str] = {}
  report = {
    "secret": [],
    "bootstrap": [],
    "global_configuration": [],
    "instrument_configuration": [],
    "deprecated_redundant": [],
    "unknown": [],
  }

  xau_instrument: dict[str, object] = {
    "enabled": True,
    "canonical_symbol": "XAU",
    "broker_symbol": "XAU",
    "timeframes": ["H1", "M15", "M5", "M1"],
    "contract": {},
    "market_data": {"lookbacks": {}},
    "analysis": {"zones": {}},
  }

  for key, raw in sorted(env_values.items()):
    if raw is None or str(raw).strip() == "":
      continue
    text = str(raw)
    if key in DEPRECATED_XAU_ENV_ALIASES:
      instrument_path = DEPRECATED_XAU_ENV_ALIASES[key]
      # instruments.XAU.contract.pip_size → contract.pip_size under XAU
      relative = instrument_path.removeprefix("instruments.XAU.")
      entry = index.get(key)
      value = _coerce_scalar(text, entry) if entry is not None else text
      _nested_set(xau_instrument, relative, value)
      report["instrument_configuration"].append({
        "env": key,
        "destination": instrument_path,
      })
      # Prefer instrument block; skip also writing the flat leaf to avoid
      # duplicate conflict with projection.
      continue

    if key in _BOOTSTRAP_ENV:
      bootstrap_env[key] = text
      report["bootstrap"].append({"env": key, "destination": "trading-bot.env"})
      continue

    entry = index.get(key)
    if entry is None:
      report["unknown"].append({"env": key})
      continue

    if entry.secret:
      secret_env[key] = text
      report["secret"].append({
        "env": key,
        "destination": "trading-bot.env",
        "path": entry.path,
      })
      continue

    if not entry.configurable:
      report["deprecated_redundant"].append({
        "env": key,
        "path": entry.path,
        "reason": "non-configurable catalog leaf",
      })
      continue

    # Global catalog leaf → YAML nested path (skip XAU flat leaves already
    # covered by DEPRECATED_XAU map; any remaining XAU_* go to instruments).
    if key.startswith("XAU_") or key.startswith("AUTO_TRADE_XAU_"):
      report["instrument_configuration"].append({
        "env": key,
        "destination": entry.path,
        "note": "mapped via flat leaf path; prefer instruments.XAU",
      })
      _nested_set(yaml_root, entry.path, _coerce_scalar(text, entry))
      continue

    _nested_set(yaml_root, entry.path, _coerce_scalar(text, entry))
    report["global_configuration"].append({
      "env": key,
      "destination": entry.path,
    })

  # Fill instrument from flat leaf leftovers when present in yaml_root
  contract = yaml_root.get("contract")
  if isinstance(contract, dict):
    instrument = contract.get("instrument")
    if isinstance(instrument, dict):
      for field_name, target in (
        ("pip_size", "contract.pip_size"),
        ("contract_units_per_lot", "contract.contract_units_per_lot"),
        ("price_digits", "contract.price_digits"),
        ("canonical_symbol", "canonical_symbol"),
      ):
        if field_name in instrument and not _has_path(xau_instrument, target):
          _nested_set(xau_instrument, target, instrument[field_name])

  yaml_root["instruments"] = {"XAU": xau_instrument}

  return {
    "yaml": yaml_root,
    "env": {**bootstrap_env, **secret_env},
    "report": report,
    "unknown_count": len(report["unknown"]),
  }


def _has_path(root: dict, path: str) -> bool:
  cursor: object = root
  for part in path.split("."):
    if not isinstance(cursor, dict) or part not in cursor:
      return False
    cursor = cursor[part]
  return True


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.migrate_env_to_config")
  parser.add_argument("--env-file", required=True, help="Input .env path")
  parser.add_argument(
    "--output-dir",
    required=True,
    help="Directory for trading-bot.yml and trading-bot.env",
  )
  parser.add_argument(
    "--report",
    default=None,
    help="Optional JSON classification report path",
  )
  args = parser.parse_args(argv)

  env_path = Path(args.env_file)
  if not env_path.is_file():
    print(f"env file not found: {env_path}", flush=True)
    return 2

  values = {
    str(key): value
    for key, value in dotenv_values(env_path).items()
  }
  result = classify_and_migrate(values)
  if result["unknown_count"]:
    print(
      json.dumps(
        {
          "status": "MIGRATION_FAILED_UNKNOWN_KEYS",
          "unknown": result["report"]["unknown"],
        },
        indent=2,
        sort_keys=True,
      )
    )
    return 1

  out_dir = Path(args.output_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  yaml_path = out_dir / "trading-bot.yml"
  env_out = out_dir / "trading-bot.env"
  yaml_path.write_text(
    yaml.safe_dump(result["yaml"], sort_keys=True, default_flow_style=False),
    encoding="utf-8",
  )
  env_lines = [
    f"{key}={value}"
    for key, value in sorted(result["env"].items())
  ]
  env_out.write_text("\n".join(env_lines) + ("\n" if env_lines else ""), encoding="utf-8")

  report_path = Path(args.report) if args.report else out_dir / "migration-report.json"
  report_path.write_text(
    json.dumps(
      {
        "status": "MIGRATION_OK",
        "yaml_path": str(yaml_path),
        "env_path": str(env_out),
        "classification": result["report"],
      },
      indent=2,
      sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
  )
  print(json.dumps({
    "status": "MIGRATION_OK",
    "yaml_path": str(yaml_path),
    "env_path": str(env_out),
    "report_path": str(report_path),
  }, indent=2, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
