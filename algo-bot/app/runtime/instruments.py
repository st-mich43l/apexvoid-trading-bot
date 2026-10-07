"""Native YAML instrument composition and routing helpers."""

from __future__ import annotations

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
  between the incoming entry and EVERY existing opposite group.
  """

  symbol: str
  allowed: bool
  minimum_separation_pips: float | None
  pip_size: float


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
    return OppositePositionPolicy(instrument_id, True, float(minimum), pip_size)
  if minimum is not None:
    raise EffectiveInstrumentError(
      f"{instrument_id} blocks opposite positions; "
      "minimum_separation_pips must not be declared"
    )
  return OppositePositionPolicy(instrument_id, False, None, pip_size)


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
    self.actionability = ConfigNode(_get(auto_algo, "actionability", {}))
    self.risk = ConfigNode(_get(auto_algo, "risk", {}))
    self.lifecycle = ConfigNode(_get(auto_algo, "lifecycle", {}))
    self.strategies = ConfigNode(_get(auto_algo, "strategies", {}))


  @property
  def opposite_position(self) -> OppositePositionPolicy:
    """Autonomous opposite-exposure policy; raises when undeclared/invalid."""
    return _opposite_position_policy(
      self.instrument_id, self._exposure, self.units.pip_size
    )

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
  "enabled_instruments",
  "for_instrument",
  "instrument_for_broker_symbol",
  "live_instruments",
]
