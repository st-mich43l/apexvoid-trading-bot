#!/usr/bin/env python3
"""Reference implementation of §14's include/merge/overlay semantics.

This is NOT the Stage C3 Python production loader (app/configuration/
still owns runtime resolution — see docs/configuration-v3-migration-
audit.md for why that cutover is a separate, larger, explicitly-deferred
piece of work). Its jobs are narrower and both bounded/safe:

  1. Prove the merge spec in §14 is actually implementable and that the
     JSON Schema (contracts/configuration/apexvoid-config-v3.schema.json)
     validates a real resolved document, not just individual category
     files in isolation.
  2. Generate the canonical cross-language fixture (§38):
     contracts/configuration/examples/resolved-production-v3.json — the
     document a real Python/Go/.NET loader must each reproduce exactly,
     once those loaders exist.

Usage (from the repository root):

    algo-bot/.venv/bin/python config/scripts/resolve_reference.py \
      [--environment production|demo_eval] [--write-fixture]

Requires PyYAML and jsonschema (see algo-bot/requirements-dev.txt).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config"
SCHEMA_PATH = REPO_ROOT / "contracts" / "configuration" / "apexvoid-config-v3.schema.json"
FIXTURE_PATH = REPO_ROOT / "contracts" / "configuration" / "examples" / "resolved-production-v3.json"


def load(relative: str):
  path = CONFIG / relative
  with path.open() as f:
    return yaml.safe_load(f)


def deep_merge(base, overlay):
  """§14: mappings deep-merge; scalars and lists are replaced wholesale by
  the overlay, never concatenated."""
  if not isinstance(base, dict) or not isinstance(overlay, dict):
    return copy.deepcopy(overlay)
  out = copy.deepcopy(base)
  for key, value in overlay.items():
    if key in out and isinstance(out[key], dict) and isinstance(value, dict):
      out[key] = deep_merge(out[key], value)
    else:
      out[key] = copy.deepcopy(value)
  return out


def resolve(environment: str) -> dict:
  root = load("apexvoid.yml")
  if root.get("version") != 3:
    raise ValueError(f"unsupported root version {root.get('version')!r}; only 3 is supported (§18)")

  merged: dict = {"version": 3}
  seen_includes: set[str] = set()
  for include in root["includes"]:
    # §14: no absolute include escape, no duplicate include, missing
    # include is an error.
    if include in seen_includes:
      raise ValueError(f"duplicate include {include!r}")
    seen_includes.add(include)
    if include.startswith("/") or ".." in Path(include).parts:
      raise ValueError(f"include {include!r} escapes the config/ root — not allowed")
    if not (CONFIG / include).exists():
      raise ValueError(f"missing include {include!r}")

    doc = load(include) or {}
    for top_key, value in doc.items():
      # §14: two base category files defining the same top-level leaf is
      # an error — only the environment overlay may replace an existing
      # base leaf. (Checked at the top-level-key granularity here since
      # every category file owns exactly one or two top-level keys by
      # construction; a real loader should check this at every leaf, not
      # just the top level, once nested category ownership is possible.)
      if top_key in merged and top_key != "version":
        raise ValueError(
          f"duplicate base ownership of top-level key {top_key!r} "
          f"(already set before {include!r} was included)"
        )
      merged[top_key] = value

  overlay_path = f"environments/{environment}.yml"
  if not (CONFIG / overlay_path).exists():
    raise ValueError(f"unknown environment {environment!r} — no {overlay_path}")
  overlay = load(overlay_path) or {}
  merged = deep_merge(merged, overlay)

  # §13: the selected environment must be visible in the resolved
  # configuration (and, once fingerprinting exists, included in it).
  merged.setdefault("runtime", {})["environment"] = environment
  return merged


def validate(resolved: dict) -> list[str]:
  import jsonschema  # deferred: dev-only dependency (requirements-dev.txt)

  schema = json.loads(SCHEMA_PATH.read_text())
  validator = jsonschema.Draft202012Validator(schema)
  return [
    f"{' -> '.join(str(p) for p in error.absolute_path)}: {error.message}"
    for error in validator.iter_errors(resolved)
  ]


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--environment", default="production", help="environments/<name>.yml to overlay")
  parser.add_argument(
    "--write-fixture", action="store_true",
    help="write the resolved document to contracts/configuration/examples/resolved-production-v3.json (only sensible with --environment production)",
  )
  args = parser.parse_args()

  try:
    resolved = resolve(args.environment)
  except ValueError as exc:
    print(f"resolve error: {exc}", file=sys.stderr)
    return 1

  errors = validate(resolved)
  if errors:
    print(f"{len(errors)} schema validation errors for environment={args.environment}:", file=sys.stderr)
    for error in errors:
      print(f"  - {error}", file=sys.stderr)
    return 1

  print(f"resolved + schema-valid for environment={args.environment}")

  if args.write_fixture:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(resolved, indent=2, sort_keys=True, default=str) + "\n")
    print(f"wrote {FIXTURE_PATH.relative_to(REPO_ROOT)}")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
