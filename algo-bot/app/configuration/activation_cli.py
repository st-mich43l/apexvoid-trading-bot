"""Offline, secret-safe diagnostics for non-authoritative activation rehearsal."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.configuration.activation_rehearsal import VerificationEvidence
from app.configuration.activation_rehearsal import run_activation_rehearsal
from app.configuration.activation_verification import detect_csharp_verification_status
from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
from app.configuration.generate import render_artifacts
from app.configuration.readiness import ActivationEvidence
from app.configuration.readiness import evaluate_activation_readiness
from app.configuration.shadow_cli import collect_source_bundle
from app.configuration.shadow_loader import load_shadow_configuration
from app.configuration.usage_audit import audit_legacy_settings_usage


_BANNER = "NON-AUTHORITATIVE ACTIVATION REHEARSAL"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _legacy_settings(
  env_file: Path | None,
  profile: str | None,
  dotenv_environment: dict[str, str],
) -> tuple[object, object]:
  # Importing the legacy module constructs its unchanged singleton. For this
  # offline CLI only, expose explicitly supplied dotenv values during import.
  with patch.dict(os.environ, dotenv_environment, clear=False):
    from app.core import config as active_config
    from app.core.config import Settings
  values: dict[str, object] = {"_env_file": env_file}
  if profile is not None:
    values["auto_trade_profile"] = profile
  return Settings(**values), active_config.settings


def _report(result, *, csharp_status: str) -> dict[str, Any]:
  return {
    "banner": _BANNER,
    "authoritative": False,
    "shadow_status": result.shadow_status.value,
    "compatibility_success": result.success,
    "direct_parity": {
      "equal": result.direct_parity_equal,
      "total": result.direct_parity_total,
    },
    "derived_parity": {
      "equal": result.derived_parity_equal,
      "total": result.derived_parity_total,
    },
    "facade_parity": {
      "equal": result.facade_parity_equal,
      "total": result.facade_parity_total,
    },
    "unsupported_usage_count": result.unsupported_usage_count,
    "activation_ready": result.readiness.ready,
    "blocker_codes": [code.value for code in result.readiness.blockers],
    "warnings": list(result.readiness.warnings),
    "evaluated_evidence": asdict(result.readiness.evaluated_evidence),
    "rollback_success": result.rollback_result.success,
    "catalog_fingerprint": result.catalog_fingerprint,
    "csharp_status": csharp_status,
  }


def _incomplete_report(source_bundle, *, csharp_status: str) -> dict[str, Any]:
  shadow = load_shadow_configuration(source_bundle)
  usage = audit_legacy_settings_usage(_REPOSITORY_ROOT)
  generated_current = all(
    (_REPOSITORY_ROOT / path).exists()
    and (_REPOSITORY_ROOT / path).read_bytes() == expected
    for path, expected in render_artifacts().items()
  )
  evidence = ActivationEvidence(
    catalog_parity_complete=(
      len(DIRECT_LEGACY_PATHS) == 316
      and len(DERIVED_LEGACY_PROPERTIES) == 4
    ),
    source_parity_complete=False,
    facade_parity_complete=False,
    derived_parity_complete=False,
    provenance_complete=False,
    generated_artifacts_current=generated_current,
    secret_redaction_complete=True,
    compatibility_usage_supported=not usage["activation_blockers"],
    rollback_rehearsal_passed=False,
    python_configuration_tests_passed=False,
    python_behavior_baseline_not_worsened=False,
    python_behavior_tests_passed=False,
    csharp_tests_verified=False,
  )
  readiness = evaluate_activation_readiness(evidence)
  return {
    "banner": _BANNER,
    "authoritative": False,
    "shadow_status": shadow.status.value,
    "compatibility_success": False,
    "direct_parity": {"equal": 0, "total": len(DIRECT_LEGACY_PATHS)},
    "derived_parity": {"equal": 0, "total": len(DERIVED_LEGACY_PROPERTIES)},
    "facade_parity": {
      "equal": 0,
      "total": len(DIRECT_LEGACY_PATHS) + len(DERIVED_LEGACY_PROPERTIES),
    },
    "unsupported_usage_count": len(usage["activation_blockers"]),
    "activation_ready": False,
    "blocker_codes": [code.value for code in readiness.blockers],
    "warnings": list(readiness.warnings),
    "evaluated_evidence": asdict(evidence),
    "rollback_success": False,
    "catalog_fingerprint": shadow.catalog_fingerprint,
    "csharp_status": csharp_status,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Offline canonical configuration activation rehearsal",
  )
  parser.add_argument("--env-file", type=Path)
  parser.add_argument("--profile")
  parser.add_argument("--report-readiness", action="store_true")
  parser.add_argument("--report-usage", action="store_true")
  parser.add_argument("--report-parity", action="store_true")
  parser.add_argument("--report-blockers", action="store_true")
  parser.add_argument("--json-output", type=Path)
  arguments = parser.parse_args(argv)
  source_bundle = collect_source_bundle(
    env_file=arguments.env_file,
    profile=arguments.profile,
  )
  csharp = detect_csharp_verification_status()
  preliminary_shadow = load_shadow_configuration(source_bundle)
  result = None
  if preliminary_shadow.success:
    dotenv_environment = {
      name: value
      for name, value in source_bundle.dotenv_values.items()
      if value is not None
    }
    legacy, active_global = _legacy_settings(
      arguments.env_file,
      arguments.profile,
      dotenv_environment,
    )
    result = run_activation_rehearsal(
      source_bundle=source_bundle,
      legacy_settings=legacy,
      active_global_settings=active_global,
      repository_root=_REPOSITORY_ROOT,
      verification=VerificationEvidence(),
    )
    report = _report(result, csharp_status=csharp.status)
  else:
    report = _incomplete_report(source_bundle, csharp_status=csharp.status)
  encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
  print(_BANNER)
  print(f"shadow status: {report['shadow_status']}")
  print(
    "direct parity: "
    f"{report['direct_parity']['equal']}/{report['direct_parity']['total']}"
  )
  print(
    "derived parity: "
    f"{report['derived_parity']['equal']}/{report['derived_parity']['total']}"
  )
  print(
    "facade parity: "
    f"{report['facade_parity']['equal']}/{report['facade_parity']['total']}"
  )
  print(f"unsupported usage: {report['unsupported_usage_count']}")
  print(f"activation ready: {str(report['activation_ready']).lower()}")
  print(
    "blocker codes: "
    + (", ".join(report["blocker_codes"]) or "none")
  )
  print(f"catalog fingerprint: {report['catalog_fingerprint']}")
  if arguments.report_usage:
    status = "supported" if not report["unsupported_usage_count"] else "blocked"
    print(f"usage compatibility: {status}")
  if arguments.report_parity:
    status = "passed" if report["rollback_success"] else "failed"
    print(f"rollback rehearsal: {status}")
  if arguments.report_blockers:
    for code in report["blocker_codes"]:
      print(f"blocker: {code}")
  if arguments.report_readiness:
    for warning in report["warnings"]:
      print(f"warning: {warning}")
  if arguments.json_output is not None:
    arguments.json_output.write_text(encoded, encoding="utf-8")
  return 0 if report["compatibility_success"] else 2


if __name__ == "__main__":
  raise SystemExit(main())
