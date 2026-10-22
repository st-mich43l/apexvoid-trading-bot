"""Group result accounting: what a closed auto group actually returned, in R, and how that figure was obtained.

``auto_trade_results.result_pips`` is the highest take-profit archived (owner directive for ``/trade_stats``), not the trade's result; it
stays for compatibility and must not be used as realized performance. The trade's result is the executor's volume-weighted realized
pips (``realized_pips``). Net R is that over the volume-weighted INITIAL risk of the same filled legs (``weighted_risk_pips``): each
leg's filled volume times the distance from its own fill to the original stop. Normalising by one reference fill instead reads a full
stop that includes the small reaction risk leg as less than one R.

Every figure carries the method that produced it, so a fill-derived result is never mistaken for an estimate:

* ``fills_v1``                 net R from the executor's weighted pips and weighted initial risk (both over the filled legs).
* ``fill_record_risk``         the executor's weighted realized pips over ``stop_pips``, the initial risk the fill record stored for the
                               group. Validated on the 96 full stops closed after the loss was booked: realized / stop_pips averages
                               -0.99 (XAU, sd 0.03) and -0.99 (FX); against ``risk_pips`` (one reference fill, the risk leg excluded)
                               XAU reads -0.85. Pips and risk come from the same fills, but the pips are the executor's, not re-derived.
* ``legacy_loss_unbooked``     a full stop with ``realized_pips`` exactly 0: the executor dropped the stop-out loss until 2026-10-14
                               (commit 9a4b9749). The loss is not in the data; -1 R is an assumption, not a measurement.
* ``reconstructed_ladder_v1``  estimated from ``booked_tp_count`` and the instrument's ladder (XAU 40/20/20/20 at 1R-4R, FX 50/50 at
                               1R-2R, stop to break-even after TP1). No fill data.
* ``legacy_highest_tp``        only ``result_pips`` / ``stop_pips`` exists: the highest TP archived, an overstatement.

Nothing here writes to history. Historical rows keep their stored values; ``estimate`` is a read-side function for reports.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

FILLS_V1 = "fills_v1"
FILL_RECORD_RISK = "fill_record_risk"
LEGACY_LOSS_UNBOOKED = "legacy_loss_unbooked"
RECONSTRUCTED_LADDER_V1 = "reconstructed_ladder_v1"
LEGACY_HIGHEST_TP = "legacy_highest_tp"

_XAU_LADDER = ((0.4, 1.0), (0.2, 2.0), (0.2, 3.0), (0.2, 4.0))
_FX_LADDER = ((0.5, 1.0), (0.5, 2.0))


def _number(value: Any) -> float | None:
  if value is None or isinstance(value, bool):
    return None
  try:
    out = float(value)
  except (TypeError, ValueError):
    return None
  return out if math.isfinite(out) else None


def net_r(volume_weighted_pips: Any, weighted_risk_pips: Any) -> float | None:
  """Net R of a group: weighted realized pips over weighted initial risk pips; None unless both are finite and the risk is positive."""
  pips, risk = _number(volume_weighted_pips), _number(weighted_risk_pips)
  if pips is None or risk is None or risk <= 0:
    return None
  return pips / risk


def from_close_event(event: Mapping[str, Any]) -> tuple[float | None, float | None, str | None]:
  """(weighted_risk_pips, net_r, result_method) for a terminal executor event; (None, None, None) when the event cannot support them."""
  risk = _number(event.get("volume_weighted_risk_pips"))
  if risk is not None and risk <= 0:
    risk = None
  value = net_r(event.get("volume_weighted_pips"), risk)
  return risk, value, (FILLS_V1 if value is not None else None)


def ladder_r(symbol: str, booked_tp_count: int | None) -> float:
  """R of a group that reached ``booked_tp_count`` ladder targets and then stopped at break-even."""
  ladder = _XAU_LADDER if str(symbol).upper() == "XAU" else _FX_LADDER
  return sum(share * multiple for share, multiple in ladder[: max(0, int(booked_tp_count or 0))])


def estimate(row: Mapping[str, Any]) -> tuple[float | None, str]:
  """Best available R for a stored row and the method it came from. Historical rows are never rewritten; this only reads them."""
  net = _number(row.get("net_r"))
  if net is not None and row.get("result_method") == FILLS_V1:
    return net, FILLS_V1
  exit_path = row.get("exit_path")
  realized, stop = _number(row.get("realized_pips")), _number(row.get("stop_pips"))
  if exit_path == "full_stop":
    if realized is not None and realized != 0 and stop and stop > 0:
      return realized / stop, FILL_RECORD_RISK
    if realized == 0:
      return -1.0, LEGACY_LOSS_UNBOOKED
    return -1.0, RECONSTRUCTED_LADDER_V1
  if realized is not None and stop and stop > 0:
    return realized / stop, FILL_RECORD_RISK
  if exit_path:
    return ladder_r(str(row.get("symbol") or "XAU"), _int(row.get("booked_tp_count"))), RECONSTRUCTED_LADDER_V1
  result = _number(row.get("result_pips"))
  if result is not None and stop and stop > 0:
    return result / stop, LEGACY_HIGHEST_TP
  return None, LEGACY_HIGHEST_TP


def _int(value: Any) -> int | None:
  number = _number(value)
  return None if number is None else int(number)
