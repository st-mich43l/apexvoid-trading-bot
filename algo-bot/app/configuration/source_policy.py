"""Declarative Python configuration source policy.

Phase 2H/2I-B describe the dotenv file, encoding, and (reserved) file-secret
directory here once, so the canonical loader observes a reviewed source
contract independent of any legacy SettingsConfigDict.

Precedence is unchanged from the historical Settings behavior: process
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
  # No secrets directory is configured for the historical path; the layer is
  # retained by the resolver for deterministic precedence and future reviewed
  # adoption.
  secrets_directory: str | None = None


PYTHON_SOURCE_POLICY = PythonConfigurationSourcePolicy()
