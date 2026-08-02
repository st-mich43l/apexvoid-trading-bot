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
  shadow = load_shadow_configuration(source_bundle)
  parity = compare_legacy_settings(legacy_settings, shadow)
  facade = (
    CanonicalSettingsFacade(shadow.config)
    if shadow.config is not None else None
  )
  if requested_authority in {
    ConfigurationAuthority.CANONICAL_REHEARSAL,
    ConfigurationAuthority.CANONICAL,
  }:
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
    authoritative_object=(
      selected
      if requested_authority is ConfigurationAuthority.CANONICAL
      else legacy_settings
    ),
    selected_is_authoritative=(
      requested_authority is not ConfigurationAuthority.CANONICAL_REHEARSAL
    ),
    readiness=readiness,
  )
