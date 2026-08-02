"""Pure, non-authoritative configuration activation rehearsal primitives."""

from __future__ import annotations

from dataclasses import dataclass

from app.configuration.authority import ConfigurationAuthority
from app.configuration.authority import build_configuration_runtime_bundle
from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
from app.configuration.source_types import ConfigurationSourceBundle


@dataclass(frozen=True, slots=True)
class RollbackRehearsalResult:
  original_legacy_identity_preserved: bool
  canonical_parity_passed: bool
  rollback_values_equal: bool
  global_state_untouched: bool
  success: bool


def rehearse_authority_rollback(
  *,
  source_bundle: ConfigurationSourceBundle,
  legacy_settings: object,
  active_global_settings: object,
) -> RollbackRehearsalResult:
  """Exercise local selection without replacing the active module singleton."""
  from app.core import config as active_config

  global_before = active_config.settings
  legacy_bundle = build_configuration_runtime_bundle(
    source_bundle=source_bundle,
    legacy_settings=legacy_settings,
  )
  rehearsal_bundle = build_configuration_runtime_bundle(
    source_bundle=source_bundle,
    legacy_settings=legacy_settings,
    requested_authority=ConfigurationAuthority.CANONICAL_REHEARSAL,
  )
  rollback_bundle = build_configuration_runtime_bundle(
    source_bundle=source_bundle,
    legacy_settings=legacy_settings,
    requested_authority=ConfigurationAuthority.LEGACY,
  )
  facade = rehearsal_bundle.canonical_facade
  names = (*DIRECT_LEGACY_PATHS, *DERIVED_LEGACY_PROPERTIES)
  parity_passed = facade is not None and all(
    type(getattr(legacy_settings, name)) is type(getattr(facade, name))
    and getattr(legacy_settings, name) == getattr(facade, name)
    for name in names
  )
  rollback_equal = all(
    type(getattr(legacy_settings, name))
    is type(getattr(rollback_bundle.selected_object, name))
    and getattr(legacy_settings, name)
    == getattr(rollback_bundle.selected_object, name)
    for name in names
  )
  identity_preserved = (
    legacy_bundle.selected_object is legacy_settings
    and rollback_bundle.selected_object is legacy_settings
  )
  global_untouched = (
    active_config.settings is global_before
    and active_config.settings is active_global_settings
  )
  success = (
    identity_preserved
    and parity_passed
    and rollback_equal
    and global_untouched
  )
  return RollbackRehearsalResult(
    original_legacy_identity_preserved=identity_preserved,
    canonical_parity_passed=parity_passed,
    rollback_values_equal=rollback_equal,
    global_state_untouched=global_untouched,
    success=success,
  )
