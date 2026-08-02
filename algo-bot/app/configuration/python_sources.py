"""Production source collection for Python canonical startup."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from dotenv import dotenv_values

from app.configuration.source_types import ConfigurationSourceBundle


def load_python_runtime_source_bundle(
  settings_model_config: Mapping[str, object],
) -> ConfigurationSourceBundle:
  """Mirror the existing Settings dotenv/process behavior without I/O services."""
  configured_file = settings_model_config.get("env_file")
  encoding = settings_model_config.get("env_file_encoding") or "utf-8"
  dotenv: dict[str, str | None] = {}
  if configured_file:
    path = Path(str(configured_file))
    if path.is_file():
      dotenv = {
        str(key): value
        for key, value in dotenv_values(path, encoding=str(encoding)).items()
      }
  return ConfigurationSourceBundle(
    init_values={},
    process_environment=dict(os.environ),
    dotenv_values=dotenv,
    # Legacy Settings declares no secrets_dir, so its file-secret layer is
    # empty. The resolver retains the layer explicitly for deterministic
    # precedence and future reviewed adoption.
    file_secret_values={},
  )
