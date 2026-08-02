"""Immutable, secret-safe source-resolution records for Phase 2C."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from app.configuration.models.root import ApexVoidConfig


class SourceKind(StrEnum):
  SCHEMA_DEFAULT = "schema_default"
  PROFILE = "profile"
  FILE_SECRET = "file_secret"
  DOTENV = "dotenv"
  PROCESS_ENV = "process_environment"
  INIT_VALUE = "init_value"
  DERIVED_COMPATIBILITY_RULE = "derived_compatibility_rule"


class ShadowLoadStatus(StrEnum):
  COMPLETE = "complete"
  INCOMPLETE_REQUIRED_INPUT = "incomplete_required_input"
  INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class SourceCandidate:
  path: str
  item_id: str
  source_kind: SourceKind
  source_name: str
  canonical_env: str | None
  supplied_alias: str | None
  explicit: bool
  secret: bool


@dataclass(frozen=True, slots=True)
class ResolvedFieldSource:
  path: str
  item_id: str
  source_kind: SourceKind
  source_name: str
  canonical_env: str | None
  supplied_alias: str | None
  explicit: bool
  overridden_lower_precedence_sources: tuple[str, ...]
  profile_name: str
  compatibility_rule: str | None
  secret: bool


@dataclass(frozen=True, slots=True)
class ResolutionWarning:
  code: str
  path: str
  source_kind: SourceKind
  source_name: str
  message: str
  canonical_env: str | None = None
  supplied_alias: str | None = None
  secret: bool = False


@dataclass(frozen=True, slots=True)
class ResolutionConflict:
  code: str
  path: str
  source_kind: SourceKind
  source_name: str
  message: str
  canonical_env: str | None = None
  supplied_names: tuple[str, ...] = ()
  secret: bool = False


@dataclass(frozen=True, slots=True)
class ResolutionTrace:
  fields: tuple[ResolvedFieldSource, ...]

  def by_path(self) -> Mapping[str, ResolvedFieldSource]:
    return {item.path: item for item in self.fields}


@dataclass(frozen=True, slots=True)
class ConfigurationSourceBundle:
  init_values: Mapping[str, object] = field(default_factory=dict)
  process_environment: Mapping[str, str] = field(default_factory=dict)
  dotenv_values: Mapping[str, str | None] = field(default_factory=dict)
  file_secret_values: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
  profile: str
  nested_input: dict[str, object] = field(repr=False)
  flat_values: dict[str, object] = field(repr=False)
  trace: ResolutionTrace
  warnings: tuple[ResolutionWarning, ...]
  conflicts: tuple[ResolutionConflict, ...]
  missing_required_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShadowLoadResult:
  config: "ApexVoidConfig | None" = field(default=None, repr=False)
  profile: str = "conservative"
  status: ShadowLoadStatus = ShadowLoadStatus.INVALID
  trace: ResolutionTrace = field(default_factory=lambda: ResolutionTrace(()))
  warnings: tuple[ResolutionWarning, ...] = ()
  conflicts: tuple[ResolutionConflict, ...] = ()
  validation_errors: tuple[str, ...] = ()
  missing_required_paths: tuple[str, ...] = ()
  catalog_fingerprint: str = ""
  profile_fingerprint: str = ""
  success: bool = False
  authoritative: bool = False

  def __post_init__(self) -> None:
    if self.authoritative:
      raise ValueError("Phase 2C shadow results cannot be authoritative")
