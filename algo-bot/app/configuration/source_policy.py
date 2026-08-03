"""Declarative Python configuration source policy.

Phase 2H decouples runtime source collection from the legacy ``Settings``
``model_config``. The dotenv file, encoding, and (reserved) file-secret
directory are described here once, so both the legacy authority and the
canonical loader observe an identical, reviewed source contract instead of
reaching into a pydantic-settings ``SettingsConfigDict`` at call time.

Precedence is unchanged from the historical ``Settings`` behavior: process
environment overrides dotenv, and explicit init values override the process
environment. This module only names the file inputs; the resolver in
``app.configuration.resolver`` owns the layered precedence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PythonConfigurationSourcePolicy:
  """File-source inputs for Python configuration collection."""

  env_file: str = ".env"
  env_file_encoding: str = "utf-8"
  # Legacy Settings declares no secrets directory; the layer is retained by
  # the resolver for deterministic precedence and future reviewed adoption.
  secrets_directory: str | None = None


PYTHON_SOURCE_POLICY = PythonConfigurationSourcePolicy()
