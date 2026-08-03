"""Phase 2I removal gate — canonical-cutover static + observation evidence.

Phase 2I-A makes canonical the managed-deployment default and removes every
production ``runtime_config_facade()`` call while retaining the legacy authority
for rollback. Deleting the legacy stack is *not* a static decision: it requires
a real canonical observation window run by an operator. This gate enforces that
separation.

* ``--check-static`` runs the machine-checkable Phase 2I-A acceptance criteria.
  When they all pass it reports ``READY_FOR_CANONICAL_OBSERVATION``. It can
  never report ``READY_TO_DELETE_LEGACY`` -- that conclusion needs observation
  evidence a static analyzer cannot produce.
* ``--check-observation <path>`` validates the *structure and completeness* of
  an operator-supplied evidence file only. It never inspects live systems and
  never fabricates results; a structurally valid, complete file yields
  ``READY_FOR_PHASE_2I_B_REVIEW`` (a review gate, still not an auto-delete).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.configuration.bootstrap_authority import (
  RuntimeConfigurationAuthority,
  resolve_runtime_configuration_authority,
)
from app.configuration.compatibility_surface_audit import (
  audit_compatibility_surface,
)
from app.configuration.generate import REPOSITORY_ROOT, render_artifacts
from app.configuration.phase2h_gate import _production_settings_imports
from app.configuration.usage_audit import audit_legacy_settings_usage


_COMPOSE = REPOSITORY_ROOT / "docker-compose.yml"
_TEMPLATE = REPOSITORY_ROOT / "deployment-template" / "docker-compose.yml.j2"
_ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"

_MINIMUM_TRADING_DAYS = 5


def _production_flat_reads(usage: dict[str, object]) -> int:
  production = usage["production"]
  return (
    len(production["attribute_reads"])
    + sum(
      len(item["dynamic_names"] or (
        [item["attribute"]] if item["attribute"] is not None else []
      ))
      for item in production["introspection"]
    )
  )


def _compose_default_canonical() -> bool:
  text = _COMPOSE.read_text(encoding="utf-8")
  return "APEXVOID_CONFIG_AUTHORITY:-canonical}" in text


def _template_default_canonical() -> bool:
  text = _TEMPLATE.read_text(encoding="utf-8")
  return "'APEXVOID_CONFIG_AUTHORITY': 'canonical'" in text


def _env_example_default_canonical() -> bool:
  text = _ENV_EXAMPLE.read_text(encoding="utf-8")
  return "APEXVOID_CONFIG_AUTHORITY=canonical" in text


def _legacy_still_startable() -> bool:
  """Static proxy: legacy authority still resolvable and its stack importable."""
  if resolve_runtime_configuration_authority({}) is not (
    RuntimeConfigurationAuthority.LEGACY
  ):
    return False
  if resolve_runtime_configuration_authority(
    {"APEXVOID_CONFIG_AUTHORITY": "legacy"}
  ) is not RuntimeConfigurationAuthority.LEGACY:
    return False
  try:
    from app.configuration.facade import CanonicalSettingsFacade  # noqa: F401
    from app.configuration.legacy_canonical_view import (  # noqa: F401
      LegacyCanonicalConfigView,
    )
    from app.configuration.legacy_settings import LegacySettings  # noqa: F401
  except Exception:
    return False
  return True


def evaluate_static_readiness() -> dict[str, object]:
  usage = audit_legacy_settings_usage(REPOSITORY_ROOT)
  compatibility = audit_compatibility_surface(REPOSITORY_ROOT)
  flat_reads = _production_flat_reads(usage)
  settings_imports = _production_settings_imports()
  dynamic_lookups = len(usage["production"]["introspection"])
  facade_calls = int(compatibility["counts"]["production_facade_calls"])
  unknown_blockers = int(compatibility["counts"]["unknown_blockers"])
  artifacts = render_artifacts()
  stale = [
    str(path) for path, expected in artifacts.items()
    if (REPOSITORY_ROOT / path).read_bytes() != expected
  ]
  compose_ok = _compose_default_canonical()
  template_ok = _template_default_canonical()
  env_ok = _env_example_default_canonical()
  legacy_startable = _legacy_still_startable()

  blockers: list[str] = []
  if settings_imports:
    blockers.append(f"production_settings_imports={len(settings_imports)}")
  if flat_reads:
    blockers.append(f"production_flat_reads={flat_reads}")
  if facade_calls:
    blockers.append(f"production_facade_calls={facade_calls}")
  if dynamic_lookups:
    blockers.append(f"production_dynamic_lookups={dynamic_lookups}")
  if not compose_ok:
    blockers.append("compose_default_not_canonical")
  if not template_ok:
    blockers.append("template_default_not_canonical")
  if not env_ok:
    blockers.append("env_example_default_not_canonical")
  if not legacy_startable:
    blockers.append("legacy_not_startable")
  if stale:
    blockers.append(f"stale_artifacts={len(stale)}")
  if unknown_blockers:
    blockers.append(f"unknown_compatibility_blockers={unknown_blockers}")

  ready = not blockers
  return {
    "phase": "2I-A",
    "check": "static",
    "status": (
      "READY_FOR_CANONICAL_OBSERVATION" if ready else "NOT_READY"
    ),
    "production_settings_imports": len(settings_imports),
    "production_settings_import_details": settings_imports,
    "production_flat_reads": flat_reads,
    "production_facade_calls": facade_calls,
    "production_dynamic_lookups": dynamic_lookups,
    "compose_default_canonical": compose_ok,
    "template_default_canonical": template_ok,
    "env_example_default_canonical": env_ok,
    "legacy_still_startable": legacy_startable,
    "stale_artifacts": stale,
    "compatibility_unknown_blockers": unknown_blockers,
    "compatibility_counts": compatibility["counts"]["by_classification"],
    "blockers": blockers,
    "note": (
      "static analysis can only certify READY_FOR_CANONICAL_OBSERVATION; "
      "READY_TO_DELETE_LEGACY requires operator observation evidence"
    ),
  }


def _validate_observation_evidence(payload: object) -> list[str]:
  """Return the list of structural problems; empty means complete + valid."""
  problems: list[str] = []
  if not isinstance(payload, dict):
    return ["evidence root is not a JSON object"]

  if payload.get("phase") != "2I-A":
    problems.append("phase must equal '2I-A'")
  if payload.get("authority") != "canonical":
    problems.append("authority must equal 'canonical'")

  window = payload.get("observation_window")
  if not isinstance(window, dict):
    problems.append("observation_window object is missing")
  else:
    for key in ("start_date", "end_date"):
      if not isinstance(window.get(key), str) or not window.get(key):
        problems.append(f"observation_window.{key} must be a non-empty string")
    trading_days = window.get("trading_days")
    if not isinstance(trading_days, list):
      problems.append("observation_window.trading_days must be a list")
    elif len(trading_days) < _MINIMUM_TRADING_DAYS:
      problems.append(
        "observation_window.trading_days must cover at least "
        f"{_MINIMUM_TRADING_DAYS} trading days"
      )

  sessions = payload.get("sessions_observed")
  if not isinstance(sessions, list) or not sessions:
    problems.append("sessions_observed must be a non-empty list")

  restarts = payload.get("restarts")
  if not isinstance(restarts, list) or not restarts:
    problems.append("restarts must be a non-empty list")
  else:
    for index, restart in enumerate(restarts):
      if not isinstance(restart, dict):
        problems.append(f"restarts[{index}] must be an object")
        continue
      for key in ("timestamp", "authority", "outcome"):
        if not isinstance(restart.get(key), str) or not restart.get(key):
          problems.append(f"restarts[{index}].{key} must be a non-empty string")

  health = payload.get("config_health_checks")
  if not isinstance(health, list) or not health:
    problems.append("config_health_checks must be a non-empty list")
  else:
    for index, check in enumerate(health):
      if not isinstance(check, dict):
        problems.append(f"config_health_checks[{index}] must be an object")
        continue
      for key in ("timestamp", "status"):
        if not isinstance(check.get(key), str) or not check.get(key):
          problems.append(
            f"config_health_checks[{index}].{key} must be a non-empty string"
          )

  if not isinstance(payload.get("incidents"), list):
    problems.append("incidents must be a list (may be empty)")

  sign_off = payload.get("sign_off")
  if not isinstance(sign_off, dict):
    problems.append("sign_off object is missing")
  else:
    for key in ("approved_by", "date"):
      if not isinstance(sign_off.get(key), str) or not sign_off.get(key):
        problems.append(f"sign_off.{key} must be a non-empty string")

  return problems


def evaluate_observation_evidence(path: Path) -> dict[str, object]:
  try:
    payload = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return {
      "phase": "2I-A",
      "check": "observation",
      "status": "NOT_READY",
      "evidence_path": str(path),
      "problems": ["evidence file not found"],
    }
  except json.JSONDecodeError as exc:
    return {
      "phase": "2I-A",
      "check": "observation",
      "status": "NOT_READY",
      "evidence_path": str(path),
      "problems": [f"evidence file is not valid JSON: {exc}"],
    }
  problems = _validate_observation_evidence(payload)
  ready = not problems
  return {
    "phase": "2I-A",
    "check": "observation",
    "status": (
      "READY_FOR_PHASE_2I_B_REVIEW" if ready else "NOT_READY"
    ),
    "evidence_path": str(path),
    "problems": problems,
    "note": (
      "structural validation only; this gate does not observe live systems "
      "and never fabricates observation results"
    ),
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  mode = parser.add_mutually_exclusive_group(required=True)
  mode.add_argument("--check-static", action="store_true")
  mode.add_argument("--check-observation", metavar="PATH")
  arguments = parser.parse_args(argv)
  if arguments.check_static:
    result = evaluate_static_readiness()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "READY_FOR_CANONICAL_OBSERVATION" else 1
  result = evaluate_observation_evidence(Path(arguments.check_observation))
  print(json.dumps(result, indent=2, sort_keys=True))
  return 0 if result["status"] == "READY_FOR_PHASE_2I_B_REVIEW" else 1


if __name__ == "__main__":
  raise SystemExit(main())
