"""Phase 2I completion gate — structural canonical-only cutover.

Usage::

  python -m app.configuration.phase2i_completion_gate --check

Successful status: PHASE_2I_COMPLETE

Structural completion only; no production observation result is inferred.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.configuration.generate import REPOSITORY_ROOT, render_artifacts
from app.configuration.phase2i_inventory import (
  AUTHORITY_ENV_SENTINEL,
  evaluate_inventory,
  render_canonical_only_surface,
)
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.python_sources import load_python_runtime_source_bundle


def _generated_artifacts_current() -> list[str]:
  stale: list[str] = []
  for relative, expected in render_artifacts().items():
    target = REPOSITORY_ROOT / relative
    if not target.exists() or target.read_bytes() != expected:
      stale.append(str(relative))
  return stale


def _canonical_startup_ok() -> tuple[bool, str | None]:
  try:
    load_python_canonical_settings(load_python_runtime_source_bundle())
    return True, None
  except CanonicalConfigurationError as exc:
    return False, exc.category


def evaluate_phase2i_completion(
  repository_root: Path | None = None,
) -> dict[str, object]:
  root = repository_root or REPOSITORY_ROOT
  inventory = evaluate_inventory(root)
  stale = _generated_artifacts_current()
  startup_ok, startup_category = _canonical_startup_ok()
  blockers = list(inventory["blockers"])
  if stale:
    blockers.append(f"stale_artifacts={len(stale)}")
  if not startup_ok:
    blockers.append(f"canonical_startup_failed={startup_category}")
  complete = not blockers
  return {
    "status": "PHASE_2I_COMPLETE" if complete else "PHASE_2I_INCOMPLETE",
    "completion_kind": "structural_completion_only",
    "observation_result_inferred": False,
    "note": (
      "structural completion only; no production observation result inferred"
    ),
    "runtime_authority_count": inventory["runtime_authorities"],
    "production_legacy_references": inventory["production_legacy_imports"],
    "test_legacy_references": inventory["test_legacy_imports"],
    "tooling_legacy_references": inventory["tooling_legacy_imports"],
    "deployment_selector_references": inventory["deployment_selector_references"],
    "active_legacy_artifacts": inventory["active_legacy_artifact_paths"],
    "stale_artifacts": stale,
    "canonical_startup_ok": startup_ok,
    "unknown_blockers": inventory["unknown_blockers"],
    "blockers": blockers,
    "authority_env_sentinel": AUTHORITY_ENV_SENTINEL,
    "surface": render_canonical_only_surface(root),
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="app.configuration.phase2i_completion_gate")
  parser.add_argument("--check", action="store_true", required=True)
  parser.parse_args(argv)
  result = evaluate_phase2i_completion()
  print(json.dumps(result, indent=2, sort_keys=True))
  return 0 if result["status"] == "PHASE_2I_COMPLETE" else 1


if __name__ == "__main__":
  raise SystemExit(main())
