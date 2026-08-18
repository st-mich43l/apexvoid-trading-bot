"""Build TradePlan V7 from an already-confirmed StrategyMatch.

Per docs/adr-trade-plan-v7-boundary.md, this is a pure translation, not a
second decision: `StrategyMatch` already carries Python's confirmed
strategy/direction/entry-zone/structural-invalidation-price/target-pip-ladder
(the scanner "owns the complete price-action decision", per
strategy_match.py's own docstring). This module reshapes that already-decided
data into the V7 contract shape by calling the SAME `evaluate_execution_policy`
function the V6 path already uses for route/stop planning
(app/autotrade/execution_policy.py) - it does not classify regime, resolve a
route from scratch, or compute a stop independently. `entry.type` is whatever
`evaluate_execution_policy` resolved (market_watch/single_limit/limit_ladder),
`stop.price` is exactly the Python-planned protective stop, `analysis.bias`/
`analysis.regime`/`source_structure.kind` are the real values captured on the
StrategyMatch at detection time (see strategy_match.py's structural_kind/
structural_timeframe/htf_bias/regime_kind fields) - never derived from
direction.

Wired into worker.py's live publish path behind AUTO_TRADE_CONTRACT_MODE
(app/autotrade/worker.py::_publish_trade_plan_v8).
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from app.analysis.structural_reaction_support import v7_thesis_id
from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.strategy_match import StrategyMatch
from app.autotrade.trade_plan import (
  ENTRY_TYPE_LIMIT_LADDER,
  ENTRY_TYPE_MARKET_WATCH,
  ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE,
  ENTRY_TYPE_SINGLE_LIMIT,
  ORDER_TYPE_LIMIT,
  ORDER_TYPE_MARKET,
  TradePlan,
  TradePlanAnalysis,
  TradePlanEntry,
  TradePlanEntryLeg,
  TradePlanExecutionPolicy,
  TradePlanManagement,
  TradePlanProvenance,
  TradePlanRisk,
  TradePlanSizing,
  TradePlanSourceStructure,
  TradePlanStop,
  TradePlanTarget,
)


class TradePlanBuildRejected(Exception):
  """Raised when the underlying execution-policy evaluation blocks a plan.

  Carries the same reason_code/message the V6 path already surfaces for the
  identical check, so operators diagnosing "why didn't this become a plan"
  see one consistent vocabulary regardless of contract mode.
  """

  def __init__(self, reason_code: str, message: str, measured: dict[str, Any]):
    super().__init__(message)
    self.reason_code = reason_code
    self.message = message
    self.measured = measured


def _parse_bar_ts(event_ts: str, fallback: int) -> int:
  try:
    return int(event_ts)
  except (TypeError, ValueError):
    return fallback


def resolve_max_spread_ticks(
  *,
  max_spread_pips: float | int,
  pip_size: Decimal | float,
  price_digits: int,
) -> int:
  """Convert configured max spread in pips into broker tick count.

  Live incident 2026-08-05: plans shipped ``max_spread_ticks=8`` hardcoded
  while XAU digits=2 (tick 0.01) and live spread sat at ~0.09 (=9 ticks).
  market_watch saw quote overlapping the zone, then Wait'd on
  ``spread_exceeds_declared_limit`` for the whole window; after price left
  the zone the last wait reason flipped to ``outside_zone`` and Telegram
  lied that price "never returned". Deriving ticks from
  ``execution.entry.max_spread_pips`` (× pip / tick) keeps the plan gate
  aligned with the same pip budget AutoTradeOptions already uses.
  """
  pip = float(pip_size)
  digits = max(0, int(price_digits))
  tick = 10.0 ** (-digits)
  if pip <= 0 or tick <= 0:
    return 8
  ticks = math.ceil(float(max_spread_pips) * pip / tick)
  return max(1, int(ticks))


def _resolve_cfg(cfg: Any | None) -> Any:
  if cfg is not None:
    return cfg
  from app.core.config import runtime_config as _rc
  return _rc


def _equal_close_ratios(count: int) -> tuple[Decimal, ...]:
  if count == 0:
    return ()
  base = (Decimal("1") / count).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
  ratios = [base] * count
  # Ratios must sum to exactly 1.0 (TradePlan.validate enforces <= 1.0001);
  # any quantization remainder goes to the final (furthest, smallest-size)
  # target rather than being silently dropped.
  remainder = Decimal("1") - base * count
  ratios[-1] += remainder
  return tuple(ratios)


def _is_approved_policy_measured(measured: Mapping[str, Any]) -> bool:
  """True when measured already carries a final execution-policy decision."""
  if not measured:
    return False
  if measured.get("planned_stop_error"):
    return False
  has_stop = measured.get("planned_stop_price") is not None
  has_route = bool(measured.get("planned_execution_route"))
  has_entry = measured.get("planned_entry_price") is not None
  return bool(has_stop and (has_route or has_entry))


def _build_entry(
  *,
  route: str,
  direction: str,
  match: StrategyMatch,
  measured: dict[str, Any],
  expires_at: int,
  max_spread_ticks: int,
  max_slippage_ticks: int,
) -> TradePlanEntry:
  if route == "market":
    return TradePlanEntry(
      type=ENTRY_TYPE_MARKET_WATCH,
      expires_at=expires_at,
      zone_low=Decimal(str(match.entry_low)),
      zone_high=Decimal(str(match.entry_high)),
      activation="quote_inside_zone",
      price_side="ask" if direction == "BUY" else "bid",
      max_spread_ticks=max_spread_ticks,
      max_slippage_ticks=max_slippage_ticks,
    )
  if route == "single_limit":
    return TradePlanEntry(
      type=ENTRY_TYPE_SINGLE_LIMIT,
      expires_at=expires_at,
      order_price=Decimal(str(measured["planned_entry_price"])),
      max_spread_ticks=max_spread_ticks,
      max_slippage_ticks=max_slippage_ticks,
    )
  if route == "market_with_limit_scale":
    leg_prices = measured.get("planned_leg_entry_prices") or []
    if len(leg_prices) < 2:
      raise TradePlanBuildRejected(
        "empty_reaction_scale_legs",
        "market_with_limit_scale route resolved with fewer than two leg prices",
        measured,
      )
    custom_ratios = measured.get("planned_leg_volume_ratios") or []
    if custom_ratios and len(custom_ratios) == len(leg_prices):
      ratios = [Decimal(str(value)) for value in custom_ratios]
      ratios[-1] += Decimal("1") - sum(ratios)
    else:
      ratios = [Decimal("0.80"), Decimal("0.20")]
      if len(leg_prices) != 2:
        ratio = (Decimal("1") / len(leg_prices)).quantize(
          Decimal("0.0001"), rounding=ROUND_HALF_UP,
        )
        ratios = [ratio] * len(leg_prices)
        ratios[-1] += Decimal("1") - ratio * len(leg_prices)
    legs = tuple(
      TradePlanEntryLeg(
        leg_id=f"L{index + 1}",
        price=Decimal(str(price)),
        volume_ratio=leg_ratio,
        order_type=ORDER_TYPE_MARKET if index == 0 else ORDER_TYPE_LIMIT,
      )
      for index, (price, leg_ratio) in enumerate(zip(leg_prices, ratios))
    )
    return TradePlanEntry(
      type=ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE,
      expires_at=expires_at,
      zone_low=Decimal(str(match.entry_low)),
      zone_high=Decimal(str(match.entry_high)),
      legs=legs,
      max_spread_ticks=max_spread_ticks,
      max_slippage_ticks=max_slippage_ticks,
    )
  if route == "zone_split":
    leg_prices = measured.get("planned_leg_entry_prices") or []
    if not leg_prices:
      raise TradePlanBuildRejected(
        "empty_ladder_legs",
        "zone_split route resolved with no leg prices",
        measured,
      )
    custom_ratios = measured.get("planned_leg_volume_ratios") or []
    if custom_ratios and len(custom_ratios) == len(leg_prices):
      # DCA-into-zone scale ladder (owner spec): execution_route.py already
      # computed the exact first-leg/remainder split - use it verbatim
      # rather than the equal-split default below.
      ratios = [Decimal(str(value)) for value in custom_ratios]
      ratios[-1] += Decimal("1") - sum(ratios)
    else:
      ratio = (Decimal("1") / len(leg_prices)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP,
      )
      ratios = [ratio] * len(leg_prices)
      ratios[-1] += Decimal("1") - ratio * len(leg_prices)
    legs = tuple(
      TradePlanEntryLeg(
        leg_id=f"L{index + 1}",
        price=Decimal(str(price)),
        volume_ratio=leg_ratio,
      )
      for index, (price, leg_ratio) in enumerate(zip(leg_prices, ratios))
    )
    return TradePlanEntry(
      type=ENTRY_TYPE_LIMIT_LADDER,
      expires_at=expires_at,
      legs=legs,
      max_spread_ticks=max_spread_ticks,
      max_slippage_ticks=max_slippage_ticks,
    )
  raise TradePlanBuildRejected(
    "unresolved_execution_route",
    f"execution policy resolved an unsupported route: {route!r}",
    measured,
  )


def build_trade_plan_from_strategy_match(
  match: StrategyMatch,
  *,
  plan_id: str,
  setup_id: str,
  thesis_id: str,
  pip_size: Decimal,
  spot_price: float,
  regime: str | None,
  cfg: Any | None = None,
  opposing_zone_low: float | None = None,
  opposing_zone_high: float | None = None,
  opposing_zone_id: str | None = None,
  executable_quote: float | None = None,
  max_volume: int,
  max_spread_ticks: int | None = None,
  max_slippage_ticks: int = 10,
  be_after_target_index: int | None = 0,
  be_buffer_ticks: int = 6,
  max_group_risk_percent: Decimal = Decimal("2.0"),
  close_ratios: Sequence[Decimal] | None = None,
  analysis_engine_version: str = "",
  market_map_id: str = "",
  config_fingerprint: str = "",
  confirmation_source: str = "",
  execution_confirmation_bar_ts: int | None = None,
  zone_episode_id: str | None = None,
  trigger_wick_extreme: float | None = None,
  now_ts: int | None = None,
  approved_measured: Mapping[str, Any] | None = None,
  same_direction_stack: bool = False,
  same_direction_size_fraction: float = 0.60,
) -> TradePlan:
  """Translate a CONFIRMED StrategyMatch into a TradePlan V7.

  Raises TradePlanBuildRejected only for hard contract failures (missing
  thesis identity, empty targets, unresolved route, or unavailable stop).
  Preference / quality signals are recorded on the measured payload and
  never deny publication.

  When ``approved_measured`` already carries planned stop + route/entry,
  this function translates that decision and does not re-run geometry
  planning.

  ``now_ts`` re-anchors the published plan's entry/plan expiry to actual
  publication time. Live incident: match.expires_at is set once, when the
  underlying StrategyMatch was first built - a setup that then sat in
  WAITING_RETEST/IN_ZONE_WAITING_M1 for several minutes before its retest
  and M1 confirmation finally completed could still be under
  match.expires_at at publish time (the earlier expiry pre-check in
  _publish_trade_plan_v8 passes), but with almost none of its original TTL
  actually left - the plan reached the C# executor seconds before that
  stale deadline and expired without ever getting a chance to submit,
  despite the executable quote already being inside the zone at
  publication. The published plan's own window must start fresh from when
  it was actually published, not inherit whatever was left of the
  original match's clock.
  """
  if not match.targets_pips:
    raise TradePlanBuildRejected(
      "empty_target_config",
      f"StrategyMatch {match.match_id!r} has no targets_pips",
      {},
    )
  if not match.structural_zone_id:
    # Fail closed rather than fall back to match_id: match_id (and the V6
    # structural_thesis_id it's derived from) is re-hashed on every new
    # confirmation timestamp, so using it as a V7 thesis_id would silently
    # let repeated confirmations of the same structure each look like a
    # brand new thesis - exactly what claim_active_thesis exists to
    # prevent. See app.analysis.structural_reaction_support.v7_thesis_id.
    raise TradePlanBuildRejected(
      "missing_stable_thesis_id",
      f"StrategyMatch {match.match_id!r} has no structural_zone_id to "
      "build a stable V7 thesis identity from",
      {},
    )
  if not match.structural_kind:
    raise TradePlanBuildRejected(
      "missing_structural_kind",
      f"StrategyMatch {match.match_id!r} has no structural_kind",
      {},
    )
  if not match.htf_bias:
    raise TradePlanBuildRejected(
      "missing_htf_bias",
      f"StrategyMatch {match.match_id!r} has no htf_bias captured at "
      "detection time",
      {},
    )

  direction = match.direction
  pip = float(pip_size)

  if approved_measured is not None and _is_approved_policy_measured(approved_measured):
    measured = dict(approved_measured)
  else:
    evaluation = evaluate_execution_policy(
      match,
      spot_price=spot_price,
      regime=regime,
      pip_size=pip,
      cfg=cfg,
      opposing_zone_low=opposing_zone_low,
      opposing_zone_high=opposing_zone_high,
      opposing_zone_id=opposing_zone_id,
      executable_quote=executable_quote,
      trigger_wick_extreme=trigger_wick_extreme,
    )
    # Builder plans geometry/stop/route only. Preference misses are telemetry
    # on the measured payload and never deny publication. Hard contract
    # failures (missing route/stop) still reject below.
    measured = dict(evaluation.measured)
  if same_direction_stack:
    from app.autotrade.active_exposure import apply_same_direction_stack_sizing

    measured = apply_same_direction_stack_sizing(
      measured,
      size_fraction=same_direction_size_fraction,
    )
  if measured.get("planned_stop_error"):
    stop_error = str(measured["planned_stop_error"])
    known_stop_errors = {
      "stop_exceeds_envelope_after_wick",
      "stop_exceeds_max_envelope",
      "stop_exceeds_envelope_furthest_leg",
      "stop_inside_opposing_zone",
      "stop_inside_entry_zone",
      "stop_not_beyond_planned_entries",
    }
    raise TradePlanBuildRejected(
      stop_error if stop_error in known_stop_errors else "protective_stop_unavailable",
      stop_error,
      measured,
    )
  if "planned_stop_price" not in measured:
    raise TradePlanBuildRejected(
      "protective_stop_unavailable",
      "execution geometry produced no protective stop",
      measured,
    )
  if not measured.get("planned_execution_route"):
    raise TradePlanBuildRejected(
      "execution_route_unresolved",
      "execution route could not be resolved",
      measured,
    )

  fixed_target_prices = tuple(
    Decimal(str(price))
    for price in (measured.get("planned_target_prices") or ())
  )
  fixed_rr = measured.get("target_policy_mode") == "fixed_rr"
  if fixed_rr:
    if len(fixed_target_prices) != 1:
      raise TradePlanBuildRejected(
        "invalid_fixed_rr_target",
        "fixed_rr policy must produce exactly one target price",
        measured,
      )
    ratios = (Decimal("1"),)
    target_values = fixed_target_prices
  else:
    ratios = (
      tuple(Decimal(str(r)) for r in close_ratios)
      if close_ratios is not None
      else _equal_close_ratios(len(match.targets_pips))
    )
    entry_reference = Decimal(str(measured["planned_entry_price"]))
    sign = Decimal("1") if direction == "BUY" else Decimal("-1")
    target_values = tuple(
      entry_reference + sign * (Decimal(pips) * pip_size)
      for pips in match.targets_pips
    )
  if len(ratios) != len(target_values):
    raise TradePlanBuildRejected(
      "target_ratio_mismatch",
      f"close_ratios length {len(ratios)} does not match targets "
      f"length {len(target_values)}",
      measured,
    )

  targets = tuple(
    TradePlanTarget(
      target_id=f"TP{index + 1}",
      type="absolute",
      price=price,
      close_ratio=ratio,
    )
    for index, (price, ratio) in enumerate(zip(target_values, ratios))
  )

  ttl_seconds = max(60, int(match.expires_at) - int(match.issued_at))
  published_expires_at = (
    int(match.expires_at) if now_ts is None else int(now_ts) + ttl_seconds
  )

  formation_bar_ts = _parse_bar_ts(
    str(match.touch_bar_ts or match.event_ts), match.issued_at,
  )
  confirmation_bar_ts = _parse_bar_ts(
    str(match.confirmation_bar_ts or match.event_ts), match.issued_at,
  )

  analysis = TradePlanAnalysis(
    strategy=match.strategy,
    strategy_family=match.family or match.strategy_mode,
    direction=direction,
    context_timeframes=(match.source_tf,),
    formation_timeframe=match.structural_timeframe or match.source_tf,
    confirmation_timeframe=match.source_tf,
    formation_bar_ts=formation_bar_ts,
    confirmation_bar_ts=confirmation_bar_ts,
    score=float(match.confluence),
    confluence=match.confluence,
    bias=match.htf_bias,
    regime=match.regime_kind or regime or "unknown",
    reasons=match.reasons,
    tags=match.tags,
  )

  source_structure = TradePlanSourceStructure(
    structure_id=match.structural_zone_id,
    kind=match.structural_kind,
    timeframe=match.structural_timeframe or match.source_tf,
    low=Decimal(str(match.structural_zone_low or match.entry_low)),
    high=Decimal(str(match.structural_zone_high or match.entry_high)),
    invalidation_price=Decimal(str(match.structure_swing)),
  )

  cfg_resolved = _resolve_cfg(cfg)
  if max_spread_ticks is None:
    max_spread_pips = float(
      getattr(
        getattr(getattr(cfg_resolved, "execution", None), "entry", None),
        "max_spread_pips",
        5,
      )
      or 5
    )
    price_digits = int(
      getattr(
        getattr(getattr(cfg_resolved, "contract", None), "instrument", None),
        "price_digits",
        2,
      )
      or 2
    )
    resolved_max_spread_ticks = resolve_max_spread_ticks(
      max_spread_pips=max_spread_pips,
      pip_size=pip_size,
      price_digits=price_digits,
    )
  else:
    resolved_max_spread_ticks = int(max_spread_ticks)

  entry = _build_entry(
    route=str(measured["planned_execution_route"]),
    direction=direction,
    match=match,
    measured=measured,
    expires_at=published_expires_at,
    max_spread_ticks=resolved_max_spread_ticks,
    max_slippage_ticks=max_slippage_ticks,
  )

  stop = TradePlanStop(
    type="absolute",
    price=Decimal(str(measured["planned_stop_price"])),
    source=(
      "m1_trigger_wick"
      if trigger_wick_extreme is not None
      else "m5_structure"
      if confirmation_source == "m5_authoritative"
      else str(measured.get("stop_source", "protective_stop_plan"))
    ),
    structure_id=match.structural_zone_id,
    reason=(
      f"execution confirmation={confirmation_source or 'legacy_m1'}; "
      "app.autotrade.execution_policy.evaluate_execution_policy -> "
      "app.autotrade.protective_stop.plan_protective_stop"
    ),
  )

  if fixed_rr or be_after_target_index is None or not targets:
    be_after_target_id = None
  else:
    be_after_target_id = targets[be_after_target_index].target_id
  management = TradePlanManagement(
    be_after_target_id=be_after_target_id,
    be_buffer_ticks=be_buffer_ticks,
    never_worsen_stop=True,
  )

  risk = TradePlanRisk(
    risk_percent=Decimal("1.0"),
    risk_multiplier=Decimal(str(measured.get(
      "effective_risk_multiplier", match.risk_multiplier,
    ))),
    max_volume=max_volume,
    max_group_risk_percent=max_group_risk_percent,
  )

  if cfg is None:
    cfg_for_fraction = cfg_resolved
  else:
    cfg_for_fraction = cfg
  first_leg_fraction = Decimal(str(
    cfg_for_fraction.execution.reaction.market_fraction
    or cfg_for_fraction.execution.zone_scaling.first_leg_fraction
    or 0.80
  ))
  if entry.legs:
    leg_ratios = tuple(leg.volume_ratio for leg in entry.legs)
  else:
    remainder = Decimal("1") - first_leg_fraction
    leg_ratios = (first_leg_fraction, remainder)
  entry_distribution = str(measured.get("entry_distribution") or "zone_scale")
  sizing = TradePlanSizing(
    mode="equity_table",
    table_version="owner_equity_v1",
    entry_distribution=entry_distribution,
    leg_ratios=leg_ratios,
  )

  plan = TradePlan(
    plan_id=plan_id,
    thesis_id=thesis_id,
    setup_id=setup_id,
    symbol=match.symbol,
    created_at=match.issued_at,
    expires_at=published_expires_at,
    analysis=analysis,
    source_structure=source_structure,
    entry=entry,
    stop=stop,
    targets=targets,
    risk=risk,
    management=management,
    execution_policy=TradePlanExecutionPolicy(
      allow_market=entry.type in {
        ENTRY_TYPE_MARKET_WATCH, ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE,
      },
      allow_limit=entry.type in {
        ENTRY_TYPE_SINGLE_LIMIT,
        ENTRY_TYPE_LIMIT_LADDER,
        ENTRY_TYPE_MARKET_WITH_LIMIT_SCALE,
      },
      allow_partial_fill=True,
      cancel_on_expiry=True,
    ),
    provenance=TradePlanProvenance(
      analysis_engine_version=analysis_engine_version,
      market_map_id=market_map_id,
      config_fingerprint=config_fingerprint,
      confirmation_source=confirmation_source,
      confirmation_bar_ts=execution_confirmation_bar_ts,
      zone_episode_id=zone_episode_id,
    ),
    sizing=sizing,
  )
  plan.validate()
  return plan
