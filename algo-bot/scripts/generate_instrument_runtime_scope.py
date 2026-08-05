"""Generate instrument-runtime-scope audit artifact."""

from __future__ import annotations

import json
from pathlib import Path

from app.configuration.instrument_runtime_scope import assert_scope_audit_complete
from app.configuration.runtime_manifest import (
  build_resolved_runtime_manifest,
  env_migration_document,
  serialize_manifest_bytes,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "configuration"


def main() -> None:
  audit = assert_scope_audit_complete()
  out = CONTRACTS / "instrument-runtime-scope.generated.json"
  out.write_text(
    json.dumps(audit, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
  )
  print(f"wrote {out}")

  # Keep migration + example fingerprints aligned with manifest V2.
  migration = env_migration_document()
  migration_path = CONTRACTS / "runtime-manifest-env-migration.generated.json"
  migration_path.write_text(
    json.dumps(migration, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
  )
  print(f"wrote {migration_path}")

  example = build_resolved_runtime_manifest(
    config_file=str(ROOT / "config" / "trading-bot.yml")
  )
  example_path = CONTRACTS / "runtime-manifest-example.generated.json"
  example_path.write_bytes(serialize_manifest_bytes(example))
  print(f"wrote {example_path}")

  schema = {
    "manifest_version": 2,
    "required_root_keys": [
      "manifest_version",
      "contract_fingerprint",
      "effective_configuration_fingerprint",
      "profile",
      "global",
      "instruments",
      "instrument_runtimes",
      "feed",
      "auto_trade",
      "live_instruments",
    ],
    "deprecated_compatibility_projections": ["feed", "auto_trade"],
    "notes": (
      "Top-level feed/auto_trade are XAU compatibility projections. "
      "Prefer instrument_runtimes.<ID> for multi-symbol consumers."
    ),
  }
  # Preserve previous schema's auto_trade_keys / feed_keys if present.
  prior = CONTRACTS / "runtime-manifest-schema.generated.json"
  if prior.exists():
    old = json.loads(prior.read_text(encoding="utf-8"))
    for key in ("auto_trade_keys", "feed_keys", "instrument_keys"):
      if key in old:
        schema[key] = old[key]
  schema_path = CONTRACTS / "runtime-manifest-schema.generated.json"
  schema_path.write_text(
    json.dumps(schema, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
  )
  print(f"wrote {schema_path}")


if __name__ == "__main__":
  main()
