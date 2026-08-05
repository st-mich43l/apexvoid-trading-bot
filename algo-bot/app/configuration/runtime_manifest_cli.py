"""CLI for compiling and checking the ResolvedRuntimeManifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.configuration.runtime_manifest import (
  RuntimeManifestError,
  build_resolved_runtime_manifest,
  fingerprint_payload,
  load_manifest_file,
  serialize_manifest_bytes,
  write_manifest_atomic,
)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Compile or verify the cross-service ResolvedRuntimeManifest",
  )
  parser.add_argument("--config-file", default=None)
  parser.add_argument("--output", type=Path, default=None)
  parser.add_argument("--check", action="store_true")
  parser.add_argument("--print-fingerprint", action="store_true")
  parser.add_argument("--compare", type=Path, default=None)
  args = parser.parse_args(argv)

  try:
    payload = build_resolved_runtime_manifest(config_file=args.config_file)
  except RuntimeManifestError as exc:
    print(f"runtime_manifest_error: {exc}", file=sys.stderr)
    return 2

  fingerprint = payload["effective_configuration_fingerprint"]
  if args.print_fingerprint or args.check:
    print(f"effective_configuration_fingerprint={fingerprint}")
    print(f"manifest_version={payload['manifest_version']}")
    print(f"profile={payload['profile']}")
    print(f"live_instruments={','.join(payload['live_instruments'])}")

  if args.compare is not None:
    existing = load_manifest_file(args.compare)
    if (
      existing.get("effective_configuration_fingerprint")
      != fingerprint
    ):
      print(
        "runtime_manifest_compare_mismatch: fingerprint differs",
        file=sys.stderr,
      )
      return 3
    if serialize_manifest_bytes(existing) != serialize_manifest_bytes(payload):
      print(
        "runtime_manifest_compare_mismatch: payload bytes differ",
        file=sys.stderr,
      )
      return 3
    print("runtime_manifest_compare=matched")

  if args.output is not None:
    written = write_manifest_atomic(payload, args.output)
    print(f"wrote {args.output} fingerprint={written}")

  if args.check and args.output is None and args.compare is None:
    # Resolve + validate schema + secret exclusion already done in build.
    print("runtime_manifest_check=ok")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
