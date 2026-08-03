"""Catalog-driven environment-option resolution with explicit alias conflicts.

Phase 2H moves the historical ``app.core.environment_options`` behavior into the
configuration package and derives it from the canonical catalog instead of a
hand-maintained alias registry. Pydantic ``AliasChoices`` stops at the first
present name, which makes a shadowed, contradictory legacy variable invisible;
this module inspects the raw environment before ``Settings``/the canonical
loader run and records the complete resolution for config-health output.

The set of options that participate in config-health alias auditing is a
curated selection of canonical ENV names (``ENVIRONMENT_OPTION_ENV_NAMES``).
Their deprecated aliases and value parsers are read from the catalog via
``app.configuration.environment_contract`` so there is exactly one source of
truth for the alias registry.

This module never reads ``os.environ``/``os.getenv`` directly. When no explicit
environment mapping is supplied it collects the process/dotenv layers through
``app.configuration.python_sources.load_python_runtime_source_bundle`` — the
single reviewed canonical-collection boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from app.configuration.environment_contract import environment_entry_for_name
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_types import ConfigurationSourceBundle


OptionParser = Callable[[str], Any]


def parse_bool(value: str) -> bool:
  normalized = value.strip().lower()
  if normalized in {"1", "true", "yes", "on"}:
    return True
  if normalized in {"0", "false", "no", "off"}:
    return False
  raise ValueError(f"invalid boolean {value!r}")


def parse_string(value: str) -> str:
  return value.strip()


def parse_int(value: str) -> int:
  return int(value.strip())


def parse_float(value: str) -> float:
  return float(value.strip())


def _parser_for_type(type_name: str) -> OptionParser:
  normalized = type_name.strip()
  if normalized == "bool":
    return parse_bool
  if "float" in normalized or normalized in {"decimal", "Decimal"}:
    return parse_float
  if "int" in normalized:
    return parse_int
  return parse_string


@dataclass(frozen=True)
class CanonicalEnvironmentOption:
  canonical_name: str
  deprecated_aliases: tuple[str, ...]
  parser: OptionParser
  resolved_value: Any = None
  source_name: str = "application_default"
  conflict: bool = False
  warnings: tuple[str, ...] = ()
  aliases_present: tuple[str, ...] = ()

  def resolve(
    self,
    environment: Mapping[str, str],
  ) -> "CanonicalEnvironmentOption":
    present = [
      name
      for name in (self.canonical_name, *self.deprecated_aliases)
      if name in environment
    ]
    if not present:
      return self

    parsed: dict[str, Any] = {}
    warnings: list[str] = []
    for name in present:
      try:
        parsed[name] = self.parser(str(environment[name]))
      except (TypeError, ValueError) as exc:
        raise ValueError(
          f"{name} has invalid value: {exc}"
        ) from exc

    aliases = tuple(
      name for name in self.deprecated_aliases if name in parsed
    )
    warnings.extend(f"deprecated_variable:{name}" for name in aliases)
    values = list(parsed.values())
    conflict = any(value != values[0] for value in values[1:])
    if conflict:
      details = ", ".join(
        f"{name}={str(value).lower() if isinstance(value, bool) else value}"
        for name, value in parsed.items()
      )
      raise ValueError(
        f"conflicting environment aliases for {self.canonical_name}: {details}"
      )

    source = (
      self.canonical_name
      if self.canonical_name in parsed
      else present[0]
    )
    return CanonicalEnvironmentOption(
      canonical_name=self.canonical_name,
      deprecated_aliases=self.deprecated_aliases,
      parser=self.parser,
      resolved_value=parsed[source],
      source_name=source,
      conflict=False,
      warnings=tuple(warnings),
      aliases_present=aliases,
    )

  def health_dict(self) -> dict[str, Any]:
    return {
      "name": self.canonical_name,
      "normalized_value": self.resolved_value,
      "source": self.source_name,
      "deprecated_aliases_present": list(self.aliases_present),
      "conflict": self.conflict,
    }


# Curated selection of canonical ENV names whose alias conflicts config-health
# audits before construction. This records *which* options participate; the
# aliases and value parsers are derived from the catalog below, so there is no
# second alias registry to keep in sync.
ENVIRONMENT_OPTION_ENV_NAMES: tuple[str, ...] = (
  "SIGNAL_VIP_CHANNEL_ID",
  "DATABASE_URL",
  "SIGNAL_PUBLIC_CHANNEL_ID",
  "SIGNAL_PUBLIC_SHOW_PIPS",
  "AUTO_TRADE_XAU_PIP_SIZE",
  "AUTO_TRADE_XAU_CONTRACT_SIZE",
  "AUTO_TRADE_SPOT_MAX_AGE_SECONDS",
  "AUTO_TRADE_CANDIDATE_STREAM",
  "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
  "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
  "AUTO_TRADE_TARGET_PLANS_PIPS",
  "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
  "AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS",
  "AUTO_TRADE_MAPPED_ZONE_ENABLED",
  "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED",
)


def _contract_for(name: str) -> CanonicalEnvironmentOption:
  entry = environment_entry_for_name(name)
  if entry is None:
    raise KeyError(f"catalog has no environment entry for {name!r}")
  return CanonicalEnvironmentOption(
    canonical_name=entry.canonical_env or name,
    deprecated_aliases=tuple(entry.deprecated_aliases),
    parser=_parser_for_type(entry.type),
  )


def environment_option_contracts() -> tuple[CanonicalEnvironmentOption, ...]:
  """Return the catalog-derived option contracts (canonical + aliases + parser)."""
  return tuple(_contract_for(name) for name in ENVIRONMENT_OPTION_ENV_NAMES)


ENVIRONMENT_OPTION_CONTRACTS = environment_option_contracts()


def _environment_from_bundle(
  bundle: ConfigurationSourceBundle,
) -> dict[str, str]:
  merged: dict[str, str] = {}
  for key, value in bundle.dotenv_values.items():
    if value is not None:
      merged[str(key)] = str(value)
  for key, value in bundle.process_environment.items():
    merged[str(key)] = str(value)
  return merged


def runtime_environment_mapping() -> dict[str, str]:
  """Return the merged dotenv<process environment mapping (canonical layers)."""
  return _environment_from_bundle(load_python_runtime_source_bundle())


def resolve_environment_options(
  environment: Mapping[str, str] | None = None,
) -> tuple[CanonicalEnvironmentOption, ...]:
  raw = runtime_environment_mapping() if environment is None else dict(environment)
  return tuple(
    contract.resolve(raw) for contract in ENVIRONMENT_OPTION_CONTRACTS
  )


def assert_no_environment_alias_conflicts(
  environment: Mapping[str, str] | None = None,
) -> None:
  """Raise ``ValueError`` if any catalog-audited alias group disagrees.

  Invoked from the composition root so ``import app.core.config`` fails fast on
  conflicting aliases, exactly as when ``config.py`` imported the legacy
  ``environment_options`` module at import time.
  """
  resolve_environment_options(environment)


def canonical_option_health(
  environment: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
  return [
    option.health_dict()
    for option in resolve_environment_options(environment)
    if option.canonical_name.startswith("AUTO_TRADE_")
  ]


def deprecated_option_warnings(
  environment: Mapping[str, str] | None = None,
) -> list[str]:
  return sorted({
    warning
    for option in resolve_environment_options(environment)
    for warning in option.warnings
  })
