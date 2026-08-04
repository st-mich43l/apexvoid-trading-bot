"""Deployment contract for Ansible / Compose config-file cutover (PR C consumer)."""

from __future__ import annotations

from typing import Any

from app.configuration.catalog import CatalogEntry
from app.configuration.catalog import iter_catalog_entries
from app.configuration.config_file import CONFIG_FILE_ENV
from app.configuration.models.instruments import DEPRECATED_XAU_ENV_ALIASES
from app.configuration.models.instruments import InstrumentConfig
from app.configuration.models.instruments import SUPPORTED_INSTRUMENT_TIMEFRAMES
from app.configuration.models.root import ApexVoidConfig
from app.configuration.source_types import SOURCE_PRECEDENCE


# Bootstrap ENV keys that remain outside trading-bot.yml.
_BOOTSTRAP_ENV = (
  CONFIG_FILE_ENV,
  "AUTO_TRADE_PROFILE",
  "REDIS_URL",
  "LOG_LEVEL",
  "LOG_DIR",
  "LOG_FILE_ENABLED",
  "LOG_RETENTION_DAYS",
  "SERVICE_VERSION",
  "GIT_SHA",
  "HOSTNAME",
)


def _instrument_schema() -> dict[str, Any]:
  fields = InstrumentConfig.model_fields
  required = sorted(
    name for name, field in fields.items()
    if field.is_required() and name != "enabled"
  )
  optional = sorted(name for name in fields if name not in required)
  return {
    "required": required,
    "optional": optional,
    "supported_timeframes": sorted(SUPPORTED_INSTRUMENT_TIMEFRAMES),
    "active_projection_symbol": "XAU",
    "projected_leaf_groups": [
      "contract.instrument",
      "market_data.lookbacks",
      "analysis.zones.symbol_contract",
    ],
  }


def _config_file_schema(entries: tuple[CatalogEntry, ...]) -> dict[str, Any]:
  allowed_roots = sorted(
    name for name in ApexVoidConfig.model_fields if name != "instruments"
  )
  allowed_paths = sorted(
    entry.path
    for entry in entries
    if entry.configurable and not entry.secret
  )
  return {
    "top_level_keys": ["version", "instruments", *allowed_roots],
    "allowed_leaf_paths": allowed_paths,
    "secrets_forbidden": True,
  }


def deployment_contract_document(
  entries: tuple[CatalogEntry, ...] | None = None,
  *,
  contract_fingerprint: str,
) -> dict[str, Any]:
  """Deterministic, secret-safe deployment contract (unused by runtime)."""
  catalog = entries if entries is not None else iter_catalog_entries()
  secrets = sorted({
    entry.canonical_env
    for entry in catalog
    if entry.secret and entry.canonical_env
  })
  return {
    "contract_version": 1,
    "fingerprint": contract_fingerprint,
    "source_precedence": [kind.value for kind in SOURCE_PRECEDENCE],
    "bootstrap_environment": list(_BOOTSTRAP_ENV),
    "secret_environment": secrets,
    "config_file_schema": _config_file_schema(catalog),
    "deprecated_environment_aliases": dict(
      sorted(DEPRECATED_XAU_ENV_ALIASES.items())
    ),
    "instrument_schema": _instrument_schema(),
  }
