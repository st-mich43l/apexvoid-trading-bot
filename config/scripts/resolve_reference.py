#!/usr/bin/env python3
"""Validate one native ApexVoid YAML root and its includes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--environment", default="production")
  args = parser.parse_args()
  root_name = "apexvoid.demo.yml" if args.environment == "demo" else "apexvoid.yml"
  os.environ["APEXVOID_CONFIG_FILE"] = str(REPO_ROOT / "config" / root_name)
  sys.path.insert(0, str(REPO_ROOT / "algo-bot"))
  from app.core.config import load_runtime_config

  try:
    config = load_runtime_config(os.environ["APEXVOID_CONFIG_FILE"])
  except Exception as exc:  # pragma: no cover - command-line failure path
    print(f"configuration validation failed: {exc}", file=sys.stderr)
    return 1
  print(f"native YAML valid environment={config.runtime.environment}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
