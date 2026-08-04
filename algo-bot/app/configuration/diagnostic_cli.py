"""Canonical-only configuration diagnostic CLI.

Usage::

  python -m app.configuration.diagnostic_cli --check
  python -m app.configuration.diagnostic_cli --check \\
    --config-file ./config/trading-bot.yml --show-sources

Successful status: CANONICAL_CONFIGURATION_VALID

This tool never constructs legacy Settings, never compares to a flat facade,
and never claims rollback readiness.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

from app.configuration.config_file import ConfigFileError
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import (
  PYTHON_SOURCE_POLICY,
  PythonConfigurationSourcePolicy,
)


def _redacted_provenance(trace) -> list[dict[str, object]]:
  rows: list[dict[str, object]] = []
  for item in trace.fields:
    rows.append({
      "path": item.path,
      "source_kind": item.source_kind.value,
      "source_name": item.source_name,
      "canonical_env": item.canonical_env,
      "profile_name": item.profile_name,
      "secret": item.secret,
      "explicit": item.explicit,
      "compatibility_rule": item.compatibility_rule,
    })
  return rows


def _instrument_summary(config) -> dict[str, object]:
  instruments = getattr(config, "instruments", None)
  if instruments is None:
    return {"count": 0, "enabled": [], "symbols": []}
  mapping = getattr(instruments, "root", instruments)
  if not isinstance(mapping, dict):
    return {"count": 0, "enabled": [], "symbols": []}
  enabled = sorted(
    key for key, value in mapping.items()
    if getattr(value, "enabled", False)
  )
  return {
    "count": len(mapping),
    "enabled": enabled,
    "symbols": sorted(mapping),
  }


def run_check(
  *,
  config_file: str | None = None,
  show_sources: bool = False,
) -> dict[str, object]:
  policy = PYTHON_SOURCE_POLICY
  if config_file is not None:
    policy = replace(policy, config_file=config_file)
  try:
    bundle = load_python_runtime_source_bundle(policy)
    result = load_python_canonical_settings(bundle)
  except ConfigFileError as exc:
    return {
      "status": "CANONICAL_CONFIGURATION_INVALID",
      "error_category": "config_file_error",
      "canonical_path": str(exc.path or "<config_file>"),
      "message": str(exc),
      "recovery_action": (
        "correct the reported configuration input and restart the service"
      ),
    }
  except CanonicalConfigurationError as exc:
    return {
      "status": "CANONICAL_CONFIGURATION_INVALID",
      "error_category": exc.category,
      "canonical_path": exc.path,
      "catalog_fingerprint": exc.catalog_fingerprint,
      "recovery_action": (
        "correct the reported configuration input and restart the service"
      ),
    }
  payload: dict[str, object] = {
    "status": "CANONICAL_CONFIGURATION_VALID",
    "profile": result.profile,
    "catalog_fingerprint": result.catalog_fingerprint,
    "profile_fingerprint": result.profile_fingerprint,
    "instruments": _instrument_summary(result.config),
    "warnings": [
      {
        "code": warning.code,
        "path": warning.path,
        "source_name": warning.source_name,
        "message": warning.message,
        "secret": warning.secret,
      }
      for warning in result.warnings
    ],
    "success": result.success,
  }
  if show_sources:
    payload["provenance"] = _redacted_provenance(result.provenance)
    payload["config_file_leaf_count"] = len(bundle.config_file_values)
  return payload


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.diagnostic_cli")
  parser.add_argument("--check", action="store_true", required=True)
  parser.add_argument(
    "--config-file",
    default=None,
    help="YAML CONFIG_FILE path (overrides APEXVOID_CONFIG_FILE)",
  )
  parser.add_argument(
    "--show-sources",
    action="store_true",
    help="Include redacted per-leaf provenance",
  )
  args = parser.parse_args(argv)
  payload = run_check(
    config_file=args.config_file,
    show_sources=args.show_sources,
  )
  print(json.dumps(payload, indent=2, sort_keys=True))
  return 0 if payload["status"] == "CANONICAL_CONFIGURATION_VALID" else 1


if __name__ == "__main__":
  raise SystemExit(main())
