#!/usr/bin/env python3
"""config-check — the one canonical validation command for Configuration
V3 (§35), for what actually exists today.

Honest scope: this is Stage C2's own self-check, not the full §35 list.
It runs:

  - YAML syntax on every config/*.yml and config/environments/*.yml file.
  - Stage C2 parity (verify_stage_c2_parity.py): every leaf either matches
    Stage C1's old-path value, or is one of the individually documented,
    individually verified divergences.
  - Schema validation (resolve_reference.py) for every environments/*.yml:
    include-graph resolution (missing/duplicate/absolute-escaping include,
    duplicate top-level base ownership), the deep-merge overlay, and the
    result against apexvoid-config-v3.schema.json.

It does NOT (yet — tracked against Stage C3/C4/C5/C6 in
docs/configuration-v3-migration-audit.md, not silently skipped):
  - Cross-language parity (Go/.NET readers don't exist yet).
  - Forbidden-ENV-usage source scanning (§36).
  - "old manifest/trading-bot.yml absence" (§28/§29 — both still the live
    authority; this script does not assume or enforce their removal).

Run from the repository root:

    algo-bot/.venv/bin/python config/scripts/config_check.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config"
PYTHON = sys.executable


def check_yaml_syntax() -> bool:
  ok = True
  files = sorted(CONFIG.glob("*.yml")) + sorted((CONFIG / "environments").glob("*.yml"))
  for path in files:
    try:
      yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
      print(f"[syntax] FAIL {path.relative_to(REPO_ROOT)}: {exc}", file=sys.stderr)
      ok = False
  if ok:
    print(f"[syntax] OK — {len(files)} files")
  return ok


def run_script(relative: str, *args: str) -> bool:
  env = dict(os.environ)
  env["PYTHONPATH"] = str(REPO_ROOT / "algo-bot")
  result = subprocess.run(
    [PYTHON, str(REPO_ROOT / relative), *args],
    cwd=REPO_ROOT / "algo-bot",
    env=env,
  )
  return result.returncode == 0


def main() -> int:
  ok = True
  ok &= check_yaml_syntax()
  ok &= run_script("config/scripts/verify_stage_c2_parity.py")
  for env_path in sorted((CONFIG / "environments").glob("*.yml")):
    ok &= run_script("config/scripts/resolve_reference.py", "--environment", env_path.stem)
  if not ok:
    print("\nconfig-check FAILED", file=sys.stderr)
    return 1
  print("\nconfig-check passed (Stage C2 scope — see this file's own docstring for what's not covered yet)")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
