#!/usr/bin/env python3
"""Validate native ApexVoid YAML roots and their includes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _environment_names() -> list[str]:
  return [path.stem for path in sorted((REPO_ROOT / "config" / "environments").glob("*.yml"))]


def _check_yaml_syntax() -> bool:
  files = sorted((REPO_ROOT / "config").glob("*.yml"))
  files += sorted((REPO_ROOT / "config" / "environments").glob("*.yml"))
  for path in files:
    try:
      yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
      print(f"configuration syntax failed: {path.relative_to(REPO_ROOT)}: {exc}", file=sys.stderr)
      return False
  print(f"configuration syntax valid files={len(files)}")
  return True


def _validate_environment(environment: str) -> bool:
  root_name = "apexvoid.demo.yml" if environment == "demo" else "apexvoid.yml"
  root = REPO_ROOT / "config" / root_name
  os.environ["APEXVOID_CONFIG_FILE"] = str(root)
  sys.path.insert(0, str(REPO_ROOT / "algo-bot"))
  from app.core.config import load_runtime_config

  try:
    config = load_runtime_config(str(root))
  except Exception as exc:  # pragma: no cover - command-line failure path
    print(f"configuration validation failed environment={environment}: {exc}", file=sys.stderr)
    return False
  print(f"native YAML valid environment={config.runtime.environment}")
  return True


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--environment", default="production")
  parser.add_argument(
    "--all",
    action="store_true",
    help="validate YAML syntax and every configured environment overlay",
  )
  args = parser.parse_args()
  if args.all:
    ok = _check_yaml_syntax()
    for environment in _environment_names():
      ok = _validate_environment(environment) and ok
    return 0 if ok else 1
  return 0 if _validate_environment(args.environment) else 1


if __name__ == "__main__":
  raise SystemExit(main())
