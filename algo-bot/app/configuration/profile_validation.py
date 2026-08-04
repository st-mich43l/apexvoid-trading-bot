"""Typed, secret-safe validation for immutable profile assignments."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from app.configuration.profiles import ProfileAssignment
from app.configuration.sources import FieldSpec
from app.configuration.sources import parse_source_value


@dataclass(frozen=True, slots=True)
class ProfileAssignmentValidation:
  path: str
  valid: bool
  spec: FieldSpec | None = None
  value: object = None
  code: str | None = None
  message: str | None = None


def validate_profile_assignment(
  *,
  profile_name: str,
  assignment: ProfileAssignment,
  specs: Mapping[str, FieldSpec],
) -> ProfileAssignmentValidation:
  """Validate and parse one profile assignment using canonical field rules."""
  spec = specs.get(assignment.path)
  if spec is None:
    return ProfileAssignmentValidation(
      path=assignment.path,
      valid=False,
      code="unknown_profile_path",
      message=(
        f"profile {profile_name!r} references unknown canonical path "
        f"{assignment.path!r}"
      ),
    )
  if not spec.entry.configurable:
    return ProfileAssignmentValidation(
      path=assignment.path,
      valid=False,
      spec=spec,
      code="profile_constant_override",
      message=(
        f"profile {profile_name!r} cannot override constant "
        f"{assignment.path!r}"
      ),
    )
  if spec.entry.secret:
    return ProfileAssignmentValidation(
      path=assignment.path,
      valid=False,
      spec=spec,
      code="profile_secret_override",
      message=(
        f"profile {profile_name!r} cannot provide secret "
        f"{assignment.path!r}"
      ),
    )
  try:
    value = parse_source_value(spec, assignment.value)
  except (TypeError, ValueError):
    return ProfileAssignmentValidation(
      path=assignment.path,
      valid=False,
      spec=spec,
      code="profile_value_invalid",
      message=(
        f"profile {profile_name!r} value for {assignment.path!r} does not "
        f"satisfy {spec.entry.type} and constraints {spec.entry.constraints}"
      ),
    )
  return ProfileAssignmentValidation(
    path=assignment.path,
    valid=True,
    spec=spec,
    value=value,
  )
