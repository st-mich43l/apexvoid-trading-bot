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

