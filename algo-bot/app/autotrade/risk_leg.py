"""The XAU "trade-off" risk leg, declared by the planner.

Owner 2026-10-08: the algo bot defines every price on the card and the executor
follows. The executor used to inject this leg itself (``TradePlanRuntime``), so a
plan and its card showed two legs while the broker held three. The planner now
declares the leg in ``entry.risk_leg`` and the card prints it; the executor places
exactly what the plan says.

The leg rests ``pips_from_stop`` inside the stop at a fixed lot size chosen by
account equity (the table is ``execution.reaction_risk_leg`` in config/execution.yml,
pinned by contracts/autotrade/xau-ladder-spec.json).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.autotrade.trade_plan import (
  ENTRY_TYPE_LIMIT_LADDER,
  ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE,
  TradePlanEntry,
  TradePlanRiskLeg,
)
from app.autotrade.xau_ladder import risk_leg_lots, risk_leg_price

_RISK_LEG_SYMBOLS = frozenset({"XAU", "XAUUSD"})
_MULTI_LEG_TYPES = (ENTRY_TYPE_LIMIT_LADDER, ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE)


@dataclass(frozen=True)
class RiskLegConfig:
  enabled: bool
  lots: Decimal
  lots_below_equity_floor: Decimal
  equity_floor: Decimal
  pips_from_stop: Decimal


def risk_leg_config(cfg: Any) -> RiskLegConfig | None:
  """The configured risk-leg table, or None when the section is absent."""
  section = getattr(getattr(cfg, "execution", None), "reaction_risk_leg", None)
  if section is None:
    return None
  return RiskLegConfig(
    enabled=bool(section.enabled),
    lots=Decimal(str(section.lots)),
    lots_below_equity_floor=Decimal(str(section.lots_below_equity_floor)),
    equity_floor=Decimal(str(section.equity_floor)),
    pips_from_stop=Decimal(str(section.pips_from_stop)),
  )


async def load_account_equity(client: Any, symbol: str) -> float | None:
  """Live account equity from the executor snapshot; None when unavailable."""
  try:
    raw = await client.get(f"auto_trade:executor_snapshot:{symbol}")
    if raw is None:
      return None
    snapshot = json.loads(raw)
    equity = float(snapshot.get("account_equity"))
  except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
    return None
  return equity if equity > 0 else None


def plan_risk_leg(
  *,
  symbol: str,
  direction: str,
  entry: TradePlanEntry,
  stop_price: Decimal,
  pip_size: Decimal,
  digits: int,
  account_equity: float | None,
  cfg: Any,
) -> TradePlanRiskLeg | None:
  """The risk leg for a XAU multi-leg entry, or None when the plan has none."""
  config = risk_leg_config(cfg)
  if config is None or not config.enabled:
    return None
  if str(symbol).upper() not in _RISK_LEG_SYMBOLS:
    return None
  if entry.type not in _MULTI_LEG_TYPES or len(entry.legs) < 2:
    return None
  price = risk_leg_price(
    direction, stop_price,
    pips_from_stop=config.pips_from_stop, pip_size=pip_size, digits=digits,
  )
  lots = risk_leg_lots(
    account_equity,
    lots=config.lots,
    lots_below_equity_floor=config.lots_below_equity_floor,
    equity_floor=config.equity_floor,
  )
  return TradePlanRiskLeg(price=Decimal(str(price)), lots=lots)
