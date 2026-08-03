"""Configuration composition root.

Phase 2H turns this module into a pure composition root: it selects the
process-wide configuration authority, builds the active ``runtime_config`` and
``settings`` surfaces, and exposes secret-safe diagnostics. The legacy flat
``Settings`` model now lives in ``app.configuration.legacy_settings`` and is
re-exported here as ``Settings`` for backward compatibility and rollback.

Production consumers must read ``runtime_config`` (or a narrow
``project_runtime_config`` snapshot) rather than the legacy ``settings``
singleton. ``runtime_config_facade`` remains only for tests/tools until
Phase 2I-B.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.configuration.bootstrap_authority import (
  RuntimeConfigurationAuthority,
  process_authority_is_explicit,
  process_implicit_authority_warning,
  runtime_configuration_authority,
)
from app.configuration.facade import CanonicalSettingsFacade
from app.configuration.fingerprints import catalog_fingerprint
from app.configuration.generated.legacy_access import (
  DERIVED_LEGACY_PROPERTIES,
  DIRECT_LEGACY_PATHS,
)
from app.configuration.legacy_canonical_view import LegacyCanonicalConfigView
from app.configuration.environment_option_resolution import (
  assert_no_environment_alias_conflicts,
)
from app.configuration.legacy_settings import LegacySettings, Settings
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_types import ResolutionTrace, ResolutionWarning

__all__ = [
  "LegacySettings",
  "Settings",
  "settings",
  "runtime_config",
  "runtime_config_facade",
  "build_active_settings",
  "active_configuration_authority",
  "active_configuration_catalog_fingerprint",
  "active_configuration_profile",
  "active_configuration_resolution_trace",
  "active_configuration_warnings",
  "active_configuration_startup_message",
  "active_configuration_authority_explicit",
  "active_configuration_deprecation_message",
  "active_configuration_implicit_authority_warning",
]


@dataclass(frozen=True, slots=True)
class _ActiveConfiguration:
  authority: RuntimeConfigurationAuthority
  authority_explicit: bool
  settings: object
  runtime_config: object
  catalog_fingerprint: str
  profile: str | None
  warnings: tuple[ResolutionWarning, ...]
  resolution_trace: ResolutionTrace | None


def _build_active_configuration(
  authority: RuntimeConfigurationAuthority,
  authority_explicit: bool = False,
) -> _ActiveConfiguration:
  if authority is RuntimeConfigurationAuthority.LEGACY:
    # Fail fast on conflicting canonical/deprecated aliases before building the
    # legacy model. Pydantic ``AliasChoices`` silently accepts the first present
    # name, so this catalog-driven check restores the import-time conflict guard
    # that the retired ``environment_options`` module used to provide.
    assert_no_environment_alias_conflicts()
    legacy = Settings()
    return _ActiveConfiguration(
      authority=authority,
      authority_explicit=authority_explicit,
      settings=legacy,
      runtime_config=LegacyCanonicalConfigView(legacy),
      catalog_fingerprint=catalog_fingerprint(),
      profile=legacy.auto_trade_profile,
      warnings=(),
      resolution_trace=None,
    )
  source_bundle = load_python_runtime_source_bundle()
  result = load_python_canonical_settings(source_bundle)
  return _ActiveConfiguration(
    authority=authority,
    authority_explicit=authority_explicit,
    settings=result.facade,
    runtime_config=result.config,
    catalog_fingerprint=result.catalog_fingerprint,
    profile=result.profile,
    warnings=result.warnings,
    resolution_trace=result.provenance,
  )


def build_active_settings() -> object:
  """Construct settings for the authority selected by the current process."""
  return _build_active_configuration(
    runtime_configuration_authority(),
    process_authority_is_explicit(),
  ).settings


_ACTIVE_CONFIGURATION = _build_active_configuration(
  runtime_configuration_authority(),
  process_authority_is_explicit(),
)
settings = _ACTIVE_CONFIGURATION.settings
runtime_config = _ACTIVE_CONFIGURATION.runtime_config
# Captured once at composition time so diagnostics are deterministic and
# secret-safe regardless of later ``os.environ`` mutation in-process.
_IMPLICIT_AUTHORITY_WARNING = process_implicit_authority_warning()


def runtime_config_facade() -> object:
  """Flat legacy-name view for tests/tools only (removed in Phase 2I-B).

  Production modules must not call this. Phase 2I-A replaced every production
  default with ``project_runtime_config`` (narrow one-shot snapshots) or direct
  ``runtime_config`` reads. The values remain identical to the legacy Settings
  surface under both authorities.
  """
  return CanonicalSettingsFacade(runtime_config)


def active_configuration_authority() -> RuntimeConfigurationAuthority:
  return _ACTIVE_CONFIGURATION.authority


def active_configuration_catalog_fingerprint() -> str:
  return _ACTIVE_CONFIGURATION.catalog_fingerprint


def active_configuration_profile() -> str | None:
  """Return the resolved profile name for the active configuration."""
  return _ACTIVE_CONFIGURATION.profile


def active_configuration_warnings() -> tuple[ResolutionWarning, ...]:
  """Return canonical resolution warnings (empty under the legacy authority)."""
  return _ACTIVE_CONFIGURATION.warnings


def active_configuration_resolution_trace() -> ResolutionTrace | None:
  """Return the canonical per-field resolution trace, if any.

  The legacy authority does not build a source-resolution trace, so this
  returns ``None`` there; the canonical authority returns the full provenance.
  """
  return _ACTIVE_CONFIGURATION.resolution_trace


def active_configuration_startup_message() -> str:
  authority = active_configuration_authority()
  if authority is RuntimeConfigurationAuthority.LEGACY:
    return "configuration_authority=legacy"
  return (
    "configuration_authority=canonical "
    f"configuration_profile={_ACTIVE_CONFIGURATION.profile} "
    f"configuration_catalog_fingerprint={_ACTIVE_CONFIGURATION.catalog_fingerprint} "
    f"configuration_facade_fields={len(DIRECT_LEGACY_PATHS)} "
    f"configuration_derived_fields={len(DERIVED_LEGACY_PROPERTIES)}"
  )


def active_configuration_authority_explicit() -> bool:
  """Whether ``APEXVOID_CONFIG_AUTHORITY`` was set explicitly at composition."""
  return _ACTIVE_CONFIGURATION.authority_explicit


def active_configuration_deprecation_message() -> str | None:
  """Deprecation diagnostic emitted once when authority is explicitly legacy.

  Returns ``None`` for the canonical authority and for the *implicit* legacy
  selection (which is surfaced by the implicit-authority warning instead), so
  callers can log it unconditionally.
  """
  if (
    _ACTIVE_CONFIGURATION.authority is RuntimeConfigurationAuthority.LEGACY
    and _ACTIVE_CONFIGURATION.authority_explicit
  ):
    return (
      "configuration_authority=legacy configuration_authority_deprecated=true "
      "rollback_mode=true planned_removal_phase=2I-B"
    )
  return None


def active_configuration_implicit_authority_warning() -> str | None:
  """Warning emitted once when the authority defaulted to legacy implicitly."""
  return _IMPLICIT_AUTHORITY_WARNING
