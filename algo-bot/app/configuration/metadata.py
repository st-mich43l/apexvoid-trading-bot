"""Canonical metadata primitives for the Catalog V2 grouped config model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
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
  RATIO = "ratio"
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
  DEPRECATED_CONFIGURATION = "deprecated_configuration"


class ConfigKind(StrEnum):
  CONFIGURABLE = "configurable"
  PROTOCOL_CONSTANT = "protocol_constant"
  ALGORITHM_CONSTANT = "algorithm_constant"


class DefaultContext(StrEnum):
  PYTHON_SCHEMA = "python_schema"
  CTRADER_FROM_ENVIRONMENT = "ctrader_from_environment"
  CTRADER_CONSTRUCTOR = "ctrader_constructor"


@dataclass(frozen=True)
class ContextDefault:
  context: DefaultContext
  value: Any


@dataclass(frozen=True)
class ConfigMetadata:
  canonical_env: str | None
  deprecated_env_aliases: tuple[str, ...]
  owner: ConfigOwner
  reload_policy: ReloadPolicy
  runtime_reload_policy: ReloadPolicy
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
  default_contexts: tuple[ContextDefault, ...] = ()
  allowed_values: tuple[Any, ...] = ()
  validation_summary: str | None = None
  evidence_notes: tuple[str, ...] = ()
  catalog_version: int = 2
  introduced_in: str = "config-catalog-v2"
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
      self.canonical_env or self.deprecated_env_aliases
    ):
      raise ValueError("constants cannot have ENV bindings")
    if self.deprecated and not (
      self.replacement_path or self.terminal_deprecation_reason
    ):
      raise ValueError(
        "deprecated metadata requires replacement_path or terminal reason"
      )
    contexts = tuple(item.context for item in self.default_contexts)
    if len(contexts) != len(set(contexts)):
      raise ValueError("default contexts must be unique")
    if self.secret and any(
      item.value != "<redacted>" for item in self.default_contexts
    ):
      raise ValueError("secret context defaults must be redacted")

  def as_dict(self) -> dict[str, Any]:
    """Return stable JSON-compatible metadata in declaration order."""
    values = asdict(self)
    for key, value in tuple(values.items()):
      if isinstance(value, StrEnum):
        values[key] = value.value
      elif isinstance(value, tuple):
        values[key] = list(value)
    values["default_contexts"] = [
      {
        "context": item.context.value,
        "value": "<redacted>" if self.secret else _json_value(item.value),
      }
      for item in self.default_contexts
    ]
    values["allowed_values"] = [
      _json_value(value) for value in self.allowed_values
    ]
    # Alias key retained in emitted metadata for environment-contract consumers
    # that read deprecated_aliases historically; CatalogEntry uses both.
    values["deprecated_aliases"] = values.pop("deprecated_env_aliases")
    return values


T = TypeVar("T")


def _json_value(value: Any) -> Any:
  if isinstance(value, StrEnum):
    return value.value
  if isinstance(value, Decimal):
    return str(value)
  if isinstance(value, tuple):
    return [_json_value(item) for item in value]
  if isinstance(value, list):
    return [_json_value(item) for item in value]
  if isinstance(value, dict):
    return {str(key): _json_value(item) for key, item in value.items()}
  return value


def display_config_id(path: str) -> str:
  """Deterministic display identity derived from the canonical path."""
  return f"config:{path}"


def config_field(
  default: T | Any = PydanticUndefined,
  *,
  canonical_env: str | None,
  deprecated_env_aliases: tuple[str, ...] = (),
  owner: ConfigOwner,
  reload: ReloadPolicy,
  runtime_reload: ReloadPolicy | None = None,
  unit: ConfigUnit,
  risk: RiskClassification,
  kind: ConfigKind = ConfigKind.CONFIGURABLE,
  secret: bool = False,
  shared_with_ctrader: bool = False,
  mismatch_policy: MismatchPolicy = MismatchPolicy.NOT_REPORTED,
  description: str,
  default_contexts: tuple[ContextDefault, ...] = (),
  allowed_values: tuple[Any, ...] = (),
  validation_summary: str | None = None,
  evidence_notes: tuple[str, ...] = (),
  catalog_version: int = 2,
  introduced_in: str = "config-catalog-v2",
  deprecated: bool = False,
  replacement_path: str | None = None,
  terminal_deprecation_reason: str | None = None,
  ge: int | float | Decimal | None = None,
  gt: int | float | Decimal | None = None,
  le: int | float | Decimal | None = None,
  lt: int | float | Decimal | None = None,
  min_length: int | None = None,
  max_length: int | None = None,
  pattern: str | None = None,
) -> Any:
  """Declare one field and its catalog metadata in the same location."""
  configurable = kind is ConfigKind.CONFIGURABLE
  metadata = ConfigMetadata(
    canonical_env=canonical_env,
    deprecated_env_aliases=deprecated_env_aliases,
    owner=owner,
    reload_policy=reload,
    runtime_reload_policy=(
      runtime_reload
      if runtime_reload is not None
      else (
        ReloadPolicy.RESTART
        if kind is ConfigKind.CONFIGURABLE
        else ReloadPolicy.CODE_RELEASE
      )
    ),
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
    default_contexts=default_contexts,
    allowed_values=allowed_values,
    validation_summary=validation_summary,
    evidence_notes=evidence_notes,
    catalog_version=catalog_version,
    introduced_in=introduced_in,
    deprecated=deprecated,
    replacement_path=replacement_path,
    terminal_deprecation_reason=terminal_deprecation_reason,
  )
  validation_alias = (
    AliasChoices(canonical_env, *deprecated_env_aliases)
    if canonical_env is not None
    else None
  )
  return Field(
    default,
    validation_alias=validation_alias,
    description=description,
    ge=ge,
    gt=gt,
    le=le,
    lt=lt,
    min_length=min_length,
    max_length=max_length,
    pattern=pattern,
    json_schema_extra={"apexvoid_config": metadata.as_dict()},
  )
