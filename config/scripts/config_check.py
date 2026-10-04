#!/usr/bin/env python3
"""Validate YAML configuration and every named environment overlay."""

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
      yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
      print(f"[syntax] FAIL {path.relative_to(REPO_ROOT)}: {exc}", file=sys.stderr)
      ok = False
  if ok:
    print(f"[syntax] OK — {len(files)} files")
  return ok


def check_environment(name: str) -> bool:
  env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "algo-bot")}
  result = subprocess.run(
    [PYTHON, str(REPO_ROOT / "config/scripts/resolve_reference.py"), "--environment", name],
    cwd=REPO_ROOT / "algo-bot",
    env=env,
  )
  return result.returncode == 0


def main() -> int:
  ok = check_yaml_syntax()
  for path in sorted((CONFIG / "environments").glob("*.yml")):
    ok &= check_environment(path.stem)
  if not ok:
    print("\nconfig-check FAILED", file=sys.stderr)
    return 1
  print("\nconfig-check passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
