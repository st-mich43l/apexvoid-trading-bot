"""Explicit, local-only configuration authority construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.configuration.facade import CanonicalSettingsFacade
from app.configuration.parity import ParityReport
from app.configuration.parity import compare_legacy_settings
from app.configuration.shadow_loader import load_shadow_configuration
from app.configuration.source_types import ConfigurationSourceBundle
from app.configuration.source_types import ShadowLoadResult
from app.configuration.source_types import ShadowLoadStatus

if TYPE_CHECKING:
  from app.configuration.readiness import ActivationReadiness


class ConfigurationAuthority(StrEnum):
  LEGACY = "legacy"
  CANONICAL_REHEARSAL = "canonical_rehearsal"
  CANONICAL = "canonical"


@dataclass(frozen=True, slots=True)
class ConfigurationRuntimeBundle:
  legacy_settings: object
  shadow_result: ShadowLoadResult
  canonical_facade: CanonicalSettingsFacade | None
  parity_report: ParityReport
  selected_authority: ConfigurationAuthority
  selected_object: object
  authoritative_object: object
  selected_is_authoritative: bool
  readiness: ActivationReadiness | None = None


def build_configuration_runtime_bundle(
  *,
  source_bundle: ConfigurationSourceBundle,
  legacy_settings: object,
  requested_authority: ConfigurationAuthority = ConfigurationAuthority.LEGACY,
  readiness: ActivationReadiness | None = None,
) -> ConfigurationRuntimeBundle:
  """Build an isolated authority bundle for local verification."""
  if requested_authority is ConfigurationAuthority.CANONICAL:
    # Import lazily because the production loader reports the authority enum in
    # its result. Startup itself calls the loader directly and never constructs
    # legacy Settings.
    from app.configuration.python_loader import load_python_canonical_settings

    canonical = load_python_canonical_settings(source_bundle)
    shadow = ShadowLoadResult(
      config=canonical.config,
      profile=canonical.profile,
      status=ShadowLoadStatus.COMPLETE,
      trace=canonical.provenance,
      warnings=canonical.warnings,
      catalog_fingerprint=canonical.catalog_fingerprint,
      profile_fingerprint=canonical.profile_fingerprint,
      success=True,
    )
    parity = compare_legacy_settings(legacy_settings, shadow)
    selected = canonical.facade
    return ConfigurationRuntimeBundle(
      legacy_settings=legacy_settings,
      shadow_result=shadow,
      canonical_facade=canonical.facade,
      parity_report=parity,
      selected_authority=requested_authority,
      selected_object=selected,
      authoritative_object=selected,
      selected_is_authoritative=True,
      readiness=readiness,
    )
  shadow = load_shadow_configuration(source_bundle)
  parity = compare_legacy_settings(legacy_settings, shadow)
  facade = (
    CanonicalSettingsFacade(shadow.config)
    if shadow.config is not None else None
  )
  if requested_authority is ConfigurationAuthority.CANONICAL_REHEARSAL:
    if facade is None:
      raise ValueError("canonical authority requires a complete configuration")
    selected = facade
  else:
    selected = legacy_settings
  return ConfigurationRuntimeBundle(
    legacy_settings=legacy_settings,
    shadow_result=shadow,
    canonical_facade=facade,
    parity_report=parity,
    selected_authority=requested_authority,
    selected_object=selected,
    authoritative_object=legacy_settings,
    selected_is_authoritative=(
      requested_authority is not ConfigurationAuthority.CANONICAL_REHEARSAL
    ),
    readiness=readiness,
  )
