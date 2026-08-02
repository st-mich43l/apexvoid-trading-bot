"""Deterministic, ambient-state-free configuration source resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pydantic_core import PydanticUndefined

from app.configuration.profiles import get_profile
from app.configuration.source_types import ConfigurationSourceBundle
from app.configuration.source_types import ResolvedConfiguration
from app.configuration.source_types import ResolvedFieldSource
from app.configuration.source_types import ResolutionConflict
from app.configuration.source_types import ResolutionTrace
from app.configuration.source_types import ResolutionWarning
from app.configuration.source_types import SourceKind
from app.configuration.sources import FieldSpec
from app.configuration.sources import field_specs
from app.configuration.sources import resolve_source_layer


_LAYERS = (
  (SourceKind.FILE_SECRET, "file_secret_values"),
  (SourceKind.DOTENV, "dotenv_values"),
  (SourceKind.PROCESS_ENV, "process_environment"),
  (SourceKind.INIT_VALUE, "init_values"),
)


def _nested(flat_values: Mapping[str, object]) -> dict[str, object]:
  root: dict[str, object] = {}
  for path, value in sorted(flat_values.items()):
    cursor = root
    parts = path.split(".")
    for part in parts[:-1]:
      child = cursor.setdefault(part, {})
      if not isinstance(child, dict):
        raise ValueError(f"canonical path collision at {path}")
      cursor = child
    cursor[parts[-1]] = value
  return root


def _profile_name(
  spec: FieldSpec,
  bundle: ConfigurationSourceBundle,
) -> tuple[str, tuple[ResolutionConflict, ...]]:
  value = spec.field.default
  conflicts: list[ResolutionConflict] = []
  for source_kind, attribute in _LAYERS:
    layer = resolve_source_layer(
      spec,
      getattr(bundle, attribute),
      source_kind=source_kind,
      source_name=attribute,
    )
    conflicts.extend(layer.conflicts)
    if layer.candidate is not None:
      value = layer.candidate.value
  try:
    profile = get_profile(str(value)).name
  except ValueError as exc:
    conflicts.append(ResolutionConflict(
      code="unsupported_profile",
      path=spec.entry.path,
      source_kind=SourceKind.INIT_VALUE,
      source_name="profile_selection",
      canonical_env=spec.entry.canonical_env,
      message=str(exc),
    ))
    profile = "conservative"
  return profile, tuple(conflicts)


def resolve_configuration(
  *,
  init_values: Mapping[str, object],
  process_environment: Mapping[str, str],
  dotenv_values: Mapping[str, str | None],
  file_secret_values: Mapping[str, str],
) -> ResolvedConfiguration:
  """Resolve explicit mappings without reading dotenv or process state."""
  bundle = ConfigurationSourceBundle(
    init_values=dict(init_values),
    process_environment=dict(process_environment),
    dotenv_values=dict(dotenv_values),
    file_secret_values=dict(file_secret_values),
  )
  specs = field_specs()
  profile, profile_conflicts = _profile_name(specs["runtime.profile"], bundle)
  selected_profile = get_profile(profile)
  values: dict[str, object] = {}
  traces: dict[str, ResolvedFieldSource] = {}
  histories: dict[str, list[str]] = {path: [] for path in specs}
  warnings: list[ResolutionWarning] = []
  conflicts: list[ResolutionConflict] = list(profile_conflicts)

  for path, spec in specs.items():
    default = spec.field.default
    missing = spec.field.is_required() or default is PydanticUndefined
    if not missing:
      values[path] = default
    traces[path] = ResolvedFieldSource(
      path=path,
      item_id=spec.entry.item_id,
      source_kind=SourceKind.SCHEMA_DEFAULT,
      source_name=("required_input_missing" if missing else "schema_default"),
      canonical_env=spec.entry.canonical_env,
      supplied_alias=None,
      explicit=False,
      overridden_lower_precedence_sources=(),
      profile_name=profile,
      compatibility_rule=None,
      secret=spec.entry.secret,
    )

  for assignment in selected_profile.assignments:
    spec = specs[assignment.path]
    previous = traces[assignment.path]
    histories[assignment.path].append(
      f"{previous.source_kind.value}:{previous.source_name}"
    )
    values[assignment.path] = assignment.value
    traces[assignment.path] = ResolvedFieldSource(
      path=assignment.path,
      item_id=spec.entry.item_id,
      source_kind=SourceKind.PROFILE,
      source_name=profile,
      canonical_env=spec.entry.canonical_env,
      supplied_alias=None,
      explicit=False,
      overridden_lower_precedence_sources=tuple(histories[assignment.path]),
      profile_name=profile,
      compatibility_rule=None,
      secret=spec.entry.secret,
    )

  for source_kind, attribute in _LAYERS:
    layer_values = getattr(bundle, attribute)
    for path, spec in specs.items():
      if not spec.entry.configurable:
        continue
      layer = resolve_source_layer(
        spec,
        layer_values,
        source_kind=source_kind,
        source_name=attribute,
      )
      warnings.extend(layer.warnings)
      conflicts.extend(layer.conflicts)
      if layer.candidate is None:
        continue
      previous = traces[path]
      histories[path].append(
        f"{previous.source_kind.value}:{previous.source_name}"
      )
      values[path] = layer.candidate.value
      source = layer.candidate.source
      traces[path] = ResolvedFieldSource(
        path=path,
        item_id=source.item_id,
        source_kind=source.source_kind,
        source_name=source.source_name,
        canonical_env=source.canonical_env,
        supplied_alias=source.supplied_alias,
        explicit=True,
        overridden_lower_precedence_sources=tuple(histories[path]),
        profile_name=profile,
        compatibility_rule=None,
        secret=source.secret,
      )

  missing = tuple(sorted(
    path for path, spec in specs.items()
    if spec.field.is_required() and path not in values
  ))
  return ResolvedConfiguration(
    profile=profile,
    nested_input=_nested(values),
    flat_values=values,
    trace=ResolutionTrace(tuple(traces[path] for path in sorted(traces))),
    warnings=tuple(warnings),
    conflicts=tuple(conflicts),
    missing_required_paths=missing,
  )

