"""Native YAML configuration composition for the Python service."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.core.config_schema import ConfigNode, native_config


class ConfigurationError(RuntimeError):
  """Raised when the configured YAML document cannot be loaded safely."""


def _default_config_path() -> Path:
  # /config is the container mount.  The repository fallback keeps local
  # tests and one-shot tools usable without an environment-specific setting.
  mounted = Path("/config/apexvoid.yml")
  if mounted.is_file():
    return mounted
  return Path(__file__).resolve().parents[3] / "config" / "apexvoid.yml"


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
  for key, value in overlay.items():
    if isinstance(value, Mapping) and isinstance(base.get(key), dict):
      _deep_merge(base[key], value)
    else:
      base[key] = deepcopy(value)
  return base


def _read_yaml(path: Path) -> dict[str, Any]:
  try:
    with path.open("r", encoding="utf-8") as handle:
      value = yaml.safe_load(handle) or {}
  except OSError as exc:
    raise ConfigurationError(f"cannot read configuration file {path}") from exc
  if not isinstance(value, dict):
    raise ConfigurationError(f"configuration root must be a mapping: {path}")
  return value


def _load_document(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
  seen = set() if seen is None else seen
  path = path.resolve()
  if path in seen:
    raise ConfigurationError(f"configuration include cycle at {path}")
  seen.add(path)
  document = _read_yaml(path)
  result: dict[str, Any] = {}
  for include in document.pop("includes", []) or []:
    include_path = (path.parent / str(include)).resolve()
    _deep_merge(result, _load_document(include_path, seen))
  _deep_merge(result, document)
  seen.remove(path)
  return result


def _set_path(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
  cursor = document
  for component in path[:-1]:
    child = cursor.setdefault(component, {})
    if not isinstance(child, dict):
      raise ConfigurationError("configuration secret path collides with a scalar")
    cursor = child
  cursor[path[-1]] = value


def _apply_secrets(document: dict[str, Any]) -> None:
  # Secrets are accepted only through their explicit ENV contract.  Tuning
  # values are intentionally never imported from the environment.
  if token := os.getenv("TELEGRAM_BOT_TOKEN"):
    _set_path(document, ("telegram", "bot_token"), token)
  if token := os.getenv("SCANNER_TELEGRAM_BOT_TOKEN"):
    _set_path(document, ("telegram", "scanner_telegram_bot_token"), token)
  telegram = document.setdefault("telegram", {})
  if isinstance(telegram, dict):
    telegram.setdefault("bot_token", os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram.setdefault("scanner_telegram_bot_token", os.getenv("SCANNER_TELEGRAM_BOT_TOKEN"))
    telegram.setdefault("telegram_owner_id", _optional_int(os.getenv("TELEGRAM_OWNER_ID")))
    presentation = telegram.setdefault("presentation", {})
    if isinstance(presentation, dict):
      presentation.setdefault("seq_reset_tz", document.get("runtime", {}).get("timezone", "UTC"))
      presentation.setdefault("anthropic_api_key", os.getenv("ANTHROPIC_API_KEY"))
  analysis = document.setdefault("analysis", {})
  if isinstance(analysis, dict):
    tiingo = analysis.setdefault("tiingo", {})
    if isinstance(tiingo, dict):
      tiingo.setdefault("api_key", os.getenv("TIINGO_API_KEY"))
  if password := os.getenv("POSTGRES_PASSWORD"):
    postgres = document.setdefault("runtime", {}).setdefault("postgres", {})
    if isinstance(postgres, dict):
      postgres["password"] = password
  postgres = document.setdefault("runtime", {}).setdefault("postgres", {})
  if isinstance(postgres, dict):
    if database_url := os.getenv("DATABASE_URL"):
      postgres["url"] = database_url
    elif "url" not in postgres:
      user = postgres.get("username", "apexvoid")
      password = postgres.get("password", "")
      host = postgres.get("host", "postgres")
      port = postgres.get("port", 5432)
      database = postgres.get("database", "signals")
      postgres["url"] = f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _optional_int(value: str | None) -> int | None:
  if value in (None, ""):
    return None
  try:
    return int(value)
  except ValueError as exc:
    raise ConfigurationError("TELEGRAM_OWNER_ID must be an integer") from exc


def load_runtime_config(path: str | os.PathLike[str] | None = None) -> ConfigNode:
  selected = Path(path or os.getenv("APEXVOID_CONFIG_FILE") or _default_config_path())
  document = _load_document(selected)
  _apply_secrets(document)
  return native_config(document)


runtime_config = load_runtime_config()

__all__ = ["ConfigurationError", "ConfigNode", "load_runtime_config", "runtime_config"]
