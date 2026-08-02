"""Pure typed parsing and per-layer alias resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Mapping, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from app.configuration.catalog import CatalogEntry
from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.root import ApexVoidConfig
from app.configuration.source_types import ResolutionConflict
from app.configuration.source_types import ResolutionWarning
from app.configuration.source_types import SourceCandidate
from app.configuration.source_types import SourceKind


@dataclass(frozen=True, slots=True)
class FieldSpec:
  entry: CatalogEntry
  field: FieldInfo


@dataclass(frozen=True, slots=True)
class ParsedCandidate:
  source: SourceCandidate
  value: object = None


@dataclass(frozen=True, slots=True)
class LayerResolution:
  candidate: ParsedCandidate | None
  warnings: tuple[ResolutionWarning, ...] = ()
  conflicts: tuple[ResolutionConflict, ...] = ()


def _iter_field_specs(
  model: type[BaseModel], prefix: tuple[str, ...] = (),
):
  for name, field in model.model_fields.items():
    path = (*prefix, name)
    metadata = (field.json_schema_extra or {}).get("apexvoid_config")
    if metadata is not None:
      yield ".".join(path), field
    annotation = field.annotation
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
      yield from _iter_field_specs(annotation, path)


def field_specs() -> dict[str, FieldSpec]:
  fields = dict(_iter_field_specs(ApexVoidConfig))
  return {
    entry.path: FieldSpec(entry=entry, field=fields[entry.path])
    for entry in iter_catalog_entries()
  }


def _adapter(field: FieldInfo) -> TypeAdapter:
  annotation = (
    Annotated[field.annotation, *field.metadata]
    if field.metadata else field.annotation
  )
  return TypeAdapter(annotation)


def parse_source_value(spec: FieldSpec, raw: object) -> object:
  """Parse one supplied value using its canonical typed declaration."""
  annotation = spec.field.annotation
  origin = get_origin(annotation)
  if isinstance(raw, str) and origin is list:
    item_type = get_args(annotation)[0]
    parts = [] if not raw.strip() else raw.split(",")
    if item_type is str:
      raw = [item.strip() for item in parts]
    else:
      raw = [item.strip() for item in parts]
  elif isinstance(raw, str) and annotation is not str:
    raw = raw.strip()
  return _adapter(spec.field).validate_python(raw)


def _input_names(
  spec: FieldSpec, source_kind: SourceKind,
) -> tuple[str, ...]:
  entry = spec.entry
  if source_kind is SourceKind.INIT_VALUE:
    return tuple(dict.fromkeys(filter(None, (
      entry.path,
      entry.legacy_attr,
      entry.canonical_env,
      *entry.deprecated_aliases,
    ))))
  return tuple(filter(None, (
    entry.canonical_env,
    *entry.deprecated_aliases,
  )))


def resolve_source_layer(
  spec: FieldSpec,
  values: Mapping[str, object],
  *,
  source_kind: SourceKind,
  source_name: str,
) -> LayerResolution:
  """Resolve canonical and deprecated names inside exactly one source."""
  names = _input_names(spec, source_kind)
  present = tuple(
    name for name in names if name in values and values[name] is not None
  )
  if not present:
    return LayerResolution(candidate=None)

  parsed: dict[str, object] = {}
  for name in present:
    try:
      parsed[name] = parse_source_value(spec, values[name])
    except (TypeError, ValueError, ValidationError) as exc:
      expected = spec.entry.type
      return LayerResolution(
        candidate=None,
        conflicts=(ResolutionConflict(
          code="source_parse_error",
          path=spec.entry.path,
          source_kind=source_kind,
          source_name=source_name,
          canonical_env=spec.entry.canonical_env,
          supplied_names=(name,),
          secret=spec.entry.secret,
          message=(
            f"{spec.entry.path} from {source_name} name {name} "
            f"does not match expected type {expected}"
          ),
        ),),
      )

  distinct = []
  for value in parsed.values():
    if value not in distinct:
      distinct.append(value)
  if len(distinct) > 1:
    return LayerResolution(
      candidate=None,
      conflicts=(ResolutionConflict(
        code="source_alias_conflict",
        path=spec.entry.path,
        source_kind=source_kind,
        source_name=source_name,
        canonical_env=spec.entry.canonical_env,
        supplied_names=present,
        secret=spec.entry.secret,
        message=(
          f"conflicting aliases for {spec.entry.path} in {source_name}: "
          f"{', '.join(present)}"
        ),
      ),),
    )

  chosen = present[0]
  warnings: list[ResolutionWarning] = []
  if len(present) > 1:
    warnings.append(ResolutionWarning(
      code="duplicate_source_names",
      path=spec.entry.path,
      source_kind=source_kind,
      source_name=source_name,
      canonical_env=spec.entry.canonical_env,
      secret=spec.entry.secret,
      message=(
        f"equivalent aliases for {spec.entry.path} in {source_name}; "
        f"using {chosen}"
      ),
    ))
  if chosen in spec.entry.deprecated_aliases:
    warnings.append(ResolutionWarning(
      code="deprecated_alias",
      path=spec.entry.path,
      source_kind=source_kind,
      source_name=source_name,
      canonical_env=spec.entry.canonical_env,
      supplied_alias=chosen,
      secret=spec.entry.secret,
      message=(
        f"deprecated alias {chosen} supplied for "
        f"{spec.entry.canonical_env} ({spec.entry.path}) in {source_name}"
      ),
    ))

  supplied_alias = (
    chosen if chosen in spec.entry.deprecated_aliases else None
  )
  return LayerResolution(
    candidate=ParsedCandidate(
      source=SourceCandidate(
        path=spec.entry.path,
        item_id=spec.entry.item_id,
        source_kind=source_kind,
        source_name=chosen,
        canonical_env=spec.entry.canonical_env,
        supplied_alias=supplied_alias,
        explicit=True,
        secret=spec.entry.secret,
      ),
      value=parsed[chosen],
    ),
    warnings=tuple(warnings),
  )

