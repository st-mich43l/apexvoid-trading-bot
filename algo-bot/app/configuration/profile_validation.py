"""Typed, secret-safe validation for immutable profile assignments."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.configuration.profiles import ProfileAssignment
from app.configuration.sources import FieldSpec
from app.configuration.sources import parse_source_value


@dataclass(frozen=True, slots=True)
class ParsedProfileAssignment:
  path: str
  spec: FieldSpec
  value: object


@dataclass(frozen=True, slots=True)
class ProfileAssignmentProblem:
  path: str
  code: str
  message: str
  spec: FieldSpec | None = None


ProfileAssignmentValidation = (
  ParsedProfileAssignment | ProfileAssignmentProblem
)


def validate_profile_assignment(
  *,
  profile_name: str,
  assignment: ProfileAssignment,
  specs: Mapping[str, FieldSpec],
) -> ProfileAssignmentValidation:
  """Validate and parse one profile assignment using canonical field rules."""
  spec = specs.get(assignment.path)
  if spec is None:
    return ProfileAssignmentProblem(
      path=assignment.path,
      code="unknown_profile_path",
      message=(
        f"profile {profile_name!r} references unknown canonical path "
        f"{assignment.path!r}"
      ),
    )
  if not spec.entry.configurable:
    return ProfileAssignmentProblem(
      path=assignment.path,
      spec=spec,
      code="profile_constant_override",
      message=(
        f"profile {profile_name!r} cannot override constant "
        f"{assignment.path!r}"
      ),
    )
  if spec.entry.secret:
    return ProfileAssignmentProblem(
      path=assignment.path,
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
    return ProfileAssignmentProblem(
      path=assignment.path,
      spec=spec,
      code="profile_value_invalid",
      message=(
        f"profile {profile_name!r} value for {assignment.path!r} does not "
        f"satisfy {spec.entry.type} and constraints {spec.entry.constraints}"
      ),
    )
  return ParsedProfileAssignment(
    path=assignment.path,
    spec=spec,
    value=value,
  )
