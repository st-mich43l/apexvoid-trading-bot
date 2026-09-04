"""Scalping strategy discovery (three archetypes)."""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

from app.scalping.microstructure import (
  detect_breakout_retest,
  detect_impulse_pullback,
  detect_sweep_reclaim,
  find_compression_box,
)
from app.scalping.context import is_impulse_pullback_session_allowed
from app.analysis.key_level_role import classify_key_level_role
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpOpportunity,
  MicroStructure,
  STRATEGY_DISPLAY,
  deterministic_id,
)
from app.runtime.price_identity import rounded_price


def _scalping_cfg(cfg: Any) -> Any:
  return getattr(getattr(cfg, "strategies", None), "scalping", None)


def _parse_float(section: Any, name: str, default: float) -> float:
  try:
    return float(getattr(section, name, default) or default)
  except (TypeError, ValueError):
    return default


def _worst_fill(*, direction: str, zone_low: float, zone_high: float) -> float:
  """The least favourable price inside the published entry zone.

  Risk and reward must both be measured from here. The trigger close is a
  detection artefact, not a fill price -- the executor may fill anywhere in
  [zone_low, zone_high], so any risk figure anchored on the close is only
  correct by coincidence.
  """
  return zone_high if str(direction).upper() == "BUY" else zone_low


def _zone_stop_ordered(
  *,
  direction: str,
  invalidation: float,
  zone_low: float,
  zone_high: float,
) -> bool:
  if str(direction).upper() == "BUY":
    return invalidation < zone_low < zone_high
  return invalidation > zone_high > zone_low


def _select_target(
  *,
  direction: str,
  worst_fill: float,
  room_pips: float | None,
  stop_pips: float | None,
  min_net: float,
  pip_size: float,
  symbol: str = "",
  cfg: Any | None = None,
) -> tuple[float, float] | None:
  """Owner 2026-08-11: every scalp is 1:2 when the available room supports
  it, 1:1 otherwise - no ladder, no picking whichever preferred level
  happens to fit, and never anything outside this pair. If neither ratio
  clears the minimum net target and fits the available room, there is no
  opportunity here at all, not a smaller/larger substitute target.

  Instruments configured with technique ``fixed_rr`` still discover scalp
  targets as 1:2 then 1:1 — technique R expansion is applied only on
  non-scalp strategies in execution policy.

  Targets are anchored on the worst-case fill inside the entry zone, not
  the trigger-bar close.
  """
  if room_pips is None or pip_size <= 0 or stop_pips is None or stop_pips <= 0:
    return None
  # Scalp discovery owns 1:2 / 1:1. Do not prefer instrument technique RR
  # (XAU structure fixed_rr) here — that would change scalp geometry.
  ratios = (2.0, 1.0)
  for reward_risk in ratios:
    target_pips = float(stop_pips) * reward_risk
    if target_pips < min_net or target_pips > float(room_pips):
      continue
    if str(direction).upper() == "BUY":
      return worst_fill + target_pips * pip_size, target_pips
    return worst_fill - target_pips * pip_size, target_pips
  return None


def _stop_pips(
  *,
  structural: float,
  cfg: Any,
  spread_pips: float = 0.0,
) -> tuple[float | None, str | None]:
  """Return ``(stop_pips, reject_reason)``.

  The minimum is a widening floor: a stop tighter than ``minimum_pips`` is pushed
  out to it, and the caller must move ``invalidation_price`` outward to match so
  the two can never disagree. The maximum is a REJECT, not a clamp -- a
  structural stop wider than the risk envelope means the setup does not fit the
  model, and shrinking it produces a stop that is not structural and an RR that
  is not true.

  The 2026-08-06 clamp into ``[min, max]`` was added because returning ``None``
  dropped the opportunity with ``discovered=0`` and no telemetry. That gap is
  closed by idle-reason + ``opportunity_blocked:*stop_exceeds_maximum`` metrics
  — not by falsifying the stop.
  """
  stop_cfg = getattr(_scalping_cfg(cfg), "stop", None)
  mn = _parse_float(stop_cfg, "minimum_pips", 12.0)
  mx = _parse_float(stop_cfg, "maximum_pips", 30.0)
  value = abs(float(structural))
  if value <= 0:
    return None, "stop_not_positive"
  if mx < mn:
    return None, "stop_envelope_invalid"
  spread_floor = _parse_float(stop_cfg, "minimum_stop_spread_multiple", 4.0)
  if spread_pips > 0 and value < spread_floor * spread_pips:
    return None, "stop_below_spread_multiple"
  if value > mx:
    return None, "stop_exceeds_maximum"
  return max(value, mn), None


