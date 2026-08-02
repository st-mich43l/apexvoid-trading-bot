"""Deterministic evidence and permit model for activation rehearsal only."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json

from app.configuration.authority import ConfigurationAuthority


class ActivationBlockerCode(StrEnum):
  CATALOG_PARITY_INCOMPLETE = "CATALOG_PARITY_INCOMPLETE"
  SOURCE_PARITY_INCOMPLETE = "SOURCE_PARITY_INCOMPLETE"
  FACADE_PARITY_INCOMPLETE = "FACADE_PARITY_INCOMPLETE"
  DERIVED_PARITY_INCOMPLETE = "DERIVED_PARITY_INCOMPLETE"
  PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"
  GENERATED_ARTIFACT_DRIFT = "GENERATED_ARTIFACT_DRIFT"
  SECRET_REDACTION_FAILED = "SECRET_REDACTION_FAILED"
  UNSUPPORTED_LEGACY_USAGE = "UNSUPPORTED_LEGACY_USAGE"
  ROLLBACK_REHEARSAL_FAILED = "ROLLBACK_REHEARSAL_FAILED"
  CONFIGURATION_TESTS_FAILED = "CONFIGURATION_TESTS_FAILED"
  PYTHON_BEHAVIOR_BASELINE_WORSENED = "PYTHON_BEHAVIOR_BASELINE_WORSENED"
  PYTHON_BEHAVIOR_TESTS_FAILED = "PYTHON_BEHAVIOR_TESTS_FAILED"
  CSHARP_TESTS_NOT_VERIFIED = "CSHARP_TESTS_NOT_VERIFIED"


@dataclass(frozen=True, slots=True)
class ActivationEvidence:
  catalog_parity_complete: bool
  source_parity_complete: bool
  facade_parity_complete: bool
  derived_parity_complete: bool
  provenance_complete: bool
  generated_artifacts_current: bool
  secret_redaction_complete: bool
  compatibility_usage_supported: bool
  rollback_rehearsal_passed: bool
  python_configuration_tests_passed: bool
  python_behavior_baseline_not_worsened: bool
  python_behavior_tests_passed: bool
  csharp_tests_verified: bool


@dataclass(frozen=True, slots=True)
class ActivationReadiness:
  ready: bool
  blockers: tuple[ActivationBlockerCode, ...]
  warnings: tuple[str, ...]
  evaluated_evidence: ActivationEvidence


_EVIDENCE_BLOCKERS = (
  ("catalog_parity_complete", ActivationBlockerCode.CATALOG_PARITY_INCOMPLETE),
  ("source_parity_complete", ActivationBlockerCode.SOURCE_PARITY_INCOMPLETE),
  ("facade_parity_complete", ActivationBlockerCode.FACADE_PARITY_INCOMPLETE),
  ("derived_parity_complete", ActivationBlockerCode.DERIVED_PARITY_INCOMPLETE),
  ("provenance_complete", ActivationBlockerCode.PROVENANCE_INCOMPLETE),
  ("generated_artifacts_current", ActivationBlockerCode.GENERATED_ARTIFACT_DRIFT),
  ("secret_redaction_complete", ActivationBlockerCode.SECRET_REDACTION_FAILED),
  ("compatibility_usage_supported", ActivationBlockerCode.UNSUPPORTED_LEGACY_USAGE),
  ("rollback_rehearsal_passed", ActivationBlockerCode.ROLLBACK_REHEARSAL_FAILED),
  ("python_configuration_tests_passed", ActivationBlockerCode.CONFIGURATION_TESTS_FAILED),
  (
    "python_behavior_baseline_not_worsened",
    ActivationBlockerCode.PYTHON_BEHAVIOR_BASELINE_WORSENED,
  ),
  ("python_behavior_tests_passed", ActivationBlockerCode.PYTHON_BEHAVIOR_TESTS_FAILED),
  ("csharp_tests_verified", ActivationBlockerCode.CSHARP_TESTS_NOT_VERIFIED),
)


def evaluate_activation_readiness(
  evidence: ActivationEvidence,
) -> ActivationReadiness:
  blockers = tuple(
    blocker
    for field_name, blocker in _EVIDENCE_BLOCKERS
    if not getattr(evidence, field_name)
  )
  return ActivationReadiness(
    ready=not blockers,
    blockers=blockers,
    warnings=(
      "Phase 2D1 readiness permits rehearsal only; legacy remains authoritative.",
    ),
    evaluated_evidence=evidence,
  )


@dataclass(frozen=True, slots=True)
class RehearsalPermit:
  authority: ConfigurationAuthority
  evidence_fingerprint_sha256: str
  production_activation_allowed: bool = False

  def __post_init__(self) -> None:
    if self.authority is not ConfigurationAuthority.CANONICAL_REHEARSAL:
      raise ValueError("a rehearsal permit cannot select production authority")
    if self.production_activation_allowed:
      raise ValueError("Phase 2D1 cannot permit production activation")


def issue_rehearsal_permit(evidence: ActivationEvidence) -> RehearsalPermit:
  readiness = evaluate_activation_readiness(evidence)
  if not readiness.ready:
    codes = ", ".join(code.value for code in readiness.blockers)
    raise RuntimeError(f"activation rehearsal evidence is incomplete: {codes}")
  payload = json.dumps(asdict(evidence), sort_keys=True, separators=(",", ":"))
  return RehearsalPermit(
    authority=ConfigurationAuthority.CANONICAL_REHEARSAL,
    evidence_fingerprint_sha256=sha256(payload.encode("utf-8")).hexdigest(),
  )
