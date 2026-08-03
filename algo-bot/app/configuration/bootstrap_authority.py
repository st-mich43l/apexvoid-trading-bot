"""Strict bootstrap selector for the process-wide configuration authority."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import os


AUTHORITY_ENV_NAME = "APEXVOID_CONFIG_AUTHORITY"


class RuntimeConfigurationAuthority(StrEnum):
  LEGACY = "legacy"
  CANONICAL = "canonical"


def resolve_runtime_configuration_authority(
  environment: Mapping[str, str],
) -> RuntimeConfigurationAuthority:
  """Resolve authority from an explicit mapping without ambient state."""
  if AUTHORITY_ENV_NAME not in environment:
    return RuntimeConfigurationAuthority.LEGACY
  raw = environment[AUTHORITY_ENV_NAME]
  normalized = raw.strip().lower()
  if not normalized:
    raise ValueError(f"{AUTHORITY_ENV_NAME} cannot be blank")
  try:
    return RuntimeConfigurationAuthority(normalized)
  except ValueError as exc:
    raise ValueError(
      f"{AUTHORITY_ENV_NAME} must be legacy or canonical"
    ) from exc


def runtime_configuration_authority() -> RuntimeConfigurationAuthority:
  """Thin process-startup adapter over the pure authority parser."""
  return resolve_runtime_configuration_authority(os.environ)


def process_authority_is_explicit() -> bool:
  """Thin process-startup adapter over :func:`authority_is_explicit`."""
  return authority_is_explicit(os.environ)


def process_implicit_authority_warning() -> str | None:
  """Thin process-startup adapter over :func:`implicit_authority_warning`."""
  return implicit_authority_warning(os.environ)


def authority_is_explicit(environment: Mapping[str, str]) -> bool:
  """True when ``APEXVOID_CONFIG_AUTHORITY`` was provided by the operator.

  Explicitness is independent of the resolved value: an explicit ``legacy``
  is a reviewed rollback choice, whereas an *absent* variable resolves to
  ``legacy`` implicitly and should nudge the operator toward ``canonical``.
  """
  return AUTHORITY_ENV_NAME in environment


def implicit_authority_warning(environment: Mapping[str, str]) -> str | None:
  """Secret-safe warning when the authority is selected implicitly.

  Returns the one-line warning only when ``APEXVOID_CONFIG_AUTHORITY`` is
  absent (implicit legacy selection). An explicit ``legacy`` returns ``None``
  here -- that path is covered by the deprecation diagnostic instead.
  """
  if AUTHORITY_ENV_NAME in environment:
    return None
  return (
    "configuration_authority_implicit=true "
    "selected_authority=legacy recommended_authority=canonical"
  )
