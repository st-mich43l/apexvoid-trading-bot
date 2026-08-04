"""Canonical-only configuration composition root.

Phase 2I-B removes the legacy Settings authority, the authority selector, and
all flat compatibility facades. Startup always loads:

  source bundle -> canonical resolver -> PythonRuntimeConfig -> runtime_config

There is no authority branch and no flat settings surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_types import ResolutionTrace, ResolutionWarning

__all__ = [
  "runtime_config",
  "active_configuration_authority",
  "active_configuration_catalog_fingerprint",
  "active_configuration_profile",
  "active_configuration_resolution_trace",
  "active_configuration_warnings",
  "active_configuration_startup_message",
]


@dataclass(frozen=True, slots=True)
class _ActiveConfiguration:
  runtime_config: object
  catalog_fingerprint: str
  profile: str | None
  warnings: tuple[ResolutionWarning, ...]
  resolution_trace: ResolutionTrace | None


def _build_active_configuration() -> _ActiveConfiguration:
  source_bundle = load_python_runtime_source_bundle()
  result = load_python_canonical_settings(source_bundle)
  return _ActiveConfiguration(
    runtime_config=result.config,
    catalog_fingerprint=result.catalog_fingerprint,
    profile=result.profile,
    warnings=result.warnings,
    resolution_trace=result.provenance,
  )


_ACTIVE_CONFIGURATION = _build_active_configuration()
runtime_config = _ACTIVE_CONFIGURATION.runtime_config


def active_configuration_authority() -> str:
  """Return the sole runtime configuration authority (always canonical)."""
  return "canonical"


def active_configuration_catalog_fingerprint() -> str:
  return _ACTIVE_CONFIGURATION.catalog_fingerprint


def active_configuration_profile() -> str | None:
  """Return the resolved profile name for the active configuration."""
  return _ACTIVE_CONFIGURATION.profile


def active_configuration_warnings() -> tuple[ResolutionWarning, ...]:
  """Return canonical resolution warnings."""
  return _ACTIVE_CONFIGURATION.warnings


def active_configuration_resolution_trace() -> ResolutionTrace | None:
  """Return the canonical per-field resolution trace."""
  return _ACTIVE_CONFIGURATION.resolution_trace


def active_configuration_startup_message() -> str:
  return (
    "configuration_authority=canonical "
    f"configuration_profile={_ACTIVE_CONFIGURATION.profile} "
    f"configuration_catalog_fingerprint={_ACTIVE_CONFIGURATION.catalog_fingerprint}"
  )
