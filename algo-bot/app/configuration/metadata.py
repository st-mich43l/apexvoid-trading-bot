"""Canonical metadata primitives for the inactive grouped config model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import AliasChoices, Field
from pydantic_core import PydanticUndefined


class ConfigOwner(StrEnum):
  PYTHON = "python"
  CTRADER = "ctrader"
  SHARED = "shared"


class ReloadPolicy(StrEnum):
  RESTART = "restart"
  NEXT_SCANNER_CYCLE = "next_scanner_cycle"
  NEXT_WORKER_CYCLE = "next_worker_cycle"
  NEW_SETUP_ONLY = "new_setup_only"
  IMMEDIATE = "immediate"
  CODE_RELEASE = "code_release"


class ConfigUnit(StrEnum):
  PRICE = "price"
  PIPS = "pips"
  ATR = "atr"
  BARS = "bars"
  SECONDS = "seconds"
  MINUTES = "minutes"
  HOURS = "hours"
  DAYS = "days"
  MILLISECONDS = "milliseconds"
  TICKS = "ticks"
  FRACTION = "fraction"
  PERCENT = "percent"
  COUNT = "count"
  LOTS = "lots"
  STRING = "string"
  BOOLEAN = "boolean"
  PORT = "port"
  MULTIPLIER = "multiplier"
  SCORE = "score"
  UTC_HOUR = "utc_hour"
  DAY_OF_WEEK = "day_of_week"
  MONEY_PER_PIP_PER_LOT = "money_per_pip_per_lot"
  CONTRACT_UNITS_PER_LOT = "contract_units_per_lot"
  VERSION = "version"
  ENUM = "enum"
  IDENTIFIER = "identifier"
  PATH = "path"
  URL = "url"


class MismatchPolicy(StrEnum):
  FATAL = "fatal"
  WARNING = "warning"
  NOT_REPORTED = "not_reported"


class RiskClassification(StrEnum):
  INFRASTRUCTURE = "infrastructure"
  CROSS_SERVICE_CONTRACT = "cross_service_contract"
  BROKER_ACCOUNT_SAFETY = "broker_account_safety"
  EXECUTION_SAFETY = "execution_safety"
  STRATEGY_BEHAVIOR = "strategy_behavior"
  ANALYSIS_BEHAVIOR = "analysis_behavior"
  LIFECYCLE = "lifecycle"
  DELIVERY = "delivery"
  OBSERVABILITY = "observability"
  DEPRECATED_LEGACY = "deprecated_legacy"


class ConfigKind(StrEnum):
  CONFIGURABLE = "configurable"
  PROTOCOL_CONSTANT = "protocol_constant"
  ALGORITHM_CONSTANT = "algorithm_constant"


@dataclass(frozen=True)
class ConfigMetadata:
  legacy_attr: str | None
  canonical_env: str | None
  deprecated_aliases: tuple[str, ...]
  owner: ConfigOwner
  reload_policy: ReloadPolicy
  unit: ConfigUnit
  risk_classification: RiskClassification
  kind: ConfigKind
  configurable: bool
  protocol_constant: bool
  algorithm_constant: bool
  secret: bool
  shared_with_ctrader: bool
  mismatch_policy: MismatchPolicy
  description: str
  catalog_version: int = 1
  introduced_in: str = "config-catalog-v1"
  deprecated: bool = False
  replacement_path: str | None = None
  terminal_deprecation_reason: str | None = None

  def __post_init__(self) -> None:
    expected = {
      ConfigKind.CONFIGURABLE: (True, False, False),
      ConfigKind.PROTOCOL_CONSTANT: (False, True, False),
      ConfigKind.ALGORITHM_CONSTANT: (False, False, True),
    }[self.kind]
    actual = (
      self.configurable,
      self.protocol_constant,
      self.algorithm_constant,
    )
    if actual != expected:
      raise ValueError(f"kind flags {actual!r} do not match {self.kind.value}")
    if self.kind is not ConfigKind.CONFIGURABLE and (
      self.canonical_env or self.deprecated_aliases
    ):
      raise ValueError("constants cannot have ENV bindings")
    if self.deprecated and not (
      self.replacement_path or self.terminal_deprecation_reason
    ):
      raise ValueError(
        "deprecated metadata requires replacement_path or terminal reason"
      )

  def as_dict(self) -> dict[str, Any]:
    """Return stable JSON-compatible metadata in declaration order."""
    values = asdict(self)
    for key, value in tuple(values.items()):
      if isinstance(value, StrEnum):
        values[key] = value.value
      elif isinstance(value, tuple):
        values[key] = list(value)
    return values


T = TypeVar("T")


def config_field(
  default: T | Any = PydanticUndefined,
  *,
  legacy_attr: str | None,
  env: str | None,
  aliases: tuple[str, ...] = (),
  owner: ConfigOwner,
  reload: ReloadPolicy,
  unit: ConfigUnit,
  risk: RiskClassification,
  kind: ConfigKind = ConfigKind.CONFIGURABLE,
  secret: bool = False,
  shared_with_ctrader: bool = False,
  mismatch_policy: MismatchPolicy = MismatchPolicy.NOT_REPORTED,
  description: str,
  catalog_version: int = 1,
  introduced_in: str = "config-catalog-v1",
  deprecated: bool = False,
  replacement_path: str | None = None,
  terminal_deprecation_reason: str | None = None,
) -> Any:
  """Declare one field and its catalog metadata in the same location."""
  configurable = kind is ConfigKind.CONFIGURABLE
  metadata = ConfigMetadata(
    legacy_attr=legacy_attr,
    canonical_env=env,
    deprecated_aliases=aliases,
    owner=owner,
    reload_policy=reload,
    unit=unit,
    risk_classification=risk,
    kind=kind,
    configurable=configurable,
    protocol_constant=kind is ConfigKind.PROTOCOL_CONSTANT,
    algorithm_constant=kind is ConfigKind.ALGORITHM_CONSTANT,
    secret=secret,
    shared_with_ctrader=shared_with_ctrader,
    mismatch_policy=mismatch_policy,
    description=description,
    catalog_version=catalog_version,
    introduced_in=introduced_in,
    deprecated=deprecated,
    replacement_path=replacement_path,
    terminal_deprecation_reason=terminal_deprecation_reason,
  )
  validation_alias = (
    AliasChoices(env, *aliases) if env is not None else None
  )
  return Field(
    default,
    validation_alias=validation_alias,
    description=description,
    json_schema_extra={"apexvoid_config": metadata.as_dict()},
  )
