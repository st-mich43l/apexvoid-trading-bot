"""Shared price and pip units for the auto-trade Redis contract."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.core.symbols import pip_for

log = logging.getLogger(__name__)


def pip_size(symbol: str) -> float:
  """Pip size for a symbol, resolving broker aliases (XAUUSD -> XAU).

  Delegates to app.core.symbols.pip_for - the single canonical symbol
  registry - instead of keeping a second, independently-maintained pip
  table here. A symbol with no configured entry raises KeyError rather
  than silently returning a generic 1.0, which would otherwise be a 10x
  error in any room/target/sizing calculation downstream.
  """
  return pip_for(symbol)


# ---------------------------------------------------------------------------
# FX3: the pip value of a JPY-quoted pair follows the live USDJPY quote, the executor's rule (ctrader-engine PipValue.JpyQuotedUsdPerLot).
#
# The USD value of one pip of a pair quoted in JPY is pip size x contract size / USDJPY. A configured constant drifts with the rate (GBPJPY's 7.0 was set at 143 and
# is 11% high at 159). The executor sizes lots with the live value; the planner's money estimates (the risk leg's loss, how many entry legs the volume can fill, the
# group's risk amount) use the same one, so the plan and the executor agree. The configured ``contract.pip_value_per_lot`` stays the fallback whenever there is no
# usable quote, and every other pair (EURUSD, GBPUSD, XAU) is returned exactly as configured.

USDJPY_SPOT_KEY = "price:USDJPY:spot"  # the key the worker's _load_spot reads
USDJPY_QUOTE_MAX_AGE_SECONDS = 300  # the executor's PipValue.MaxQuoteAgeSeconds
_MIN_USDJPY = Decimal("50")  # a USDJPY outside 50-400 is a bad tick, not a rate
_MAX_USDJPY = Decimal("400")


def is_jpy_quoted(symbol: str) -> bool:
  """USDJPY, GBPJPY and any other six-letter pair quoted in JPY (the executor's ``PipValue.IsJpyQuoted``)."""
  text = str(symbol or "").strip().upper()
  return len(text) == 6 and text.endswith("JPY")


def jpy_quoted_pip_value(
  *, pip_size: float, contract_units_per_lot: float, bid: float, ask: float, quote_ts: float | None = None, now: float | None = None,
) -> float | None:
  """USD pip value per lot of a JPY-quoted pair from a USDJPY quote, rounded to 4 places; ``None`` when the quote is not usable (the caller keeps the configured value).

  Not usable: a non-positive input, ``ask < bid``, a mid outside 50-400, or a quote older than 300 s (a zero timestamp is not aged, as in the executor).
  """
  if not (pip_size > 0 and contract_units_per_lot > 0 and bid > 0 and ask >= bid):
    return None
  if quote_ts and quote_ts > 0 and (time.time() if now is None else now) - quote_ts > USDJPY_QUOTE_MAX_AGE_SECONDS:
    return None
  mid = (Decimal(str(bid)) + Decimal(str(ask))) / 2
  if mid < _MIN_USDJPY or mid > _MAX_USDJPY:
    return None
  value = Decimal(str(pip_size)) * Decimal(str(contract_units_per_lot)) / mid
  return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


async def live_pip_value_per_lot(client: Any, symbol: str, units: Any, *, now: float | None = None) -> float:
  """The pip value per lot the planner should estimate with: live for a JPY-quoted pair, the configured value otherwise or when no usable USDJPY quote exists."""
  configured = float(units.pip_value_per_lot)
  if not is_jpy_quoted(symbol):
    return configured
  try:
    raw = await client.get(USDJPY_SPOT_KEY)
    if raw is None:
      return configured
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    live = jpy_quoted_pip_value(
      pip_size=float(units.pip_size), contract_units_per_lot=float(units.contract_units_per_lot),
      bid=float(payload["bid"]), ask=float(payload["ask"]), quote_ts=float(payload["ts"]), now=now,
    )
  except Exception:  # noqa: BLE001 - an unreadable quote is "no quote": keep the configured value
    log.debug("usdjpy quote unreadable for the pip value of %s; using the configured value", symbol, exc_info=True)
    return configured
  return configured if live is None else live


async def effective_units(client: Any, symbol: str, units: Any, *, now: float | None = None) -> Any:
  """``units`` with the live pip value (a copy), or the very same object when nothing differs (every non-JPY pair)."""
  value = await live_pip_value_per_lot(client, symbol, units, now=now)
  if value == float(units.pip_value_per_lot):
    return units
  return replace(units, pip_value_per_lot=value)
