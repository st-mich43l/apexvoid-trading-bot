"""Phase 2H readiness gate — configuration consumer migration complete."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from app.configuration.generate import PHASE_2G_ROOTS, REPOSITORY_ROOT, render_artifacts
from app.configuration.usage_audit import audit_legacy_settings_usage


_MANIFEST = (
  REPOSITORY_ROOT
  / "contracts/configuration/consumer-migration-phase-2g.generated.json"
)
_PRODUCTION_APP = REPOSITORY_ROOT / "algo-bot" / "app"
_ALLOWED_SETTINGS_IMPORT_PATHS = frozenset({
  "algo-bot/app/core/config.py",
})


def _rel(path: Path) -> str:
  return path.relative_to(REPOSITORY_ROOT).as_posix()


def _production_settings_imports() -> list[str]:
  bad: list[str] = []
  for path in _PRODUCTION_APP.rglob("*.py"):
    rel = _rel(path)
    if rel.startswith("algo-bot/app/configuration/"):
      continue
    if rel in _ALLOWED_SETTINGS_IMPORT_PATHS:
      continue
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    for node in ast.walk(tree):
      if isinstance(node, ast.ImportFrom) and node.module == "app.core.config":
        for alias in node.names:
          if alias.name in {"settings", "Settings", "CanonicalSettingsFacade",
                            "LegacyCanonicalConfigView"}:
            bad.append(f"{rel}:{node.lineno}:{alias.name}")
      if isinstance(node, ast.ImportFrom) and node.module == "app.core":
        for alias in node.names:
          if alias.name == "config":
            # `from app.core import config` then config.settings is also banned.
            text = path.read_text(encoding="utf-8")
            if re.search(r"\bconfig\.settings\b", text):
              bad.append(f"{rel}:{node.lineno}:config.settings")
  return bad


def evaluate_phase2h_readiness() -> dict[str, object]:
  usage = audit_legacy_settings_usage(REPOSITORY_ROOT)
  production = usage["production"]
  flat_reads = (
    len(production["attribute_reads"])
    + sum(
      len(item["dynamic_names"] or (
        [item["attribute"]] if item["attribute"] is not None else []
      ))
      for item in production["introspection"]
    )
  )
  imports = _production_settings_imports()
  manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
  unknown = int(manifest["counts"]["unknown_blockers"])
  remaining = int(manifest["counts"]["eligible_reads_remaining"])
  artifacts = render_artifacts()
  stale = [
    str(path) for path, expected in artifacts.items()
    if (REPOSITORY_ROOT / path).read_bytes() != expected
  ]
  authority = "canonical"
  blockers: list[str] = []
  if flat_reads:
    blockers.append(f"production_flat_reads={flat_reads}")
  if imports:
    blockers.append(f"production_settings_imports={len(imports)}")
  if production["introspection"]:
    blockers.append(
      f"production_dynamic_lookups={len(production['introspection'])}"
    )
  if unknown:
    blockers.append(f"unknown_blockers={unknown}")
  if remaining:
    blockers.append(f"eligible_reads_remaining={remaining}")
  if stale:
    blockers.append(f"stale_artifacts={len(stale)}")
  if set(manifest["candidate_roots"]) != set(PHASE_2G_ROOTS):
    blockers.append("manifest_roots_mismatch")
  ready = not blockers
  return {
    "status": "READY_FOR_PHASE_2H" if ready else "NOT_READY",
    "authority_default_assumed": authority,
    "production_flat_reads": flat_reads,
    "production_settings_imports": len(imports),
    "production_settings_import_details": imports,
    "production_dynamic_lookups": len(production["introspection"]),
    "unknown_blockers": unknown,
    "eligible_reads_remaining": remaining,
    "stale_artifacts": stale,
    "blockers": blockers,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--check", action="store_true", required=True)
  parser.parse_args(argv)
  result = evaluate_phase2h_readiness()
  print(json.dumps(result, indent=2, sort_keys=True))
  return 0 if result["status"] == "READY_FOR_PHASE_2H" else 1


if __name__ == "__main__":
  raise SystemExit(main())
