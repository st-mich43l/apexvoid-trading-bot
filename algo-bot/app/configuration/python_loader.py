"""Fail-closed production loader for Python canonical configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.configuration.authority import ConfigurationAuthority
from app.configuration.facade import CanonicalSettingsFacade
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.profiles import get_profile, profile_fingerprint
from app.configuration.resolver import resolve_configuration
from app.configuration.shadow_loader import catalog_fingerprint
from app.configuration.source_types import ConfigurationSourceBundle
from app.configuration.source_types import ResolutionTrace
from app.configuration.source_types import ResolutionWarning


ROLLBACK_ACTION = (
  "set APEXVOID_CONFIG_AUTHORITY=legacy and restart the service"
)


class CanonicalConfigurationError(RuntimeError):
  """Secret-safe canonical startup failure."""

  def __init__(
    self,
    *,
    category: str,
    path: str,
    source_name: str | None = None,
  ) -> None:
    fingerprint = catalog_fingerprint()
    source = f" source={source_name}" if source_name else ""
    super().__init__(
      "configuration_authority=canonical "
      f"error_category={category} canonical_path={path}{source} "
      f"catalog_fingerprint={fingerprint} rollback_action=\"{ROLLBACK_ACTION}\""
    )
    self.category = category
    self.path = path
    self.source_name = source_name
    self.catalog_fingerprint = fingerprint


@dataclass(frozen=True, slots=True)
class PythonCanonicalLoadResult:
  config: PythonRuntimeConfig = field(repr=False)
  facade: CanonicalSettingsFacade = field(repr=False)
  authority: ConfigurationAuthority
  profile: str
  catalog_fingerprint: str
  profile_fingerprint: str
  warnings: tuple[ResolutionWarning, ...]
  provenance: ResolutionTrace
  success: bool = True


def _validation_location(error: ValidationError) -> str:
  errors = error.errors(include_input=False, include_url=False)
  if not errors:
    return "<root>"
  return ".".join(str(part) for part in errors[0]["loc"]) or "<root>"


def load_python_canonical_settings(
  source_bundle: ConfigurationSourceBundle,
) -> PythonCanonicalLoadResult:
  """Resolve the Python projection and fail without constructing legacy state."""
  resolved = resolve_configuration(
    init_values=source_bundle.init_values,
    process_environment=source_bundle.process_environment,
    dotenv_values=source_bundle.dotenv_values,
    file_secret_values=source_bundle.file_secret_values,
    model=PythonRuntimeConfig,
  )
  if resolved.conflicts:
    conflict = resolved.conflicts[0]
    raise CanonicalConfigurationError(
      category=conflict.code,
      path=conflict.path,
      source_name=conflict.source_name,
    )
  if resolved.missing_required_paths:
    raise CanonicalConfigurationError(
      category="missing_required_input",
      path=resolved.missing_required_paths[0],
    )
  try:
    config = PythonRuntimeConfig.model_validate(resolved.nested_input)
  except ValidationError as exc:
    raise CanonicalConfigurationError(
      category="validation_error",
      path=_validation_location(exc),
    ) from exc
  return PythonCanonicalLoadResult(
    config=config,
    facade=CanonicalSettingsFacade(config),
    authority=ConfigurationAuthority.CANONICAL,
    profile=resolved.profile,
    catalog_fingerprint=catalog_fingerprint(),
    profile_fingerprint=profile_fingerprint(get_profile(resolved.profile)),
    warnings=resolved.warnings,
    provenance=resolved.trace,
  )

