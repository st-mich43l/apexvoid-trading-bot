"""Typed per-instrument configuration registry (outside ENV leaf catalog)."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from app.configuration.models.base import FrozenConfigModel


SUPPORTED_INSTRUMENT_TIMEFRAMES = frozenset({"H1", "M15", "M5", "M1", "H4", "D1"})


class InstrumentContractConfig(FrozenConfigModel):
  pip_size: float = Field(gt=0)
  contract_units_per_lot: float = Field(gt=0)
  price_digits: int = Field(ge=0, le=8)


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
  enabled: bool = True
  canonical_symbol: str
  broker_symbol: str
  timeframes: list[str] = Field(default_factory=lambda: ["H1", "M15", "M5", "M1"])
  contract: InstrumentContractConfig | None = None
  market_data: InstrumentMarketDataConfig | None = None
  analysis: InstrumentAnalysisConfig | None = None

  @field_validator("canonical_symbol", "broker_symbol")
  @classmethod
  def _non_empty_symbol(cls, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
      raise ValueError("symbol must be non-empty")
    return cleaned

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

  @model_validator(mode="after")
  def _require_contract_when_enabled(self) -> InstrumentConfig:
    if self.enabled and self.contract is None:
      raise ValueError("enabled instruments require contract configuration")
    return self


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
    for instrument_id, instrument in self.root.items():
      if not instrument_id or not str(instrument_id).strip():
        raise ValueError("instrument id must be non-empty")
      if not instrument.enabled:
        continue
      broker = instrument.broker_symbol
      prior = brokers.get(broker)
      if prior is not None and prior != instrument_id:
        raise ValueError(
          f"duplicate broker_symbol {broker!r} among enabled instruments "
          f"{prior!r} and {instrument_id!r}"
        )
      brokers[broker] = instrument_id
    return self

  def get(self, instrument_id: str) -> InstrumentConfig | None:
    return self.root.get(instrument_id)

  def enabled_ids(self) -> tuple[str, ...]:
    return tuple(
      sorted(key for key, value in self.root.items() if value.enabled)
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
    timeframes=["H1", "M15", "M5", "M1"],
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
    timeframes=["H1", "M15", "M5", "M1"],
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
