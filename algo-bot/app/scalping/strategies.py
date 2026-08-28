"""Scalping strategy discovery (three archetypes)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

from app.scalping.microstructure import (
  detect_breakout_retest,
  detect_impulse_pullback,
  detect_sweep_reclaim,
  find_compression_box,
  macro_momentum_direction,
)
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
  symbol: str = "",
  cfg: Any | None = None,
) -> tuple[float, float] | None:
  """Owner 2026-08-11: every scalp is 1:2 when the available room supports
  it, 1:1 otherwise - no ladder, no picking whichever preferred level
  happens to fit, and never anything outside this pair. If neither ratio
  clears the minimum net target and fits the available room, there is no
  opportunity here at all, not a smaller/larger substitute target.

  Instruments configured with ``fixed_rr`` keep their configured ratio as the
  preference and may fall back to exactly 1:1 when clean room cannot hold it.

  XAU publish turns a selected 1:2 into TP1@1R (50%) + TP2@2R (50%) with
  SL moved to BE after TP1 books (runner protected). A selected 1:1
  publishes a **single** target and books **full volume** at that print
  (close_ratio=1.0; no BE trail needed). A fixed-RR instrument expands
  the selected final target into its configured R ladder during
  execution-policy planning.
  """
  if room_pips is None or pip_size <= 0 or stop_pips is None or stop_pips <= 0:
    return None
  from app.core.instrument_geometry import fixed_reward_risk
  configured_rr = fixed_reward_risk(symbol, cfg) if symbol else None
  ratios = (
    (configured_rr, 1.0)
    if configured_rr is not None and configured_rr > 1.0
    else (configured_rr,)
    if configured_rr is not None
    else (2.0, 1.0)
  )
  for reward_risk in ratios:
    target_pips = float(stop_pips) * reward_risk
    if target_pips < min_net or target_pips > float(room_pips):
      continue
    if str(direction).upper() == "BUY":
      return entry + target_pips * pip_size, target_pips
    return entry - target_pips * pip_size, target_pips
  return None


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
  """
  mom_cfg = getattr(_hfs_cfg(cfg), "momentum", None)
  lookback = int(_parse_float(mom_cfg, "macro_veto_lookback_bars", 60.0))
  min_displacement_atr = _parse_float(mom_cfg, "macro_veto_min_displacement_atr", 2.5)
  macro = macro_momentum_direction(
    m1_df, atr=atr, min_displacement_atr=min_displacement_atr, lookback_bars=lookback,
  )
  return macro is not None and macro != str(direction).upper()


def _technique_require_sweep_body(cfg: Any) -> bool:
  from app.autotrade.killzone import technique_require_sweep_body

  return technique_require_sweep_body(cfg)


def _enabled(cfg: Any, name: str) -> bool:
  """Archetype enable flags are fail-closed when missing (not default-on)."""
  root = _hfs_cfg(cfg)
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
        symbol=context.symbol,
        cfg=cfg,
      )
      if stop is not None and target is not None:
        target_price, target_pips = target
        rr = target_pips / stop if stop else 0.0
        source = deterministic_id(
          "range", context.symbol, "BUY", rounded_price(low, pip_size),
        )
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
        symbol=context.symbol,
        cfg=cfg,
      )
      if stop is not None and target is not None:
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
  buy_max = _parse_float(loc, "pullback_buy_maximum_position", 0.60)
  sell_min = _parse_float(loc, "pullback_sell_minimum_position", 0.40)
  min_net = _parse_float(getattr(_hfs_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  buffer = max(pip_size * 2, context.atr * _parse_float(getattr(_hfs_cfg(cfg), "stop", None), "buffer_atr", 0.10))
  out: list[ScalpOpportunity] = []
  pos = context.dealing_range_position
  htf = str(context.htf_bias or "unknown").casefold()

  for direction in ("BUY", "SELL"):
    if direction == "BUY" and pos is not None and pos > buy_max:
      continue
    if direction == "SELL" and pos is not None and pos < sell_min:
      continue
    # Live 2026-08-12: soft HTF penalty was not enough — hard-block counter-HTF.
    if htf == "up" and direction == "SELL":
      continue
    if htf == "down" and direction == "BUY":
      continue
    ev = detect_impulse_pullback(m1_df, direction=direction)
    if ev is None:
      continue
    if ev.get("rejected"):
      continue
    # Continuation must not require sweep-reclaim (that gate belongs to
    # range_sweep). Owner 2026-08-26: require_sweep_body was killing L1.
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
      symbol=context.symbol,
      cfg=cfg,
    )
    if stop is None or target is None:
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
        "impulse_origin": ev.get("origin"),
        "impulse_extreme": ev.get("extreme"),
      },
    ))
  return out


def _breakout_cfg(cfg: Any) -> Any:
  return getattr(_hfs_cfg(cfg), "breakout", None)


def _breakout_knobs(cfg: Any, *, atr: float, pip_size: float) -> dict[str, Any]:
  bo = _breakout_cfg(cfg)
  act = getattr(_hfs_cfg(cfg), "activation", None)
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
  min_net = _parse_float(getattr(_hfs_cfg(cfg), "target", None), "minimum_net_target_pips", 15.0)
  buffer = max(pip_size * 2, context.atr * 0.1)
  retest_lookback = knobs["retest_lookback_bars"]
  out: list[ScalpOpportunity] = []

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
      symbol=context.symbol,
      cfg=cfg,
    )
    if stop is None or target is None:
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
) -> list[ScalpOpportunity]:
  found: list[ScalpOpportunity] = []
  found.extend(discover_range_sweep(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
  found.extend(discover_impulse_pullback(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
  found.extend(discover_breakout_retest(context, micro, m1_df, cfg, pip_size=pip_size, now=now))
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
    code = diagnose_breakout_reject(context, m1_df, cfg, pip_size=pip_size)
    if code and code != "armed":
      reasons.append(f"breakout_retest:{code}")
    elif code == "armed":
      reasons.append("breakout_retest:armed_but_unbookable")
    else:
      reasons.append("breakout_retest:not_matched")
  return reasons or ["no_microstructure_match"]
