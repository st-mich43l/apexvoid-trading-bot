"""Catalog-derived environment-variable contract.

The canonical catalog already owns every configurable field's ENV binding
(``canonical_env``) and its deprecated aliases. Phase 2H exposes that binding
as a first-class, queryable environment contract so tooling, docs, and the
``.env.example`` generator all read one source of truth instead of duplicating
the ENV name registry.

All defaults surfaced here are the catalog defaults, which are already redacted
for secret fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.configuration.catalog import CatalogEntry, iter_catalog_entries


@dataclass(frozen=True, slots=True)
class EnvironmentContractEntry:
  canonical_env: str
  path: str
  display_id: str
  type: str
  unit: str
  owner: str
  required: bool
  secret: bool
  shared_with_ctrader: bool
  deprecated_aliases: tuple[str, ...]
  default: object
  deprecated: bool
  replacement_path: str | None
  description: str

  def as_dict(self) -> dict[str, object]:
    return {
      "canonical_env": self.canonical_env,
      "path": self.path,
      "display_id": self.display_id,
      "type": self.type,
      "unit": self.unit,
      "owner": self.owner,
      "required": self.required,
      "secret": self.secret,
      "shared_with_ctrader": self.shared_with_ctrader,
      "deprecated_aliases": list(self.deprecated_aliases),
      "default": "<redacted>" if self.secret else self.default,
      "deprecated": self.deprecated,
      "replacement_path": self.replacement_path,
      "description": self.description,
    }


def _entry(catalog_entry: CatalogEntry) -> EnvironmentContractEntry:
  return EnvironmentContractEntry(
    canonical_env=catalog_entry.canonical_env or "",
    path=catalog_entry.path,
    display_id=catalog_entry.display_id,
    type=catalog_entry.type,
    unit=catalog_entry.unit,
    owner=catalog_entry.owner,
    required=catalog_entry.required,
    secret=catalog_entry.secret,
    shared_with_ctrader=catalog_entry.shared_with_ctrader,
    deprecated_aliases=catalog_entry.deprecated_aliases,
    default=catalog_entry.default,
    deprecated=catalog_entry.deprecated,
    replacement_path=catalog_entry.replacement_path,
    description=catalog_entry.description,
  )


def iter_environment_contract_entries() -> tuple[EnvironmentContractEntry, ...]:
  """Return every catalog field that binds a canonical environment variable."""
  entries = [
    _entry(entry)
    for entry in iter_catalog_entries()
    if entry.canonical_env
  ]
  entries.sort(key=lambda item: item.canonical_env)
  return tuple(entries)


def environment_entry_for_name(name: str) -> EnvironmentContractEntry | None:
  """Resolve a canonical ENV name or a deprecated alias to its contract entry."""
  for entry in iter_environment_contract_entries():
    if entry.canonical_env == name or name in entry.deprecated_aliases:
      return entry
  return None


def environment_entry_for_path(path: str) -> EnvironmentContractEntry | None:
  """Resolve a canonical dotted path to its environment contract entry."""
  for entry in iter_environment_contract_entries():
    if entry.path == path:
      return entry
  return None


def deprecated_environment_aliases() -> tuple[dict[str, object], ...]:
  """Return every deprecated ENV alias mapped to its canonical replacement."""
  rows: list[dict[str, object]] = []
  for entry in iter_environment_contract_entries():
    for alias in entry.deprecated_aliases:
      rows.append({
        "deprecated_alias": alias,
        "canonical_env": entry.canonical_env,
        "path": entry.path,
        "type": entry.type,
      })
  rows.sort(key=lambda row: (str(row["deprecated_alias"]), str(row["canonical_env"])))
  return tuple(rows)


def environment_contract_document() -> dict[str, object]:
  """Return the serializable environment contract payload (without header)."""
  entries = iter_environment_contract_entries()
  return {
    "count": len(entries),
    "secret_count": sum(entry.secret for entry in entries),
    "shared_with_ctrader_count": sum(
      entry.shared_with_ctrader for entry in entries
    ),
    "deprecated_alias_count": sum(
      len(entry.deprecated_aliases) for entry in entries
    ),
    "entries": [entry.as_dict() for entry in entries],
  }


def deprecated_environment_document() -> dict[str, object]:
  aliases = deprecated_environment_aliases()
  return {
    "count": len(aliases),
    "aliases": list(aliases),
  }


def environment_reference_markdown() -> list[str]:
  """Return the environment reference markdown body lines (no header/footer)."""
  entries = iter_environment_contract_entries()
  lines = [
    "# Configuration environment reference",
    "",
    "> Generated from the canonical configuration catalog "
    "(`app.configuration.environment_contract`). Do not edit manually.",
    "",
    f"- Environment-bound fields: `{len(entries)}`",
    f"- Deprecated aliases: `{sum(len(e.deprecated_aliases) for e in entries)}`",
    "",
    "Secret values are never emitted; secret defaults render as `<redacted>`.",
    "",
    "| ENV | Canonical path | Type | Secret | Shared | Deprecated aliases | Default |",
    "|---|---|---|---|---|---|---|",
  ]
  for entry in entries:
    aliases = ", ".join(f"`{alias}`" for alias in entry.deprecated_aliases) or "—"
    default = "<redacted>" if entry.secret else entry.default
    lines.append(
      f"| `{entry.canonical_env}` | `{entry.path}` | `{entry.type}` | "
      f"{'yes' if entry.secret else 'no'} | "
      f"{'yes' if entry.shared_with_ctrader else 'no'} | {aliases} | "
      f"`{default}` |"
    )
  return lines
