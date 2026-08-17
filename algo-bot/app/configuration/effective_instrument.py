"""Effective per-instrument configuration composed from the resolved runtime root.

Composition (does not re-load sources)::

  schema + profile + global config + instrument policy + instrument overrides
  + compatibility projections
  = EffectiveInstrumentConfig
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.instruments import (
  CTRADER_VOLUME_HUNDREDTHS,
  InstrumentConfig,
  InstrumentLookbacksConfig,
  InstrumentRollout,
  InstrumentZoneWidthConfig,
  InstrumentsConfig,
  XAU_CURRENT_V1_POLICY,
  effective_rollout,
  resolve_policy_name,
)
from app.configuration.source_types import ResolutionTrace
from app.configuration.source_types import SourceKind


class EffectiveInstrumentError(ValueError):
  """Fail-closed effective instrument composition error."""


class InstrumentIdentityConfig(FrozenConfigModel):
  instrument_id: str
  canonical_symbol: str
  broker_symbol: str
  aliases: tuple[str, ...]
  rollout: InstrumentRollout
  timeframes: tuple[str, ...]


class InstrumentUnitsConfig(FrozenConfigModel):
  pip_size: float
  price_digits: int
  contract_units_per_lot: float
  pip_value_per_lot: float
  volume_units_per_lot: int
  max_lots: float

  def plan_max_volume(self) -> int:
    """cTrader volume-unit ceiling stamped on TradePlan.risk.max_volume."""
    volume = int(round(float(self.max_lots) * int(self.volume_units_per_lot)))
    if volume <= 0:
      raise ValueError("plan max_volume must be positive")
    return volume


class EffectiveInstrumentMarketDataConfig(FrozenConfigModel):
  """Instrument-scoped market-data view plus shared runtime market_data.

  ``runtime`` is typed as BaseModel because the Python projection may be a
  dynamically created sibling of the catalog MarketDataConfig shell.
  """

  model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

  lookbacks: InstrumentLookbacksConfig
  runtime: BaseModel


class EffectiveInstrumentAnalysisConfig(FrozenConfigModel):
  """Instrument zone geometry plus shared runtime analysis config."""

  model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

  zones: InstrumentZoneWidthConfig
  runtime: BaseModel


@dataclass(frozen=True, slots=True)
class EffectiveValueProvenance:
  path: str
  source_kind: str
  source_name: str
  secret: bool = False

  def as_dict(self) -> dict[str, object]:
    if self.secret:
      return {
        "path": self.path,
        "source_kind": self.source_kind,
        "source_name": self.source_name,
        "secret": True,
        "value": "<redacted>",
      }
    return {
      "path": self.path,
      "source_kind": self.source_kind,
      "source_name": self.source_name,
      "secret": False,
    }


class EffectiveInstrumentProvenance(FrozenConfigModel):
  entries: tuple[EffectiveValueProvenance, ...] = ()

  def by_path(self) -> Mapping[str, EffectiveValueProvenance]:
    return {item.path: item for item in self.entries}

  def as_secret_safe_dict(self) -> dict[str, object]:
    return {
      "entries": [item.as_dict() for item in self.entries],
    }


class EffectiveInstrumentConfig(FrozenConfigModel):
  """Complete configuration surface required to evaluate one instrument.

  Shared trading domains (strategies/actionability/execution/risk/lifecycle)
  retain references to the already-resolved runtime models for this PR.
  """

  model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

  identity: InstrumentIdentityConfig
  units: InstrumentUnitsConfig
  market_data: EffectiveInstrumentMarketDataConfig
  analysis: EffectiveInstrumentAnalysisConfig
  strategies: BaseModel
  actionability: BaseModel
  execution: BaseModel
  risk: BaseModel
  lifecycle: BaseModel
  policy_name: str
  provenance: EffectiveInstrumentProvenance

  @property
  def instrument_id(self) -> str:
    return self.identity.instrument_id

  @property
  def rollout(self) -> InstrumentRollout:
    return self.identity.rollout

  def is_live(self) -> bool:
    return self.identity.rollout is InstrumentRollout.LIVE

  def is_executable(self) -> bool:
    return self.identity.rollout in {
      InstrumentRollout.PAPER,
      InstrumentRollout.LIVE,
    }


def _catalog_by_path() -> dict[str, Any]:
  return {entry.path: entry for entry in iter_catalog_entries()}


def _normalize_symbol_key(value: str) -> str:
  return value.strip().upper()


def _instrument_lookup_map(
  instruments: InstrumentsConfig,
) -> dict[str, str]:
  """Map normalized symbols/aliases → instrument id."""
  mapping: dict[str, str] = {}
  for instrument_id, instrument in instruments.root.items():
    keys = {
      _normalize_symbol_key(instrument_id),
      _normalize_symbol_key(instrument.canonical_symbol),
      _normalize_symbol_key(instrument.broker_symbol),
      *(_normalize_symbol_key(alias) for alias in instrument.aliases),
    }
    # Preserve current XAUUSD feed mapping for XAU even when broker_symbol is XAU.
    if _normalize_symbol_key(instrument_id) == "XAU" or (
      _normalize_symbol_key(instrument.canonical_symbol) == "XAU"
    ):
      keys.add("XAUUSD")
    for key in keys:
      prior = mapping.get(key)
      if prior is not None and prior != instrument_id:
        raise EffectiveInstrumentError(
          f"ambiguous symbol {key!r} maps to both {prior!r} and {instrument_id!r}"
        )
      mapping[key] = instrument_id
  return mapping


def _require_units(
  instrument_id: str,
  instrument: InstrumentConfig,
) -> InstrumentUnitsConfig:
  contract = instrument.contract
  if contract is None:
    raise EffectiveInstrumentError(
      f"instrument {instrument_id!r} is missing contract units "
      "(pip_size/price_digits/contract_units_per_lot)"
    )
  if contract.pip_size <= 0:
    raise EffectiveInstrumentError(
      f"instrument {instrument_id!r} pip_size must be positive"
    )
  if contract.contract_units_per_lot <= 0:
    raise EffectiveInstrumentError(
      f"instrument {instrument_id!r} contract_units_per_lot must be positive"
    )
  derived = float(contract.pip_size) * float(contract.contract_units_per_lot)
  pip_value = (
    float(contract.pip_value_per_lot)
    if contract.pip_value_per_lot is not None
    else derived
  )
  if pip_value <= 0:
    raise EffectiveInstrumentError(
      f"instrument {instrument_id!r} pip_value_per_lot must be positive"
    )
  if contract.volume_units_per_lot is not None:
    volume_units = int(contract.volume_units_per_lot)
  else:
    volume_units = int(round(
      float(contract.contract_units_per_lot) * CTRADER_VOLUME_HUNDREDTHS
    ))
  if volume_units <= 0:
    raise EffectiveInstrumentError(
      f"instrument {instrument_id!r} volume_units_per_lot must be positive"
    )
  max_lots = float(contract.max_lots)
  if max_lots <= 0:
    raise EffectiveInstrumentError(
      f"instrument {instrument_id!r} max_lots must be positive"
    )
  return InstrumentUnitsConfig(
    pip_size=float(contract.pip_size),
    price_digits=int(contract.price_digits),
    contract_units_per_lot=float(contract.contract_units_per_lot),
    pip_value_per_lot=pip_value,
    volume_units_per_lot=volume_units,
    max_lots=max_lots,
  )


def _require_lookbacks(
  instrument_id: str,
  instrument: InstrumentConfig,
  runtime_market_data: Any,
) -> InstrumentLookbacksConfig:
  if instrument.market_data is not None:
    return instrument.market_data.lookbacks
  # XAU compatibility: fall back to projected global lookbacks.
  if _normalize_symbol_key(instrument_id) == "XAU":
    return InstrumentLookbacksConfig(
      h1_bars=runtime_market_data.lookbacks.h1_bars,
      m15_bars=runtime_market_data.lookbacks.m15_bars,
      m5_bars=runtime_market_data.lookbacks.m5_bars,
      m1_bars=runtime_market_data.lookbacks.m1_bars,
    )
  raise EffectiveInstrumentError(
    f"instrument {instrument_id!r} is missing required lookbacks"
  )


def _require_zones(
  instrument_id: str,
  instrument: InstrumentConfig,
  runtime_analysis: Any,
) -> InstrumentZoneWidthConfig:
  if instrument.analysis is not None:
    return instrument.analysis.zones
  if _normalize_symbol_key(instrument_id) == "XAU":
    zones = runtime_analysis.zones.symbol_contract
    return InstrumentZoneWidthConfig(
      minimum_width_price=zones.minimum_width_price,
      preferred_minimum_width_price=zones.preferred_minimum_width_price,
      preferred_maximum_width_price=zones.preferred_maximum_width_price,
      major_maximum_width_price=zones.major_maximum_width_price,
    )
  raise EffectiveInstrumentError(
    f"instrument {instrument_id!r} is missing analysis zone geometry"
  )


def _validate_overrides(instrument_id: str, overrides: Mapping[str, Any]) -> None:
  catalog = _catalog_by_path()
  for path in overrides:
    entry = catalog.get(path)
    if entry is None:
      raise EffectiveInstrumentError(
        f"instrument {instrument_id!r} override path {path!r} is unknown"
      )
    if entry.secret:
      raise EffectiveInstrumentError(
        f"instrument {instrument_id!r} cannot override secret path {path!r}"
      )
    if entry.protocol_constant or entry.kind == "protocol_constant":
      raise EffectiveInstrumentError(
        f"instrument {instrument_id!r} cannot override protocol constant {path!r}"
      )


def _apply_domain_overrides(
  runtime: Any,
  overrides: Mapping[str, Any],
) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
  """Return shared-domain models with sparse path overrides applied.

  Order: market_data, analysis, strategies, actionability, execution, risk, lifecycle.
  """
  domains = {
    "market_data": runtime.market_data,
    "analysis": runtime.analysis,
    "strategies": runtime.strategies,
    "actionability": runtime.actionability,
    "execution": runtime.execution,
    "risk": runtime.risk,
    "lifecycle": runtime.lifecycle,
  }
  nested: dict[str, dict[str, Any]] = {name: {} for name in domains}
  for dotted, value in overrides.items():
    parts = dotted.split(".")
    root = parts[0]
    if root not in domains:
      # Instrument-local paths (identity/units) are not applied to shared domains.
      continue
    cursor = nested[root]
    for part in parts[1:-1]:
      cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value

  def merge(model: Any, updates: Mapping[str, Any], *, path_prefix: str) -> Any:
    if not updates:
      return model
    payload: dict[str, Any] = {}
    for key, value in updates.items():
      dotted = f"{path_prefix}.{key}" if path_prefix else key
      if not hasattr(model, key):
        raise EffectiveInstrumentError(
          f"instrument override path {dotted!r} is not present on the "
          "Python runtime configuration projection"
        )
      child = getattr(model, key)
      if isinstance(value, Mapping) and hasattr(child, "model_copy"):
        payload[key] = merge(child, value, path_prefix=dotted)
      else:
        payload[key] = value
    return model.model_copy(update=payload)

  return (
    merge(domains["market_data"], nested["market_data"], path_prefix="market_data"),
    merge(domains["analysis"], nested["analysis"], path_prefix="analysis"),
    merge(domains["strategies"], nested["strategies"], path_prefix="strategies"),
    merge(
      domains["actionability"],
      nested["actionability"],
      path_prefix="actionability",
    ),
    merge(domains["execution"], nested["execution"], path_prefix="execution"),
    merge(domains["risk"], nested["risk"], path_prefix="risk"),
    merge(domains["lifecycle"], nested["lifecycle"], path_prefix="lifecycle"),
  )


def _trace_or_policy(
  *,
  path: str,
  source_kind: SourceKind,
  source_name: str,
  resolution_trace: ResolutionTrace | None,
) -> EffectiveValueProvenance:
  if resolution_trace is not None:
    existing = resolution_trace.by_path().get(path)
    if existing is not None and not existing.secret:
      return EffectiveValueProvenance(
        path=path,
        source_kind=existing.source_kind.value,
        source_name=existing.source_name,
        secret=False,
      )
    if existing is not None and existing.secret:
      return EffectiveValueProvenance(
        path=path,
        source_kind=existing.source_kind.value,
        source_name=existing.source_name,
        secret=True,
      )
  return EffectiveValueProvenance(
    path=path,
    source_kind=source_kind.value,
    source_name=source_name,
    secret=False,
  )


def build_effective_instrument(
  runtime: Any,
  instrument_id: str,
  *,
  resolution_trace: ResolutionTrace | None = None,
) -> EffectiveInstrumentConfig:
  """Compose an immutable effective instrument context from a resolved root."""
  instruments: InstrumentsConfig = runtime.instruments
  key = instrument_id.strip()
  instrument = instruments.root.get(key)
  if instrument is None:
    # Allow lookup by canonical/broker/alias via map.
    mapped = _instrument_lookup_map(instruments).get(_normalize_symbol_key(key))
    if mapped is None:
      raise EffectiveInstrumentError(f"unknown instrument {instrument_id!r}")
    key = mapped
    instrument = instruments.root[key]

  rollout = effective_rollout(instrument)
  try:
    policy_name = resolve_policy_name(key, instrument)
  except ValueError as exc:
    raise EffectiveInstrumentError(str(exc)) from None
  if policy_name != XAU_CURRENT_V1_POLICY:
    raise EffectiveInstrumentError(
      f"instrument {key!r} policy {policy_name!r} is not supported"
    )

  if rollout is not InstrumentRollout.DISABLED:
    units = _require_units(key, instrument)
    lookbacks = _require_lookbacks(key, instrument, runtime.market_data)
    zones = _require_zones(key, instrument, runtime.analysis)
  else:
    # Disabled instruments may omit contract; expose placeholder units only when present.
    if instrument.contract is None:
      raise EffectiveInstrumentError(
        f"instrument {key!r} effective context requires contract metadata; "
        "declare the instrument disabled without requesting for_instrument, "
        "or supply contract fields"
      )
    units = _require_units(key, instrument)
    lookbacks = (
      instrument.market_data.lookbacks
      if instrument.market_data is not None
      else _require_lookbacks(key, instrument, runtime.market_data)
    )
    zones = (
      instrument.analysis.zones
      if instrument.analysis is not None
      else _require_zones(key, instrument, runtime.analysis)
    )

  _validate_overrides(key, instrument.overrides)
  (
    market_data,
    analysis,
    strategies,
    actionability,
    execution,
    risk,
    lifecycle,
  ) = _apply_domain_overrides(runtime, instrument.overrides)

  aliases = tuple(dict.fromkeys((
    *instrument.aliases,
    *(("XAUUSD",) if _normalize_symbol_key(instrument.canonical_symbol) == "XAU" else ()),
  )))

  identity = InstrumentIdentityConfig(
    instrument_id=key,
    canonical_symbol=instrument.canonical_symbol,
    broker_symbol=instrument.broker_symbol,
    aliases=aliases,
    rollout=rollout,
    timeframes=tuple(instrument.timeframes),
  )

  provenance_entries: list[EffectiveValueProvenance] = [
    EffectiveValueProvenance(
      path=f"instruments.{key}.rollout",
      source_kind=(
        SourceKind.DERIVED_COMPATIBILITY_RULE.value
        if instrument.rollout is None
        else SourceKind.CONFIG_FILE.value
      ),
      source_name=(
        "enabled_compatibility_mapping"
        if instrument.rollout is None
        else "instrument_rollout"
      ),
    ),
    EffectiveValueProvenance(
      path=f"instruments.{key}.policy",
      source_kind=(
        SourceKind.CONFIG_FILE.value
        if instrument.policy is not None
        else SourceKind.DERIVED_COMPATIBILITY_RULE.value
      ),
      source_name=(
        "instrument_policy"
        if instrument.policy is not None
        else "default_xau_current_v1_policy"
      ),
    ),
    EffectiveValueProvenance(
      path=f"instruments.{key}.contract.pip_size",
      source_kind=SourceKind.CONFIG_FILE.value,
      source_name="instrument_registry",
    ),
  ]
  for path in sorted(instrument.overrides):
    provenance_entries.append(EffectiveValueProvenance(
      path=path,
      source_kind="instrument_override",
      source_name=f"instruments.{key}.overrides",
    ))
  # Preserve selected global traces for shared domains.
  for leaf in (
    "execution.entry.maximum_chase_distance_pips",
    "risk.sizing.mode",
    "lifecycle.candidate.execution_maximum_age_seconds",
  ):
    provenance_entries.append(_trace_or_policy(
      path=leaf,
      source_kind=SourceKind.CONFIG_FILE,
      source_name=policy_name,
      resolution_trace=resolution_trace,
    ))

  return EffectiveInstrumentConfig(
    identity=identity,
    units=units,
    market_data=EffectiveInstrumentMarketDataConfig(
      lookbacks=lookbacks,
      runtime=market_data,
    ),
    analysis=EffectiveInstrumentAnalysisConfig(
      zones=zones,
      runtime=analysis,
    ),
    strategies=strategies,
    actionability=actionability,
    execution=execution,
    risk=risk,
    lifecycle=lifecycle,
    policy_name=policy_name,
    provenance=EffectiveInstrumentProvenance(entries=tuple(provenance_entries)),
  )


def list_enabled_instrument_ids(runtime: Any) -> tuple[str, ...]:
  return runtime.instruments.enabled_ids()


def list_live_instrument_ids(runtime: Any) -> tuple[str, ...]:
  return runtime.instruments.live_ids()


def instrument_id_for_broker_symbol(runtime: Any, broker_symbol: str) -> str:
  mapping = _instrument_lookup_map(runtime.instruments)
  key = _normalize_symbol_key(broker_symbol)
  instrument_id = mapping.get(key)
  if instrument_id is None:
    raise EffectiveInstrumentError(
      f"unknown broker symbol {broker_symbol!r}"
    )
  return instrument_id