def _stop_buffer(
  context: ScalpContextSnapshot,
  cfg: Any,
  pip_size: float,
) -> float:
  """Return the M1 volatility/spread floor beyond a structural level."""
  root = _scalping_cfg(cfg)
  stop_cfg = getattr(root, "stop", None)
  policy = getattr(root, "policy", None)
  m1_atr = _parse_float(context, "m1_atr", 0.0)
  if not math.isfinite(m1_atr) or m1_atr <= 0:
    m1_atr = max(float(pip_size) * 3.0, float(pip_size))
  atr_multiple = _parse_float(stop_cfg, "buffer_m1_atr_multiple", 1.2)
  spread_multiple = _parse_float(
    stop_cfg, "buffer_minimum_spread_multiple", 1.5,
  )
  maximum_spread = _parse_float(policy, "maximum_spread_pips", 5.0)
  return max(
    m1_atr * atr_multiple,
    float(pip_size) * maximum_spread * spread_multiple,
  )


def _impulse_reference(
  context: ScalpContextSnapshot,
  *,
  direction: str,
  pullback_extreme: float,
  buffer: float,
  cfg: Any,
  pip_size: float,
) -> dict[str, Any] | None:
  """Choose an unmitigated M5 zone first, then a nearby canonical level."""
  root = _scalping_cfg(cfg)
  location = getattr(root, "location", None)
  proximity_multiple = _parse_float(
    location, "level_proximity_atr_multiple", 1.0,
  )
  m1_atr = max(float(context.m1_atr or 0.0), float(pip_size) * 3.0)
  proximity = max(0.0, proximity_multiple * m1_atr)
  side = "demand" if direction == "BUY" else "supply"
  candidates: list[dict[str, Any]] = []

  for zone in context.zones:
    try:
      bottom = float(zone["bottom"])
      top = float(zone["top"])
      zone_side = str(zone.get("side") or "").casefold()
      if zone_side not in {side, direction.casefold()}:
        continue
      if bool(zone.get("mitigated", False)) or top <= bottom:
        continue
      if bottom <= pullback_extreme <= top:
        distance = 0.0
      else:
        distance = min(
          abs(pullback_extreme - bottom), abs(pullback_extreme - top),
        )
      if distance > proximity:
        continue
      candidates.append({
        "kind": "zone",
        "bottom": bottom,
        "top": top,
        "level": bottom if direction == "BUY" else top,
        "distance": distance,
        "score": float(zone.get("score") or 0.0),
        "touches": int(zone.get("touches") or 0),
        "zone_score": float(zone.get("score") or 0.0),
        "zone_touches": int(zone.get("touches") or 0),
      })
    except (KeyError, TypeError, ValueError):
      continue

  if candidates:
    reference = max(
      candidates,
      key=lambda item: (
        item["score"], item["touches"], -item["distance"],
      ),
    )
    maximum_zone_atr = _parse_float(
      getattr(root, "stop", None), "zone_maximum_atr_multiple", 1.5,
    )
    if reference["top"] - reference["bottom"] > maximum_zone_atr * m1_atr:
      return {"rejected": True, "reason": "impulse_zone_too_wide"}
    return reference

  levels: list[dict[str, Any]] = []
  for level in context.key_levels:
    try:
      price = float(level["price"])
      band = max(0.0, float(level.get("band") or 0.0))
      distance = abs(price - pullback_extreme)
      if distance > proximity + band:
        continue
      if direction == "BUY" and price > pullback_extreme + proximity:
        continue
      if direction == "SELL" and price < pullback_extreme - proximity:
        continue
      levels.append({
        "kind": "level",
        "level": price,
        "raw_kind": str(level.get("kind") or ""),
        "band": band,
        "bottom": price,
        "top": price,
        "distance": distance,
        "score": float(level.get("score") or 0.0),
        "touches": int(level.get("touches") or 0),
        "zone_score": 0.0,
        "zone_touches": 0,
      })
    except (KeyError, TypeError, ValueError):
      continue
  if not levels:
    return None
  return min(levels, key=lambda item: (item["distance"], -item["touches"]))


def _impulse_level_role(
  context: ScalpContextSnapshot,
  reference: dict[str, Any],
  *,
  direction: str,
  cfg: Any,
) -> str:
  analysis = getattr(getattr(cfg, "analysis", None), "breakout", None)
  accept_bars = int(getattr(analysis, "accept_bars", 2) or 2)
  if reference["kind"] == "zone":
    kind = "support" if direction == "BUY" else "resistance"
    band_low = float(reference["bottom"])
    band_high = float(reference["top"])
  else:
    raw_kind = str(reference.get("raw_kind") or "")
    kind = raw_kind or ("support" if direction == "BUY" else "resistance")
    band = max(0.0, float(reference.get("band") or 0.0))
    band_low = float(reference["level"]) - band
    band_high = float(reference["level"]) + band
  closed = context.measured.get("m5_closes", [])
  closed_bars = pd.DataFrame({"close": list(closed)})
  return classify_key_level_role(
    kind=kind,
    level_price=float(reference["level"]),
    band_low=band_low,
    band_high=band_high,
    closed_bars=closed_bars,
    breakout_accept_bars=accept_bars,
  ).role


