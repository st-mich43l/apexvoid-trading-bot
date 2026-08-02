"""Non-authoritative validation of deterministic source resolution."""

from __future__ import annotations

from hashlib import sha256
import json

from pydantic import ValidationError

from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.root import ApexVoidConfig
from app.configuration.profiles import get_profile
from app.configuration.profiles import profile_fingerprint
from app.configuration.resolver import resolve_configuration
from app.configuration.source_types import ConfigurationSourceBundle
from app.configuration.source_types import ShadowLoadResult
from app.configuration.source_types import ShadowLoadStatus


def catalog_fingerprint() -> str:
  payload = (
    json.dumps(
      [entry.as_dict() for entry in iter_catalog_entries()],
      indent=2,
      sort_keys=True,
      ensure_ascii=False,
      separators=(",", ": "),
    )
    + "\n"
  ).encode("utf-8")
  return sha256(payload).hexdigest()


def _safe_validation_errors(error: ValidationError) -> tuple[str, ...]:
  result = []
  for item in error.errors(include_input=False, include_url=False):
    location = ".".join(str(part) for part in item["loc"])
    result.append(f"{location}: {item['type']}: {item['msg']}")
  return tuple(result)


def load_shadow_configuration(
  source_bundle: ConfigurationSourceBundle,
) -> ShadowLoadResult:
  """Resolve and validate the inactive root without changing startup state."""
  resolved = resolve_configuration(
    init_values=source_bundle.init_values,
    process_environment=source_bundle.process_environment,
    dotenv_values=source_bundle.dotenv_values,
    file_secret_values=source_bundle.file_secret_values,
  )
  common = {
    "profile": resolved.profile,
    "trace": resolved.trace,
    "warnings": resolved.warnings,
    "conflicts": resolved.conflicts,
    "missing_required_paths": resolved.missing_required_paths,
    "catalog_fingerprint": catalog_fingerprint(),
    "profile_fingerprint": profile_fingerprint(
      get_profile(resolved.profile)
    ),
    "authoritative": False,
  }
  if resolved.conflicts:
    return ShadowLoadResult(
      status=ShadowLoadStatus.INVALID,
      validation_errors=tuple(item.message for item in resolved.conflicts),
      **common,
    )
  if resolved.missing_required_paths:
    return ShadowLoadResult(
      status=ShadowLoadStatus.INCOMPLETE_REQUIRED_INPUT,
      **common,
    )
  try:
    config = ApexVoidConfig.model_validate(resolved.nested_input)
  except ValidationError as exc:
    return ShadowLoadResult(
      status=ShadowLoadStatus.INVALID,
      validation_errors=_safe_validation_errors(exc),
      **common,
    )
  return ShadowLoadResult(
    config=config,
    status=ShadowLoadStatus.COMPLETE,
    success=True,
    **common,
  )
