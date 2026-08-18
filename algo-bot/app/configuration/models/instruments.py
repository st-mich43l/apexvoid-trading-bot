"""Typed per-instrument configuration registry (outside ENV leaf catalog)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from app.configuration.models.base import FrozenConfigModel


SUPPORTED_INSTRUMENT_TIMEFRAMES = frozenset({"H1", "M15", "M5", "M1", "H4", "D1"})

# Compatibility policy: inherit global trading domains from the resolved root.
XAU_CURRENT_V1_POLICY = "xau_current_v1"
REGISTERED_INSTRUMENT_POLICIES = frozenset({XAU_CURRENT_V1_POLICY})


class InstrumentRollout(StrEnum):
  DISABLED = "disabled"
  FEED_ONLY = "feed_only"
  ANALYSIS_ONLY = "analysis_only"
  PAPER = "paper"
  LIVE = "live"


# cTrader ProtoOA volume is hundredths of a contract unit, so 1.0 lot
# volume = contract_units_per_lot * 100 (XAU 10_000, FX majors 10_000_000).
CTRADER_VOLUME_HUNDREDTHS = 100


class InstrumentContractConfig(FrozenConfigModel):
  pip_size: float = Field(gt=0)
  contract_units_per_lot: float = Field(gt=0)
  price_digits: int = Field(ge=0, le=8)
  # USD account pip value per 1.0 lot. When omitted, derived as
  # pip_size * contract_units_per_lot (correct for USD-quoted XAU/EURUSD).
  # JPY-quoted pairs must set this explicitly (quote units are not dollars).
  pip_value_per_lot: float | None = Field(default=None, gt=0)
  # Broker (cTrader) volume units in 1.0 lot. Must match Symbol.LotSize.
  # Omit to derive as contract_units_per_lot * 100.
  volume_units_per_lot: int | None = Field(default=None, gt=0)
  # Hard ceiling in lots for TradePlan.risk.max_volume. Equity-table size
  # must fit under this; the engine never silently clamps.
  max_lots: float = Field(default=10.0, gt=0)
  # Extra multiplier on equity-table lots. FX uses >1 so a short 1:2
  # target still books similar dollar risk to gold's wider stop.
  lot_multiplier: float = Field(default=1.0, gt=0)


class InstrumentLookbacksConfig(FrozenConfigModel):
  h1_bars: int = Field(ge=50)
  m15_bars: int = Field(ge=50)
  m5_bars: int = Field(ge=50)
  m1_bars: int = Field(ge=50)


class InstrumentMarketDataConfig(FrozenConfigModel):
  lookbacks: InstrumentLookbacksConfig


class InstrumentZoneWidthConfig(FrozenConfigModel):
  minimum_width_price: float = Field(gt=0)
  preferred_minimum_width_price: float = Field(gt=0)
  preferred_maximum_width_price: float = Field(gt=0)
  major_maximum_width_price: float = Field(gt=0)

  @model_validator(mode="after")
  def validate_width_order(self) -> InstrumentZoneWidthConfig:
    if not (
      0
      < self.minimum_width_price
      <= self.preferred_minimum_width_price
      <= self.preferred_maximum_width_price
      <= self.major_maximum_width_price
    ):
      raise ValueError(
        "zone widths must satisfy "
        "minimum <= preferred minimum <= preferred maximum <= major maximum"
      )
    return self


class InstrumentAnalysisConfig(FrozenConfigModel):
  zones: InstrumentZoneWidthConfig


class InstrumentConfig(FrozenConfigModel):
  """Per-instrument declaration.

  Legacy ``enabled: true/false`` remains supported. When ``rollout`` is omitted,
  it is derived as ``live`` (enabled) or ``disabled`` (!enabled) so existing
  production XAU YAML keeps its current runtime meaning.
  """

  enabled: bool = True
  rollout: InstrumentRollout | None = None
  policy: str | None = None
  canonical_symbol: str
  broker_symbol: str
  aliases: tuple[str, ...] = ()
  timeframes: list[str] = Field(default_factory=lambda: ["H1", "M15", "M5", "M1"])
  contract: InstrumentContractConfig | None = None
  market_data: InstrumentMarketDataConfig | None = None
  analysis: InstrumentAnalysisConfig | None = None
  # Sparse dotted-path overrides applied when building EffectiveInstrumentConfig.
  overrides: dict[str, Any] = Field(default_factory=dict)

  @model_validator(mode="before")
  @classmethod
  def _reject_enabled_rollout_conflicts(cls, data: Any) -> Any:
    if not isinstance(data, dict):
      return data
    if "rollout" not in data or "enabled" not in data:
      return data
    enabled = bool(data["enabled"])
    rollout = data["rollout"]
    if isinstance(rollout, InstrumentRollout):
      rollout_value = rollout.value
    else:
      rollout_value = str(rollout)
    if enabled and rollout_value == InstrumentRollout.DISABLED.value:
      raise ValueError(
        "conflicting instrument state: enabled=true with rollout=disabled"
      )
    if (not enabled) and rollout_value != InstrumentRollout.DISABLED.value:
      raise ValueError(
        "conflicting instrument state: enabled=false with "
        f"rollout={rollout_value!r}"
      )
    return data

  @field_validator("canonical_symbol", "broker_symbol")
  @classmethod
  def _non_empty_symbol(cls, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
      raise ValueError("symbol must be non-empty")
    return cleaned

  @field_validator("aliases", mode="before")
  @classmethod
  def _normalize_aliases(cls, value: Any) -> tuple[str, ...]:
    if value is None:
      return ()
    if isinstance(value, str):
      items = [value]
    else:
      items = list(value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
      alias = str(item).strip().upper()
      if not alias or alias in seen:
        continue
      seen.add(alias)
      cleaned.append(alias)
    return tuple(cleaned)

  @field_validator("timeframes")
  @classmethod
  def _validate_timeframes(cls, value: list[str]) -> list[str]:
    if not value:
      raise ValueError("timeframes must not be empty")
    normalized = [item.strip().upper() for item in value]
    unknown = sorted({
      item for item in normalized if item not in SUPPORTED_INSTRUMENT_TIMEFRAMES
    })
    if unknown:
      raise ValueError(
        "unsupported instrument timeframes: "
        + ", ".join(unknown)
      )
    return normalized

  @field_validator("policy")
  @classmethod
  def _validate_policy(cls, value: str | None) -> str | None:
    if value is None:
      return None
    cleaned = value.strip()
    if not cleaned:
      raise ValueError("policy must be non-empty when provided")
    if cleaned not in REGISTERED_INSTRUMENT_POLICIES:
      raise ValueError(
        f"unknown instrument policy {cleaned!r}; known policies: "
        + ", ".join(sorted(REGISTERED_INSTRUMENT_POLICIES))
      )
    return cleaned

  @field_validator("overrides")
  @classmethod
  def _validate_overrides_are_mapping(cls, value: Any) -> dict[str, Any]:
    if value is None:
      return {}
    if not isinstance(value, dict):
      raise ValueError("overrides must be a mapping of dotted path to value")
    for key in value:
      if not isinstance(key, str) or not key.strip():
        raise ValueError("override paths must be non-empty strings")
    return {str(key).strip(): item for key, item in value.items()}

  @model_validator(mode="after")
  def _require_contract_when_active(self) -> InstrumentConfig:
    if effective_rollout(self) is not InstrumentRollout.DISABLED and self.contract is None:
      raise ValueError(
        "non-disabled instruments require contract configuration"
      )
    return self


def effective_rollout(instrument: InstrumentConfig) -> InstrumentRollout:
  """Resolve the effective rollout, preserving legacy enabled semantics."""
  if instrument.rollout is not None:
    return instrument.rollout
  return (
    InstrumentRollout.LIVE if instrument.enabled else InstrumentRollout.DISABLED
  )


def resolve_policy_name(
  instrument_id: str,
  instrument: InstrumentConfig,
) -> str:
  """Select the named policy for an instrument."""
  if instrument.policy is not None:
    return instrument.policy
  canonical = instrument.canonical_symbol.strip().upper()
  if instrument_id.strip().upper() == "XAU" or canonical == "XAU":
    return XAU_CURRENT_V1_POLICY
  rollout = effective_rollout(instrument)
  if rollout in {
    InstrumentRollout.ANALYSIS_ONLY,
    InstrumentRollout.PAPER,
    InstrumentRollout.LIVE,
  }:
    raise ValueError(
      f"instrument {instrument_id!r} requires an explicit policy when "
      f"rollout={rollout.value}"
    )
  # feed_only / disabled may omit policy; still bind the compatibility policy
  # so callers receive a deterministic name for provenance.
  return XAU_CURRENT_V1_POLICY


class InstrumentsConfig(FrozenConfigModel):
  """Mapping of instrument id → configuration; excluded from ENV leaf catalog.

  YAML and loaders may supply a bare ``{SYMBOL: {...}}`` mapping; it is wrapped
  into ``root`` automatically.
  """

  root: dict[str, InstrumentConfig] = Field(default_factory=dict)

  @model_validator(mode="before")
  @classmethod
  def _wrap_bare_mapping(cls, data: Any) -> Any:
    if isinstance(data, dict) and "root" not in data:
      return {"root": data}
    return data

  @model_validator(mode="after")
  def _validate_registry(self) -> InstrumentsConfig:
    brokers: dict[str, str] = {}
    canonicals: dict[str, str] = {}
    for instrument_id, instrument in self.root.items():
      if not instrument_id or not str(instrument_id).strip():
        raise ValueError("instrument id must be non-empty")
      if effective_rollout(instrument) is InstrumentRollout.DISABLED:
        continue
      broker = instrument.broker_symbol.strip().upper()
      prior_broker = brokers.get(broker)
      if prior_broker is not None and prior_broker != instrument_id:
        raise ValueError(
          f"duplicate broker_symbol {instrument.broker_symbol!r} among "
          f"active instruments {prior_broker!r} and {instrument_id!r}"
        )
      brokers[broker] = instrument_id
      for alias in instrument.aliases:
        alias_key = alias.strip().upper()
        prior_alias = brokers.get(alias_key)
        if prior_alias is not None and prior_alias != instrument_id:
          raise ValueError(
            f"duplicate broker alias {alias!r} among active instruments "
            f"{prior_alias!r} and {instrument_id!r}"
          )
        brokers[alias_key] = instrument_id
      canonical = instrument.canonical_symbol.strip().upper()
      prior_canonical = canonicals.get(canonical)
      if prior_canonical is not None and prior_canonical != instrument_id:
        raise ValueError(
          f"duplicate canonical_symbol {instrument.canonical_symbol!r} among "
          f"active instruments {prior_canonical!r} and {instrument_id!r}"
        )
      canonicals[canonical] = instrument_id
    return self

  def get(self, instrument_id: str) -> InstrumentConfig | None:
    return self.root.get(instrument_id)

  def enabled_ids(self) -> tuple[str, ...]:
    """Instrument ids that are not disabled (legacy active set)."""
    return tuple(
      sorted(
        key
        for key, value in self.root.items()
        if effective_rollout(value) is not InstrumentRollout.DISABLED
      )
    )

  def live_ids(self) -> tuple[str, ...]:
    return tuple(
      sorted(
        key
        for key, value in self.root.items()
        if effective_rollout(value) is InstrumentRollout.LIVE
      )
    )

  def as_mapping(self) -> Mapping[str, InstrumentConfig]:
    return dict(self.root)


EMPTY_INSTRUMENTS = InstrumentsConfig()


def default_xau_instrument() -> InstrumentConfig:
  """Schema-default XAU instrument matching current flat leaf defaults."""
  return InstrumentConfig(
    enabled=True,
    canonical_symbol="XAU",
    broker_symbol="XAU",
    aliases=("XAUUSD",),
    timeframes=["H1", "M15", "M5", "M1"],
    policy=XAU_CURRENT_V1_POLICY,
    contract=InstrumentContractConfig(
      pip_size=0.1,
      contract_units_per_lot=100.0,
      price_digits=2,
    ),
    market_data=InstrumentMarketDataConfig(
      lookbacks=InstrumentLookbacksConfig(
        h1_bars=400,
        m15_bars=250,
        m5_bars=150,
        m1_bars=150,
      ),
    ),
    analysis=InstrumentAnalysisConfig(
      zones=InstrumentZoneWidthConfig(
        minimum_width_price=3.0,
        preferred_minimum_width_price=3.0,
        preferred_maximum_width_price=6.0,
        major_maximum_width_price=10.0,
      ),
    ),
  )


# Paths projected from instruments.XAU into existing flat leaves.
XAU_LEAF_PROJECTION: tuple[tuple[str, str], ...] = (
  ("contract.pip_size", "contract.instrument.pip_size"),
  ("contract.contract_units_per_lot", "contract.instrument.contract_units_per_lot"),
  ("contract.price_digits", "contract.instrument.price_digits"),
  ("canonical_symbol", "contract.instrument.canonical_symbol"),
  ("market_data.lookbacks.h1_bars", "market_data.lookbacks.h1_bars"),
  ("market_data.lookbacks.m15_bars", "market_data.lookbacks.m15_bars"),
  ("market_data.lookbacks.m5_bars", "market_data.lookbacks.m5_bars"),
  ("market_data.lookbacks.m1_bars", "market_data.lookbacks.m1_bars"),
  (
    "analysis.zones.minimum_width_price",
    "analysis.zones.symbol_contract.minimum_width_price",
  ),
  (
    "analysis.zones.preferred_minimum_width_price",
    "analysis.zones.symbol_contract.preferred_minimum_width_price",
  ),
  (
    "analysis.zones.preferred_maximum_width_price",
    "analysis.zones.symbol_contract.preferred_maximum_width_price",
  ),
  (
    "analysis.zones.major_maximum_width_price",
    "analysis.zones.symbol_contract.major_maximum_width_price",
  ),
)


DEPRECATED_XAU_ENV_ALIASES: dict[str, str] = {
  "AUTO_TRADE_XAU_PIP_SIZE": "instruments.XAU.contract.pip_size",
  "AUTO_TRADE_XAU_CONTRACT_SIZE": "instruments.XAU.contract.contract_units_per_lot",
  "AUTO_TRADE_XAU_PRICE_DIGITS": "instruments.XAU.contract.price_digits",
  "XAU_LOOKBACK_H1_BARS": "instruments.XAU.market_data.lookbacks.h1_bars",
  "XAU_LOOKBACK_M15_BARS": "instruments.XAU.market_data.lookbacks.m15_bars",
  "XAU_LOOKBACK_M5_BARS": "instruments.XAU.market_data.lookbacks.m5_bars",
  "XAU_LOOKBACK_M1_BARS": "instruments.XAU.market_data.lookbacks.m1_bars",
  "XAU_ZONE_MIN_WIDTH_PRICE": "instruments.XAU.analysis.zones.minimum_width_price",
  "XAU_ZONE_PREFERRED_MIN_WIDTH_PRICE": (
    "instruments.XAU.analysis.zones.preferred_minimum_width_price"
  ),
  "XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE": (
    "instruments.XAU.analysis.zones.preferred_maximum_width_price"
  ),
  "XAU_MAJOR_ZONE_MAX_WIDTH_PRICE": (
    "instruments.XAU.analysis.zones.major_maximum_width_price"
  ),
}


def instrument_attr(instrument: InstrumentConfig, dotted: str) -> object | None:
  cursor: object = instrument
  for part in dotted.split("."):
    if cursor is None:
      return None
    cursor = getattr(cursor, part, None)
  return cursor


def project_xau_leaf_values(instrument: InstrumentConfig) -> dict[str, object]:
  """Project an XAU InstrumentConfig onto existing flat catalog leaf paths."""
  values: dict[str, object] = {}
  for source, leaf in XAU_LEAF_PROJECTION:
    value = instrument_attr(instrument, source)
    if value is not None:
      values[leaf] = value
  return values


def hydrate_xau_from_leaves(flat_values: Mapping[str, object]) -> InstrumentConfig:
  """Build instruments.XAU from effective flat leaves (ENV-only parity path)."""
  return InstrumentConfig(
    enabled=True,
    canonical_symbol=str(flat_values.get("contract.instrument.canonical_symbol", "XAU")),
    broker_symbol=str(flat_values.get("contract.instrument.symbols", "XAU")),
    aliases=("XAUUSD",),
    timeframes=["H1", "M15", "M5", "M1"],
    policy=XAU_CURRENT_V1_POLICY,
    contract=InstrumentContractConfig(
      pip_size=float(flat_values["contract.instrument.pip_size"]),
      contract_units_per_lot=float(
        flat_values["contract.instrument.contract_units_per_lot"]
      ),
      price_digits=int(flat_values["contract.instrument.price_digits"]),
    ),
    market_data=InstrumentMarketDataConfig(
      lookbacks=InstrumentLookbacksConfig(
        h1_bars=int(flat_values["market_data.lookbacks.h1_bars"]),
        m15_bars=int(flat_values["market_data.lookbacks.m15_bars"]),
        m5_bars=int(flat_values["market_data.lookbacks.m5_bars"]),
        m1_bars=int(flat_values["market_data.lookbacks.m1_bars"]),
      ),
    ),
    analysis=InstrumentAnalysisConfig(
      zones=InstrumentZoneWidthConfig(
        minimum_width_price=float(
          flat_values["analysis.zones.symbol_contract.minimum_width_price"]
        ),
        preferred_minimum_width_price=float(
          flat_values[
            "analysis.zones.symbol_contract.preferred_minimum_width_price"
          ]
        ),
        preferred_maximum_width_price=float(
          flat_values[
            "analysis.zones.symbol_contract.preferred_maximum_width_price"
          ]
        ),
        major_maximum_width_price=float(
          flat_values["analysis.zones.symbol_contract.major_maximum_width_price"]
        ),
      ),
    ),
  )
