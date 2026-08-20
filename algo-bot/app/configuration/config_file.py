"""YAML configuration file source loader (CONFIG_FILE layer)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.configuration.catalog import iter_catalog_entries
from app.configuration.instrument_packs import expand_instrument_declarations
from app.configuration.models.instruments import (
  InstrumentsConfig,
  EMPTY_INSTRUMENTS,
  project_xau_leaf_values,
)
from app.configuration.models.root import ApexVoidConfig


CONFIG_FILE_ENV = "APEXVOID_CONFIG_FILE"

_CATALOG_ROOT_GROUPS = frozenset(
  name for name in ApexVoidConfig.model_fields if name != "instruments"
)
_TOP_LEVEL_KEYS = frozenset({
  "version",
  "instruments",
  "instrument_packs",
  *_CATALOG_ROOT_GROUPS,
})


class ConfigFileError(ValueError):
  """Fail-closed YAML configuration error with a path-based message."""

  def __init__(self, message: str, *, path: str | None = None) -> None:
    prefix = f"config_file path={path} " if path else "config_file "
    super().__init__(prefix + message)
    self.path = path


@dataclass(frozen=True, slots=True)
class LoadedConfigFile:
  """Parsed CONFIG_FILE contribution for the resolver."""

  path: str | None
  flat_values: Mapping[str, object]
  instruments: InstrumentsConfig
  version: int | None = None


def empty_config_file() -> LoadedConfigFile:
  return LoadedConfigFile(
    path=None,
    flat_values={},
    instruments=EMPTY_INSTRUMENTS,
    version=None,
  )


def _leaf_path_set(model: type = ApexVoidConfig) -> dict[str, object]:
  return {entry.path: entry for entry in iter_catalog_entries(model)}


def _secret_paths(model: type = ApexVoidConfig) -> frozenset[str]:
  return frozenset(
    entry.path for entry in iter_catalog_entries(model) if entry.secret
  )


def _flatten_catalog_values(
  nested: Mapping[str, Any],
  *,
  known_paths: Mapping[str, object],
  secret_paths: frozenset[str],
  file_path: str,
) -> dict[str, object]:
  flat: dict[str, object] = {}

  def walk(node: Any, prefix: tuple[str, ...]) -> None:
    path = ".".join(prefix)
    if path in known_paths:
      if path in secret_paths:
        raise ConfigFileError(
          f"secret leaf {path} is not allowed in CONFIG_FILE; "
          "place secrets in trading-bot.env / Vault only",
          path=file_path,
        )
      flat[path] = node
      return
    if not isinstance(node, dict):
      raise ConfigFileError(
        f"unknown configuration path {path}",
        path=file_path,
      )
    if not node and path and path not in known_paths:
      # empty intermediate group is meaningless; treat as unknown
      raise ConfigFileError(
        f"unknown configuration path {path}",
        path=file_path,
      )
    for key, value in node.items():
      if not isinstance(key, str) or not key:
        raise ConfigFileError(
          f"invalid key under {path or '<root>'}",
          path=file_path,
        )
      walk(value, (*prefix, key))

  for key, value in nested.items():
    walk(value, (key,))
  return flat


def _parse_instruments(
  raw: object,
  *,
  file_path: str,
  packs: dict[str, Any] | None = None,
) -> InstrumentsConfig:
  if raw is None:
    return EMPTY_INSTRUMENTS
  if not isinstance(raw, dict):
    raise ConfigFileError(
      "instruments must be a mapping of symbol id to instrument config",
      path=file_path,
    )
  try:
    expanded = expand_instrument_declarations(raw, packs)
    return InstrumentsConfig.model_validate(expanded)
  except ValueError as exc:
    raise ConfigFileError(str(exc), path=file_path) from None
  except ValidationError as exc:
    loc = ".".join(str(part) for part in exc.errors()[0]["loc"]) if exc.errors() else "instruments"
    raise ConfigFileError(
      f"instruments validation failed at {loc}: {exc.errors()[0]['msg']}",
      path=file_path,
    ) from None


def _instrument_projections(instruments: InstrumentsConfig) -> dict[str, object]:
  """Expand instruments.XAU into flat leaf candidates for the CONFIG_FILE layer."""
  xau = instruments.get("XAU")
  if xau is None:
    return {}
  return project_xau_leaf_values(xau)


def load_config_file(
  path: str | Path | None,
  *,
  missing_ok: bool = True,
) -> LoadedConfigFile:
  """Load a YAML config file; absence yields an empty layer when missing_ok."""
  if path is None or str(path).strip() == "":
    return empty_config_file()

  file_path = str(Path(path).expanduser())
  target = Path(file_path)
  if not target.is_file():
    if missing_ok:
      return empty_config_file()
    raise ConfigFileError("file does not exist", path=file_path)

  try:
    text = target.read_text(encoding="utf-8")
  except OSError as exc:
    raise ConfigFileError(f"unreadable: {exc}", path=file_path) from None

  try:
    loaded = yaml.safe_load(text)
  except yaml.YAMLError as exc:
    raise ConfigFileError(f"malformed YAML: {exc}", path=file_path) from None

  if loaded is None:
    return LoadedConfigFile(
      path=file_path,
      flat_values={},
      instruments=EMPTY_INSTRUMENTS,
      version=None,
    )
  if not isinstance(loaded, dict):
    raise ConfigFileError(
      "top-level YAML value must be a mapping",
      path=file_path,
    )

  unknown_top = sorted(set(loaded) - _TOP_LEVEL_KEYS)
  if unknown_top:
    raise ConfigFileError(
      "unknown top-level keys: " + ", ".join(unknown_top),
      path=file_path,
    )

  version = loaded.get("version")
  if version is not None and not isinstance(version, int):
    raise ConfigFileError("version must be an integer", path=file_path)

  packs_raw = loaded.get("instrument_packs")
  if packs_raw is not None and not isinstance(packs_raw, dict):
    raise ConfigFileError(
      "instrument_packs must be a mapping of pack name to defaults",
      path=file_path,
    )

  instruments = _parse_instruments(
    loaded.get("instruments"),
    file_path=file_path,
    packs=packs_raw if isinstance(packs_raw, dict) else None,
  )

  global_nested = {
    key: value
    for key, value in loaded.items()
    if key in _CATALOG_ROOT_GROUPS
  }
  # instruments is not a catalog leaf group on the schema root for ENV
  # catalog; reject if someone nests under a misspelled group already handled.

  known = _leaf_path_set()
  secrets = _secret_paths()
  flat = _flatten_catalog_values(
    global_nested,
    known_paths=known,
    secret_paths=secrets,
    file_path=file_path,
  )
  projected = _instrument_projections(instruments)
  for leaf, value in projected.items():
    if leaf in flat and flat[leaf] != value:
      raise ConfigFileError(
        f"conflicting values for {leaf} between catalog group and "
        "instruments.XAU projection",
        path=file_path,
      )
    flat[leaf] = value

  return LoadedConfigFile(
    path=file_path,
    flat_values=flat,
    instruments=instruments,
    version=version,
  )


def resolve_config_file_path(
  *,
  process_environment: Mapping[str, str],
  cli_path: str | None = None,
) -> str | None:
  """CLI --config-file wins over APEXVOID_CONFIG_FILE."""
  if cli_path is not None and str(cli_path).strip():
    return str(cli_path).strip()
  env_path = process_environment.get(CONFIG_FILE_ENV)
  if env_path is not None and str(env_path).strip():
    return str(env_path).strip()
  return None
