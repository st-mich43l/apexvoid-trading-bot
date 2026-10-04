"""Native YAML configuration schema used by the Python service.

The Python service deliberately keeps the YAML shape intact.  There is no
second settings vocabulary: a value read from ``runtime_config`` has the same
path as the value in the loaded YAML document.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterator, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class FrozenConfigModel(BaseModel):
  """Small immutable base for contract models that are not YAML settings."""

  model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class ConfigNode:
  """Read-only attribute view over one native YAML mapping.

  It intentionally implements only mapping/attribute access.  No aliases,
  profiles, source tracking or compatibility projection is hidden here.
  """

  __slots__ = ("_values",)

  def __init__(self, values: Mapping[str, Any] | None = None) -> None:
    self._values = {
      str(key): _wrap(value)
      for key, value in (values or {}).items()
    }

  def __getattr__(self, name: str) -> Any:
    try:
      return self._values[name]
    except KeyError as exc:
      raise AttributeError(name) from exc

  def __getitem__(self, key: str) -> Any:
    return self._values[key]

  def get(self, key: str, default: Any = None) -> Any:
    return self._values.get(key, default)

  def keys(self):
    return self._values.keys()

  def items(self):
    return self._values.items()

  def values(self):
    return self._values.values()

  def __iter__(self) -> Iterator[str]:
    return iter(self._values)

  def __contains__(self, key: object) -> bool:
    return key in self._values

  def __bool__(self) -> bool:
    return bool(self._values)

  def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False):
    values = deepcopy(self.to_dict()) if deep else self.to_dict()
    values.update(update or {})
    return type(self)(values)

  def to_dict(self) -> dict[str, Any]:
    return {key: _unwrap(value) for key, value in self._values.items()}

  def __repr__(self) -> str:
    return f"ConfigNode({self._values!r})"


def _wrap(value: Any) -> Any:
  if isinstance(value, Mapping):
    return ConfigNode(value)
  if isinstance(value, list):
    return [_wrap(item) for item in value]
  return value


def _unwrap(value: Any) -> Any:
  if isinstance(value, ConfigNode):
    return value.to_dict()
  if isinstance(value, list):
    return [_unwrap(item) for item in value]
  return value


class NativeConfigDocument(BaseModel):
  """Top-level shape validation for the native configuration document."""

  model_config = ConfigDict(extra="allow")

  version: int = Field(ge=1)
  includes: list[str] = Field(default_factory=list)
  runtime: dict[str, Any]
  instruments: dict[str, Any]
  analysis: dict[str, Any]
  auto_algo: dict[str, Any]
  execution: dict[str, Any]
  telegram: dict[str, Any]

  @model_validator(mode="after")
  def validate_instrument_declarations(self):
    for symbol, declaration in self.instruments.items():
      if symbol == "instrument_packs":
        continue
      if not isinstance(declaration, dict):
        raise ValueError(f"instruments.{symbol} must be a mapping")
      for required in ("canonical_symbol", "broker_symbol"):
        # Pack declarations are separate from concrete instruments.
        if "pack" not in declaration and required not in declaration:
          raise ValueError(f"instruments.{symbol}.{required} is required")
    return self


def validate_config(values: Mapping[str, Any]) -> dict[str, Any]:
  """Validate and return a plain native document."""
  try:
    return NativeConfigDocument.model_validate(dict(values)).model_dump(
      mode="python",
    )
  except ValidationError as exc:
    location = ".".join(str(part) for part in exc.errors()[0]["loc"])
    raise ValueError(f"invalid native configuration at {location}") from exc


def native_config(values: Mapping[str, Any]) -> ConfigNode:
  """Validate a document and expose its exact YAML paths as attributes."""
  return ConfigNode(validate_config(values))


__all__ = [
  "ConfigNode",
  "FrozenConfigModel",
  "NativeConfigDocument",
  "native_config",
  "validate_config",
]
