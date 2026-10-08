"""Instrument-owned execution containment for Go opportunities.

An instrument may *observe* a Go strategy, or a structure timeframe, without
trading it: Go keeps producing, storing and arbitrating-out the opportunities,
but no StrategyMatch and therefore no TradePlan may exist for them. The two
leaves live under ``instruments.<SYMBOL>.overrides.execution.go_opportunity``:

* ``observe_only_strategies`` - catalog strategy ids.
* ``observe_only_structure_timeframes`` - timeframes whose structure a setup was
  built on when that is not its observation timeframe (the M15 supply/demand
  extension), carried as the typed ``structure_timeframe`` field.

The same predicate runs at match creation (``go_opportunity_policy``) and again
at plan admission (``worker._publish_trade_plan_v8``), so a match stored before
a containment change, or one reaching the worker by any other path, still cannot
become a plan.
"""

from __future__ import annotations

from typing import Any

from app.analysis_client.provenance import CATALOG_TAG

STRUCTURE_TIMEFRAME_TAG = "go_structure_tf:"
CONTAINED_STRATEGY = "execution_contained"
CONTAINED_STRUCTURE_TIMEFRAME = "execution_contained_structure_timeframe"

# Events produced before the typed ``structure_timeframe`` field existed carry
# the higher timeframe only as ``htf_zone_<tf>`` evidence. Read it as a fallback
# so a rolling deploy cannot let an old-format M15 event slip past containment.
_LEGACY_EVIDENCE_PREFIX = "htf_zone_"


def _go_opportunity_node(symbol: str) -> Any:
  from app.core.instrument_geometry import instrument_runtime

  try:
    return instrument_runtime(str(symbol).upper()).execution.go_opportunity
  except (AttributeError, KeyError):
    return None


def _names(symbol: str, leaf: str) -> frozenset[str]:
  node = _go_opportunity_node(symbol)
  try:
    names = getattr(node, leaf)
  except AttributeError:
    return frozenset()
  return frozenset(str(name) for name in (names or ()))


def observe_only_strategies(symbol: str) -> frozenset[str]:
  """Go strategies whose opportunities this instrument observes but never trades."""
  return _names(symbol, "observe_only_strategies")


def observe_only_structure_timeframes(symbol: str) -> frozenset[str]:
  """Structure timeframes this instrument observes but never trades."""
  return frozenset(name.upper() for name in _names(symbol, "observe_only_structure_timeframes"))


def structure_timeframe_of(structure_timeframe: str | None, evidence_codes: tuple[str, ...] = ()) -> str | None:
  """The typed structure timeframe, else the legacy ``htf_zone_<tf>`` evidence."""
  if structure_timeframe:
    return str(structure_timeframe).upper()
  for code in evidence_codes:
    if code.startswith(_LEGACY_EVIDENCE_PREFIX):
      return code[len(_LEGACY_EVIDENCE_PREFIX):].upper() or None
  return None


def containment_reason(symbol: str, catalog_id: str | None, structure_timeframe: str | None) -> str | None:
  """The decision reason when the instrument must not trade this, else None."""
  if catalog_id and catalog_id in observe_only_strategies(symbol):
    return CONTAINED_STRATEGY
  if structure_timeframe and structure_timeframe.upper() in observe_only_structure_timeframes(symbol):
    return CONTAINED_STRUCTURE_TIMEFRAME
  return None


def match_containment_reason(match: Any) -> str | None:
  """Containment of an already-built match, read from its provenance tags."""
  catalog_id = None
  structure_tf = None
  for tag in getattr(match, "tags", ()) or ():
    if tag.startswith(CATALOG_TAG):
      catalog_id = tag[len(CATALOG_TAG):]
    elif tag.startswith(STRUCTURE_TIMEFRAME_TAG):
      structure_tf = tag[len(STRUCTURE_TIMEFRAME_TAG):]
  return containment_reason(getattr(match, "symbol", ""), catalog_id, structure_tf)
