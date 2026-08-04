"""Generate deterministic, secret-safe configuration contract artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.configuration.catalog import CatalogEntry
from app.configuration.catalog import infer_ctrader_type
from app.configuration.catalog import iter_catalog_entries
from app.configuration.env_example_policy import render_env_example
from app.configuration.environment_contract import deprecated_environment_document
from app.configuration.environment_contract import environment_contract_document
from app.configuration.environment_contract import environment_reference_markdown
from app.configuration.environment_usage_audit import audit_environment_usage
from app.configuration.fingerprints import configuration_contract_fingerprint
from app.configuration.fingerprints import configuration_document_fingerprint
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.profiles import PROFILES
from app.configuration.profiles import profile_fingerprint
from app.configuration.deployment_contract import deployment_contract_document
from app.configuration.source_types import SOURCE_PRECEDENCE


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CATALOG_VERSION = 2
ARCHITECTURE_VERSION = 1
SOURCE_MODEL = "app.configuration.models.root.ApexVoidConfig"


def _json_bytes(value: Any) -> bytes:
  return (
    json.dumps(
      value,
      indent=2,
      sort_keys=True,
      ensure_ascii=False,
      separators=(",", ": "),
    )
    + "\n"
  ).encode("utf-8")


def _header(
  contract_fingerprint: str,
  *,
  document_fingerprint: str | None = None,
) -> dict[str, Any]:
  payload = {
    "catalog_version": CATALOG_VERSION,
    "configuration_contract_fingerprint_sha256": contract_fingerprint,
    "source_fingerprint_sha256": contract_fingerprint,
    "source_model": SOURCE_MODEL,
  }
  if document_fingerprint is not None:
    payload["configuration_document_fingerprint_sha256"] = document_fingerprint
  return payload


def _contexts(entry: CatalogEntry) -> dict[str, Any]:
  return {
    item["context"]: item["value"]
    for item in entry.default_contexts
  }


def _catalog_artifact(
  entries: tuple[CatalogEntry, ...],
  contract_fingerprint: str,
  document_fingerprint: str,
) -> dict[str, Any]:
  kinds = Counter(entry.kind for entry in entries)
  owners = Counter(entry.owner for entry in entries)
  return {
    **_header(contract_fingerprint, document_fingerprint=document_fingerprint),
    "counts": {
      "total_items": len(entries),
      "configurable": kinds["configurable"],
      "protocol_constants": kinds["protocol_constant"],
      "algorithm_constants": kinds["algorithm_constant"],
      "shared": sum(entry.shared_with_ctrader for entry in entries),
      "secrets": sum(entry.secret for entry in entries),
      "known_conflicts": sum(
        entry.mismatch_policy == "warning" for entry in entries
      ),
      "owners": dict(sorted(owners.items())),
    },
    "items": [entry.as_dict() for entry in entries],
  }


def _shared_artifact(
  entries: tuple[CatalogEntry, ...],
  contract_fingerprint: str,
) -> dict[str, Any]:
  shared = tuple(entry for entry in entries if entry.shared_with_ctrader)
  items = []
  for entry in shared:
    contexts = _contexts(entry)
    values = [json.dumps(value, sort_keys=True) for value in contexts.values()]
    items.append({
      "display_id": entry.display_id,
      "path": entry.path,
      "canonical_env": entry.canonical_env,
      "deprecated_aliases": list(entry.deprecated_aliases),
      "python_type": entry.type,
      "ctrader_type": infer_ctrader_type(entry),
      "unit": entry.unit,
      "python_schema_default": contexts.get("python_schema"),
      "ctrader_from_environment_default": contexts.get(
        "ctrader_from_environment"
      ),
      "ctrader_constructor_default": contexts.get("ctrader_constructor"),
      "default_contexts": list(entry.default_contexts),
      "default_conflict": len(set(values)) > 1,
      "known_conflict": entry.mismatch_policy == "warning",
      "allowed_values": list(entry.allowed_values),
      "validation_summary": entry.validation_summary,
      "evidence_notes": list(entry.evidence_notes),
      "mismatch_policy": entry.mismatch_policy,
      "secret": entry.secret,
      "kind": entry.kind,
      "configurable": entry.configurable,
    })
  return {
    **_header(contract_fingerprint),
    "count": len(items),
    "known_conflict_count": sum(item["known_conflict"] for item in items),
    "preserved_evidence": [
      {
        "display_id": entry.display_id,
        "path": entry.path,
        "notes": list(entry.evidence_notes),
      }
      for entry in entries
      if entry.evidence_notes
    ],
    "items": items,
  }


def _protocol_artifact(
  entries: tuple[CatalogEntry, ...],
  contract_fingerprint: str,
) -> dict[str, Any]:
  constants = tuple(entry for entry in entries if entry.protocol_constant)
  return {
    **_header(contract_fingerprint),
    "count": len(constants),
    "items": [
      {
        "display_id": entry.display_id,
        "path": entry.path,
        "type": entry.type,
        "unit": entry.unit,
        "value": entry.default,
        "owner": entry.owner,
        "shared_with_ctrader": entry.shared_with_ctrader,
        "mismatch_policy": entry.mismatch_policy,
        "description": entry.description,
      }
      for entry in constants
    ],
  }


def _profiles_artifact(contract_fingerprint: str) -> dict[str, Any]:
  profiles = []
  for name, profile in sorted(PROFILES.items()):
    profiles.append({
      "name": name,
      "assignment_count": len(profile.assignments),
      "profile_fingerprint_sha256": profile_fingerprint(profile),
      "assignments": [
        {"path": assignment.path, "value": assignment.value}
        for assignment in profile.assignments
      ],
    })
  return {
    **_header(contract_fingerprint),
    "profile_count": len(profiles),
    "profiles": profiles,
  }


def _python_projection_artifact(
  entries: tuple[CatalogEntry, ...],
  contract_fingerprint: str,
) -> dict[str, Any]:
  projected = iter_catalog_entries(PythonRuntimeConfig)
  projected_paths = {entry.path for entry in projected}
  excluded = tuple(entry for entry in entries if entry.path not in projected_paths)
  return {
    **_header(contract_fingerprint),
    "projection_model": (
      "app.configuration.models.python_runtime.PythonRuntimeConfig"
    ),
    "included_leaf_count": len(projected),
    "excluded_ctrader_only_count": len(excluded),
    "included_shared_field_count": sum(
      entry.owner == "shared" for entry in projected
    ),
    "included_secret_count": sum(entry.secret for entry in projected),
    "included_paths": [entry.path for entry in projected],
    "excluded_paths": [entry.path for entry in excluded],
  }


def _architecture_artifact(
  entries: tuple[CatalogEntry, ...],
  contract_fingerprint: str,
  document_fingerprint: str,
) -> dict[str, Any]:
  kinds = Counter(entry.kind for entry in entries)
  owners = Counter(entry.owner for entry in entries)
  projected = iter_catalog_entries(PythonRuntimeConfig)
  env_count = sum(1 for entry in entries if entry.configurable)
  deprecated_alias_count = sum(len(entry.deprecated_aliases) for entry in entries)
  return {
    "architecture_version": ARCHITECTURE_VERSION,
    "catalog_version": CATALOG_VERSION,
    "runtime_root": "PythonRuntimeConfig",
    "runtime_authority_count": 1,
    "source_policy": (
      "schema>profile>file_secrets>config_file>dotenv>process_env>init"
    ),
    "source_precedence": [kind.value for kind in SOURCE_PRECEDENCE],
    "catalog_entry_count": len(entries),
    "configurable_count": kinds["configurable"],
    "protocol_constant_count": kinds["protocol_constant"],
    "algorithm_constant_count": kinds["algorithm_constant"],
    "owner_counts": dict(sorted(owners.items())),
    "python_projection_count": len(projected),
    "ctrader_only_count": len(entries) - len(projected),
    "environment_entry_count": env_count,
    "deprecated_alias_count": deprecated_alias_count,
    "secret_count": sum(entry.secret for entry in entries),
    "shared_count": sum(entry.shared_with_ctrader for entry in entries),
    "profile_names": sorted(PROFILES),
    "contract_fingerprint": contract_fingerprint,
    "document_fingerprint": document_fingerprint,
    "startup_loader": (
      "app.configuration.python_loader.load_python_canonical_settings"
    ),
    "integrity_status": "CONFIGURATION_INTEGRITY_OK",
    "configuration_contract_fingerprint_sha256": contract_fingerprint,
    "configuration_document_fingerprint_sha256": document_fingerprint,
  }


def _markdown(
  entries: tuple[CatalogEntry, ...],
  contract_fingerprint: str,
  document_fingerprint: str,
) -> bytes:
  lines = [
    "# Generated configuration catalog",
    "",
    "> Generated from the typed `ApexVoidConfig` Catalog V2 schema. Do not edit manually.",
    "",
    f"- Catalog version: `{CATALOG_VERSION}`",
    f"- Contract fingerprint: `{contract_fingerprint}`",
    f"- Document fingerprint: `{document_fingerprint}`",
    f"- Items: `{len(entries)}`",
    "- Runtime status: canonical-only; `app.core.config.runtime_config` is authoritative",
    "",
  ]
  by_root: dict[str, list[CatalogEntry]] = {}
  for entry in entries:
    by_root.setdefault(entry.path.split(".", 1)[0], []).append(entry)
  for root, root_entries in sorted(by_root.items()):
    lines.extend([
      f"## {root}",
      "",
      "| Path | ENV | Type | Unit | Kind | Default |",
      "|---|---|---|---|---|---|",
    ])
    for entry in root_entries:
      default = json.dumps(entry.default, ensure_ascii=False, sort_keys=True)
      env = entry.canonical_env or "—"
      lines.append(
        f"| `{entry.path}` | `{env}` | `{entry.type}` | "
        f"`{entry.unit}` | `{entry.kind}` | `{default}` |"
      )
    lines.append("")
  return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _generated_package_init() -> bytes:
  return (
    '"""Generated package marker for configuration tooling."""\n'
    "\n"
    "# Generated by app.configuration.generate. Do not edit manually.\n"
  ).encode("utf-8")


def _environment_contract_artifact(contract_fingerprint: str) -> dict[str, Any]:
  return {**_header(contract_fingerprint), **environment_contract_document()}


def _deprecated_environment_artifact(contract_fingerprint: str) -> dict[str, Any]:
  return {**_header(contract_fingerprint), **deprecated_environment_document()}


def _environment_reference_markdown(contract_fingerprint: str) -> bytes:
  lines = environment_reference_markdown()
  lines = [
    *lines[:4],
    f"- Contract fingerprint: `{contract_fingerprint}`",
    *lines[4:],
  ]
  return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def render_artifacts() -> dict[Path, bytes]:
  entries = iter_catalog_entries()
  contract_fingerprint = configuration_contract_fingerprint(entries)
  document_fingerprint = configuration_document_fingerprint(entries)
  environment_usage = audit_environment_usage(REPOSITORY_ROOT)
  return {
    Path("contracts/configuration/environment-usage.generated.json"):
      _json_bytes(environment_usage),
    Path("contracts/configuration/environment-contract.generated.json"):
      _json_bytes(_environment_contract_artifact(contract_fingerprint)),
    Path("contracts/configuration/deprecated-environment.generated.json"):
      _json_bytes(_deprecated_environment_artifact(contract_fingerprint)),
    Path("docs/configuration/environment-reference.generated.md"):
      _environment_reference_markdown(contract_fingerprint),
    Path(".env.example"):
      render_env_example().encode("utf-8"),
    Path("contracts/configuration/config-catalog.generated.json"):
      _json_bytes(_catalog_artifact(
        entries, contract_fingerprint, document_fingerprint,
      )),
    Path("contracts/configuration/shared-config.generated.json"):
      _json_bytes(_shared_artifact(entries, contract_fingerprint)),
    Path("contracts/configuration/protocol-constants.generated.json"):
      _json_bytes(_protocol_artifact(entries, contract_fingerprint)),
    Path("contracts/configuration/profiles.generated.json"):
      _json_bytes(_profiles_artifact(contract_fingerprint)),
    Path("contracts/configuration/python-runtime-projection.generated.json"):
      _json_bytes(_python_projection_artifact(entries, contract_fingerprint)),
    Path("contracts/configuration/configuration-architecture.generated.json"):
      _json_bytes(_architecture_artifact(
        entries, contract_fingerprint, document_fingerprint,
      )),
    Path("contracts/configuration/deployment-contract.generated.json"):
      _json_bytes(deployment_contract_document(
        entries, contract_fingerprint=contract_fingerprint,
      )),
    Path("docs/configuration/config-catalog.generated.md"):
      _markdown(entries, contract_fingerprint, document_fingerprint),
    Path("algo-bot/app/configuration/generated/__init__.py"):
      _generated_package_init(),
  }


def write_artifacts(artifacts: dict[Path, bytes]) -> None:
  for relative_path, content in artifacts.items():
    target = REPOSITORY_ROOT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    print(f"wrote {relative_path}")


def check_artifacts(artifacts: dict[Path, bytes]) -> int:
  drifted = []
  for relative_path, expected in artifacts.items():
    target = REPOSITORY_ROOT / relative_path
    actual = target.read_bytes() if target.exists() else None
    if actual != expected:
      drifted.append(relative_path)
  if drifted:
    print("configuration artifacts are stale:", file=sys.stderr)
    for relative_path in drifted:
      print(f"  - {relative_path}", file=sys.stderr)
    return 1
  print(f"configuration artifacts current: {len(artifacts)} files")
  return 0


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  mode = parser.add_mutually_exclusive_group(required=True)
  mode.add_argument("--write", action="store_true")
  mode.add_argument("--check", action="store_true")
  arguments = parser.parse_args(argv)
  artifacts = render_artifacts()
  if arguments.write:
    write_artifacts(artifacts)
    return 0
  return check_artifacts(artifacts)


if __name__ == "__main__":
  raise SystemExit(main())
