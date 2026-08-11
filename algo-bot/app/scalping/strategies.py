"""Scalping strategy discovery (four archetypes)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

from app.scalping.microstructure import (
  detect_breakout_retest,
  detect_impulse_pullback,
  detect_momentum_ignition,
  detect_sweep_reclaim,
  macro_momentum_direction,
)
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_MOMENTUM_CHASE,
  ARCHETYPE_RANGE_SWEEP,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpOpportunity,
  MicroStructure,
  STRATEGY_DISPLAY,
  deterministic_id,
)


def _hfs_cfg(cfg: Any) -> Any:
  return getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None)


def _parse_float(section: Any, name: str, default: float) -> float:
  try:
    return float(getattr(section, name, default) or default)
  except (TypeError, ValueError):
    return default


def _select_target(
  *,
  direction: str,
  entry: float,
  room_pips: float | None,
  stop_pips: float | None,
  min_net: float,
  pip_size: float,
) -> tuple[float, float] | None:
  """Owner 2026-08-06: scalp target distance always equals stop distance.

  A scalp is a 1:1 gamble by design -- no ladder, no picking whichever
  preferred level happens to fit. If the stop's own distance doesn't clear
  the minimum net target or doesn't fit the available room, there is no
  opportunity here at all, not a smaller/larger substitute target.
  """
  if room_pips is None or pip_size <= 0 or stop_pips is None or stop_pips <= 0:
    return None
  target_pips = float(stop_pips)
  if target_pips < min_net or target_pips > float(room_pips):
    return None
  if str(direction).upper() == "BUY":
    return entry + target_pips * pip_size, target_pips
  return entry - target_pips * pip_size, target_pips


def _stop_pips(
  *,
  structural: float,
  cfg: Any,
) -> float | None:
  stop_cfg = getattr(_hfs_cfg(cfg), "stop", None)
  mn = _parse_float(stop_cfg, "minimum_pips", 12.0)
  mx = _parse_float(stop_cfg, "maximum_pips", 30.0)
  value = abs(float(structural))
  if value <= 0:
    return None
  # Prod 2026-08-06: deep XAU wicks (> max) or tight reclaim (< min) used
  # to return None and drop the whole opportunity with discovered=0 and no
  # telemetry. Clamp into the HFS envelope like the main protective-stop
  # path so edge sweeps still publish.
  if mx < mn:
    return None
  return min(max(value, mn), mx)


def _fights_fresh_macro_momentum(
  m1_df: pd.DataFrame, *, direction: str, atr: float, cfg: Any,
) -> bool:
  """True when a wide-window move opposes this entry's own direction.

  Live 2026-08-06: impulse_pullback sold the "top" of a range whose high
  was the pre-crash level, mid-reclaim of a flash crash under an hour old
  -- its own 30-bar lookback never saw the move that made that level
  matter. range_sweep has the identical blind spot at a range edge.
  Doesn't apply to momentum_chase, which *is* the fresh-move entry.
  """
  mom_cfg = getattr(_hfs_cfg(cfg), "momentum", None)
  lookback = int(_parse_float(mom_cfg, "macro_veto_lookback_bars", 60.0))
  min_displacement_atr = _parse_float(mom_cfg, "macro_veto_min_displacement_atr", 2.5)
  macro = macro_momentum_direction(
    m1_df, atr=atr, min_displacement_atr=min_displacement_atr, lookback_bars=lookback,
  )
  return macro is not None and macro != str(direction).upper()


def _technique_require_sweep_body(cfg: Any) -> bool:
  from app.autotrade.killzone import technique_enforce

  if not technique_enforce(cfg):
    return False
  tech = getattr(getattr(cfg, "execution", None), "technique", None)
  if tech is None:
    return True
  return bool(getattr(tech, "require_sweep_body", True))


def _enabled(cfg: Any, name: str) -> bool:
  root = _hfs_cfg(cfg)
  arch = getattr(root, "archetypes", None)
  attr = f"{name}_enabled"
  return bool(getattr(arch, attr, True))


def discover_range_sweep(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
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

  loc = getattr(_hfs_cfg(cfg), "location", None)
  buy_max = _parse_float(loc, "range_buy_maximum_position", 0.35)
  sell_min = _parse_float(loc, "range_sell_minimum_position", 0.65)
  pos = context.dealing_range_position
  # Owner 2026-08-06: near-EQ mute used to return [] and kill Asia discovery
  # for hours while price sat mid-range. Location filters below already gate
  # BUY/SELL by dealing position — do not blank the whole archetype here.

  out: list[ScalpOpportunity] = []
  buffer = max(pip_size * 2, context.atr * 0.05)
  min_net = _parse_float(getattr(_hfs_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  act = getattr(_hfs_cfg(cfg), "activation", None)
  lookback = max(1, int(getattr(act, "trigger_maximum_age_bars", 2) or 2))

  # BUY lower edge
  buy_ev = detect_sweep_reclaim(
    m1_df,
    direction="BUY",
    edge_price=low,
    tolerance=buffer,
    lookback_bars=lookback,
  )
  if buy_ev is not None:
    if pos is not None and pos > buy_max:
      pass
    elif _fights_fresh_macro_momentum(
      m1_df, direction="BUY", atr=context.atr, cfg=cfg,
    ):
      pass
    else:
      entry = float(buy_ev["close"])
      stop_price = float(buy_ev["extreme"]) - buffer
      stop = _stop_pips(structural=(entry - stop_price) / pip_size, cfg=cfg)
      target = _select_target(
        direction="BUY",
        entry=entry,
        room_pips=context.buy_corridor_room_pips,
        stop_pips=stop,
        min_net=min_net,
        pip_size=pip_size,
      )
      if stop is not None and target is not None:
        target_price, target_pips = target
        rr = target_pips / stop if stop else 0.0
        source = deterministic_id("range", context.symbol, "BUY", round(low, 2))
        oid = deterministic_id(
          context.symbol, ARCHETYPE_RANGE_SWEEP, "BUY", context.context_id, source,
        )
        zone_low = low - buffer
        zone_high = low + buffer * 2
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
          invalidation_price=stop_price,
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
  if sell_ev is not None:
    if pos is not None and pos < sell_min:
      pass
    elif _fights_fresh_macro_momentum(
      m1_df, direction="SELL", atr=context.atr, cfg=cfg,
    ):
      pass
    else:
      entry = float(sell_ev["close"])
      stop_price = float(sell_ev["extreme"]) + buffer
      stop = _stop_pips(structural=(stop_price - entry) / pip_size, cfg=cfg)
      target = _select_target(
        direction="SELL",
        entry=entry,
        room_pips=context.sell_corridor_room_pips,
        stop_pips=stop,
        min_net=min_net,
        pip_size=pip_size,
      )
      if stop is not None and target is not None:
        target_price, target_pips = target
        rr = target_pips / stop if stop else 0.0
        source = deterministic_id("range", context.symbol, "SELL", round(high, 2))
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
          zone_low=high - buffer * 2,
          zone_high=high + buffer,
          key_level=high,
          trigger_type=str(sell_ev["pattern"]),
          trigger_bar_ts=int(sell_ev["bar_ts"]),
          trigger_price=entry,
          invalidation_price=stop_price,
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
) -> list[ScalpOpportunity]:
  if not _enabled(cfg, "impulse_pullback"):
    return []
  if ARCHETYPE_IMPULSE_PULLBACK not in context.permitted_archetypes:
    return []

  loc = getattr(_hfs_cfg(cfg), "location", None)
  buy_max = _parse_float(loc, "pullback_buy_maximum_position", 0.75)
  sell_min = _parse_float(loc, "pullback_sell_minimum_position", 0.25)
  min_net = _parse_float(getattr(_hfs_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  buffer = max(pip_size * 2, context.atr * _parse_float(getattr(_hfs_cfg(cfg), "stop", None), "buffer_atr", 0.10))
  out: list[ScalpOpportunity] = []
  pos = context.dealing_range_position

  for direction in ("BUY", "SELL"):
    if direction == "BUY" and pos is not None and pos > buy_max:
      continue
    if direction == "SELL" and pos is not None and pos < sell_min:
      continue
    ev = detect_impulse_pullback(m1_df, direction=direction)
    if ev is None:
      continue
    if ev.get("rejected"):
      continue
    if _technique_require_sweep_body(cfg):
      edge = (
        float(context.active_range_low)
        if direction == "BUY"
        else float(context.active_range_high)
      )
      sweep = detect_sweep_reclaim(
        m1_df,
        direction=direction,
        edge_price=edge,
        tolerance=buffer,
        lookback_bars=max(
          1,
          int(getattr(getattr(_hfs_cfg(cfg), "activation", None), "trigger_maximum_age_bars", 2) or 2),
        ),
      )
      if sweep is None:
        # Diagnostic (2026-08-11): execution.technique.require_sweep_body
        # silently drops an otherwise-matched impulse_pullback candidate
        # with zero telemetry - same visibility gap as detectors._pd_gate.
        # Only logs when everything else (ev matched, not rejected) already
        # passed and this specific check is what killed it.
        log.info(
          "sweep gate rejection symbol=%s archetype=impulse_pullback "
          "direction=%s",
          context.symbol, direction,
        )
        continue
    if _fights_fresh_macro_momentum(
      m1_df, direction=direction, atr=context.atr, cfg=cfg,
    ):
      continue
    entry = float(ev["close"])
    if direction == "BUY":
      stop_price = float(ev["origin"]) - buffer
      stop = _stop_pips(structural=(entry - stop_price) / pip_size, cfg=cfg)
      room = context.buy_corridor_room_pips
    else:
      stop_price = float(ev["origin"]) + buffer
      stop = _stop_pips(structural=(stop_price - entry) / pip_size, cfg=cfg)
      room = context.sell_corridor_room_pips
    target = _select_target(
      direction=direction,
      entry=entry,
      room_pips=room,
      stop_pips=stop,
      min_net=min_net,
      pip_size=pip_size,
    )
    if stop is None or target is None:
      continue
    target_price, target_pips = target
    if room is not None and target_pips > room * 0.9:
      # mostly consumed
      continue
    source = deterministic_id(
      "impulse", context.symbol, direction, round(float(ev["origin"]), 2), round(float(ev["extreme"]), 2),
    )
    oid = deterministic_id(
      context.symbol, ARCHETYPE_IMPULSE_PULLBACK, direction, context.context_id, source,
    )
    band = max(buffer, pip_size * 3)
    out.append(ScalpOpportunity(
      version=OPPORTUNITY_VERSION,
      opportunity_id=oid,
      context_id=context.context_id,
      symbol=context.symbol,
      archetype=ARCHETYPE_IMPULSE_PULLBACK,
      direction=direction,
      discovered_at=int(now),
      source_bar_ts=int(ev["bar_ts"]),
      zone_low=entry - band,
      zone_high=entry + band,
      key_level=entry,
      trigger_type=str(ev["pattern"]),
      trigger_bar_ts=int(ev["bar_ts"]),
      trigger_price=entry,
      invalidation_price=stop_price,
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
      },
    ))
  return out


def discover_breakout_retest(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
) -> list[ScalpOpportunity]:
  if not _enabled(cfg, "breakout_retest"):
    return []
  if ARCHETYPE_BREAKOUT_RETEST not in context.permitted_archetypes:
    return []
  low = context.active_range_low
  high = context.active_range_high
  if low is None or high is None:
    return []

  min_net = _parse_float(getattr(_hfs_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  buffer = max(pip_size * 2, context.atr * 0.1)
  min_disp = max(pip_size * 3, context.atr * 0.15)
  out: list[ScalpOpportunity] = []

  for direction in ("BUY", "SELL"):
    ev = detect_breakout_retest(
      m1_df,
      direction=direction,
      box_high=high,
      box_low=low,
      min_displacement=min_disp,
    )
    if ev is None or ev.get("state") == "wait_retest" or not ev.get("accepted_break"):
      continue
    entry = float(ev["close"])
    level = float(ev["level"])
    if direction == "BUY":
      stop_price = min(float(m1_df["low"].iloc[-1]), level) - buffer
      stop = _stop_pips(structural=(entry - stop_price) / pip_size, cfg=cfg)
      room = context.buy_corridor_room_pips
    else:
      stop_price = max(float(m1_df["high"].iloc[-1]), level) + buffer
      stop = _stop_pips(structural=(stop_price - entry) / pip_size, cfg=cfg)
      room = context.sell_corridor_room_pips
    target = _select_target(
      direction=direction,
      entry=entry,
      room_pips=room,
      stop_pips=stop,
      min_net=min_net,
      pip_size=pip_size,
    )
    if stop is None or target is None:
      continue
    target_price, target_pips = target
    source = deterministic_id("box", context.symbol, direction, round(low, 2), round(high, 2))
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
      zone_low=level - buffer,
      zone_high=level + buffer,
      key_level=level,
      trigger_type=str(ev["pattern"]),
      trigger_bar_ts=int(ev["bar_ts"]),
      trigger_price=entry,
      invalidation_price=stop_price,
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
        "breakout_evidence": {
          "accepted_break": True,
          "correct_key_level_role": True,
          "retest_of_broken_level": True,
          "directionally_valid_close": True,
          "target_room_beyond_breakout": True,
        },
      },
    ))
  return out


def discover_momentum_chase(
  context: ScalpContextSnapshot,
  micro: MicroStructure,
  m1_df: pd.DataFrame,
  cfg: Any,
  *,
  pip_size: float,
  now: int,
) -> list[ScalpOpportunity]:
  """Chase a live, still-accelerating thrust instead of waiting for it to
  pause. The other three archetypes all require some kind of pause first
  (retracement, retest, sweep+reclaim) -- none of them fire on a straight,
  uninterrupted run, which can cover the entire distance a scalp would have
  wanted before ever handing back the setup they're waiting for.
  """
  if not _enabled(cfg, "momentum_chase"):
    return []
  if ARCHETYPE_MOMENTUM_CHASE not in context.permitted_archetypes:
    return []

  loc = getattr(_hfs_cfg(cfg), "location", None)
  # A chase is trend-following by nature; the ceiling here only exists so
  # it doesn't buy the very top / sell the very bottom of the permitted
  # range, not to mute it the way the mean-reverting archetypes are muted.
  buy_max = _parse_float(loc, "momentum_buy_maximum_position", 0.85)
  sell_min = _parse_float(loc, "momentum_sell_minimum_position", 0.15)
  min_net = _parse_float(getattr(_hfs_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  mom_cfg = getattr(_hfs_cfg(cfg), "momentum", None)
  min_displacement_atr = _parse_float(mom_cfg, "min_displacement_atr", 1.2)
  lookback_bars = int(_parse_float(mom_cfg, "lookback_bars", 5.0))
  min_directional_bars = int(_parse_float(mom_cfg, "min_directional_bars", 4.0))
  buffer = max(pip_size * 2, context.atr * 0.15)
  out: list[ScalpOpportunity] = []
  pos = context.dealing_range_position

  for direction in ("BUY", "SELL"):
    if direction == "BUY" and pos is not None and pos > buy_max:
      continue
    if direction == "SELL" and pos is not None and pos < sell_min:
      continue
    ev = detect_momentum_ignition(
      m1_df,
      direction=direction,
      atr=context.atr,
      min_displacement_atr=min_displacement_atr,
      lookback_bars=lookback_bars,
      min_directional_bars=min_directional_bars,
    )
    if ev is None or ev.get("rejected"):
      continue
    if _technique_require_sweep_body(cfg):
      edge = (
        float(context.active_range_low)
        if direction == "BUY"
        else float(context.active_range_high)
      )
      sweep = detect_sweep_reclaim(
        m1_df,
        direction=direction,
        edge_price=edge,
        tolerance=buffer,
        lookback_bars=max(1, lookback_bars),
      )
      if sweep is None:
        # Diagnostic (2026-08-11): see the matching note in
        # discover_impulse_pullback - same silent gate, same fix.
        log.info(
          "sweep gate rejection symbol=%s archetype=momentum_chase "
          "direction=%s",
          context.symbol, direction,
        )
        continue
    entry = float(ev["close"])
    if direction == "BUY":
      stop_price = float(ev["extreme"]) - buffer
      stop = _stop_pips(structural=(entry - stop_price) / pip_size, cfg=cfg)
      room = context.buy_corridor_room_pips
    else:
      stop_price = float(ev["extreme"]) + buffer
      stop = _stop_pips(structural=(stop_price - entry) / pip_size, cfg=cfg)
      room = context.sell_corridor_room_pips
    target = _select_target(
      direction=direction,
      entry=entry,
      room_pips=room,
      stop_pips=stop,
      min_net=min_net,
      pip_size=pip_size,
    )
    if stop is None or target is None:
      continue
    target_price, target_pips = target
    source = deterministic_id(
      "momentum", context.symbol, direction, round(entry, 2), int(ev["bar_ts"]),
    )
    oid = deterministic_id(
      context.symbol, ARCHETYPE_MOMENTUM_CHASE, direction, context.context_id, source,
    )
    band = max(buffer, pip_size * 3)
    out.append(ScalpOpportunity(
      version=OPPORTUNITY_VERSION,
      opportunity_id=oid,
      context_id=context.context_id,
      symbol=context.symbol,
      archetype=ARCHETYPE_MOMENTUM_CHASE,
      direction=direction,
      discovered_at=int(now),
      source_bar_ts=int(ev["bar_ts"]),
      zone_low=entry - band,
      zone_high=entry + band,
      key_level=entry,
      trigger_type=str(ev["pattern"]),
      trigger_bar_ts=int(ev["bar_ts"]),
      trigger_price=entry,
      invalidation_price=stop_price,
      expected_target_price=target_price,
      expected_target_pips=target_pips,
      expected_stop_pips=stop,
      expected_reward_risk=target_pips / stop,
      location_position=pos,
      score=0.0,
      reasons=("momentum_ignition",),
      expires_at=int(now) + 15 * 60,
      episode_id=source,
      source_identity=source,
      measured={
        "strategy": STRATEGY_DISPLAY[ARCHETYPE_MOMENTUM_CHASE],
        "displacement_atr": ev.get("displacement_atr"),
        "directional_bars": ev.get("directional_bars"),
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
) -> list[ScalpOpportunity]:
  found: list[ScalpOpportunity] = []
  found.extend(discover_range_sweep(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
  found.extend(discover_impulse_pullback(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
  found.extend(discover_breakout_retest(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
  found.extend(discover_momentum_chase(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
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
      act = getattr(_hfs_cfg(cfg), "activation", None)
      lookback = max(1, int(getattr(act, "trigger_maximum_age_bars", 2) or 2))
      buffer = max(pip_size * 2, context.atr * 0.05)
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
        loc = getattr(_hfs_cfg(cfg), "location", None)
        buy_max = _parse_float(loc, "range_buy_maximum_position", 0.35)
        sell_min = _parse_float(loc, "range_sell_minimum_position", 0.65)
        if buy_ev is not None and pos is not None and pos > buy_max:
          reasons.append("range_sweep:buy_location_blocked")
        if sell_ev is not None and pos is not None and pos < sell_min:
          reasons.append("range_sweep:sell_location_blocked")
  if ARCHETYPE_IMPULSE_PULLBACK in context.permitted_archetypes and _enabled(cfg, "impulse_pullback"):
    reasons.append("impulse_pullback:not_matched")
  if ARCHETYPE_BREAKOUT_RETEST in context.permitted_archetypes and _enabled(cfg, "breakout_retest"):
    reasons.append("breakout_retest:not_matched")
  if ARCHETYPE_MOMENTUM_CHASE in context.permitted_archetypes and _enabled(cfg, "momentum_chase"):
    reasons.append("momentum_chase:not_matched")
  return reasons or ["no_microstructure_match"]
