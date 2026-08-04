"""Production source collection for Python canonical startup."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from app.configuration.config_file import (
  load_config_file,
  resolve_config_file_path,
)
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
  legacy precedence extended with CONFIG_FILE:

  file_secret < config_file < dotenv < process environment < explicit init.
  """
  process_environment = dict(os.environ)
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
  config_path = resolve_config_file_path(
    process_environment=process_environment,
    cli_path=policy.config_file,
  )
  # Unset path → empty CONFIG_FILE layer (ENV-only deploys keep working).
  # Explicit path that is missing/unreadable/malformed fails closed.
  loaded = load_config_file(config_path, missing_ok=config_path is None)
  return ConfigurationSourceBundle(
    init_values={},
    process_environment=process_environment,
    dotenv_values=dotenv,
    # Legacy Settings declares no secrets_dir, so its file-secret layer is
    # empty. The resolver retains the layer explicitly for deterministic
    # precedence and future reviewed adoption.
    file_secret_values=file_secrets,
    config_file_values=dict(loaded.flat_values),
    instruments=loaded.instruments.root,
  )
