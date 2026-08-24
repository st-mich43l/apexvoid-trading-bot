"""Typed per-instrument configuration registry (outside ENV leaf catalog)."""

from __future__ import annotations

from enum import StrEnum
import math
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from app.configuration.models.base import FrozenConfigModel


SUPPORTED_INSTRUMENT_TIMEFRAMES = frozenset({"H1", "M15", "M5", "M1", "H4", "D1"})

# Compatibility policy: inherit global trading domains from the resolved root.
XAU_CURRENT_V1_POLICY = "xau_current_v1"
FX_FIXED_2R_V1_POLICY = "fx_fixed_2r_v1"
# 2026 GBP/JPY dig: ATR(14) ~180 pips/day vs EURUSD's ~70, and moves reverse
# hard once they've run -- front-load profit-taking (40/25/35 instead of the
# standard 25/25/50) so more of the win is locked in before a violent
# snap-back gives it back. Same 2R ladder/trail/entry_clips as
# fx_fixed_2r_v1, only the close_ratios split differs.
FX_FIXED_2R_FRONTLOAD_V1_POLICY = "fx_fixed_2r_frontload_v1"
REGISTERED_INSTRUMENT_POLICIES = frozenset({
  FX_FIXED_2R_V1_POLICY,
  FX_FIXED_2R_FRONTLOAD_V1_POLICY,
  XAU_CURRENT_V1_POLICY,
})

# Required targeting.close_ratios per fixed_rr policy variant -- every other
# fixed_rr field (reward_risk, target_r_multiples, trail_after_r, trail_to_r,
# entry_clips) is shared across variants; only the close-ratio split differs.
FIXED_RR_POLICY_CLOSE_RATIOS: dict[str, tuple[float, float, float]] = {
  FX_FIXED_2R_V1_POLICY: (0.25, 0.25, 0.50),
  FX_FIXED_2R_FRONTLOAD_V1_POLICY: (0.40, 0.25, 0.35),
}


class InstrumentRollout(StrEnum):
  DISABLED = "disabled"
  FEED_ONLY = "feed_only"
  ANALYSIS_ONLY = "analysis_only"
  PAPER = "paper"
  LIVE = "live"


class InstrumentTargetMode(StrEnum):
  LADDER_PIPS = "ladder_pips"
  FIXED_RR = "fixed_rr"


class InstrumentManualEntryMode(StrEnum):
  """Broker entry distribution for owner-authored manual signals."""

  SINGLE = "single"
  ZONE_LADDER = "zone_ladder"


class InstrumentManualRiskReference(StrEnum):
  """Entry edge used for the one approved manual-trade risk contract."""

  SHALLOW = "shallow"


class InstrumentManualConfig(FrozenConfigModel):
  """Per-instrument owner manual-signal and ``/algo`` capabilities.

  This is deliberately separate from autonomous ``targeting``. A fixed-RR
  exit policy must not silently mean "FX", "single entry", or "multiply the
  owner's size" for every future instrument that happens to reuse it.
  """

  # An explicitly present but incomplete manual block must still fail closed.
  # Production packs opt in to both capabilities deliberately.
  enabled: bool = False
  algo_enabled: bool = False
  entry_mode: InstrumentManualEntryMode = InstrumentManualEntryMode.SINGLE
  risk_reference: InstrumentManualRiskReference = (
    InstrumentManualRiskReference.SHALLOW
  )
  risk_multiplier: float = Field(default=1.0, gt=0)
  # Manual exit distribution is explicit; autonomous targeting ratios must
  # never silently change an owner-authored trade. Used when TP counts match.
  target_close_ratios: tuple[float, ...] = ()
  # Optional first-target close fraction. Candidate construction adapts this
  # to any owner-supplied TP count: one TP closes 100%; otherwise TP1 gets
  # this fraction and the remainder is split deterministically.
  tp1_close_fraction: float | None = Field(default=None, gt=0, lt=1)

  @model_validator(mode="after")
  def validate_manual_profile(self) -> InstrumentManualConfig:
    if self.algo_enabled and not self.enabled:
      raise ValueError("manual.algo_enabled requires manual.enabled")
    if not math.isfinite(float(self.risk_multiplier)):
      raise ValueError("manual.risk_multiplier must be finite")
    if (
      self.tp1_close_fraction is not None
      and not math.isfinite(float(self.tp1_close_fraction))
    ):
      raise ValueError("manual.tp1_close_fraction must be finite")
    ratios = tuple(float(value) for value in self.target_close_ratios)
    if any(not math.isfinite(value) or value <= 0 for value in ratios):
      raise ValueError("manual.target_close_ratios must be positive and finite")
    if ratios and not math.isclose(
      sum(ratios),
      1.0,
      rel_tol=0.0,
      abs_tol=1e-6,
    ):
      raise ValueError("manual.target_close_ratios must sum to 1")
    if ratios and self.tp1_close_fraction is not None:
      raise ValueError(
        "manual target_close_ratios and tp1_close_fraction are mutually exclusive"
      )
    return self


