"""Validate a Configuration YAML root without loading service secrets."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from app.configuration.v3_root import resolve_v3_document


_SECRET_KEYS = {
  "password",
  "client_secret",
  "access_token",
  "refresh_token",
  "bot_token",
}


def _reject_secrets(value: Any, path: str = "") -> None:
  if isinstance(value, dict):
    for key, child in value.items():
      child_path = f"{path}.{key}" if path else str(key)
      if str(key).lower() in _SECRET_KEYS:
        raise ValueError(f"secret value is not allowed in YAML: {child_path}")
      _reject_secrets(child, child_path)
  elif isinstance(value, list):
    for child in value:
      _reject_secrets(child, path)


def validate_root(path: Path) -> dict[str, Any]:
  document = resolve_v3_document(path)
  _reject_secrets(document)
  required = {
    "runtime",
    "transport",
    "database",
    "instrument_packs",
    "instruments",
    "analysis",
    "auto_algo",
    "manual_algo",
    "execution",
    "telegram",
    "journal",
  }
  missing = sorted(required - document.keys())
  if missing:
    raise ValueError(f"missing configuration categories: {', '.join(missing)}")
  if not document["instruments"]:
    raise ValueError("instruments must not be empty")
  return document


def main() -> int:
  parser = argparse.ArgumentParser(description="Validate an ApexVoid YAML root")
  parser.add_argument("root", type=Path)
  args = parser.parse_args()
  validate_root(args.root)
  print(f"configuration_valid root={args.root}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
