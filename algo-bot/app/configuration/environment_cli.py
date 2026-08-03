"""CLI for inspecting the configuration environment contract and usage.

Usage::

  python -m app.configuration.environment_cli --check
  python -m app.configuration.environment_cli --strict
  python -m app.configuration.environment_cli --report-deprecated
  python -m app.configuration.environment_cli --report-unknown

``--check`` fails when any raw environment access is unclassified
(``UNKNOWN_BLOCKER``). ``--strict`` additionally fails when production code
still performs a directly forbidden ambient read. The report modes are
informational and always exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.configuration.environment_aliases import (
  detect_environment_alias_conflicts,
  present_deprecated_aliases,
)
from app.configuration.environment_contract import deprecated_environment_aliases
from app.configuration.environment_usage_audit import audit_environment_usage


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _audit() -> dict[str, object]:
  return audit_environment_usage(REPOSITORY_ROOT)


def _check(strict: bool) -> int:
  audit = _audit()
  unknown = int(audit["unknown_blockers"])
  forbidden = int(audit["direct_production_env_forbidden"])
  conflicts = detect_environment_alias_conflicts(os.environ)
  result = {
    "counts": audit["counts"],
    "unknown_blockers": unknown,
    "direct_production_env_forbidden": forbidden,
    "alias_conflicts": [conflict.as_dict() for conflict in conflicts],
    "strict": strict,
  }
  print(json.dumps(result, indent=2, sort_keys=True))
  if conflicts:
    return 1
  if unknown:
    return 1
  if strict and forbidden:
    return 1
  return 0


def _report_deprecated() -> int:
  contract = deprecated_environment_aliases()
  present = {usage.deprecated_alias for usage in present_deprecated_aliases(os.environ)}
  rows = [
    {**row, "present_in_environment": row["deprecated_alias"] in present}
    for row in contract
  ]
  print(json.dumps({"count": len(rows), "aliases": rows}, indent=2, sort_keys=True))
  return 0


def _report_unknown() -> int:
  audit = _audit()
  flagged = [
    access for access in audit["accesses"]
    if not access["allowed"]
  ]
  print(json.dumps({"count": len(flagged), "accesses": flagged}, indent=2, sort_keys=True))
  return 0


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.environment_cli")
  mode = parser.add_mutually_exclusive_group(required=True)
  mode.add_argument("--check", action="store_true")
  mode.add_argument("--strict", action="store_true")
  mode.add_argument("--report-deprecated", action="store_true")
  mode.add_argument("--report-unknown", action="store_true")
  arguments = parser.parse_args(argv)
  if arguments.report_deprecated:
    return _report_deprecated()
  if arguments.report_unknown:
    return _report_unknown()
  return _check(strict=arguments.strict)


if __name__ == "__main__":
  raise SystemExit(main())