class InstrumentTargetingConfig(FrozenConfigModel):
  """Instrument-owned exit contract.

  ``fixed_rr`` produces an R-multiple ladder from the final protective stop.
  It does not inherit XAU's absolute pip ladder.
  """

  mode: InstrumentTargetMode = InstrumentTargetMode.LADDER_PIPS
  reward_risk: float | None = Field(default=None, gt=0)
  target_r_multiples: tuple[float, ...] = ()
  close_ratios: tuple[float, ...] = ()
  trail_after_r: float | None = Field(default=None, gt=0)
  trail_to_r: float | None = Field(default=None, gt=0)
  # Equal-size DCA clips for technique/scalp micro-grid entries. FX uses 2
  # (market + one deeper limit); XAU keeps the default five-clip grid.
  entry_clips: int = Field(default=5, ge=2, le=5)

  @model_validator(mode="after")
  def validate_reward_risk(self) -> InstrumentTargetingConfig:
    if self.mode is InstrumentTargetMode.FIXED_RR:
      if self.reward_risk is None:
        raise ValueError("fixed_rr targeting requires reward_risk")
      levels = tuple(float(value) for value in self.target_r_multiples)
      ratios = tuple(float(value) for value in self.close_ratios)
      if not levels:
        raise ValueError("fixed_rr targeting requires target_r_multiples")
      if not ratios:
        raise ValueError("fixed_rr targeting requires close_ratios")
      if len(levels) != len(ratios):
        raise ValueError(
          "fixed_rr target_r_multiples and close_ratios must have equal length"
        )
      if (
        any(not math.isfinite(value) or value <= 0 for value in levels)
        or tuple(sorted(set(levels))) != levels
      ):
        raise ValueError(
          "fixed_rr target_r_multiples must be positive and strictly increasing"
        )
      if not math.isclose(
        levels[-1], float(self.reward_risk), rel_tol=0.0, abs_tol=1e-9,
      ):
        raise ValueError(
          "fixed_rr final target_r_multiple must equal reward_risk"
        )
      if any(not math.isfinite(value) or value <= 0 for value in ratios):
        raise ValueError("fixed_rr close_ratios must be positive")
      if not math.isclose(sum(ratios), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("fixed_rr close_ratios must sum to 1.0")
      if (self.trail_after_r is None) != (self.trail_to_r is None):
        raise ValueError(
          "fixed_rr trail_after_r and trail_to_r must be set together"
        )
      if self.trail_after_r is not None and self.trail_to_r is not None:
        trail_after = float(self.trail_after_r)
        trail_to = float(self.trail_to_r)
        if not math.isfinite(trail_after) or not math.isfinite(trail_to):
          raise ValueError("fixed_rr trailing R values must be finite")
        if not any(
          math.isclose(trail_after, level, rel_tol=0.0, abs_tol=1e-9)
          for level in levels
        ):
          raise ValueError("fixed_rr trail_after_r must name a target R level")
        if not any(
          math.isclose(trail_to, level, rel_tol=0.0, abs_tol=1e-9)
          for level in levels
        ):
          raise ValueError("fixed_rr trail_to_r must name a target R level")
        if trail_to >= trail_after:
          raise ValueError("fixed_rr trail_to_r must be below trail_after_r")
        if math.isclose(
          trail_after, levels[-1], rel_tol=0.0, abs_tol=1e-9,
        ):
          raise ValueError("fixed_rr trail_after_r must precede the final target")
    elif (
      self.reward_risk is not None
      or self.target_r_multiples
      or self.close_ratios
      or self.trail_after_r is not None
      or self.trail_to_r is not None
    ):
      raise ValueError(
        "ladder_pips targeting must not set fixed-RR fields"
      )
    return self


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


# Named non-scalp reaction windows. Add a name here when onboarding a pair
# rather than copying hour strings into every instrument block.
#
# Tokyo is Japanese open (00:00 UTC / 09:00 JST) through London open, so
# JPY pairs are not London/NY-only. Prior 0-3 left mid-Tokyo (03–07 UTC,
# still JST afternoon) dead despite local liquidity.
REGISTERED_REACTION_SESSIONS: dict[str, str] = {
  "london_ny": "7-11,13-16",
  "tokyo_london": "0-11",
  # USDJPY dig: liquid across all three home sessions (JPY driver in
  # Tokyo, USD driver in NY, plus London), unlike GBPJPY's Tokyo/London-
  # only, no-NY-dump-window profile as a cross pair.
  "tokyo_london_ny": "0-11,13-16",
}


class InstrumentStopEnvelopeConfig(FrozenConfigModel):
  """Structural stop band for one instrument.

  Expands to reaction/trend/range floor and cap leaves so a new pair does not
  copy eight dotted override paths that must stay in sync.
  """

  min_pips: int = Field(ge=1)
  max_pips: int = Field(ge=1)
  sl_distance: float = Field(gt=0)

  @model_validator(mode="after")
  def validate_band(self) -> InstrumentStopEnvelopeConfig:
    if self.min_pips > self.max_pips:
      raise ValueError("stop_envelope.min_pips must be <= max_pips")
    return self


class InstrumentActivationProfileConfig(FrozenConfigModel):
  """Reaction confirmation + spread gate for one instrument."""

  require_sweep_body: bool = True
  trigger_maximum_age_bars: int = Field(default=3, ge=1)
  max_spread_pips: int = Field(ge=1)


class InstrumentMarketMapScaleConfig(FrozenConfigModel):
  change_min: float = Field(gt=0)
  fallback_radius_price: float = Field(gt=0)
  scalp_radius_price: float = Field(gt=0)


class InstrumentPriceScaleConfig(FrozenConfigModel):
  """Pair-native analysis/risk geometry in price units.

  Expands to round_step, market-map radii, zone merge, opposing gap, and FVG
  width so a new FX pair does not invent dotted override paths.
  """

  round_step: float = Field(gt=0)
  market_map: InstrumentMarketMapScaleConfig
  zone_merge_gap_price: float = Field(gt=0)
  zone_merge_max_width: float = Field(gt=0)
  opposing_minimum_separation_price: float = Field(gt=0)
  fvg_entry_max_width_price: float = Field(gt=0)


def resolve_reaction_session_windows(session: str) -> str:
  token = str(session or "").strip()
  if not token:
    raise ValueError("reaction_session must be non-empty")
  named = REGISTERED_REACTION_SESSIONS.get(token)
  if named is not None:
    return named
  if "-" in token and any(char.isdigit() for char in token):
    return token
  raise ValueError(
    f"unknown reaction_session {session!r}; known names: "
    + ", ".join(sorted(REGISTERED_REACTION_SESSIONS))
    + ", or a window list like '7-11,13-16'"
  )


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
  targeting: InstrumentTargetingConfig = Field(
    default_factory=InstrumentTargetingConfig,
  )
  # ``None`` preserves enough information to apply the narrow compatibility
  # defaults in ``resolve_manual_profile``. New declarations should state the
  # profile directly (normally through an instrument pack).
  manual: InstrumentManualConfig | None = None
  market_data: InstrumentMarketDataConfig | None = None
  analysis: InstrumentAnalysisConfig | None = None
  # Named session pack (`london_ny`) or a raw window list (`7-11,13-16`).
  reaction_session: str | None = None
  stop_envelope: InstrumentStopEnvelopeConfig | None = None
  activation: InstrumentActivationProfileConfig | None = None
  price_scale: InstrumentPriceScaleConfig | None = None
  # Sparse dotted-path overrides applied when building EffectiveInstrumentConfig.
  # Prefer session / envelope / activation / price_scale for new pairs; keep
  # this bag as an escape hatch for a single leaf.
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
    required_close_ratios = FIXED_RR_POLICY_CLOSE_RATIOS.get(self.policy)
    if required_close_ratios is not None and not (
      self.targeting.mode is InstrumentTargetMode.FIXED_RR
      and self.targeting.reward_risk == 2.0
      and self.targeting.target_r_multiples == (1.0, 1.5, 2.0)
      and self.targeting.close_ratios == required_close_ratios
      and self.targeting.trail_after_r == 1.5
      and self.targeting.trail_to_r == 1.0
      and self.targeting.entry_clips == 2
    ):
      pct = tuple(f"{ratio:.0%}" for ratio in required_close_ratios)
      raise ValueError(
        f"{self.policy} requires targeting.mode=fixed_rr and "
        f"targets 1R/1.5R/2R at {pct[0]}/{pct[1]}/{pct[2]}, "
        "trailing 1.5R to 1R, entry_clips=2"
      )
    if (
      self.targeting.mode is InstrumentTargetMode.FIXED_RR
      and self.policy not in FIXED_RR_POLICY_CLOSE_RATIOS
    ):
      raise ValueError(
        "fixed_rr targeting requires policy in "
        + ", ".join(sorted(FIXED_RR_POLICY_CLOSE_RATIOS))
      )
    if self.reaction_session:
      resolve_reaction_session_windows(self.reaction_session)
    executable = effective_rollout(self) in {
      InstrumentRollout.PAPER,
      InstrumentRollout.LIVE,
    }
    if self.policy in FIXED_RR_POLICY_CLOSE_RATIOS and executable:
      if not self.reaction_session:
        raise ValueError(
          f"{self.policy} live/paper instruments require reaction_session "
          f"(known: {', '.join(sorted(REGISTERED_REACTION_SESSIONS))})"
        )
      if self.stop_envelope is None:
        raise ValueError(
          f"{self.policy} live/paper instruments require stop_envelope"
        )
      if self.activation is None:
        raise ValueError(
          f"{self.policy} live/paper instruments require activation"
        )
      if self.price_scale is None:
        raise ValueError(
          f"{self.policy} live/paper instruments require price_scale"
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


def resolve_manual_profile(
  instrument_id: str,
  instrument: InstrumentConfig,
  *,
  legacy_fixed_rr_risk_multiplier: float = 1.5,
) -> InstrumentManualConfig:
  """Resolve explicit manual capabilities plus backward-compatible defaults.

  Compatibility is intentionally narrow: XAU keeps its three-entry zone
  ladder, and existing fixed-RR instruments keep their historical single
  entry and FX volume multiplier. Any other profile omitted by a future
  instrument fails closed for manual submission instead of inheriting XAU.
  """
  if instrument.manual is not None:
    return instrument.manual
  canonical = instrument.canonical_symbol.strip().upper()
  if instrument_id.strip().upper() == "XAU" or canonical == "XAU":
    return InstrumentManualConfig(
      enabled=True,
      algo_enabled=True,
      entry_mode=InstrumentManualEntryMode.ZONE_LADDER,
      risk_reference=InstrumentManualRiskReference.SHALLOW,
      risk_multiplier=1.0,
      target_close_ratios=(),
      tp1_close_fraction=None,
    )
  if instrument.targeting.mode is InstrumentTargetMode.FIXED_RR:
    return InstrumentManualConfig(
      enabled=True,
      algo_enabled=True,
      entry_mode=InstrumentManualEntryMode.SINGLE,
      risk_reference=InstrumentManualRiskReference.SHALLOW,
      risk_multiplier=legacy_fixed_rr_risk_multiplier,
      target_close_ratios=instrument.targeting.close_ratios,
      tp1_close_fraction=None,
    )
  return InstrumentManualConfig(
    enabled=False,
    algo_enabled=False,
    entry_mode=InstrumentManualEntryMode.SINGLE,
    risk_reference=InstrumentManualRiskReference.SHALLOW,
    risk_multiplier=1.0,
    target_close_ratios=(),
    tp1_close_fraction=None,
  )


def compose_instrument_domain_overrides(
  instrument: InstrumentConfig,
) -> dict[str, Any]:
  """Expand session/envelope/activation/price_scale/policy into domain paths.

  Explicit ``instrument.overrides`` win, so a pair can still pin one leaf
  without abandoning the structural pack. XAU keeps global mapped-zone width;
  only FX derives ``zone_min_width_abs`` from analysis zone floors.
  """
  composed: dict[str, Any] = {}
  if instrument.reaction_session:
    composed["execution.technique.reaction_publish_windows"] = (
      resolve_reaction_session_windows(instrument.reaction_session)
    )
  envelope = instrument.stop_envelope
  if envelope is not None:
    composed.update({
      "execution.reaction.stop_min_pips": envelope.min_pips,
      "execution.reaction.stop_max_pips": envelope.max_pips,
      "execution.stops.reaction.room_floor_pips": envelope.min_pips,
      "execution.range.room_stop_floor_pips": envelope.min_pips,
      "execution.stops.trend.minimum_pips": envelope.min_pips,
      "execution.trend.stop_max_pips": envelope.max_pips,
      "execution.scaling.add.min_stop_pips": envelope.min_pips,
      "execution.stops.sl_distance": envelope.sl_distance,
    })
  if instrument.policy in FIXED_RR_POLICY_CLOSE_RATIOS:
    ratio = float(instrument.targeting.reward_risk or 2.0)
    composed["execution.range.min_rr"] = ratio
    composed["execution.reaction.room_stop_min_rr"] = ratio
    composed["strategies.high_frequency_scalp.policy.minimum_reward_risk"] = ratio
    if envelope is not None:
      composed["execution.range.min_target_pips"] = int(
        round(float(envelope.min_pips) * ratio)
      )
    if instrument.analysis is not None:
      composed["execution.mapped_zone.zone_min_width_abs"] = (
        instrument.analysis.zones.minimum_width_price
      )
  activation = instrument.activation
  if activation is not None:
    composed["execution.technique.require_sweep_body"] = (
      activation.require_sweep_body
    )
    composed["execution.activation.reaction_trigger_maximum_age_bars"] = (
      activation.trigger_maximum_age_bars
    )
    composed["execution.entry.max_spread_pips"] = activation.max_spread_pips
  scale = instrument.price_scale
  if scale is not None:
    composed.update({
      "analysis.levels.round_step": scale.round_step,
      "analysis.market_map.change_min": scale.market_map.change_min,
      "analysis.market_map.fallback_radius_price": (
        scale.market_map.fallback_radius_price
      ),
      "analysis.market_map.scalp_radius_price": (
        scale.market_map.scalp_radius_price
      ),
      "analysis.zones.confluence.merge_gap_price": scale.zone_merge_gap_price,
      "analysis.zones.merge_max_width": scale.zone_merge_max_width,
      "risk.exposure.opposing_minimum_separation_price": (
        scale.opposing_minimum_separation_price
      ),
      "strategies.technique.fvg.entry_max_width_price": (
        scale.fvg_entry_max_width_price
      ),
      "strategies.technique.crt.entry_max_width_price": (
        scale.fvg_entry_max_width_price
      ),
    })
  composed.update(instrument.overrides)
  return composed


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
