"""Production source collection for Python canonical startup."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from app.configuration.source_policy import (
  PYTHON_SOURCE_POLICY,
  PythonConfigurationSourcePolicy,
)
from app.configuration.source_types import ConfigurationSourceBundle


def load_python_runtime_source_bundle(
  policy: PythonConfigurationSourcePolicy = PYTHON_SOURCE_POLICY,
) -> ConfigurationSourceBundle:
  """Mirror the historical Settings dotenv/process behavior without I/O services.

  The dotenv file and encoding are taken from ``policy`` rather than from a
  pydantic ``SettingsConfigDict``. The resulting layered bundle preserves the
  legacy precedence: dotenv < process environment < explicit init values.
  """
  dotenv: dict[str, str | None] = {}
  if policy.env_file:
    path = Path(str(policy.env_file))
    if path.is_file():
      dotenv = {
        str(key): value
        for key, value in dotenv_values(
          path, encoding=str(policy.env_file_encoding or "utf-8")
        ).items()
      }
  file_secrets: dict[str, str] = {}
  return ConfigurationSourceBundle(
    init_values={},
    process_environment=dict(os.environ),
    dotenv_values=dotenv,
    # Legacy Settings declares no secrets_dir, so its file-secret layer is
    # empty. The resolver retains the layer explicitly for deterministic
    # precedence and future reviewed adoption.
    file_secret_values=file_secrets,
  )
