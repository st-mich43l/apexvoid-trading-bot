"""Immutable canonical-path view over an authoritative legacy Settings object."""

from __future__ import annotations

from app.configuration.generated.legacy_access import (
  CANONICAL_LEGACY_PATH_PREFIXES,
  CANONICAL_PATH_TO_LEGACY_ATTR,
)


class LegacyCanonicalNode:
  """Resolve one lazy canonical subtree without loading canonical settings."""

  __slots__ = ("_legacy_settings", "_path")

  def __init__(self, legacy_settings: object, path: tuple[str, ...]) -> None:
    object.__setattr__(self, "_legacy_settings", legacy_settings)
    object.__setattr__(self, "_path", path)

  def __getattr__(self, name: str) -> object:
    path = (*self._path, name)
    legacy_attribute = CANONICAL_PATH_TO_LEGACY_ATTR.get(path)
    if legacy_attribute is not None:
      return getattr(self._legacy_settings, legacy_attribute)
    if path in CANONICAL_LEGACY_PATH_PREFIXES:
      return LegacyCanonicalNode(self._legacy_settings, path)
    raise AttributeError(
      f"{type(self).__name__} has no authority-neutral canonical path "
      f"{'.'.join(path)!r}"
    )

  def __setattr__(self, name: str, value: object) -> None:
    del name, value
    raise TypeError("LegacyCanonicalConfigView is immutable")

  def __delattr__(self, name: str) -> None:
    del name
    raise TypeError("LegacyCanonicalConfigView is immutable")

  def __repr__(self) -> str:
    path = ".".join(self._path) or "<root>"
    return f"{type(self).__name__}(path={path!r}, immutable=True)"


class LegacyCanonicalConfigView(LegacyCanonicalNode):
  """Authority-neutral grouped reads backed by the existing legacy singleton."""

  __slots__ = ()

  def __init__(self, legacy_settings: object) -> None:
    super().__init__(legacy_settings, ())

  def __repr__(self) -> str:
    return (
      "LegacyCanonicalConfigView(legacy_backed_paths=316, immutable=True)"
    )
