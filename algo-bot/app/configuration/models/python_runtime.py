"""Canonical configuration projection owned by the Python service."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import TYPE_CHECKING, Any, Mapping, Self, cast

from pydantic import BaseModel, PrivateAttr, create_model

from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.root import ApexVoidConfig

if TYPE_CHECKING:
  from app.configuration.effective_instrument import EffectiveInstrumentConfig

_PYTHON_RUNTIME_OWNERS = frozenset({"python", "shared"})


class _EffectiveInstrumentCache:
  """Per-runtime cache that is never carried into a copied configuration."""

  __slots__ = ("lock", "values")

  def __init__(self) -> None:
    self.lock = RLock()
    self.values: dict[str, EffectiveInstrumentConfig] = {}

  def __deepcopy__(self, memo: dict[int, object]) -> _EffectiveInstrumentCache:
    # A deep model_copy represents a distinct configuration snapshot.  Its
    # effective contexts must be composed from that snapshot, not this cache.
    copied = type(self)()
    memo[id(self)] = copied
    return copied


class RuntimeInstrumentAPI(FrozenConfigModel):
  """Instrument-context helpers available on PythonRuntimeConfig instances."""

  _effective_instrument_cache: _EffectiveInstrumentCache = PrivateAttr(
    default_factory=_EffectiveInstrumentCache,
  )

  def model_copy(
    self,
    *,
    update: Mapping[str, Any] | None = None,
    deep: bool = False,
  ) -> Self:
    copied = super().model_copy(update=update, deep=deep)
    # Pydantic copies private attributes by reference for a shallow copy.
    # Configuration fixtures and tests commonly customize the frozen runtime
    # with model_copy(update=...), so retaining the old effective contexts
    # would silently return stale instrument settings.
    copied._effective_instrument_cache = _EffectiveInstrumentCache()
    return copied

  def for_instrument(self, symbol: str) -> EffectiveInstrumentConfig:
    from app.configuration.effective_instrument import build_effective_instrument

    cache_key = symbol.strip().upper()
    cache = self._effective_instrument_cache
    with cache.lock:
      cached = cache.values.get(cache_key)
      if cached is not None:
        return cached

      trace = None
      try:
        from app.core import config as core_config
        trace = core_config.active_configuration_resolution_trace()
      except Exception:
        trace = None
      effective = build_effective_instrument(
        self,
        symbol,
        resolution_trace=trace,
      )

      instrument_id = effective.identity.instrument_id.strip().upper()
      # Do not eagerly seed unvalidated aliases from one direct instrument-id
      # lookup. A malformed config can assign another instrument's alias; the
      # full builder detects that ambiguity when the alias is actually looked
      # up. Eager alias seeding would bypass that validation. Once validation
      # succeeds, reuse the already-cached immutable instrument context.
      existing = cache.values.get(instrument_id)
      if existing is not None:
        cache.values[cache_key] = existing
        return existing
      cache.values[cache_key] = effective
      cache.values[instrument_id] = effective
      return effective

  def enabled_instruments(self) -> tuple[str, ...]:
    from app.configuration.effective_instrument import list_enabled_instrument_ids

    return list_enabled_instrument_ids(self)

  def live_instruments(self) -> tuple[str, ...]:
    from app.configuration.effective_instrument import list_live_instrument_ids

    return list_live_instrument_ids(self)

  def instrument_for_broker_symbol(self, broker_symbol: str) -> EffectiveInstrumentConfig:
    from app.configuration.effective_instrument import (
      instrument_id_for_broker_symbol,
    )

    instrument_id = instrument_id_for_broker_symbol(self, broker_symbol)
    return self.for_instrument(instrument_id)


def _leaf_owner(field: object) -> str | None:
  metadata = getattr(field, "json_schema_extra", None) or {}
  apexvoid = metadata.get("apexvoid_config")
  return None if apexvoid is None else str(apexvoid["owner"])


def _descendant_owners(model: type[BaseModel]) -> frozenset[str]:
  owners: set[str] = set()
  for field in model.model_fields.values():
    owner = _leaf_owner(field)
    if owner is not None:
      owners.add(owner)
    annotation = field.annotation
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
      owners.update(_descendant_owners(annotation))
  return frozenset(owners)


def _project_model(
  model: type[BaseModel],
  *,
  projected_name: str | None = None,
  base: type[BaseModel] = FrozenConfigModel,
) -> type[BaseModel]:
  """Derive a strict model using canonical FieldInfo and nested model types."""
  owners = _descendant_owners(model)
  if owners <= _PYTHON_RUNTIME_OWNERS:
    return model
  decorators = model.__pydantic_decorators__
  if decorators.field_validators or decorators.model_validators:
    raise TypeError(
      f"mixed-owner model {model.__name__} has validators that require "
      "an explicit projection review"
    )
  definitions: dict[str, tuple[object, object]] = {}
  for field_name, source_field in model.model_fields.items():
    annotation = source_field.annotation
    projected_field = deepcopy(source_field)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
      child_owners = _descendant_owners(annotation)
      # Non-catalog containers (e.g. instruments registry) have no leaf
      # owners; retain them on the Python projection unchanged.
      if not child_owners:
        definitions[field_name] = (annotation, projected_field)
        continue
      if not child_owners & _PYTHON_RUNTIME_OWNERS:
        continue
      projected_annotation = _project_model(annotation)
      projected_field.annotation = projected_annotation
      if projected_field.default_factory is annotation:
        projected_field.default_factory = projected_annotation
      definitions[field_name] = (projected_annotation, projected_field)
      continue
    if _leaf_owner(source_field) in _PYTHON_RUNTIME_OWNERS:
      definitions[field_name] = (annotation, projected_field)
  return create_model(
    projected_name or f"{model.__name__}PythonRuntime",
    __base__=base,
    __module__=__name__,
    **definitions,
  )


PythonRuntimeConfig = cast(
  type[ApexVoidConfig],
  _project_model(
    ApexVoidConfig,
    projected_name="PythonRuntimeConfig",
    base=RuntimeInstrumentAPI,
  ),
)
PythonRuntimeConfig.__doc__ = (
  "Frozen canonical root containing Python-owned and shared fields only, "
  "with effective instrument context helpers."
)
