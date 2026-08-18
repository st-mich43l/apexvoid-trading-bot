"""Instrument-scoped view of the canonical Python runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InstrumentRuntimeView:
  """Canonical domains after applying one instrument's sparse overrides."""

  identity: Any
  units: Any
  targeting: Any
  market_data: Any
  analysis: Any
  strategies: Any
  actionability: Any
  execution: Any
  risk: Any
  lifecycle: Any
  policy_name: str
  delivery: Any
  contract: Any
  runtime: Any
  manual_algo: Any
  instruments: Any


def instrument_runtime_view(
  symbol: str,
  root: Any | None = None,
) -> InstrumentRuntimeView:
  """Resolve ``symbol`` once and expose the normal runtime domain shape.

  ``EffectiveInstrumentConfig`` deliberately wraps the two shared domains
  that also own instrument-local metadata (``analysis`` and ``market_data``).
  Most strategy code consumes the ordinary root shape, so this adapter keeps
  those callers instrument-aware without teaching each one about composition.
  """
  if root is None:
    from app.core.config import runtime_config

    root = runtime_config
  effective = root.for_instrument(symbol)
  return InstrumentRuntimeView(
    identity=effective.identity,
    units=effective.units,
    targeting=effective.targeting,
    market_data=effective.market_data.runtime,
    analysis=effective.analysis.runtime,
    strategies=effective.strategies,
    actionability=effective.actionability,
    execution=effective.execution,
    risk=effective.risk,
    lifecycle=effective.lifecycle,
    policy_name=effective.policy_name,
    delivery=root.delivery,
    contract=root.contract,
    runtime=root.runtime,
    manual_algo=root.manual_algo,
    instruments=root.instruments,
  )
