"""Canonical environment-option resolution with explicit alias conflicts.

Pydantic ``AliasChoices`` stops at the first present name, which makes a
shadowed, contradictory legacy variable invisible.  This module inspects the
raw environment before ``Settings`` is constructed and records the complete
resolution for config-health output.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from dotenv import dotenv_values


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


# Complete Settings AliasChoices audit. Secret/channel aliases are checked for
# conflicts too, but config-health only publishes AUTO_TRADE_* options.
ENVIRONMENT_OPTION_CONTRACTS = (
  CanonicalEnvironmentOption(
    "SIGNAL_VIP_CHANNEL_ID",
    ("TELEGRAM_CHANNEL_ID", "TELEGRAM_CHAT_ID"),
    parse_int,
  ),
  CanonicalEnvironmentOption(
    "DATABASE_URL",
    ("POSTGRES_DSN",),
    parse_string,
  ),
  CanonicalEnvironmentOption(
    "SIGNAL_PUBLIC_CHANNEL_ID",
    ("XAU_PUBLIC_CHANNEL_ID",),
    parse_int,
  ),
  CanonicalEnvironmentOption(
    "SIGNAL_PUBLIC_SHOW_PIPS",
    ("PUBLIC_SHOW_PIPS",),
    parse_bool,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_XAU_PIP_SIZE",
    ("AUTO_TRADE_PIP_SIZE",),
    parse_float,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_XAU_CONTRACT_SIZE",
    ("AUTO_TRADE_CONTRACT_SIZE",),
    parse_float,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_SPOT_MAX_AGE_SECONDS",
    ("AUTO_TRADE_SPOT_MAX_AGE",),
    parse_int,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_CANDIDATE_STREAM",
    ("AUTO_TRADE_STREAM",),
    parse_string,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
    ("AUTO_TRADE_CANDIDATE_TTL",),
    parse_int,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
    ("AUTO_TRADE_CANDIDATE_MAX_AGE",),
    parse_int,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_TARGET_PLANS_PIPS",
    ("AUTO_TRADE_TP_PIPS",),
    parse_string,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
    (
      "AUTO_TRADE_STRATEGY_BRIDGE_ENABLED",
      "AUTO_TRADE_FORMING_GATE_ENABLED",
    ),
    parse_bool,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS",
    ("AUTO_TRADE_FORMING_MAX_AGE_SECONDS",),
    parse_int,
  ),
  CanonicalEnvironmentOption(
    "AUTO_TRADE_MAPPED_ZONE_ENABLED",
    ("AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED",),
    parse_bool,
  ),
)


def raw_environment(
  env_file: str | Path | None = ".env",
) -> dict[str, str]:
  merged: dict[str, str] = {}
  if env_file:
    path = Path(env_file)
    if path.exists():
      merged.update({
        str(key): str(value)
        for key, value in dotenv_values(path).items()
        if value is not None
      })
  merged.update(os.environ)
  return merged


def resolve_environment_options(
  environment: Mapping[str, str] | None = None,
) -> tuple[CanonicalEnvironmentOption, ...]:
  raw = raw_environment() if environment is None else dict(environment)
  return tuple(contract.resolve(raw) for contract in ENVIRONMENT_OPTION_CONTRACTS)


RESOLVED_ENVIRONMENT_OPTIONS = resolve_environment_options()


def canonical_option_health() -> list[dict[str, Any]]:
  return [
    option.health_dict()
    for option in RESOLVED_ENVIRONMENT_OPTIONS
    if option.canonical_name.startswith("AUTO_TRADE_")
  ]


def deprecated_option_warnings() -> list[str]:
  return sorted({
    warning
    for option in RESOLVED_ENVIRONMENT_OPTIONS
    for warning in option.warnings
  })

