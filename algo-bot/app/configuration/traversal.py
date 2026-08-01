"""Derive catalog metadata recursively from Pydantic model fields."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, get_args, get_origin

from pydantic import BaseModel


def _model_type(annotation: Any) -> type[BaseModel] | None:
  if isinstance(annotation, type) and issubclass(annotation, BaseModel):
    return annotation
  for argument in get_args(annotation):
    if isinstance(argument, type) and issubclass(argument, BaseModel):
      return argument
  origin = get_origin(annotation)
  if isinstance(origin, type) and issubclass(origin, BaseModel):
    return origin
  return None


def iter_config_metadata(
  model: type[BaseModel],
  prefix: tuple[str, ...] = (),
) -> Iterator[tuple[str, dict[str, Any]]]:
  """Yield stable path/metadata pairs without a separate field registry."""
  for name, field in model.model_fields.items():
    path = (*prefix, name)
    extra = field.json_schema_extra or {}
    metadata = extra.get("apexvoid_config") if isinstance(extra, dict) else None
    if metadata is not None:
      yield ".".join(path), dict(metadata)
    nested = _model_type(field.annotation)
    if nested is not None:
      yield from iter_config_metadata(nested, path)
