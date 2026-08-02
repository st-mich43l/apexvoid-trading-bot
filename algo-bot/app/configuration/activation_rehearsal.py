"""Pure, non-authoritative configuration activation rehearsal primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.configuration.authority import ConfigurationAuthority
from app.configuration.authority import build_configuration_runtime_bundle
from app.configuration.catalog import iter_catalog_entries
from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
from app.configuration.generated.legacy_access import CATALOG_FINGERPRINT_SHA256
from app.configuration.generate import REPOSITORY_ROOT
from app.configuration.generate import render_artifacts
from app.configuration.readiness import ActivationEvidence
from app.configuration.readiness import ActivationReadiness
from app.configuration.readiness import evaluate_activation_readiness
from app.configuration.source_types import ConfigurationSourceBundle
from app.configuration.source_types import ShadowLoadStatus
from app.configuration.usage_audit import audit_legacy_settings_usage


@dataclass(frozen=True, slots=True)
class RollbackRehearsalResult:
  original_legacy_identity_preserved: bool
  canonical_parity_passed: bool
  rollback_values_equal: bool
  global_state_untouched: bool
  success: bool


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
  python_configuration_tests_passed: bool = False
  python_behavior_baseline_not_worsened: bool = False
  python_behavior_tests_passed: bool = False
  csharp_tests_verified: bool = False


@dataclass(frozen=True, slots=True)
class ActivationRehearsalResult:
  legacy_settings: object
  shadow_status: ShadowLoadStatus
  facade: object | None
  direct_parity_equal: int
  direct_parity_total: int
  derived_parity_equal: int
  derived_parity_total: int
  facade_parity_equal: int
  facade_parity_total: int
  unsupported_usage_count: int
  rollback_result: RollbackRehearsalResult
  readiness: ActivationReadiness
  catalog_fingerprint: str
  success: bool
  authoritative: bool = False

  def __post_init__(self) -> None:
    if self.authoritative:
      raise ValueError("activation rehearsal cannot be authoritative")


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


def _generated_artifacts_current() -> bool:
  return all(
    (REPOSITORY_ROOT / path).exists()
    and (REPOSITORY_ROOT / path).read_bytes() == expected
    for path, expected in render_artifacts().items()
  )


def run_activation_rehearsal(
  *,
  source_bundle: ConfigurationSourceBundle,
  legacy_settings: object,
  active_global_settings: object,
  repository_root: Path,
  verification: VerificationEvidence = VerificationEvidence(),
) -> ActivationRehearsalResult:
  """Collect compatibility evidence without selecting production authority."""
  bundle = build_configuration_runtime_bundle(
    source_bundle=source_bundle,
    legacy_settings=legacy_settings,
  )
  facade = bundle.canonical_facade
  direct_names = tuple(DIRECT_LEGACY_PATHS)
  derived_names = tuple(DERIVED_LEGACY_PROPERTIES)
  facade_names = (*direct_names, *derived_names)
  facade_equal = sum(
    facade is not None
    and type(getattr(legacy_settings, name)) is type(getattr(facade, name))
    and getattr(legacy_settings, name) == getattr(facade, name)
    for name in facade_names
  )
  derived_equal = sum(
    facade is not None
    and type(getattr(legacy_settings, name)) is type(getattr(facade, name))
    and getattr(legacy_settings, name) == getattr(facade, name)
    for name in derived_names
  )
  usage = audit_legacy_settings_usage(repository_root)
  unsupported_count = len(usage["activation_blockers"])
  rollback = rehearse_authority_rollback(
    source_bundle=source_bundle,
    legacy_settings=legacy_settings,
    active_global_settings=active_global_settings,
  )
  trace_paths = bundle.shadow_result.trace.by_path()
  legacy_entries = tuple(
    entry for entry in iter_catalog_entries() if entry.legacy_attr is not None
  )
  evidence = ActivationEvidence(
    catalog_parity_complete=(
      len(direct_names) == 316
      and len(derived_names) == 4
      and bundle.shadow_result.catalog_fingerprint
      == CATALOG_FINGERPRINT_SHA256
    ),
    source_parity_complete=bundle.shadow_result.success,
    facade_parity_complete=(facade_equal == len(facade_names)),
    derived_parity_complete=(derived_equal == len(derived_names)),
    provenance_complete=all(entry.path in trace_paths for entry in legacy_entries),
    generated_artifacts_current=_generated_artifacts_current(),
    secret_redaction_complete=(
      "token" not in repr(facade).lower()
      and "password" not in repr(facade).lower()
    ),
    compatibility_usage_supported=(unsupported_count == 0),
    rollback_rehearsal_passed=rollback.success,
    python_configuration_tests_passed=(
      verification.python_configuration_tests_passed
    ),
    python_behavior_baseline_not_worsened=(
      verification.python_behavior_baseline_not_worsened
    ),
    python_behavior_tests_passed=verification.python_behavior_tests_passed,
    csharp_tests_verified=verification.csharp_tests_verified,
  )
  readiness = evaluate_activation_readiness(evidence)
  compatibility_success = (
    bundle.shadow_result.success
    and bundle.parity_report.success
    and facade_equal == len(facade_names)
    and unsupported_count == 0
    and rollback.success
  )
  return ActivationRehearsalResult(
    legacy_settings=legacy_settings,
    shadow_status=bundle.shadow_result.status,
    facade=facade,
    direct_parity_equal=bundle.parity_report.equal_count,
    direct_parity_total=bundle.parity_report.total_count,
    derived_parity_equal=derived_equal,
    derived_parity_total=len(derived_names),
    facade_parity_equal=facade_equal,
    facade_parity_total=len(facade_names),
    unsupported_usage_count=unsupported_count,
    rollback_result=rollback,
    readiness=readiness,
    catalog_fingerprint=bundle.shadow_result.catalog_fingerprint,
    success=compatibility_success,
  )
