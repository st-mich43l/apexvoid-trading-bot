"""Native YAML instrument composition and routing helpers."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from app.core.config_schema import ConfigNode


class EffectiveInstrumentError(ValueError):
  """Raised when a symbol is absent or incomplete in instruments.yml."""


class InstrumentRollout(StrEnum):
  DISABLED = "disabled"
  FEED_ONLY = "feed_only"
  ANALYSIS_ONLY = "analysis_only"
  PAPER = "paper"
  LIVE = "live"


class InstrumentTargetMode(StrEnum):
  LADDER_PIPS = "ladder_pips"
  FIXED_RR = "fixed_rr"


class InstrumentEntryMode(StrEnum):
  SINGLE = "single"
  ZONE_LADDER = "zone_ladder"


@dataclass(frozen=True, slots=True)
class InstrumentUnits:
  pip_size: float
  price_digits: int
  contract_units_per_lot: float
  pip_value_per_lot: float
  volume_units_per_lot: int
  max_lots: float

  def plan_max_volume(self) -> int:
    return int(round(self.max_lots * self.volume_units_per_lot))


@dataclass(frozen=True, slots=True)
class OppositePositionPolicy:
  """Instrument-owned rule for autonomous opposite-direction exposure.

  ``allowed=False`` blocks any opposite exposure on the symbol regardless of
  distance. ``allowed=True`` requires ``minimum_separation_pips`` (inclusive)
  between the incoming entry and EVERY existing opposite group; an incoming
  scalp-lane trade needs only ``scalp_minimum_separation_pips`` (inclusive).
  """

  symbol: str
  allowed: bool
  minimum_separation_pips: float | None
  pip_size: float
  # Separation an incoming SCALP-lane trade needs (XAU only; <= minimum). ``None``
  # on a hand-built policy means "no scalp exemption": the standard minimum applies.
  scalp_minimum_separation_pips: float | None = None


def _opposite_position_policy(
  instrument_id: str,
  exposure: Any,
  pip_size: float,
) -> OppositePositionPolicy:
  """Parse ``exposure.opposite_position``; missing/invalid fails closed."""
  node = _plain(exposure) if exposure is not None else None
  policy = node.get("opposite_position") if isinstance(node, Mapping) else None
  if not isinstance(policy, Mapping):
    raise EffectiveInstrumentError(
      f"{instrument_id} is missing exposure.opposite_position policy"
    )
  allowed = policy.get("allowed")
  if not isinstance(allowed, bool):
    raise EffectiveInstrumentError(
      f"{instrument_id} exposure.opposite_position.allowed must be a boolean"
    )
  minimum = policy.get("minimum_separation_pips")
  if allowed:
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or minimum <= 0:
      raise EffectiveInstrumentError(
        f"{instrument_id} allows opposite positions and needs a positive "
        "exposure.opposite_position.minimum_separation_pips"
      )
    scalp_minimum = policy.get("scalp_minimum_separation_pips")
    if (
      isinstance(scalp_minimum, bool)
      or not isinstance(scalp_minimum, (int, float))
      or scalp_minimum <= 0
    ):
      raise EffectiveInstrumentError(
        f"{instrument_id} allows opposite positions and needs a positive "
        "exposure.opposite_position.scalp_minimum_separation_pips"
      )
    if scalp_minimum > minimum:
      raise EffectiveInstrumentError(
        f"{instrument_id} exposure.opposite_position.scalp_minimum_separation_pips "
        f"({scalp_minimum}) must not exceed minimum_separation_pips ({minimum})"
      )
    return OppositePositionPolicy(
      instrument_id, True, float(minimum), pip_size, float(scalp_minimum)
    )
  if minimum is not None:
    raise EffectiveInstrumentError(
      f"{instrument_id} blocks opposite positions; "
      "minimum_separation_pips must not be declared"
    )
  if policy.get("scalp_minimum_separation_pips") is not None:
    raise EffectiveInstrumentError(
      f"{instrument_id} blocks opposite positions; "
      "scalp_minimum_separation_pips must not be declared"
    )
  return OppositePositionPolicy(instrument_id, False, None, pip_size)


@dataclass(frozen=True, slots=True)
class SameDirectionLimits:
  """One set of same-direction exposure limits (``None`` = not limited)."""

  max_groups: int | None = None
  max_cumulative_risk: float | None = None
  max_symbol_risk: float | None = None


@dataclass(frozen=True, slots=True)
class SameDirectionPolicy:
  """Instrument-owned bound on same-direction (and symbol) exposure.

  Absent from an instrument's ``exposure`` block the legacy behaviour applies
  unchanged (scalps stack without a count). When declared, every limit is
  measured over pending, submitted, partially filled and open groups of the
  symbol, in account currency for the risk limits.

  ``max_groups``: concurrent groups per symbol and direction.
  ``max_cumulative_risk``: sum of every group's worst-case remaining loss plus
  the incoming group's planned loss, per symbol and direction.
  ``max_symbol_risk``: the same sum over both directions.
  Unknown exposure (a group whose risk or existence cannot be established)
  always fails closed.
  """

  symbol: str
  limits: SameDirectionLimits
  strategy_overrides: tuple[tuple[str, SameDirectionLimits], ...] = ()
  risk_free_groups_count: bool = True
  duplicate_thesis_block: bool = True
  reservation_ttl_seconds: int = 600
  snapshot_max_age_seconds: int = 120

  def limits_for(self, strategy: str | None) -> SameDirectionLimits:
    key = str(strategy or "").strip().lower().replace(" ", "_")
    for name, override in self.strategy_overrides:
      if name == key:
        # An override changes the limits it declares; the others stay the instrument's.
        return SameDirectionLimits(
          max_groups=override.max_groups if override.max_groups is not None else self.limits.max_groups,
          max_cumulative_risk=(
            override.max_cumulative_risk if override.max_cumulative_risk is not None else self.limits.max_cumulative_risk
          ),
          max_symbol_risk=override.max_symbol_risk if override.max_symbol_risk is not None else self.limits.max_symbol_risk,
        )
    return self.limits


def _positive_number(value: Any, label: str) -> float | None:
  if value is None:
    return None
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
    raise EffectiveInstrumentError(f"{label} must be a positive number")
  return float(value)


def _same_direction_limits(node: Any, label: str) -> SameDirectionLimits:
  if not isinstance(node, Mapping):
    raise EffectiveInstrumentError(f"{label} must be a mapping")
  unknown = set(node) - {"max_groups", "max_cumulative_risk", "max_symbol_risk"}
  if unknown:
    raise EffectiveInstrumentError(f"{label} has unknown keys {sorted(unknown)}")
  groups = node.get("max_groups")
  if groups is not None and (isinstance(groups, bool) or not isinstance(groups, int) or groups < 1):
    raise EffectiveInstrumentError(f"{label}.max_groups must be an integer >= 1")
  limits = SameDirectionLimits(
    max_groups=groups,
    max_cumulative_risk=_positive_number(node.get("max_cumulative_risk"), f"{label}.max_cumulative_risk"),
    max_symbol_risk=_positive_number(node.get("max_symbol_risk"), f"{label}.max_symbol_risk"),
  )
  if limits.max_groups is None and limits.max_cumulative_risk is None and limits.max_symbol_risk is None:
    raise EffectiveInstrumentError(f"{label} declares no limit")
  return limits


def _same_direction_policy(
  instrument_id: str,
  exposure: Any,
) -> SameDirectionPolicy | None:
  """Parse ``exposure.same_direction``; absent = legacy behaviour (``None``)."""
  node = _plain(exposure) if exposure is not None else None
  policy = node.get("same_direction") if isinstance(node, Mapping) else None
  if policy is None:
    return None
  label = f"{instrument_id} exposure.same_direction"
  if not isinstance(policy, Mapping):
    raise EffectiveInstrumentError(f"{label} must be a mapping")
  allowed = {
    "max_groups", "max_cumulative_risk", "max_symbol_risk", "strategy_overrides",
    "risk_free_groups_count", "duplicate_thesis_block", "reservation_ttl_seconds",
    "snapshot_max_age_seconds",
  }
  unknown = set(policy) - allowed
  if unknown:
    raise EffectiveInstrumentError(f"{label} has unknown keys {sorted(unknown)}")
  limits = _same_direction_limits(
    {key: policy.get(key) for key in ("max_groups", "max_cumulative_risk", "max_symbol_risk")},
    label,
  )
  overrides: list[tuple[str, SameDirectionLimits]] = []
  raw_overrides = policy.get("strategy_overrides")
  if raw_overrides is None:
    raw_overrides = {}
  if not isinstance(raw_overrides, Mapping):
    raise EffectiveInstrumentError(f"{label}.strategy_overrides must be a mapping")
  for name, value in sorted(raw_overrides.items()):
    overrides.append((
      str(name).strip().lower().replace(" ", "_"),
      _same_direction_limits(value, f"{label}.strategy_overrides.{name}"),
    ))
  flags = {}
  for key, default in (("risk_free_groups_count", True), ("duplicate_thesis_block", True)):
    value = policy.get(key, default)
    if not isinstance(value, bool):
      raise EffectiveInstrumentError(f"{label}.{key} must be a boolean")
    flags[key] = value
  seconds = {}
  for key, default in (("reservation_ttl_seconds", 600), ("snapshot_max_age_seconds", 120)):
    value = policy.get(key, default)
    minimum = 180 if key == "reservation_ttl_seconds" else 10
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
      raise EffectiveInstrumentError(f"{label}.{key} must be an integer >= {minimum}")
    seconds[key] = value
  return SameDirectionPolicy(
    symbol=instrument_id,
    limits=limits,
    strategy_overrides=tuple(overrides),
    risk_free_groups_count=flags["risk_free_groups_count"],
    duplicate_thesis_block=flags["duplicate_thesis_block"],
    reservation_ttl_seconds=seconds["reservation_ttl_seconds"],
    snapshot_max_age_seconds=seconds["snapshot_max_age_seconds"],
  )


def _plain(value: Any) -> Any:
  if isinstance(value, ConfigNode):
    return {key: _plain(item) for key, item in value.items()}
  if isinstance(value, list):
    return [_plain(item) for item in value]
  return deepcopy(value)


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
  for key, value in overlay.items():
    if isinstance(value, Mapping) and isinstance(base.get(key), dict):
      _merge(base[key], value)
    else:
      base[key] = _plain(value)
  return base


def _get(mapping: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
  current: Any = mapping
  for part in dotted.split("."):
    if not isinstance(current, Mapping) or part not in current:
      return default
    current = current[part]
  return current


def _put(mapping: dict[str, Any], dotted: str, value: Any) -> None:
  parts = dotted.split(".")
  current = mapping
  for part in parts[:-1]:
    current = current.setdefault(part, {})
  current[parts[-1]] = _plain(value)


def _rollout(declaration: Mapping[str, Any]) -> InstrumentRollout:
  raw = declaration.get("rollout")
  if raw is None:
    raw = "live" if declaration.get("enabled", True) else "disabled"
  try:
    return InstrumentRollout(str(raw))
  except ValueError as exc:
    raise EffectiveInstrumentError(f"invalid instrument rollout {raw!r}") from exc


def _instrument_declaration(runtime: Any, symbol: str) -> tuple[str, dict[str, Any]]:
  registry = _plain(getattr(runtime, "instruments"))
  packs = _plain(getattr(runtime, "instrument_packs", {}))
  if not isinstance(registry, Mapping) or not isinstance(packs, Mapping):
    raise EffectiveInstrumentError("instruments.yml must define mappings")
  lookup: dict[str, str] = {}
  for instrument_id, raw in registry.items():
    if not isinstance(raw, Mapping):
      continue
    keys = {
      str(instrument_id).upper(),
      str(raw.get("canonical_symbol", "")).upper(),
      str(raw.get("broker_symbol", "")).upper(),
      *(str(item).upper() for item in raw.get("aliases", []) or []),
    }
    if str(raw.get("canonical_symbol", "")).upper() == "XAU":
      keys.add("XAUUSD")
    for key in keys - {""}:
      prior = lookup.get(key)
      if prior is not None and prior != instrument_id:
        raise EffectiveInstrumentError(f"ambiguous instrument symbol {key!r}")
      lookup[key] = str(instrument_id)
  instrument_id = lookup.get(str(symbol).strip().upper())
  if instrument_id is None:
    raise EffectiveInstrumentError(f"unknown instrument {symbol!r}")
  declaration = registry[instrument_id]
  if not isinstance(declaration, Mapping):
    raise EffectiveInstrumentError(f"invalid declaration for {instrument_id}")
  result: dict[str, Any] = {}
  pack_name = declaration.get("pack")
  if pack_name:
    pack = packs.get(str(pack_name))
    if not isinstance(pack, Mapping):
      raise EffectiveInstrumentError(f"unknown instrument pack {pack_name!r}")
    _merge(result, pack)
  _merge(result, declaration)
  return instrument_id, result


# Leaves a strategy-scoped targeting block may set (the same names, with the same meaning, as the instrument's ``targeting``); anything else is an error.
_SCOPED_TARGETING_KEYS = frozenset({
  "mode", "reward_risk", "target_r_multiples", "close_ratios", "breakeven_after_r", "entry_clips",
})


def strategy_key(name: Any) -> str:
  """The config key of a strategy: lower case, every run of non-alphanumerics one underscore ("Key Level" and "key_level" are both ``key_level``)."""
  return re.sub(r"[^a-z0-9]+", "_", str(name or "").casefold()).strip("_")


def _finite_number(value: Any, label: str, owner: str) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
    raise EffectiveInstrumentError(f"{owner}: {label} must be a finite number, got {value!r}")
  return float(value)


def _scoped_targeting_table(instrument_id: str, base: Mapping[str, Any], declared: Any) -> dict[str, dict[str, Any]]:
  """Validate ``execution.targeting_by_strategy`` and resolve each block over the instrument's ``targeting``.

  A block is a complete exit ladder for ONE strategy on this instrument: ``mode: fixed_rr`` (the only mode), ``target_r_multiples`` ascending and
  positive, ``reward_risk`` equal to the last multiple, ``close_ratios`` of the same length, positive and summing to 1, ``breakeven_after_r`` null or a
  positive number, optional ``entry_clips`` (otherwise the instrument's). Invalid blocks fail at configuration load, never at trade time. An absent
  table is the empty one: every strategy keeps the instrument's ``targeting``.
  """
  plain = _plain(declared) if declared else {}
  if not plain:
    return {}
  if not isinstance(plain, Mapping):
    raise EffectiveInstrumentError(f"{instrument_id}: execution.targeting_by_strategy must be a mapping")
  base_mode = base.get("mode")
  if str(getattr(base_mode, "value", base_mode)) != InstrumentTargetMode.FIXED_RR.value:
    raise EffectiveInstrumentError(f"{instrument_id}: targeting_by_strategy needs the instrument's targeting.mode to be fixed_rr")
  table: dict[str, dict[str, Any]] = {}
  for raw_key, block in plain.items():
    key = strategy_key(raw_key)
    owner = f"{instrument_id}: targeting_by_strategy.{raw_key}"
    if not key or key in table:
      raise EffectiveInstrumentError(f"{owner}: empty or duplicate strategy key")
    if not isinstance(block, Mapping):
      raise EffectiveInstrumentError(f"{owner} must be a mapping")
    unknown = sorted(set(block) - _SCOPED_TARGETING_KEYS)
    if unknown:
      raise EffectiveInstrumentError(f"{owner}: unknown leaves {unknown}")
    if str(block.get("mode", "fixed_rr")) != InstrumentTargetMode.FIXED_RR.value:
      raise EffectiveInstrumentError(f"{owner}: mode must be fixed_rr")
    multiples = block.get("target_r_multiples")
    ratios = block.get("close_ratios")
    if not isinstance(multiples, (list, tuple)) or not multiples:
      raise EffectiveInstrumentError(f"{owner}: target_r_multiples must be a non-empty list")
    if not isinstance(ratios, (list, tuple)) or len(ratios) != len(multiples):
      raise EffectiveInstrumentError(f"{owner}: close_ratios must have one ratio per target_r_multiples entry")
    multiples = tuple(_finite_number(v, "target_r_multiples entry", owner) for v in multiples)
    ratios = tuple(_finite_number(v, "close_ratios entry", owner) for v in ratios)
    if any(m <= 0 for m in multiples) or any(b <= a for a, b in zip(multiples, multiples[1:])):
      raise EffectiveInstrumentError(f"{owner}: target_r_multiples must be positive and strictly ascending")
    if any(r <= 0 for r in ratios) or abs(sum(ratios) - 1.0) > 1e-9:
      raise EffectiveInstrumentError(f"{owner}: close_ratios must be positive and sum to 1 (got {sum(ratios)})")
    reward_risk = _finite_number(block.get("reward_risk"), "reward_risk", owner)
    if abs(reward_risk - multiples[-1]) > 1e-9:
      raise EffectiveInstrumentError(f"{owner}: reward_risk {reward_risk} must equal the last target_r_multiples entry {multiples[-1]}")
    breakeven = block.get("breakeven_after_r")
    if breakeven is not None and _finite_number(breakeven, "breakeven_after_r", owner) <= 0:
      raise EffectiveInstrumentError(f"{owner}: breakeven_after_r must be null or positive")
    clips = block.get("entry_clips", base.get("entry_clips"))
    if clips is not None and (isinstance(clips, bool) or not isinstance(clips, int) or clips < 1):
      raise EffectiveInstrumentError(f"{owner}: entry_clips must be a positive integer")
    resolved = dict(base)
    resolved.update(
      mode=InstrumentTargetMode.FIXED_RR, reward_risk=reward_risk, target_r_multiples=multiples, close_ratios=ratios,
      breakeven_after_r=None if breakeven is None else float(breakeven),
    )
    if clips is not None:
      resolved["entry_clips"] = clips
    table[key] = resolved
  return table


class EffectiveInstrument:
  """Immutable-ish native view used by execution code."""

  def __init__(self, runtime: Any, symbol: str):
    instrument_id, raw = _instrument_declaration(runtime, symbol)
    rollout = _rollout(raw)
    contract = raw.get("contract") or {}
    if not isinstance(contract, Mapping):
      raise EffectiveInstrumentError(f"invalid contract for {instrument_id}")
    pip_size = float(contract.get("pip_size", 0))
    contract_units = float(contract.get("contract_units_per_lot", 0))
    if pip_size <= 0 or contract_units <= 0:
      raise EffectiveInstrumentError(f"{instrument_id} is missing contract units")
    self.identity = ConfigNode({
      "instrument_id": instrument_id,
      "canonical_symbol": raw.get("canonical_symbol", instrument_id),
      "broker_symbol": raw.get("broker_symbol", instrument_id),
      "aliases": tuple(raw.get("aliases", []) or []),
      "rollout": rollout,
      "timeframes": tuple(raw.get("timeframes", [])),
    })
    self.units = InstrumentUnits(
      pip_size=pip_size,
      price_digits=int(contract.get("price_digits", 0)),
      contract_units_per_lot=contract_units,
      pip_value_per_lot=float(contract.get("pip_value_per_lot") or pip_size * contract_units),
      volume_units_per_lot=int(contract.get("volume_units_per_lot") or round(contract_units * 100)),
      max_lots=float(contract.get("max_lots", 10.0)),
    )
    # Keep the native contract block available to callers that need a
    # broker-facing field (for example price digits) while ``units`` remains
    # the typed execution surface.  This is a view of YAML, not another
    # configuration source.
    self.contract = ConfigNode(contract)
    targeting = dict(raw.get("targeting", {}) or {})
    for sequence_key in ("target_r_multiples", "close_ratios"):
      if sequence_key in targeting and targeting[sequence_key] is not None:
        targeting[sequence_key] = tuple(targeting[sequence_key])
    # These are optional native targeting leaves.  Keep the resolved view
    # total for callers that legitimately ask whether trailing is configured;
    # absence means disabled, not a malformed instrument declaration.
    targeting.setdefault("trail_after_r", None)
    targeting.setdefault("trail_to_r", None)
    if "mode" in targeting:
      targeting["mode"] = InstrumentTargetMode(str(targeting["mode"]))
    self.targeting = ConfigNode(targeting)
    self.auto_entry = ConfigNode(raw.get("auto_entry", {}))
    # FX1: the pack's sizing block (``sizing: {mode: risk, risk_percent: N}``, or ``risk_percent_by_equity``). It was in instruments.yml since 2026-11-06 but never
    # reached the plan builder, which reads ``cfg.sizing``: every plan fell back to the owner equity table (x1.5 for FX), about 3% of equity instead of 2%.
    self.sizing = ConfigNode(_plain(raw.get("sizing")) or {})
    manual = dict(raw.get("manual", {}) or {})
    # Keep manual ladder metadata in the native instrument declaration while
    # sharing the explicitly declared targeting ladder when a manual section
    # omits the same values.
    manual.setdefault("target_r_multiples", targeting.get("target_r_multiples", []))
    manual.setdefault("target_close_ratios", targeting.get("close_ratios", []))
    manual.setdefault("tp1_close_fraction", None)
    if "entry_mode" in manual:
      manual["entry_mode"] = InstrumentEntryMode(str(manual["entry_mode"]))
    self.manual = ConfigNode(manual)
    self.policy_name = str(raw.get("policy") or ("xau_fixed_4r_v1" if instrument_id == "XAU" else "fx_fixed_2r_v1"))
    self.rollout = rollout
    self._raw = raw
    self._exposure = raw.get("exposure")

    analysis = _plain(getattr(runtime, "analysis"))
    execution = _plain(getattr(runtime, "execution"))
    auto_algo = _plain(getattr(runtime, "auto_algo"))
    for path, value in (raw.get("overrides") or {}).items():
      if not isinstance(path, str):
        continue
      root = path.split(".", 1)[0]
      target = {
        "analysis": analysis,
        "execution": execution,
        "auto_algo": auto_algo,
      }.get(root)
      if target is not None:
        _merge(target, {path.split(".", 1)[1]: value} if "." in path else value)
    envelope = raw.get("stop_envelope") or {}
    if envelope:
      for path, value in {
        "execution.reaction.stop_min_pips": envelope.get("min_pips"),
        "execution.reaction.stop_max_pips": envelope.get("max_pips"),
        "execution.stops.reaction.room_floor_pips": envelope.get("min_pips"),
        "execution.range.room_stop_floor_pips": envelope.get("min_pips"),
        "execution.stops.trend.minimum_pips": envelope.get("min_pips"),
        "execution.trend.stop_max_pips": envelope.get("max_pips"),
        "execution.stops.sl_distance": envelope.get("sl_distance"),
      }.items():
        if value is not None:
          _put(execution, path.removeprefix("execution."), value)
    scale = raw.get("price_scale") or {}
    if scale:
      _put(analysis, "levels.round_step", scale.get("round_step"))
      market_map = scale.get("market_map") or {}
      for key in ("change_min", "fallback_radius_price", "scalp_radius_price"):
        if key in market_map:
          _put(analysis, f"market_map.{key}", market_map[key])
      _put(analysis, "zones.confluence.merge_gap_price", scale.get("zone_merge_gap_price"))
      _put(analysis, "zones.merge_max_width", scale.get("zone_merge_max_width"))
      _put(auto_algo, "strategies.technique.fvg.entry_max_width_price", scale.get("fvg_entry_max_width_price"))
    lookbacks = _get(raw, "market_data.lookbacks", {})
    zones = _get(raw, "analysis.zones", _get(analysis, "zones", {}))
    self.market_data = ConfigNode({"lookbacks": lookbacks, "runtime": analysis})
    self.analysis = ConfigNode({"zones": zones, "runtime": analysis})
    self.execution = ConfigNode(execution)
    # KL12: an exit ladder for one strategy on this instrument (validated now, resolved over the instrument's targeting).
    self._targeting_by_strategy = {
      key: ConfigNode(value)
      for key, value in _scoped_targeting_table(instrument_id, targeting, _get(execution, "targeting_by_strategy", {})).items()
    }
    self.actionability = ConfigNode(_get(auto_algo, "actionability", {}))
    self.risk = ConfigNode(_get(auto_algo, "risk", {}))
    self.lifecycle = ConfigNode(_get(auto_algo, "lifecycle", {}))
    self.strategies = ConfigNode(_get(auto_algo, "strategies", {}))


  def targeting_for_strategy(self, strategy: Any) -> Any | None:
    """The exit ladder this instrument configures for ``strategy`` (``execution.targeting_by_strategy``), or ``None`` when it has none: the caller then
    uses the instrument's ``targeting``. The strategy is matched by key, so "Key Level" and "key_level" are the same."""
    return self._targeting_by_strategy.get(strategy_key(strategy))

  @property
  def entry_zone_min_width_pips(self) -> float:
    """The narrowest entry zone a non-scalping opportunity may be planned from (``stop_envelope.min_entry_zone_width_pips``); 0 when the instrument has none."""
    envelope = self._raw.get("stop_envelope") or {}
    try:
      value = float(envelope.get("min_entry_zone_width_pips") or 0.0)
    except (TypeError, ValueError):
      return 0.0
    return value if value > 0 else 0.0

  @property
  def opposite_position(self) -> OppositePositionPolicy:
    """Autonomous opposite-exposure policy; raises when undeclared/invalid."""
    return _opposite_position_policy(
      self.instrument_id, self._exposure, self.units.pip_size
    )

  @property
  def same_direction_policy(self) -> SameDirectionPolicy | None:
    """Same-direction exposure bound, or ``None`` when the instrument declares none."""
    return _same_direction_policy(self.instrument_id, self._exposure)

  @property
  def instrument_id(self) -> str:
    return str(self.identity.instrument_id)


def for_instrument(runtime: Any, symbol: str) -> EffectiveInstrument:
  return EffectiveInstrument(runtime, symbol)


def enabled_instruments(runtime: Any) -> tuple[str, ...]:
  registry = _plain(getattr(runtime, "instruments"))
  return tuple(sorted(
    key for key, raw in registry.items()
    if isinstance(raw, Mapping) and key != "instrument_packs" and _rollout(raw) is not InstrumentRollout.DISABLED
  ))


def live_instruments(runtime: Any) -> tuple[str, ...]:
  registry = _plain(getattr(runtime, "instruments"))
  return tuple(sorted(
    key for key, raw in registry.items()
    if isinstance(raw, Mapping) and key != "instrument_packs" and _rollout(raw) is InstrumentRollout.LIVE
  ))


def instrument_for_broker_symbol(runtime: Any, symbol: str) -> EffectiveInstrument:
  return for_instrument(runtime, symbol)


__all__ = [
  "EffectiveInstrument",
  "EffectiveInstrumentError",
  "InstrumentRollout",
  "InstrumentEntryMode",
  "InstrumentTargetMode",
  "OppositePositionPolicy",
  "SameDirectionLimits",
  "SameDirectionPolicy",
  "enabled_instruments",
  "for_instrument",
  "instrument_for_broker_symbol",
  "live_instruments",
]
