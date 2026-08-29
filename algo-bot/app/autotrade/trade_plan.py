"""TradePlan V8 — Python's sole trade-planning contract.

V6 (`TradeCandidate`, `auto_trade_candidate_contract_version = 6`) let both
Python and C# resolve an execution route and compute a protective stop, then
cross-validate the two independently derived answers. TradePlan V8 is the
live contract (see `docs/adr-trade-plan-v8-cutover.md`): Python declares a
single, complete, versioned plan — exact entry instruction, absolute stop,
and absolute targets; C# consumes the plan and never recomputes any of it.

There is no `planned_*` / `final_*` / `base_*` field family here, and no
counterpart in `ctrader-engine` that independently derives a route or a stop
to compare against these values. `TradePlan.validate()` only checks
execution-safety shape (numbers finite, stop on the correct side, targets
ordered) — it never re-derives the *values* themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

TRADE_PLAN_VERSION = 8
TRADE_PLAN_SUPPORTED_VERSIONS = frozenset({8})

ENTRY_TYPE_MARKET_WATCH = "market_watch"
ENTRY_TYPE_MARKET = "market"
ENTRY_TYPE_SINGLE_LIMIT = "single_limit"
ENTRY_TYPE_LIMIT_LADDER = "limit_ladder"
ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE = "market_with_limit_scale"
ENTRY_TYPES = (
  ENTRY_TYPE_MARKET_WATCH,
  ENTRY_TYPE_MARKET,
  ENTRY_TYPE_SINGLE_LIMIT,
  ENTRY_TYPE_LIMIT_LADDER,
  ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE,
)

ORDER_TYPE_MARKET = "market"
ORDER_TYPE_LIMIT = "limit"
ORDER_TYPES = (ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT)

DIRECTIONS = ("BUY", "SELL")


class TradePlanError(ValueError):
  """Raised when a TradePlan V8 payload is structurally invalid.

  This is a *shape* error (missing/malformed field, stop on the wrong side,
  targets out of order) — never a "C# disagrees with the derived value"
  error, because C# never derives a competing value.
  """


def _require(data: Mapping[str, Any], name: str) -> Any:
  if name not in data or data[name] is None:
    raise TradePlanError(f"{name} is required")
  return data[name]


def _decimal(value: Any, name: str) -> Decimal:
  try:
    result = Decimal(str(value))
  except Exception as exc:
    raise TradePlanError(f"{name} is not a valid number: {value!r}") from exc
  if not result.is_finite():
    raise TradePlanError(f"{name} must be finite: {value!r}")
  return result


def _direction(data: Mapping[str, Any], name: str = "direction") -> str:
  value = str(_require(data, name))
  if value not in DIRECTIONS:
    raise TradePlanError(f"{name} must be one of {DIRECTIONS}: {value!r}")
  return value


@dataclass(frozen=True)
class TradePlanAnalysis:
  strategy: str
  strategy_family: str
  direction: str
  context_timeframes: tuple[str, ...]
  formation_timeframe: str
  confirmation_timeframe: str
  formation_bar_ts: int
  confirmation_bar_ts: int
  score: float
  confluence: int
  bias: str
  regime: str
  reasons: tuple[str, ...] = ()
  tags: tuple[str, ...] = ()

  def to_dict(self) -> dict:
    return {
      "strategy": self.strategy,
      "strategy_family": self.strategy_family,
      "direction": self.direction,
      "context_timeframes": list(self.context_timeframes),
      "formation_timeframe": self.formation_timeframe,
      "confirmation_timeframe": self.confirmation_timeframe,
      "formation_bar_ts": self.formation_bar_ts,
      "confirmation_bar_ts": self.confirmation_bar_ts,
      "score": self.score,
      "confluence": self.confluence,
      "bias": self.bias,
      "regime": self.regime,
      "reasons": list(self.reasons),
      "tags": list(self.tags),
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanAnalysis":
    return cls(
      strategy=str(_require(data, "strategy")),
      strategy_family=str(_require(data, "strategy_family")),
      direction=_direction(data),
      context_timeframes=tuple(data.get("context_timeframes") or ()),
      formation_timeframe=str(_require(data, "formation_timeframe")),
      confirmation_timeframe=str(_require(data, "confirmation_timeframe")),
      formation_bar_ts=int(_require(data, "formation_bar_ts")),
      confirmation_bar_ts=int(_require(data, "confirmation_bar_ts")),
      score=float(data.get("score", 0.0)),
      confluence=int(data.get("confluence", 0)),
      bias=str(_require(data, "bias")),
      regime=str(_require(data, "regime")),
      reasons=tuple(data.get("reasons") or ()),
      tags=tuple(data.get("tags") or ()),
    )


@dataclass(frozen=True)
class TradePlanSourceStructure:
  structure_id: str
  kind: str
  timeframe: str
  low: Decimal
  high: Decimal
  invalidation_price: Decimal

  def to_dict(self) -> dict:
    return {
      "structure_id": self.structure_id,
      "kind": self.kind,
      "timeframe": self.timeframe,
      "low": str(self.low),
      "high": str(self.high),
      "invalidation_price": str(self.invalidation_price),
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanSourceStructure":
    low = _decimal(_require(data, "low"), "source_structure.low")
    high = _decimal(_require(data, "high"), "source_structure.high")
    if low >= high:
      raise TradePlanError("source_structure.low must be below source_structure.high")
    return cls(
      structure_id=str(_require(data, "structure_id")),
      kind=str(_require(data, "kind")),
      timeframe=str(_require(data, "timeframe")),
      low=low,
      high=high,
      invalidation_price=_decimal(
        _require(data, "invalidation_price"), "source_structure.invalidation_price",
      ),
    )


@dataclass(frozen=True)
class TradePlanEntryLeg:
  leg_id: str
  price: Decimal
  volume_ratio: Decimal
  # Optional: "market" | "limit". Required semantically for
  # market_with_limit_scale (L1 market, L2 limit). Omitted on classic
  # limit_ladder legs so the executor may use marketable-limit detection.
  order_type: str | None = None

  def to_dict(self) -> dict:
    payload = {
      "leg_id": self.leg_id,
      "price": str(self.price),
      "volume_ratio": str(self.volume_ratio),
    }
    if self.order_type is not None:
      payload["order_type"] = self.order_type
    return payload

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanEntryLeg":
    ratio = _decimal(_require(data, "volume_ratio"), "entry.legs[].volume_ratio")
    if ratio <= 0:
      raise TradePlanError(f"entry.legs[].volume_ratio must be positive: {ratio}")
    order_type = data.get("order_type")
    if order_type is not None:
      order_type = str(order_type)
      if order_type not in ORDER_TYPES:
        raise TradePlanError(
          f"entry.legs[].order_type must be one of {ORDER_TYPES}: {order_type!r}",
        )
    return cls(
      leg_id=str(_require(data, "leg_id")),
      price=_decimal(_require(data, "price"), "entry.legs[].price"),
      volume_ratio=ratio,
      order_type=order_type,
    )


@dataclass(frozen=True)
class TradePlanEntry:
  type: str
  expires_at: int
  zone_low: Decimal | None = None
  zone_high: Decimal | None = None
  activation: str | None = None
  price_side: str | None = None
  max_spread_ticks: int | None = None
  max_slippage_ticks: int | None = None
  order_price: Decimal | None = None
  legs: tuple[TradePlanEntryLeg, ...] = ()

  def to_dict(self) -> dict:
    return {
      "type": self.type,
      "zone_low": None if self.zone_low is None else str(self.zone_low),
      "zone_high": None if self.zone_high is None else str(self.zone_high),
      "activation": self.activation,
      "price_side": self.price_side,
      "max_spread_ticks": self.max_spread_ticks,
      "max_slippage_ticks": self.max_slippage_ticks,
      "order_price": None if self.order_price is None else str(self.order_price),
      "expires_at": self.expires_at,
      "legs": [leg.to_dict() for leg in self.legs],
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanEntry":
    entry_type = str(_require(data, "type"))
    if entry_type not in ENTRY_TYPES:
      raise TradePlanError(f"entry.type must be one of {ENTRY_TYPES}: {entry_type!r}")

    zone_low = data.get("zone_low")
    zone_high = data.get("zone_high")
    zone_low_d = None if zone_low is None else _decimal(zone_low, "entry.zone_low")
    zone_high_d = None if zone_high is None else _decimal(zone_high, "entry.zone_high")
    if zone_low_d is not None and zone_high_d is not None and zone_low_d >= zone_high_d:
      raise TradePlanError("entry.zone_low must be below entry.zone_high")

    order_price = data.get("order_price")
    order_price_d = None if order_price is None else _decimal(order_price, "entry.order_price")

    legs = tuple(TradePlanEntryLeg.from_dict(leg) for leg in data.get("legs") or ())

    if entry_type == ENTRY_TYPE_MARKET_WATCH:
      if zone_low_d is None or zone_high_d is None:
        raise TradePlanError("market_watch entry requires zone_low and zone_high")
      if not _require(data, "activation"):
        raise TradePlanError("market_watch entry requires activation")
      if str(_require(data, "price_side")) not in ("bid", "ask"):
        raise TradePlanError("market_watch entry.price_side must be 'bid' or 'ask'")
    elif entry_type == ENTRY_TYPE_MARKET:
      if order_price_d is None:
        raise TradePlanError("market entry requires order_price")
    elif entry_type == ENTRY_TYPE_SINGLE_LIMIT:
      if order_price_d is None:
        raise TradePlanError("single_limit entry requires order_price")
    elif entry_type == ENTRY_TYPE_LIMIT_LADDER:
      if not legs:
        raise TradePlanError("limit_ladder entry requires at least one leg")
      total_ratio = sum(leg.volume_ratio for leg in legs)
      if abs(total_ratio - Decimal("1")) > Decimal("0.0001"):
        raise TradePlanError(
          f"limit_ladder entry.legs volume_ratio must sum to 1.0, got {total_ratio}",
        )
    elif entry_type == ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE:
      if len(legs) < 2:
        raise TradePlanError(
          "market_with_limit_scale entry requires at least two legs",
        )
      total_ratio = sum(leg.volume_ratio for leg in legs)
      if abs(total_ratio - Decimal("1")) > Decimal("0.0001"):
        raise TradePlanError(
          "market_with_limit_scale entry.legs volume_ratio must sum to 1.0, "
          f"got {total_ratio}",
        )
      first_type = legs[0].order_type or ORDER_TYPE_MARKET
      second_type = legs[1].order_type or ORDER_TYPE_LIMIT
      if first_type != ORDER_TYPE_MARKET:
        raise TradePlanError(
          "market_with_limit_scale L1 order_type must be market",
        )
      if second_type != ORDER_TYPE_LIMIT:
        raise TradePlanError(
          "market_with_limit_scale L2 order_type must be limit",
        )

    return cls(
      type=entry_type,
      zone_low=zone_low_d,
      zone_high=zone_high_d,
      activation=data.get("activation"),
      price_side=data.get("price_side"),
      max_spread_ticks=data.get("max_spread_ticks"),
      max_slippage_ticks=data.get("max_slippage_ticks"),
      order_price=order_price_d,
      expires_at=int(_require(data, "expires_at")),
      legs=legs,
    )

  def entry_prices(self) -> tuple[Decimal, ...]:
    """Every price at which this entry could actually fill.

    Used only to validate the stop is on the correct side of every possible
    entry price — never to derive a *new* price.
    """
    if self.type == ENTRY_TYPE_MARKET_WATCH:
      assert self.zone_low is not None and self.zone_high is not None
      return (self.zone_low, self.zone_high)
    if self.type in (ENTRY_TYPE_MARKET, ENTRY_TYPE_SINGLE_LIMIT):
      assert self.order_price is not None
      return (self.order_price,)
    return tuple(leg.price for leg in self.legs)


@dataclass(frozen=True)
class TradePlanStop:
  type: str
  price: Decimal
  source: str
  structure_id: str | None = None
  reason: str = ""

  def to_dict(self) -> dict:
    return {
      "type": self.type,
      "price": str(self.price),
      "source": self.source,
      "structure_id": self.structure_id,
      "reason": self.reason,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanStop":
    stop_type = str(_require(data, "type"))
    if stop_type != "absolute":
      raise TradePlanError(f"stop.type must be 'absolute': {stop_type!r}")
    price = _decimal(_require(data, "price"), "stop.price")
    if price <= 0:
      raise TradePlanError(f"stop.price must be positive: {price}")
    return cls(
      type=stop_type,
      price=price,
      source=str(_require(data, "source")),
      structure_id=data.get("structure_id"),
      reason=str(data.get("reason", "")),
    )


@dataclass(frozen=True)
class TradePlanTarget:
  target_id: str
  type: str
  price: Decimal
  close_ratio: Decimal

  def to_dict(self) -> dict:
    return {
      "target_id": self.target_id,
      "type": self.type,
      "price": str(self.price),
      "close_ratio": str(self.close_ratio),
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanTarget":
    target_type = str(_require(data, "type"))
    if target_type != "absolute":
      raise TradePlanError(f"target.type must be 'absolute': {target_type!r}")
    ratio = _decimal(_require(data, "close_ratio"), "target.close_ratio")
    if ratio <= 0 or ratio > 1:
      raise TradePlanError(f"target.close_ratio must be in (0, 1]: {ratio}")
    return cls(
      target_id=str(_require(data, "target_id")),
      type=target_type,
      price=_decimal(_require(data, "price"), "target.price"),
      close_ratio=ratio,
    )


@dataclass(frozen=True)
class TradePlanRisk:
  risk_percent: Decimal
  risk_multiplier: Decimal
  max_volume: int
  max_group_risk_percent: Decimal

  def to_dict(self) -> dict:
    return {
      "risk_percent": str(self.risk_percent),
      "risk_multiplier": str(self.risk_multiplier),
      "max_volume": self.max_volume,
      "max_group_risk_percent": str(self.max_group_risk_percent),
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanRisk":
    risk_percent = _decimal(_require(data, "risk_percent"), "risk.risk_percent")
    if risk_percent <= 0:
      raise TradePlanError(f"risk.risk_percent must be positive: {risk_percent}")
    return cls(
      risk_percent=risk_percent,
      risk_multiplier=_decimal(
        data.get("risk_multiplier", "1.0"), "risk.risk_multiplier",
      ),
      max_volume=int(_require(data, "max_volume")),
      max_group_risk_percent=_decimal(
        _require(data, "max_group_risk_percent"), "risk.max_group_risk_percent",
      ),
    )


@dataclass(frozen=True)
class TradePlanSizing:
  mode: str
  table_version: str
  entry_distribution: str
  leg_ratios: tuple[Decimal, ...] = ()

  def to_dict(self) -> dict:
    return {
      "mode": self.mode,
      "table_version": self.table_version,
      "entry_distribution": self.entry_distribution,
      "leg_ratios": [str(ratio) for ratio in self.leg_ratios],
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanSizing":
    mode = str(_require(data, "mode"))
    ratios = tuple(
      _decimal(value, "sizing.leg_ratios[]")
      for value in data.get("leg_ratios") or ()
    )
    return cls(
      mode=mode,
      table_version=str(_require(data, "table_version")),
      entry_distribution=str(_require(data, "entry_distribution")),
      leg_ratios=ratios,
    )


@dataclass(frozen=True)
class TradePlanManagement:
  be_after_target_id: str | None
  be_buffer_ticks: int
  never_worsen_stop: bool = True
  trail_after_target_id: str | None = None
  trail_to_target_id: str | None = None

  def to_dict(self) -> dict:
    return {
      "be_after_target_id": self.be_after_target_id,
      "be_buffer_ticks": self.be_buffer_ticks,
      "never_worsen_stop": self.never_worsen_stop,
      "trail_after_target_id": self.trail_after_target_id,
      "trail_to_target_id": self.trail_to_target_id,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanManagement":
    return cls(
      be_after_target_id=data.get("be_after_target_id"),
      be_buffer_ticks=int(data.get("be_buffer_ticks", 0)),
      never_worsen_stop=bool(data.get("never_worsen_stop", True)),
      trail_after_target_id=data.get("trail_after_target_id"),
      trail_to_target_id=data.get("trail_to_target_id"),
    )


@dataclass(frozen=True)
class TradePlanExecutionPolicy:
  allow_market: bool = True
  allow_limit: bool = True
  allow_partial_fill: bool = True
  cancel_on_expiry: bool = True

  def to_dict(self) -> dict:
    return {
      "allow_market": self.allow_market,
      "allow_limit": self.allow_limit,
      "allow_partial_fill": self.allow_partial_fill,
      "cancel_on_expiry": self.cancel_on_expiry,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanExecutionPolicy":
    return cls(
      allow_market=bool(data.get("allow_market", True)),
      allow_limit=bool(data.get("allow_limit", True)),
      allow_partial_fill=bool(data.get("allow_partial_fill", True)),
      cancel_on_expiry=bool(data.get("cancel_on_expiry", True)),
    )


@dataclass(frozen=True)
class TradePlanProvenance:
  analysis_engine_version: str
  market_map_id: str
  config_fingerprint: str
  confirmation_source: str = ""
  confirmation_bar_ts: int | None = None
  zone_episode_id: str | None = None

  def to_dict(self) -> dict:
    return {
      "analysis_engine_version": self.analysis_engine_version,
      "market_map_id": self.market_map_id,
      "config_fingerprint": self.config_fingerprint,
      "confirmation_source": self.confirmation_source,
      "confirmation_bar_ts": self.confirmation_bar_ts,
      "zone_episode_id": self.zone_episode_id,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlanProvenance":
    return cls(
      analysis_engine_version=str(data.get("analysis_engine_version", "")),
      market_map_id=str(data.get("market_map_id", "")),
      config_fingerprint=str(data.get("config_fingerprint", "")),
      confirmation_source=str(data.get("confirmation_source", "")),
      confirmation_bar_ts=(
        None
        if data.get("confirmation_bar_ts") is None
        else int(data["confirmation_bar_ts"])
      ),
      zone_episode_id=(
        None
        if data.get("zone_episode_id") is None
        else str(data["zone_episode_id"])
      ),
    )


@dataclass(frozen=True)
class TradePlan:
  plan_id: str
  thesis_id: str
  setup_id: str
  symbol: str
  created_at: int
  expires_at: int
  analysis: TradePlanAnalysis
  source_structure: TradePlanSourceStructure
  entry: TradePlanEntry
  stop: TradePlanStop
  targets: tuple[TradePlanTarget, ...]
  risk: TradePlanRisk
  management: TradePlanManagement
  execution_policy: TradePlanExecutionPolicy
  provenance: TradePlanProvenance
  version: int = TRADE_PLAN_VERSION
  sizing: TradePlanSizing | None = None

  def to_dict(self) -> dict:
    payload = {
      "version": self.version,
      "plan_id": self.plan_id,
      "thesis_id": self.thesis_id,
      "setup_id": self.setup_id,
      "symbol": self.symbol,
      "created_at": self.created_at,
      "expires_at": self.expires_at,
      "analysis": self.analysis.to_dict(),
      "source_structure": self.source_structure.to_dict(),
      "entry": self.entry.to_dict(),
      "stop": self.stop.to_dict(),
      "targets": [target.to_dict() for target in self.targets],
      "risk": self.risk.to_dict(),
      "management": self.management.to_dict(),
      "execution_policy": self.execution_policy.to_dict(),
      "provenance": self.provenance.to_dict(),
    }
    if self.sizing is not None:
      payload["sizing"] = self.sizing.to_dict()
    return payload

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "TradePlan":
    version = int(_require(data, "version"))
    if version not in TRADE_PLAN_SUPPORTED_VERSIONS:
      raise TradePlanError(
        f"unsupported TradePlan version {version}, expected one of "
        f"{sorted(TRADE_PLAN_SUPPORTED_VERSIONS)}",
      )
    sizing_data = data.get("sizing")
    plan = cls(
      version=version,
      plan_id=str(_require(data, "plan_id")),
      thesis_id=str(_require(data, "thesis_id")),
      setup_id=str(_require(data, "setup_id")),
      symbol=str(_require(data, "symbol")),
      created_at=int(_require(data, "created_at")),
      expires_at=int(_require(data, "expires_at")),
      analysis=TradePlanAnalysis.from_dict(_require(data, "analysis")),
      source_structure=TradePlanSourceStructure.from_dict(
        _require(data, "source_structure"),
      ),
      entry=TradePlanEntry.from_dict(_require(data, "entry")),
      stop=TradePlanStop.from_dict(_require(data, "stop")),
      targets=tuple(
        TradePlanTarget.from_dict(target) for target in _require(data, "targets")
      ),
      risk=TradePlanRisk.from_dict(_require(data, "risk")),
      management=TradePlanManagement.from_dict(data.get("management") or {}),
      execution_policy=TradePlanExecutionPolicy.from_dict(
        data.get("execution_policy") or {},
      ),
      provenance=TradePlanProvenance.from_dict(data.get("provenance") or {}),
      sizing=(
        None if sizing_data is None else TradePlanSizing.from_dict(sizing_data)
      ),
    )
    plan.validate()
    return plan

  def validate(self) -> None:
    """Execution-safety shape checks only — never re-derives a value.

    Mirrors exactly what the C# TradePlan executor is allowed to check: the stop is
    finite and on the correct side of every possible entry price, and
    targets are ordered away from entry in the trade's direction. Anything
    beyond this (is the *strategy* right, is the *structure* right) is not
    this method's job — Python already decided that before building the
    plan, and C# must never re-decide it.
    """
    direction = self.analysis.direction
    entry_prices = self.entry.entry_prices()
    if not entry_prices:
      raise TradePlanError("entry has no resolvable prices")

    if direction == "BUY":
      if any(self.stop.price >= price for price in entry_prices):
        raise TradePlanError("BUY stop.price must be below every entry price")
    else:
      if any(self.stop.price <= price for price in entry_prices):
        raise TradePlanError("SELL stop.price must be above every entry price")

    if not self.targets:
      raise TradePlanError("plan must declare at least one target")

    furthest_entry = max(entry_prices) if direction == "BUY" else min(entry_prices)
    prices = [target.price for target in self.targets]
    if direction == "BUY":
      if any(price <= furthest_entry for price in prices):
        raise TradePlanError("BUY targets must all be above the entry zone")
      if prices != sorted(prices):
        raise TradePlanError("BUY targets must be ordered TP1 < TP2 < TP3 ...")
    else:
      if any(price >= furthest_entry for price in prices):
        raise TradePlanError("SELL targets must all be below the entry zone")
      if prices != sorted(prices, reverse=True):
        raise TradePlanError("SELL targets must be ordered TP1 > TP2 > TP3 ...")

    total_ratio = sum(target.close_ratio for target in self.targets)
    if total_ratio > Decimal("1.0001"):
      raise TradePlanError(f"target close_ratio total exceeds 1.0: {total_ratio}")

    if self.management.be_after_target_id is not None:
      target_ids = {target.target_id for target in self.targets}
      if self.management.be_after_target_id not in target_ids:
        raise TradePlanError(
          f"management.be_after_target_id {self.management.be_after_target_id!r} "
          f"is not one of the declared targets {sorted(target_ids)}",
        )

    trail_after_id = self.management.trail_after_target_id
    trail_to_id = self.management.trail_to_target_id
    if (trail_after_id is None) != (trail_to_id is None):
      raise TradePlanError(
        "management.trail_after_target_id and trail_to_target_id "
        "must be set together",
      )
    if trail_after_id is not None and trail_to_id is not None:
      target_ids = [target.target_id for target in self.targets]
      if trail_after_id not in target_ids:
        raise TradePlanError(
          f"management.trail_after_target_id {trail_after_id!r} "
          f"is not one of the declared targets {target_ids}",
        )
      if trail_to_id not in target_ids:
        raise TradePlanError(
          f"management.trail_to_target_id {trail_to_id!r} "
          f"is not one of the declared targets {target_ids}",
        )
      trail_after_index = target_ids.index(trail_after_id)
      trail_to_index = target_ids.index(trail_to_id)
      if trail_to_index >= trail_after_index:
        raise TradePlanError(
          "management.trail_to_target_id must precede "
          "trail_after_target_id",
        )
      if trail_after_index >= len(target_ids) - 1:
        raise TradePlanError(
          "management.trail_after_target_id must precede the final target",
        )