def _technique_require_sweep_body(cfg: Any) -> bool:
  from app.autotrade.killzone import technique_require_sweep_body

  return technique_require_sweep_body(cfg)


def _enabled(cfg: Any, name: str) -> bool:
  """Archetype enable flags are fail-closed when missing (not default-on)."""
  root = _scalping_cfg(cfg)
  arch = getattr(root, "archetypes", None)
  attr = f"{name}_enabled"
  return bool(getattr(arch, attr, False))


def discover_range_sweep(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
  spread_pips: float = 0.0,
  idle_reasons: list[str] | None = None,
) -> list[ScalpOpportunity]:
  if not _enabled(cfg, "range_sweep"):
    return []
  if ARCHETYPE_RANGE_SWEEP not in context.permitted_archetypes:
    return []
  low = context.active_range_low
  high = context.active_range_high
  if low is None or high is None or high <= low:
    return []
  width_pips = (high - low) / pip_size
  if width_pips < 25:
    return []

  loc = getattr(_scalping_cfg(cfg), "location", None)
  buy_max = _parse_float(loc, "range_buy_maximum_position", 0.35)
  sell_min = _parse_float(loc, "range_sell_minimum_position", 0.65)
  pos = context.dealing_range_position
  # Owner 2026-08-06: near-EQ mute used to return [] and kill Asia discovery
  # for hours while price sat mid-range. Location filters below already gate
  # BUY/SELL by dealing position — do not blank the whole archetype here.

  out: list[ScalpOpportunity] = []
  reasons = idle_reasons if idle_reasons is not None else []
  buffer = _stop_buffer(context, cfg, pip_size)
  min_net = _parse_float(getattr(_scalping_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  act = getattr(_scalping_cfg(cfg), "activation", None)
  lookback = max(1, int(getattr(act, "trigger_maximum_age_bars", 2) or 2))

  # BUY lower edge
  buy_ev = detect_sweep_reclaim(
    m1_df,
    direction="BUY",
    edge_price=low,
    tolerance=buffer,
    lookback_bars=lookback,
  )
  if buy_ev is not None and (pos is None or pos <= buy_max):
      entry = float(buy_ev["close"])
      zone_low = low - buffer
      zone_high = low + buffer * 2
      worst = _worst_fill(direction="BUY", zone_low=zone_low, zone_high=zone_high)
      stop_price = float(buy_ev["extreme"]) - buffer
      structural = (worst - stop_price) / pip_size
      stop, reject = _stop_pips(
        structural=structural, cfg=cfg, spread_pips=spread_pips,
      )
      if stop is None:
        if reject:
          reasons.append(f"{ARCHETYPE_RANGE_SWEEP}:{reject}")
      else:
        invalidation = worst - stop * pip_size
        if not _zone_stop_ordered(
          direction="BUY",
          invalidation=invalidation,
          zone_low=zone_low,
          zone_high=zone_high,
        ):
          reasons.append(f"{ARCHETYPE_RANGE_SWEEP}:stop_inside_zone")
        else:
          target = _select_target(
            direction="BUY",
            worst_fill=worst,
            room_pips=context.buy_corridor_room_pips,
            stop_pips=stop,
            min_net=min_net,
            pip_size=pip_size,
            symbol=context.symbol,
            cfg=cfg,
          )
          if target is not None:
            target_price, target_pips = target
            rr = target_pips / stop if stop else 0.0
            source = deterministic_id(
              "range", context.symbol, "BUY", rounded_price(low, pip_size),
            )
            oid = deterministic_id(
              context.symbol, ARCHETYPE_RANGE_SWEEP, "BUY", context.context_id, source,
            )
            out.append(ScalpOpportunity(
              version=OPPORTUNITY_VERSION,
              opportunity_id=oid,
              context_id=context.context_id,
              symbol=context.symbol,
              archetype=ARCHETYPE_RANGE_SWEEP,
              direction="BUY",
              discovered_at=int(now),
              source_bar_ts=int(buy_ev["bar_ts"]),
              zone_low=zone_low,
              zone_high=zone_high,
              key_level=low,
              trigger_type=str(buy_ev["pattern"]),
              trigger_bar_ts=int(buy_ev["bar_ts"]),
              trigger_price=entry,
              invalidation_price=invalidation,
              expected_target_price=target_price,
              expected_target_pips=target_pips,
              expected_stop_pips=stop,
              expected_reward_risk=rr,
              location_position=pos,
              score=0.0,
              reasons=("lower_edge_sweep_reclaim",),
              expires_at=int(now) + 15 * 60,
              episode_id=source,
              source_identity=source,
              measured={"strategy": STRATEGY_DISPLAY[ARCHETYPE_RANGE_SWEEP]},
            ))

  sell_ev = detect_sweep_reclaim(
    m1_df,
    direction="SELL",
    edge_price=high,
    tolerance=buffer,
    lookback_bars=lookback,
  )
  if sell_ev is not None and (pos is None or pos >= sell_min):
      entry = float(sell_ev["close"])
      zone_low = high - buffer * 2
      zone_high = high + buffer
      worst = _worst_fill(direction="SELL", zone_low=zone_low, zone_high=zone_high)
      stop_price = float(sell_ev["extreme"]) + buffer
      structural = (stop_price - worst) / pip_size
      stop, reject = _stop_pips(
        structural=structural, cfg=cfg, spread_pips=spread_pips,
      )
      if stop is None:
        if reject:
          reasons.append(f"{ARCHETYPE_RANGE_SWEEP}:{reject}")
      else:
        invalidation = worst + stop * pip_size
        if not _zone_stop_ordered(
          direction="SELL",
          invalidation=invalidation,
          zone_low=zone_low,
          zone_high=zone_high,
        ):
          reasons.append(f"{ARCHETYPE_RANGE_SWEEP}:stop_inside_zone")
        else:
          target = _select_target(
            direction="SELL",
            worst_fill=worst,
            room_pips=context.sell_corridor_room_pips,
            stop_pips=stop,
            min_net=min_net,
            pip_size=pip_size,
            symbol=context.symbol,
            cfg=cfg,
          )
          if target is not None:
            target_price, target_pips = target
            rr = target_pips / stop if stop else 0.0
            source = deterministic_id(
              "range", context.symbol, "SELL", rounded_price(high, pip_size),
            )
            oid = deterministic_id(
              context.symbol, ARCHETYPE_RANGE_SWEEP, "SELL", context.context_id, source,
            )
            out.append(ScalpOpportunity(
              version=OPPORTUNITY_VERSION,
              opportunity_id=oid,
              context_id=context.context_id,
              symbol=context.symbol,
              archetype=ARCHETYPE_RANGE_SWEEP,
              direction="SELL",
              discovered_at=int(now),
              source_bar_ts=int(sell_ev["bar_ts"]),
              zone_low=zone_low,
              zone_high=zone_high,
              key_level=high,
              trigger_type=str(sell_ev["pattern"]),
              trigger_bar_ts=int(sell_ev["bar_ts"]),
              trigger_price=entry,
              invalidation_price=invalidation,
              expected_target_price=target_price,
              expected_target_pips=target_pips,
              expected_stop_pips=stop,
              expected_reward_risk=rr,
              location_position=pos,
              score=0.0,
              reasons=("upper_edge_sweep_reclaim",),
              expires_at=int(now) + 15 * 60,
              episode_id=source,
              source_identity=source,
              measured={"strategy": STRATEGY_DISPLAY[ARCHETYPE_RANGE_SWEEP]},
            ))
  return out


def discover_impulse_pullback(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
  spread_pips: float = 0.0,
  idle_reasons: list[str] | None = None,
) -> list[ScalpOpportunity]:
  if not _enabled(cfg, "impulse_pullback"):
    return []
  if ARCHETYPE_IMPULSE_PULLBACK not in context.permitted_archetypes:
    return []
  if not is_impulse_pullback_session_allowed(context.session, cfg):
    return []

  loc = getattr(_scalping_cfg(cfg), "location", None)
  buy_max = _parse_float(loc, "pullback_buy_maximum_position", 0.60)
  sell_min = _parse_float(loc, "pullback_sell_minimum_position", 0.40)
  min_net = _parse_float(getattr(_scalping_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  buffer = _stop_buffer(context, cfg, pip_size)
  out: list[ScalpOpportunity] = []
  reasons = idle_reasons if idle_reasons is not None else []
  pos = context.dealing_range_position

  for direction in ("BUY", "SELL"):
    if direction == "BUY" and pos is not None and pos > buy_max:
      continue
    if direction == "SELL" and pos is not None and pos < sell_min:
      continue
    arch = getattr(_scalping_cfg(cfg), "archetypes", None)
    ev = detect_impulse_pullback(
      m1_df,
      direction=direction,
      pullback_extreme_confirm_bars=int(
        getattr(arch, "pullback_extreme_confirm_bars", 2) or 2
      ),
    )
    if ev is None:
      continue
    if ev.get("rejected"):
      reasons.append(
        f"{ARCHETYPE_IMPULSE_PULLBACK}:{ev.get('reason', 'rejected')}"
      )
      continue
    entry = float(ev["close"])
    m1_atr = max(float(context.m1_atr or 0.0), pip_size * 3.0)
    impulse_len = float(ev.get("impulse_len") or 0.0)
    body_dominance = float(ev.get("body_dominance") or 0.0)
    displacement_multiple = impulse_len / m1_atr if m1_atr > 0 else 0.0
    if displacement_multiple < _parse_float(
      arch, "impulse_displacement_atr_multiple", 4.0,
    ) or body_dominance < _parse_float(
      arch, "impulse_body_dominance", 0.5,
    ):
      reasons.append(f"{ARCHETYPE_IMPULSE_PULLBACK}:impulse_no_displacement")
      continue
    mean_impulse_body = float(ev.get("mean_impulse_body") or 0.0)
    mean_pullback_body = float(ev.get("mean_pullback_body") or 0.0)
    if (
      mean_impulse_body <= 0
      or mean_pullback_body >= mean_impulse_body * _parse_float(
        arch, "pullback_corrective_ratio", 0.7,
      )
    ):
      reasons.append(f"{ARCHETYPE_IMPULSE_PULLBACK}:pullback_not_corrective")
      continue
    reference = _impulse_reference(
      context,
      direction=direction,
      pullback_extreme=float(ev["pullback_extreme"]),
      buffer=buffer,
      cfg=cfg,
      pip_size=pip_size,
    )
    if reference is None:
      reasons.append(f"{ARCHETYPE_IMPULSE_PULLBACK}:impulse_no_level_reference")
      continue
    if reference.get("rejected"):
      reasons.append(
        f"{ARCHETYPE_IMPULSE_PULLBACK}:{reference.get('reason')}"
      )
      continue
    key_level_role = _impulse_level_role(
      context, reference, direction=direction, cfg=cfg,
    )
    expected_role = "support" if direction == "BUY" else "resistance"
    if key_level_role != expected_role:
      reasons.append(f"{ARCHETYPE_IMPULSE_PULLBACK}:impulse_level_role_mismatch")
      continue
    key_level = float(reference["level"])
    if reference["kind"] == "zone":
      zone_low = float(reference["bottom"])
      zone_high = float(reference["top"])
    elif direction == "BUY":
      zone_low = key_level
      zone_high = key_level + buffer
    else:
      zone_low = key_level - buffer
      zone_high = key_level
    worst = _worst_fill(direction=direction, zone_low=zone_low, zone_high=zone_high)
    if direction == "BUY":
      stop_price = key_level - buffer
      structural = (worst - stop_price) / pip_size
      room = context.buy_corridor_room_pips
    else:
      stop_price = key_level + buffer
      structural = (stop_price - worst) / pip_size
      room = context.sell_corridor_room_pips
    stop, reject = _stop_pips(
      structural=structural, cfg=cfg, spread_pips=spread_pips,
    )
    if stop is None:
      if reject:
        reasons.append(f"{ARCHETYPE_IMPULSE_PULLBACK}:{reject}")
      continue
    invalidation = (
      worst - stop * pip_size
      if direction == "BUY"
      else worst + stop * pip_size
    )
    if not _zone_stop_ordered(
      direction=direction,
      invalidation=invalidation,
      zone_low=zone_low,
      zone_high=zone_high,
    ):
      reasons.append(f"{ARCHETYPE_IMPULSE_PULLBACK}:stop_inside_zone")
      continue
    target = _select_target(
      direction=direction,
      worst_fill=worst,
      room_pips=room,
      stop_pips=stop,
      min_net=min_net,
      pip_size=pip_size,
      symbol=context.symbol,
      cfg=cfg,
    )
    if target is None:
      continue
    target_price, target_pips = target
    if room is not None and target_pips > room * 0.9:
      # mostly consumed
      continue
    source = deterministic_id(
      "impulse",
      context.symbol,
      direction,
      rounded_price(float(ev["origin"]), pip_size),
      rounded_price(float(ev["extreme"]), pip_size),
      rounded_price(key_level, pip_size),
    )
    oid = deterministic_id(
      context.symbol, ARCHETYPE_IMPULSE_PULLBACK, direction, context.context_id, source,
    )
    out.append(ScalpOpportunity(
      version=OPPORTUNITY_VERSION,
      opportunity_id=oid,
      context_id=context.context_id,
      symbol=context.symbol,
      archetype=ARCHETYPE_IMPULSE_PULLBACK,
      direction=direction,
      discovered_at=int(now),
      source_bar_ts=int(ev["bar_ts"]),
      zone_low=zone_low,
      zone_high=zone_high,
      key_level=key_level,
      trigger_type=str(ev["pattern"]),
      trigger_bar_ts=int(ev["bar_ts"]),
      trigger_price=entry,
      invalidation_price=invalidation,
      expected_target_price=target_price,
      expected_target_pips=target_pips,
      expected_stop_pips=stop,
      expected_reward_risk=target_pips / stop,
      location_position=pos,
      score=0.0,
      reasons=("impulse_pullback_continuation",),
      expires_at=int(now) + 15 * 60,
      episode_id=source,
      source_identity=source,
      measured={
        "strategy": STRATEGY_DISPLAY[ARCHETYPE_IMPULSE_PULLBACK],
        "retracement": ev.get("retracement"),
        "impulse_origin": ev.get("origin"),
        "impulse_extreme": ev.get("extreme"),
        "pullback_extreme": ev.get("pullback_extreme"),
        "impulse_bars": ev.get("impulse_bars"),
        "pullback_bars": ev.get("pullback_bars"),
        "impulse_atr_multiple": displacement_multiple,
        "body_dominance": body_dominance,
        "preferred_fib": ev.get("preferred"),
        "level_kind": reference["kind"],
        "key_level_role": key_level_role,
        "zone_score": reference.get("zone_score", 0.0),
        "zone_touches": reference.get("zone_touches", 0),
        "level_distance_pips": reference["distance"] / pip_size,
        "m1_atr": m1_atr,
        "session": context.session,
        "htf_bias": context.htf_bias,
        "bias_alignment": (
          "aligned"
          if (
            (direction == "BUY" and context.htf_bias == "up")
            or (direction == "SELL" and context.htf_bias == "down")
          )
          else "counter"
          if context.htf_bias in {"up", "down"}
          else "neutral"
        ),
      },
    ))
  return out


def _breakout_cfg(cfg: Any) -> Any:
  return getattr(_scalping_cfg(cfg), "breakout", None)


def _breakout_knobs(cfg: Any, *, atr: float, pip_size: float) -> dict[str, Any]:
  bo = _breakout_cfg(cfg)
  act = getattr(_scalping_cfg(cfg), "activation", None)
  box_max_atr = _parse_float(bo, "box_max_atr", 1.5)
  min_break_atr = _parse_float(bo, "min_break_atr", 0.25)
  min_box_bars = int(getattr(bo, "min_box_bars", 8) or 8) if bo is not None else 8
  max_box_bars = int(getattr(bo, "max_box_bars", 20) or 20) if bo is not None else 20
  touch_tol_atr = _parse_float(bo, "touch_tol_atr", 0.20)
  min_touches = int(getattr(bo, "min_touches_per_side", 2) or 2) if bo is not None else 2
  require_rej = bool(getattr(bo, "require_retest_rejection", True)) if bo is not None else True
  # Prefer explicit breakout lookback; floor so trigger_age=2 cannot sterilize.
  configured_retest = getattr(bo, "retest_lookback_bars", None) if bo is not None else None
  if configured_retest is not None:
    retest_lookback = max(4, int(configured_retest or 4))
  else:
    retest_lookback = max(4, int(getattr(act, "trigger_maximum_age_bars", 2) or 2))
  min_disp = max(pip_size * 3, float(atr) * min_break_atr)
  return {
    "box_max_atr": box_max_atr,
    "min_break_atr": min_break_atr,
    "min_box_bars": min_box_bars,
    "max_box_bars": max_box_bars,
    "touch_tol_atr": touch_tol_atr,
    "min_touches_per_side": min_touches,
    "require_retest_rejection": require_rej,
    "retest_lookback_bars": retest_lookback,
    "min_displacement": min_disp,
  }


def diagnose_breakout_reject(
  context: ScalpContextSnapshot,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
) -> str | None:
  """Dominant reject/state code for Redis ``breakout:{reason}`` telemetry."""
  if not _enabled(cfg, "breakout_retest"):
    return "disabled"
  if ARCHETYPE_BREAKOUT_RETEST not in context.permitted_archetypes:
    return "not_permitted"
  knobs = _breakout_knobs(cfg, atr=float(context.atr or 0.0), pip_size=pip_size)
  box = find_compression_box(
    m1_df,
    atr=float(context.atr or 0.0),
    min_box_bars=knobs["min_box_bars"],
    max_box_bars=knobs["max_box_bars"],
    box_max_atr=knobs["box_max_atr"],
    min_touches_per_side=knobs["min_touches_per_side"],
    touch_tol_atr=knobs["touch_tol_atr"],
  )
  if box is None:
    return "no_box"
  states: list[str] = []
  for direction in ("BUY", "SELL"):
    ev = detect_breakout_retest(
      m1_df,
      direction=direction,
      box_high=float(box["box_high"]),
      box_low=float(box["box_low"]),
      min_displacement=knobs["min_displacement"],
      retest_lookback_bars=knobs["retest_lookback_bars"],
      require_retest_rejection=knobs["require_retest_rejection"],
    )
    if ev is None:
      continue
    state = str(ev.get("state") or "")
    if state == "armed" or ev.get("accepted_break"):
      return "armed"
    if state:
      states.append(state)
  # Prefer the most advanced non-armed state for telemetry.
  priority = ("failed_break", "wait_retest", "wait_break", "no_box")
  for code in priority:
    if code in states:
      return code
  return states[0] if states else "wait_break"


def discover_breakout_retest(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
  spread_pips: float = 0.0,
  idle_reasons: list[str] | None = None,
) -> list[ScalpOpportunity]:
  if not _enabled(cfg, "breakout_retest"):
    return []
  if ARCHETYPE_BREAKOUT_RETEST not in context.permitted_archetypes:
    return []

  knobs = _breakout_knobs(cfg, atr=float(context.atr or 0.0), pip_size=pip_size)
  box = find_compression_box(
    m1_df,
    atr=float(context.atr or 0.0),
    min_box_bars=knobs["min_box_bars"],
    max_box_bars=knobs["max_box_bars"],
    box_max_atr=knobs["box_max_atr"],
    min_touches_per_side=knobs["min_touches_per_side"],
    touch_tol_atr=knobs["touch_tol_atr"],
  )
  if box is None:
    return []

  low = float(box["box_low"])
  high = float(box["box_high"])
  min_net = _parse_float(getattr(_scalping_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  buffer = _stop_buffer(context, cfg, pip_size)
  retest_lookback = knobs["retest_lookback_bars"]
  out: list[ScalpOpportunity] = []
  reasons = idle_reasons if idle_reasons is not None else []

  for direction in ("BUY", "SELL"):
    ev = detect_breakout_retest(
      m1_df,
      direction=direction,
      box_high=high,
      box_low=low,
      min_displacement=knobs["min_displacement"],
      retest_lookback_bars=retest_lookback,
      require_retest_rejection=knobs["require_retest_rejection"],
    )
    if ev is None or ev.get("state") != "armed" or not ev.get("accepted_break"):
      continue
    entry = float(ev["close"])
    level = float(ev["level"])
    zone_low = level - buffer
    zone_high = level + buffer
    worst = _worst_fill(direction=direction, zone_low=zone_low, zone_high=zone_high)
    if direction == "BUY":
      stop_price = min(float(m1_df["low"].iloc[-1]), level) - buffer
      structural = (worst - stop_price) / pip_size
      room = context.buy_corridor_room_pips
    else:
      stop_price = max(float(m1_df["high"].iloc[-1]), level) + buffer
      structural = (stop_price - worst) / pip_size
      room = context.sell_corridor_room_pips
    stop, reject = _stop_pips(
      structural=structural, cfg=cfg, spread_pips=spread_pips,
    )
    if stop is None:
      if reject:
        reasons.append(f"{ARCHETYPE_BREAKOUT_RETEST}:{reject}")
      continue
    invalidation = (
      worst - stop * pip_size
      if direction == "BUY"
      else worst + stop * pip_size
    )
    if not _zone_stop_ordered(
      direction=direction,
      invalidation=invalidation,
      zone_low=zone_low,
      zone_high=zone_high,
    ):
      reasons.append(f"{ARCHETYPE_BREAKOUT_RETEST}:stop_inside_zone")
      continue
    target = _select_target(
      direction=direction,
      worst_fill=worst,
      room_pips=room,
      stop_pips=stop,
      min_net=min_net,
      pip_size=pip_size,
      symbol=context.symbol,
      cfg=cfg,
    )
    if target is None:
      continue
    target_price, target_pips = target
    room_ok = room is not None and float(room) >= float(target_pips)
    source = deterministic_id(
      "box",
      context.symbol,
      direction,
      rounded_price(low, pip_size),
      rounded_price(high, pip_size),
    )
    oid = deterministic_id(
      context.symbol, ARCHETYPE_BREAKOUT_RETEST, direction, context.context_id, source,
    )
    out.append(ScalpOpportunity(
      version=OPPORTUNITY_VERSION,
      opportunity_id=oid,
      context_id=context.context_id,
      symbol=context.symbol,
      archetype=ARCHETYPE_BREAKOUT_RETEST,
      direction=direction,
      discovered_at=int(now),
      source_bar_ts=int(ev["bar_ts"]),
      zone_low=zone_low,
      zone_high=zone_high,
      key_level=level,
      trigger_type=str(ev["pattern"]),
      trigger_bar_ts=int(ev["bar_ts"]),
      trigger_price=entry,
      invalidation_price=invalidation,
      expected_target_price=target_price,
      expected_target_pips=target_pips,
      expected_stop_pips=stop,
      expected_reward_risk=target_pips / stop,
      location_position=context.dealing_range_position,
      score=0.0,
      reasons=("micro_breakout_retest",),
      expires_at=int(now) + 15 * 60,
      episode_id=source,
      source_identity=source,
      measured={
        "strategy": STRATEGY_DISPLAY[ARCHETYPE_BREAKOUT_RETEST],
        "compression_box": {
          "box_low": low,
          "box_high": high,
          "box_bars": box.get("box_bars"),
          "compression_atr": box.get("compression_atr"),
          "touch_count": box.get("touch_count"),
        },
        "breakout_evidence": {
          "accepted_break": bool(ev.get("accepted_break")),
          "correct_key_level_role": bool(ev.get("correct_key_level_role")),
          "retest_of_broken_level": bool(ev.get("retest_of_broken_level")),
          "retest_rejection": bool(ev.get("retest_rejection")),
          "directionally_valid_close": bool(ev.get("directionally_valid_close")),
          "target_room_beyond_breakout": bool(room_ok),
          "break_displacement": ev.get("break_displacement"),
          "state": "armed",
        },
      },
    ))
  return out


def discover_all(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
  spread_pips: float = 0.0,
  idle_reasons: list[str] | None = None,
) -> list[ScalpOpportunity]:
  found: list[ScalpOpportunity] = []
  reasons = idle_reasons if idle_reasons is not None else []
  found.extend(
    discover_range_sweep(
      context, micro, m1_df, cfg, pip_size=pip_size, now=now,
      spread_pips=spread_pips, idle_reasons=reasons,
    )
  )
  found.extend(
    discover_impulse_pullback(
      context, micro, m1_df, cfg, pip_size=pip_size, now=now,
      spread_pips=spread_pips, idle_reasons=reasons,
    )
  )
  found.extend(
    discover_breakout_retest(
      context, micro, m1_df, cfg, pip_size=pip_size, now=now,
      spread_pips=spread_pips, idle_reasons=reasons,
    )
  )
  # Deduplicate by opportunity_id
  by_id = {item.opportunity_id: item for item in found}
  return list(by_id.values())


def idle_discovery_reasons(
  context: ScalpContextSnapshot,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
) -> list[str]:
  """Explain an empty discover_all cycle for last_cycle telemetry."""
  reasons: list[str] = []
  if not context.permitted_archetypes:
    reasons.append("no_permitted_archetypes")
    return reasons
  if ARCHETYPE_RANGE_SWEEP in context.permitted_archetypes and _enabled(cfg, "range_sweep"):
    low = context.active_range_low
    high = context.active_range_high
    if low is None or high is None or high <= low:
      reasons.append("range_sweep:missing_active_range")
    else:
      width_pips = (high - low) / pip_size
      if width_pips < 25:
        reasons.append("range_sweep:range_too_narrow")
      # near_equilibrium is telemetry-only / not an absolute mute (owner 2026-08-06).
      act = getattr(_scalping_cfg(cfg), "activation", None)
      lookback = max(1, int(getattr(act, "trigger_maximum_age_bars", 2) or 2))
      buffer = _stop_buffer(context, cfg, pip_size)
      buy_ev = detect_sweep_reclaim(
        m1_df, direction="BUY", edge_price=low, tolerance=buffer, lookback_bars=lookback,
      )
      sell_ev = detect_sweep_reclaim(
        m1_df, direction="SELL", edge_price=high, tolerance=buffer, lookback_bars=lookback,
      )
      if buy_ev is None and sell_ev is None:
        reasons.append("range_sweep:no_edge_sweep_reclaim")
      else:
        pos = context.dealing_range_position
        loc = getattr(_scalping_cfg(cfg), "location", None)
        buy_max = _parse_float(loc, "range_buy_maximum_position", 0.35)
        sell_min = _parse_float(loc, "range_sell_minimum_position", 0.65)
        if buy_ev is not None and pos is not None and pos > buy_max:
          reasons.append("range_sweep:buy_location_blocked")
        if sell_ev is not None and pos is not None and pos < sell_min:
          reasons.append("range_sweep:sell_location_blocked")
  if ARCHETYPE_IMPULSE_PULLBACK in context.permitted_archetypes and _enabled(cfg, "impulse_pullback"):
    if not is_impulse_pullback_session_allowed(context.session, cfg):
      reasons.append(f"impulse_pullback:outside_allowed_session:{context.session}")
    else:
      reasons.append("impulse_pullback:not_matched")
  if ARCHETYPE_BREAKOUT_RETEST in context.permitted_archetypes and _enabled(cfg, "breakout_retest"):
    code = diagnose_breakout_reject(context, m1_df, cfg, pip_size=pip_size)
    if code and code != "armed":
      reasons.append(f"breakout_retest:{code}")
    elif code == "armed":
      reasons.append("breakout_retest:armed_but_unbookable")
    else:
      reasons.append("breakout_retest:not_matched")
  return reasons or ["no_microstructure_match"]
