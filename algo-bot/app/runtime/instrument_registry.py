"""Canonical instrument runtime registry built from resolved configuration.

Does not read ENV, parse YAML, or rebuild effective policies — it only
organizes already-resolved ``EffectiveInstrumentConfig`` instances.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from pydantic import ConfigDict

from app.configuration.effective_instrument import (
  EffectiveInstrumentConfig,
  EffectiveInstrumentError,
  build_effective_instrument,
  list_enabled_instrument_ids,
)
from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.instruments import InstrumentRollout
from app.runtime.rollout_gates import (
  permits_analysis,
  permits_broker_execution,
  permits_candidate_publication,
  permits_feed,
)


class InstrumentRuntimeError(ValueError):
  """Fail-closed instrument routing error."""


# EffectiveInstrumentConfig already owns the required domain surface.
InstrumentRuntimeContext = EffectiveInstrumentConfig


class InstrumentRuntimeRegistry(FrozenConfigModel):
  """Immutable registry of instrument runtimes keyed by instrument id."""

  model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

  by_id: Mapping[str, InstrumentRuntimeContext]
  alias_to_id: Mapping[str, str]

  def get(self, symbol: str) -> InstrumentRuntimeContext:
    key = symbol.strip().upper()
    instrument_id = self.alias_to_id.get(key)
    if instrument_id is None:
      raise InstrumentRuntimeError(f"unknown instrument symbol {symbol!r}")
    return self.by_id[instrument_id]

  def try_get(self, symbol: str) -> InstrumentRuntimeContext | None:
    try:
      return self.get(symbol)
    except InstrumentRuntimeError:
      return None

  def all(self) -> tuple[InstrumentRuntimeContext, ...]:
    return tuple(self.by_id[key] for key in sorted(self.by_id))

  def feed_instruments(self) -> tuple[InstrumentRuntimeContext, ...]:
    return tuple(ctx for ctx in self.all() if permits_feed(ctx.rollout))

  def analysis_instruments(self) -> tuple[InstrumentRuntimeContext, ...]:
    return tuple(ctx for ctx in self.all() if permits_analysis(ctx.rollout))

  def executable_instruments(self) -> tuple[InstrumentRuntimeContext, ...]:
    return tuple(
      ctx for ctx in self.all() if permits_candidate_publication(ctx.rollout)
    )

  def live_instruments(self) -> tuple[InstrumentRuntimeContext, ...]:
    return tuple(
      ctx for ctx in self.all() if permits_broker_execution(ctx.rollout)
    )

  def scanner_symbols(
    self,
    *,
    compatibility_filter: Iterable[str] | None = None,
  ) -> tuple[str, ...]:
    """Deterministic analysis set: rollout ∩ CSV filter ∩ enabled runtimes."""
    analysis = {
      ctx.identity.canonical_symbol: ctx for ctx in self.analysis_instruments()
    }
    if compatibility_filter is None:
      return tuple(sorted(analysis))
    allowed = {item.strip().upper() for item in compatibility_filter if item.strip()}
    return tuple(sorted(symbol for symbol in analysis if symbol in allowed))


def _build_alias_map(
  contexts: Mapping[str, InstrumentRuntimeContext],
) -> dict[str, str]:
  mapping: dict[str, str] = {}
  for instrument_id, ctx in contexts.items():
    keys = {
      instrument_id.upper(),
      ctx.identity.canonical_symbol.upper(),
      ctx.identity.broker_symbol.upper(),
      *(alias.upper() for alias in ctx.identity.aliases),
    }
    # Preserve XAUUSD → XAU for the current feed mapping.
    if ctx.identity.canonical_symbol.upper() == "XAU":
      keys.add("XAUUSD")
    for key in keys:
      prior = mapping.get(key)
      if prior is not None and prior != instrument_id:
        raise InstrumentRuntimeError(
          f"duplicate alias {key!r} maps to both {prior!r} and {instrument_id!r}"
        )
      mapping[key] = instrument_id
  return mapping


def build_instrument_runtime_registry(
  runtime_config: object,
  *,
  instrument_ids: Iterable[str] | None = None,
  resolution_trace: object | None = None,
) -> InstrumentRuntimeRegistry:
  """Construct the registry from a resolved PythonRuntimeConfig-like root."""
  if instrument_ids is None:
    try:
      ids = list_enabled_instrument_ids(runtime_config)  # type: ignore[arg-type]
    except EffectiveInstrumentError as exc:
      raise InstrumentRuntimeError(str(exc)) from exc
    # Include disabled instruments that are still declared so routing can
    # reject them explicitly rather than treating them as unknown.
    declared = getattr(runtime_config, "instruments", None)
    if declared is not None and hasattr(declared, "root"):
      ids = tuple(sorted({*ids, *declared.root.keys()}))
  else:
    ids = tuple(sorted({item.strip().upper() for item in instrument_ids}))

  contexts: dict[str, InstrumentRuntimeContext] = {}
  for instrument_id in ids:
    try:
      contexts[instrument_id] = build_effective_instrument(
        runtime_config,  # type: ignore[arg-type]
        instrument_id,
        resolution_trace=resolution_trace,  # type: ignore[arg-type]
      )
    except EffectiveInstrumentError as exc:
      # Disabled instruments without full units may still be declared; skip
      # only when rollout resolves to disabled and units are absent.
      instrument = runtime_config.instruments.root.get(instrument_id)  # type: ignore[attr-defined]
      if instrument is not None:
        from app.configuration.models.instruments import effective_rollout

        if effective_rollout(instrument) is InstrumentRollout.DISABLED:
          continue
      raise InstrumentRuntimeError(str(exc)) from exc

  if not contexts:
    raise InstrumentRuntimeError("instrument runtime registry is empty")

  return InstrumentRuntimeRegistry(
    by_id=contexts,
    alias_to_id=_build_alias_map(contexts),
  )
