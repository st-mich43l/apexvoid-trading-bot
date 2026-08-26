"""Shared M1 + M5 scalp context loader (single entry for micro + context bars).

Both ZoneWatch technique and M1 scalp loops consume the same OHLC windows and
``ScalpContextSnapshot`` / micro shapes. Naming is ``scalp`` — not product-tag
``HFS``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.analysis.ohlc_source import RedisOHLCSource
from app.runtime.price_identity import pip_price_digits
from app.scalping.context import build_scalp_context_snapshot
from app.scalping.microstructure import build_micro_structure
from app.scalping.models import ScalpContextSnapshot


def _m5_atr(m5: pd.DataFrame, *, pip_size: float) -> float:
  if m5 is None or m5.empty or len(m5) < 2:
    return max(float(pip_size) * 50.0, float(pip_size))
  hi = m5["high"].astype(float).tail(14)
  lo = m5["low"].astype(float).tail(14)
  atr = float(hi.mean() - lo.mean())
  if atr <= 0 or atr != atr:
    return max(float(pip_size) * 50.0, float(pip_size))
  return atr


async def load_scalp_ohlc_windows(
  source: RedisOHLCSource,
  symbol: str,
  *,
  m1_bars: int = 120,
  m5_bars: int = 120,
  m15_bars: int = 120,
  h1_bars: int = 120,
) -> dict[str, pd.DataFrame]:
  """Load the standard scalp multi-TF window set from Redis OHLC."""
  m1 = await source.window(symbol, "M1", int(m1_bars))
  m5 = await source.window(symbol, "M5", int(m5_bars))
  m15 = await source.window(symbol, "M15", int(m15_bars))
  h1 = await source.window(symbol, "H1", int(h1_bars))
  return {"m1": m1, "m5": m5, "m15": m15, "h1": h1}


def build_scalp_context_and_micro(
  *,
  symbol: str,
  windows: dict[str, pd.DataFrame],
  price: float,
  pip_size: float,
  now: int,
  cfg: Any | None = None,
  htf_bias: str = "unknown",
  m5_structure: str = "range",
  regime: str = "range",
) -> tuple[ScalpContextSnapshot | None, Any]:
  """Build M5 context + M1 micro from a shared window dict.

  Returns ``(context, micro)``. Context may be None when inputs are insufficient.
  Micro is built whenever an M1 frame is present.
  """
  m1 = windows.get("m1")
  m5 = windows.get("m5")
  m15 = windows.get("m15")
  h1 = windows.get("h1")
  atr = _m5_atr(
    m5 if isinstance(m5, pd.DataFrame) else pd.DataFrame(),
    pip_size=pip_size,
  )
  context = build_scalp_context_snapshot(
    symbol=symbol,
    m5=m5 if isinstance(m5, pd.DataFrame) else pd.DataFrame(),
    m15=m15 if isinstance(m15, pd.DataFrame) else None,
    h1=h1 if isinstance(h1, pd.DataFrame) else None,
    price=float(price),
    pip_size=float(pip_size),
    atr=atr,
    now=int(now),
    cfg=cfg,
    htf_bias=htf_bias,
    m5_structure=m5_structure,
    regime=regime,
  )
  micro = None
  if isinstance(m1, pd.DataFrame):
    digits = int(
      getattr(
        getattr(cfg, "units", None),
        "price_digits",
        pip_price_digits(pip_size),
      )
      if cfg is not None
      else pip_price_digits(pip_size)
    )
    micro = build_micro_structure(
      m1,
      equal_tol=0.5 * float(pip_size),
      price_digits=digits,
    )
  return context, micro
