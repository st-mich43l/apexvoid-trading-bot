"""Metadata-driven environment alias inspection.

Phase 2H replaces the hand-maintained ENV alias registry with catalog-derived
inspection. Every canonical ENV name and its deprecated aliases already live in
the catalog (``canonical_env`` + ``deprecated_aliases``); this module reads that
contract to detect deprecated-alias usage and conflicting alias values in a
given environment mapping.

This is a reporting surface (used by the environment CLI and tests). It does
not participate in runtime configuration construction, so it never alters
trading behavior. The canonical resolver
(``app.configuration.sources.resolve_source_layer``) remains the authority that
enforces alias conflicts during canonical startup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.configuration.environment_contract import (
  EnvironmentContractEntry,
  iter_environment_contract_entries,
)

_BOOLEAN_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOLEAN_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class DeprecatedAliasUsage:
  deprecated_alias: str
  canonical_env: str
  path: str

  def as_dict(self) -> dict[str, str]:
    return {
      "deprecated_alias": self.deprecated_alias,
      "canonical_env": self.canonical_env,
      "path": self.path,
    }


@dataclass(frozen=True, slots=True)
class EnvironmentAliasConflict:
  canonical_env: str
  path: str
  supplied_names: tuple[str, ...]

  def as_dict(self) -> dict[str, object]:
    return {
      "canonical_env": self.canonical_env,
      "path": self.path,
      "supplied_names": list(self.supplied_names),
    }


def _normalize(entry: EnvironmentContractEntry, raw: str) -> str:
  value = raw.strip()
  if entry.type == "bool":
    lowered = value.lower()
    if lowered in _BOOLEAN_TRUE:
      return "true"
    if lowered in _BOOLEAN_FALSE:
      return "false"
  return value


def present_deprecated_aliases(
  environment: Mapping[str, str],
) -> tuple[DeprecatedAliasUsage, ...]:
  """Return every deprecated alias that is set in ``environment``."""
  usages: list[DeprecatedAliasUsage] = []
  for entry in iter_environment_contract_entries():
    for alias in entry.deprecated_aliases:
      if alias in environment:
        usages.append(DeprecatedAliasUsage(
          deprecated_alias=alias,
          canonical_env=entry.canonical_env,
          path=entry.path,
        ))
  usages.sort(key=lambda usage: (usage.deprecated_alias, usage.canonical_env))
  return tuple(usages)


def detect_environment_alias_conflicts(
  environment: Mapping[str, str],
) -> tuple[EnvironmentAliasConflict, ...]:
  """Return canonical entries whose supplied alias names disagree in value.

  A conflict is when the canonical ENV name and/or one or more of its
  deprecated aliases are set to different (type-normalized) values in the same
  environment mapping. Equivalent duplicate values are not conflicts.
  """
  conflicts: list[EnvironmentAliasConflict] = []
  for entry in iter_environment_contract_entries():
    names = (entry.canonical_env, *entry.deprecated_aliases)
    present = tuple(name for name in names if name in environment)
    if len(present) < 2:
      continue
    values = {
      _normalize(entry, str(environment[name])) for name in present
    }
    if len(values) > 1:
      conflicts.append(EnvironmentAliasConflict(
        canonical_env=entry.canonical_env,
        path=entry.path,
        supplied_names=present,
      ))
  conflicts.sort(key=lambda conflict: conflict.canonical_env)
  return tuple(conflicts)
