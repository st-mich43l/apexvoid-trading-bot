"""Offline, non-authoritative and secret-safe shadow-load diagnostics."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.configuration.shadow_loader import load_shadow_configuration
from app.configuration.source_types import ConfigurationSourceBundle


def collect_source_bundle(
  *,
  env_file: Path | None,
  profile: str | None,
) -> ConfigurationSourceBundle:
  dotenv = (
    {
      str(key): value
      for key, value in dotenv_values(env_file).items()
    }
    if env_file is not None else {}
  )
  init = {"runtime.profile": profile} if profile is not None else {}
  return ConfigurationSourceBundle(
    init_values=init,
    process_environment=dict(os.environ),
    dotenv_values=dotenv,
  )


def _report(result, *, include_sources: bool, include_warnings: bool):
  report: dict[str, Any] = {
    "banner": "NON-AUTHORITATIVE SHADOW LOAD",
    "authoritative": False,
    "success": result.success,
    "status": result.status.value,
    "profile": result.profile,
    "catalog_fingerprint": result.catalog_fingerprint,
    "profile_fingerprint": result.profile_fingerprint,
    "resolved_field_count": len(result.trace.fields),
    "missing_required_paths": list(result.missing_required_paths),
    "warning_count": len(result.warnings),
    "conflict_count": len(result.conflicts),
    "validation_error_count": len(result.validation_errors),
  }
  if include_sources:
    report["sources"] = [
      {
        "path": item.path,
        "item_id": item.item_id,
        "source_kind": item.source_kind.value,
        "source_name": item.source_name,
        "canonical_env": item.canonical_env,
        "supplied_alias": item.supplied_alias,
        "explicit": item.explicit,
        "overridden_lower_precedence_sources": list(
          item.overridden_lower_precedence_sources
        ),
        "profile_name": item.profile_name,
        "compatibility_rule": item.compatibility_rule,
        "secret": item.secret,
      }
      for item in result.trace.fields
    ]
  if include_warnings:
    report["warnings"] = [
      {**asdict(item), "source_kind": item.source_kind.value}
      for item in result.warnings
    ]
    report["conflicts"] = [
      {**asdict(item), "source_kind": item.source_kind.value}
      for item in result.conflicts
    ]
    report["validation_errors"] = list(result.validation_errors)
  return report


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Offline ApexVoidConfig shadow diagnostics",
  )
  parser.add_argument("--env-file", type=Path)
  parser.add_argument("--profile")
  parser.add_argument("--report-summary", action="store_true")
  parser.add_argument("--report-sources", action="store_true")
  parser.add_argument("--report-warnings", action="store_true")
  parser.add_argument("--json-output", type=Path)
  arguments = parser.parse_args(argv)
  result = load_shadow_configuration(collect_source_bundle(
    env_file=arguments.env_file,
    profile=arguments.profile,
  ))
  report = _report(
    result,
    include_sources=arguments.report_sources,
    include_warnings=arguments.report_warnings,
  )
  encoded = json.dumps(
    report, indent=2, sort_keys=True, ensure_ascii=False,
  ) + "\n"
  print("NON-AUTHORITATIVE SHADOW LOAD")
  print(
    f"status={result.status.value} profile={result.profile} "
    f"fields={len(result.trace.fields)} warnings={len(result.warnings)} "
    f"conflicts={len(result.conflicts)}"
  )
  if arguments.report_sources:
    for item in result.trace.fields:
      print(f"{item.path}: {item.source_kind.value} ({item.source_name})")
  if arguments.report_warnings:
    for item in (*result.warnings, *result.conflicts):
      print(item.message)
    for error in result.validation_errors:
      print(error)
  if arguments.json_output is not None:
    arguments.json_output.write_text(encoded, encoding="utf-8")
  return 0 if result.success else 2


if __name__ == "__main__":
  raise SystemExit(main())
