"""Canonical-only configuration diagnostic CLI.

Usage::

  python -m app.configuration.diagnostic_cli --check

Successful status: CANONICAL_CONFIGURATION_VALID

This tool never constructs legacy Settings, never compares to a flat facade,
and never claims rollback readiness.
"""

from __future__ import annotations

import argparse
import json

from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.python_sources import load_python_runtime_source_bundle


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
    })
  return rows


def run_check() -> dict[str, object]:
  try:
    bundle = load_python_runtime_source_bundle()
    result = load_python_canonical_settings(bundle)
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
  return {
    "status": "CANONICAL_CONFIGURATION_VALID",
    "profile": result.profile,
    "catalog_fingerprint": result.catalog_fingerprint,
    "profile_fingerprint": result.profile_fingerprint,
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
    "provenance": _redacted_provenance(result.provenance),
    "success": result.success,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.diagnostic_cli")
  parser.add_argument("--check", action="store_true", required=True)
  parser.parse_args(argv)
  payload = run_check()
  print(json.dumps(payload, indent=2, sort_keys=True))
  return 0 if payload["status"] == "CANONICAL_CONFIGURATION_VALID" else 1


if __name__ == "__main__":
  raise SystemExit(main())
