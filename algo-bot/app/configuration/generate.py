"""Generate deterministic, secret-safe configuration contract artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.configuration.catalog import DERIVED_LEGACY_PROPERTIES
from app.configuration.catalog import CatalogEntry
from app.configuration.catalog import infer_ctrader_type
from app.configuration.catalog import iter_catalog_entries
from app.configuration.profiles import PROFILES
from app.configuration.profiles import profile_fingerprint
from app.configuration.usage_audit import audit_legacy_settings_usage
from app.configuration.models.python_runtime import PythonRuntimeConfig


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CATALOG_VERSION = 1
SOURCE_MODEL = "app.configuration.models.root.ApexVoidConfig"
PHASE_2E_ROOTS = frozenset({"bootstrap", "delivery", "market_data"})


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


def _fingerprint(entries: tuple[CatalogEntry, ...]) -> str:
  payload = _json_bytes([entry.as_dict() for entry in entries])
  return hashlib.sha256(payload).hexdigest()


def _header(fingerprint: str) -> dict[str, Any]:
  return {
    "catalog_version": CATALOG_VERSION,
    "source_fingerprint_sha256": fingerprint,
    "source_model": SOURCE_MODEL,
  }


def _contexts(entry: CatalogEntry) -> dict[str, Any]:
  return {
    item["context"]: item["value"]
    for item in entry.default_contexts
  }


def _catalog_artifact(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
) -> dict[str, Any]:
  kinds = Counter(entry.kind for entry in entries)
  owners = Counter(entry.owner for entry in entries)
  return {
    **_header(fingerprint),
    "counts": {
      "total_items": len(entries),
      "legacy_fields": sum(entry.legacy_attr is not None for entry in entries),
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


def _legacy_artifact(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
) -> dict[str, Any]:
  legacy = tuple(
    sorted(
      (entry for entry in entries if entry.legacy_attr is not None),
      key=lambda entry: entry.legacy_attr or "",
    )
  )
  return {
    **_header(fingerprint),
    "count": len(legacy),
    "map": {
      entry.legacy_attr: entry.path
      for entry in legacy
      if entry.legacy_attr is not None
    },
    "entries": [
      {
        "item_id": entry.item_id,
        "legacy_attr": entry.legacy_attr,
        "path": entry.path,
        "type": entry.type,
        "required": entry.required,
      }
      for entry in legacy
    ],
  }


def _derived_artifact(fingerprint: str) -> dict[str, Any]:
  return {
    **_header(fingerprint),
    "count": len(DERIVED_LEGACY_PROPERTIES),
    "properties": [
      item.as_dict()
      for item in sorted(
        DERIVED_LEGACY_PROPERTIES,
        key=lambda item: item.property_name,
      )
    ],
  }


def _shared_artifact(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
) -> dict[str, Any]:
  shared = tuple(entry for entry in entries if entry.shared_with_ctrader)
  items = []
  for entry in shared:
    contexts = _contexts(entry)
    values = [json.dumps(value, sort_keys=True) for value in contexts.values()]
    items.append({
      "item_id": entry.item_id,
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
    **_header(fingerprint),
    "count": len(items),
    "known_conflict_count": sum(item["known_conflict"] for item in items),
    "preserved_evidence": [
      {
        "item_id": entry.item_id,
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
  fingerprint: str,
) -> dict[str, Any]:
  constants = tuple(entry for entry in entries if entry.protocol_constant)
  return {
    **_header(fingerprint),
    "count": len(constants),
    "items": [
      {
        "item_id": entry.item_id,
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


def _profiles_artifact(fingerprint: str) -> dict[str, Any]:
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
    **_header(fingerprint),
    "profile_count": len(profiles),
    "profiles": profiles,
  }


def _python_projection_artifact(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
) -> dict[str, Any]:
  projected = iter_catalog_entries(PythonRuntimeConfig)
  projected_paths = {entry.path for entry in projected}
  excluded = tuple(entry for entry in entries if entry.path not in projected_paths)
  return {
    **_header(fingerprint),
    "projection_model": (
      "app.configuration.models.python_runtime.PythonRuntimeConfig"
    ),
    "included_leaf_count": len(projected),
    "excluded_ctrader_only_count": len(excluded),
    "included_direct_legacy_field_count": sum(
      entry.legacy_attr is not None for entry in projected
    ),
    "included_shared_field_count": sum(
      entry.owner == "shared" for entry in projected
    ),
    "included_secret_count": sum(entry.secret for entry in projected),
    "included_paths": [entry.path for entry in projected],
    "excluded_paths": [entry.path for entry in excluded],
  }


def _consumer_migration_artifact(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
  usage: dict[str, object],
) -> dict[str, Any]:
  """Render the Phase 2E operational-read migration ledger from AST facts."""
  direct_paths = {
    entry.legacy_attr: entry.path
    for entry in entries
    if entry.legacy_attr is not None
  }
  reverse_paths = {path: attribute for attribute, path in direct_paths.items()}
  derived_paths = {
    item.property_name: item.source_path
    for item in DERIVED_LEGACY_PROPERTIES
  }
  rows: list[dict[str, Any]] = []

  def add_row(
    item: dict[str, Any],
    *,
    legacy_attribute: str | None,
    canonical_path: str | None,
    classification: str,
    status: str,
    support: bool,
    reason: str | None,
  ) -> None:
    parts = canonical_path.split(".") if canonical_path else []
    rows.append({
      "file": item["path"],
      "line": item["line"],
      "legacy_attribute": legacy_attribute,
      "canonical_path": canonical_path,
      "root_domain": parts[0] if parts else None,
      "subdomain": parts[1] if len(parts) > 1 else None,
      "migration_classification": classification,
      "migration_status": status,
      "authority_neutral_support": support,
      "deferred_reason": reason,
    })

  production = usage["production"]
  for item in production["canonical_reads"]:
    path = item["canonical_path"]
    root = path.split(".", 1)[0]
    legacy_attribute = reverse_paths.get(path)
    if root in PHASE_2E_ROOTS and legacy_attribute is not None:
      add_row(
        item,
        legacy_attribute=legacy_attribute,
        canonical_path=path,
        classification="PHASE_2E_MIGRATE",
        status="migrated",
        support=True,
        reason=None,
      )
    else:
      add_row(
        item,
        legacy_attribute=legacy_attribute,
        canonical_path=path,
        classification="UNKNOWN_BLOCKER",
        status="blocked",
        support=False,
        reason="canonical production read is outside the supported Phase 2E policy",
      )

  def add_legacy_read(item: dict[str, Any], attribute: str) -> None:
    path = direct_paths.get(attribute)
    if path is not None:
      root = path.split(".", 1)[0]
      if root in PHASE_2E_ROOTS:
        add_row(
          item,
          legacy_attribute=attribute,
          canonical_path=path,
          classification="PHASE_2E_MIGRATE",
          status="pending",
          support=True,
          reason=None,
        )
      else:
        add_row(
          item,
          legacy_attribute=attribute,
          canonical_path=path,
          classification="PHASE_2F_DEFER",
          status="deferred",
          support=True,
          reason=f"canonical root {root} is outside Phase 2E scope",
        )
      return
    derived_path = derived_paths.get(attribute)
    add_row(
      item,
      legacy_attribute=attribute,
      canonical_path=derived_path,
      classification="NON_LEGACY_CANONICAL_DEFER",
      status="deferred",
      support=False,
      reason=(
        "legacy property is derived rather than directly owned"
        if derived_path else
        "optional compatibility attribute has no typed-catalog canonical path"
      ),
    )

  for item in production["attribute_reads"]:
    add_legacy_read(item, item["attribute"])
  for item in production["introspection"]:
    names = item["dynamic_names"] or (
      [item["attribute"]] if item["attribute"] is not None else []
    )
    for attribute in names:
      add_legacy_read(item, attribute)

  rows.sort(key=lambda item: (
    item["file"],
    item["line"],
    item["legacy_attribute"] or "",
    item["canonical_path"] or "",
  ))
  migrated = sum(item["migration_status"] == "migrated" for item in rows)
  eligible_remaining = sum(
    item["migration_classification"] == "PHASE_2E_MIGRATE"
    and item["migration_status"] != "migrated"
    for item in rows
  )
  deferred = sum(item["migration_status"] == "deferred" for item in rows)
  unknown = sum(
    item["migration_classification"] == "UNKNOWN_BLOCKER" for item in rows
  )
  return {
    **_header(fingerprint),
    "phase": "2E",
    "candidate_roots": sorted(PHASE_2E_ROOTS),
    "counts": {
      "eligible_production_reads_before": migrated + eligible_remaining,
      "migrated_reads": migrated,
      "eligible_reads_remaining": eligible_remaining,
      "deferred_reads": deferred,
      "unknown_blockers": unknown,
    },
    "reads": rows,
  }


def _markdown(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
) -> bytes:
  lines = [
    "# Generated configuration catalog",
    "",
    "> Generated from the inactive typed `ApexVoidConfig` schema. Do not edit manually.",
    "",
    f"- Catalog version: `{CATALOG_VERSION}`",
    f"- Source fingerprint: `{fingerprint}`",
    f"- Items: `{len(entries)}`",
    "- Runtime status: inactive; legacy `app.core.config.Settings` remains active",
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
    '"""Generated, immutable Python configuration access contracts."""\n'
    "\n"
    "# Generated by app.configuration.generate. Do not edit manually.\n"
  ).encode("utf-8")


def _python_mapping(
  name: str,
  annotation: str,
  rows: list[tuple[object, str]],
) -> list[str]:
  lines = [f"{name}: Final[{annotation}] = MappingProxyType({{"]
  lines.extend(f"  {key!r}: {value}," for key, value in rows)
  lines.append("})")
  return lines


def _legacy_access_python(
  entries: tuple[CatalogEntry, ...],
  fingerprint: str,
) -> bytes:
  legacy = sorted(
    (entry for entry in entries if entry.legacy_attr is not None),
    key=lambda entry: entry.legacy_attr or "",
  )
  reverse: dict[tuple[str, ...], str] = {}
  for entry in legacy:
    path = tuple(entry.path.split("."))
    if path in reverse:
      raise ValueError(
        f"duplicate canonical legacy path {entry.path}: "
        f"{reverse[path]} and {entry.legacy_attr}"
      )
    reverse[path] = entry.legacy_attr or ""
  prefixes = {
    path[:length]
    for path in reverse
    for length in range(1, len(path) + 1)
  }
  lines = [
    '"""Generated static access contract for CanonicalSettingsFacade."""',
    "",
    "# Generated by app.configuration.generate. Do not edit manually.",
    "from __future__ import annotations",
    "",
    "from dataclasses import dataclass",
    "from types import MappingProxyType",
    "from typing import Final, Mapping",
    "",
    f"CATALOG_FINGERPRINT_SHA256: Final = {fingerprint!r}",
    "",
    "",
    "@dataclass(frozen=True, slots=True)",
    "class DerivedLegacyAccessSpec:",
    "  source_path: tuple[str, ...] | None",
    "  source_property: str | None",
    "  transformation: str",
    "  return_type: str",
    "",
    "",
  ]
  lines.extend(_python_mapping(
    "DIRECT_LEGACY_PATHS",
    "Mapping[str, tuple[str, ...]]",
    [
      (entry.legacy_attr or "", repr(tuple(entry.path.split("."))))
      for entry in legacy
    ],
  ))
  lines.extend(["", ""])
  lines.extend(_python_mapping(
    "CANONICAL_PATH_TO_LEGACY_ATTR",
    "Mapping[tuple[str, ...], str]",
    [(path, repr(attribute)) for path, attribute in sorted(reverse.items())],
  ))
  lines.extend([
    "",
    "",
    "CANONICAL_LEGACY_PATH_PREFIXES: Final[frozenset[tuple[str, ...]]] = frozenset({",
    *[f"  {path!r}," for path in sorted(prefixes)],
    "})",
  ])
  lines.extend(["", ""])
  derived_rows = []
  for item in sorted(DERIVED_LEGACY_PROPERTIES, key=lambda value: value.property_name):
    source_path = (
      repr(tuple(item.source_path.split(".")))
      if item.source_path is not None else "None"
    )
    derived_rows.append((
      item.property_name,
      "DerivedLegacyAccessSpec("
      f"source_path={source_path}, source_property={item.source_property!r}, "
      f"transformation={item.transformation!r}, return_type={item.return_type!r})",
    ))
  lines.extend(_python_mapping(
    "DERIVED_LEGACY_PROPERTIES",
    "Mapping[str, DerivedLegacyAccessSpec]",
    derived_rows,
  ))
  lines.extend(["", ""])
  lines.extend(_python_mapping(
    "LEGACY_FIELD_TYPES",
    "Mapping[str, str]",
    [(entry.legacy_attr or "", repr(entry.type)) for entry in legacy],
  ))
  lines.extend([
    "",
    "",
    "REQUIRED_LEGACY_FIELDS: Final[frozenset[str]] = frozenset({",
    *[f"  {entry.legacy_attr!r}," for entry in legacy if entry.required],
    "})",
    "",
    "SECRET_LEGACY_FIELDS: Final[frozenset[str]] = frozenset({",
    *[f"  {entry.legacy_attr!r}," for entry in legacy if entry.secret],
    "})",
    "",
  ])
  return "\n".join(lines).encode("utf-8")


def render_artifacts() -> dict[Path, bytes]:
  entries = iter_catalog_entries()
  fingerprint = _fingerprint(entries)
  usage = audit_legacy_settings_usage(REPOSITORY_ROOT)
  return {
    Path("contracts/configuration/config-catalog.generated.json"):
      _json_bytes(_catalog_artifact(entries, fingerprint)),
    Path("contracts/configuration/legacy-map.generated.json"):
      _json_bytes(_legacy_artifact(entries, fingerprint)),
    Path("contracts/configuration/legacy-derived.generated.json"):
      _json_bytes(_derived_artifact(fingerprint)),
    Path("contracts/configuration/shared-config.generated.json"):
      _json_bytes(_shared_artifact(entries, fingerprint)),
    Path("contracts/configuration/protocol-constants.generated.json"):
      _json_bytes(_protocol_artifact(entries, fingerprint)),
    Path("contracts/configuration/profiles.generated.json"):
      _json_bytes(_profiles_artifact(fingerprint)),
    Path("contracts/configuration/python-runtime-projection.generated.json"):
      _json_bytes(_python_projection_artifact(entries, fingerprint)),
    Path("docs/configuration/config-catalog.generated.md"):
      _markdown(entries, fingerprint),
    Path("contracts/configuration/legacy-usage.generated.json"):
      _json_bytes(usage),
    Path("contracts/configuration/consumer-migration-phase-2e.generated.json"):
      _json_bytes(_consumer_migration_artifact(entries, fingerprint, usage)),
    Path("algo-bot/app/configuration/generated/__init__.py"):
      _generated_package_init(),
    Path("algo-bot/app/configuration/generated/legacy_access.py"):
      _legacy_access_python(entries, fingerprint),
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
