"""Read-only compatibility facade over canonical configuration."""

from __future__ import annotations

from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
from pydantic import BaseModel


def _path_value(config: BaseModel, path: tuple[str, ...]) -> object:
  value: object = config
  for part in path:
    value = getattr(value, part)
  return value


def _derived_value(config: BaseModel, name: str) -> object:
  """Apply only the four catalogued, reviewed legacy transformations."""
  telegram = config.delivery.telegram
  if name == "signal_vip_channel_id":
    return telegram.telegram_channel_id
  if name == "telegram_chat_id":
    return str(telegram.telegram_channel_id)
  if name == "xau_public_channel_id":
    return telegram.signal_public_channel_id
  if name == "xau_vip_channel_id":
    return telegram.telegram_channel_id
  raise AttributeError(name)


class CanonicalSettingsFacade:
  """Expose the proven legacy read surface without emulating BaseSettings."""

  __slots__ = ("_config",)

  def __init__(self, config: BaseModel) -> None:
    object.__setattr__(self, "_config", config)

  def __getattr__(self, name: str) -> object:
    path = DIRECT_LEGACY_PATHS.get(name)
    if path is not None:
      return _path_value(self._config, path)
    if name in DERIVED_LEGACY_PROPERTIES:
      return _derived_value(self._config, name)
    raise AttributeError(
      f"{type(self).__name__!s} has unsupported legacy attribute {name!r}"
    )

  def __setattr__(self, name: str, value: object) -> None:
    del name, value
    raise TypeError("CanonicalSettingsFacade is immutable")

  def __delattr__(self, name: str) -> None:
    del name
    raise TypeError("CanonicalSettingsFacade is immutable")

  def __dir__(self) -> list[str]:
    return sorted({
      *super().__dir__(),
      *DIRECT_LEGACY_PATHS,
      *DERIVED_LEGACY_PROPERTIES,
    })

  def __repr__(self) -> str:
    return (
      f"{type(self).__name__}(direct_fields={len(DIRECT_LEGACY_PATHS)}, "
      f"derived_fields={len(DERIVED_LEGACY_PROPERTIES)}, immutable=True)"
    )

  @property
  def canonical_config(self) -> BaseModel:
    return self._config

  def get_legacy_value(self, name: str) -> object:
    return getattr(self, name)

  def legacy_field_names(self) -> tuple[str, ...]:
    return tuple(sorted((*DIRECT_LEGACY_PATHS, *DERIVED_LEGACY_PROPERTIES)))
