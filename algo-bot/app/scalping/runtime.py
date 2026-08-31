"""Shadow/paper M1 scalping event loop."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.analysis.ohlc_source import RedisOHLCSource
from app.autotrade import units
from app.core.config import runtime_config
from app.runtime.instrument_config import instrument_runtime_view
from app.runtime.price_identity import pip_price_digits
from app.persistence import redis_state
from app.scalping.activation import evaluate_scalp_activation
from app.scalping.context import (
  is_context_fresh,
  is_scalping_symbol,
  load_current_context,
  save_context,
)
from app.scalping.unified_context import (
  build_scalp_context_and_micro,
  load_scalp_ohlc_windows,
)
from app.scalping.models import (
  ARMED,
  DISCOVERED,
  EXECUTABLE,
  MISSED,
  PUBLISHED,
  ScalpLifecycleRecord,
  ScalpSignal,
  deterministic_id,
)
from app.scalping.lifecycle import (
  load_lifecycle,
  save_lifecycle,
  transition,
)
from app.scalping.microstructure import build_micro_structure
from app.scalping.publish import build_scalp_strategy_match, publish_scalp_live
from app.scalping.ranking import rank_opportunities, score_opportunity
from app.scalping.risk import (
  apply_daily_reset,
  apply_loss_streak_cooldown_reset,
  evaluate_risk,
  live_exposure_ids,
  load_risk,
  reconcile_open_positions,
  save_risk,
)
from app.scalping.strategies import (
  diagnose_breakout_reject,
  discover_all,
  idle_discovery_reasons,
)
from app.scalping.telemetry import incr, record_cycle, set_last
from app.autotrade import worker


log = logging.getLogger(__name__)


def _scalping_cfg(cfg: Any = None) -> Any:
  cfg = cfg or runtime_config
  return getattr(getattr(cfg, "strategies", None), "scalping", None)


def _mode(cfg: Any = None) -> str:
  section = _scalping_cfg(cfg)
  mode = str(getattr(section, "mode", "off") or "off").strip().lower()
  return mode if mode in {"off", "shadow", "paper", "live"} else "off"


def _instrument_cfg(symbol: str, cfg: Any) -> Any:
  if callable(getattr(cfg, "for_instrument", None)):
    return instrument_runtime_view(symbol, cfg)
  return cfg


def _pip_size(symbol: str, cfg: Any) -> float:
  configured = getattr(getattr(cfg, "units", None), "pip_size", None)
  return float(configured) if configured is not None else units.pip_size(symbol)


def _parse_bar_event(data: object) -> tuple[str, str, int] | None:
  text = data.decode() if isinstance(data, bytes) else str(data)
  parts = text.strip().split(":")
  if len(parts) < 3:
    return None
  symbol, tf = parts[0].upper(), parts[1].upper()
  try:
    bar_ts = int(parts[2])
  except ValueError:
    return None
  return symbol, tf, bar_ts


async def _load_quote(client: Any, symbol: str) -> tuple[float, float, int] | None:
  raw = await client.get(f"price:{symbol.upper()}:spot")
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
    return float(payload["bid"]), float(payload["ask"]), int(payload["ts"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None


def _bias_alignment(htf_bias: str, direction: str) -> str:
  bias = str(htf_bias or "unknown").casefold()
  dir_u = str(direction or "").upper()
  if bias == "up" and dir_u == "BUY":
    return "aligned"
  if bias == "down" and dir_u == "SELL":
    return "aligned"
  if bias == "up" and dir_u == "SELL":
    return "counter"
  if bias == "down" and dir_u == "BUY":
    return "counter"
  return "neutral"


async def _ensure_context(
  client: Any,
  source: RedisOHLCSource,
  *,
  symbol: str,
  now: int,
  cfg: Any,
  force: bool = False,
) -> tuple[Any, float]:
  existing = await load_current_context(client, symbol, cfg)
  ctx_cfg = getattr(_scalping_cfg(cfg), "context", None)
  max_age = int(getattr(ctx_cfg, "maximum_m5_age_seconds", 420) or 420)
  if (
    existing is not None
    and not force
    and is_context_fresh(existing, now, max_age, cfg)
  ):
    return existing, 0.0

  windows = await load_scalp_ohlc_windows(
    source, symbol, m1_bars=1, m5_bars=120, m15_bars=120, h1_bars=120,
  )
  m5 = windows["m5"]
  quote = await _load_quote(client, symbol)
  if quote is None or m5 is None or m5.empty:
    return existing, 0.0
  bid, ask, _ = quote
  mid = (bid + ask) / 2.0
  pip = _pip_size(symbol, cfg)
  snapshot, _micro, analysis_labels_ms = await asyncio.to_thread(
    build_scalp_context_and_micro,
    symbol=symbol,
    windows=windows,
    price=mid,
    pip_size=pip,
    now=now,
    cfg=cfg,
  )
  if snapshot is None:
    return existing, analysis_labels_ms
  current_ttl = int(getattr(ctx_cfg, "current_context_ttl_seconds", 3600) or 3600)
  historic_ttl = int(getattr(ctx_cfg, "historic_context_ttl_seconds", 86400) or 86400)
  await save_context(
    client,
    snapshot,
    current_ttl=current_ttl,
    historic_ttl=historic_ttl,
  )
  await set_last(client, "context", symbol, json.loads(snapshot.to_json()))
  return snapshot, analysis_labels_ms


async def process_m1_bar(
  client: Any,
  *,
  symbol: str,
  bar_ts: int,
  ohlc_source: RedisOHLCSource | None = None,
  cfg: Any | None = None,
) -> dict[str, Any]:
  """Idempotent one-bar scalping cycle. Never publishes broker candidates."""
  cfg = _instrument_cfg(symbol, cfg or runtime_config)
  mode = _mode(cfg)
  result: dict[str, Any] = {
    "symbol": symbol,
    "bar_ts": bar_ts,
    "mode": mode,
    "allowed": [],
    "blocked": [],
  }
  if mode == "off":
    result["reason"] = "scalp_mode_off"
    return result
  if not is_scalping_symbol(symbol, cfg):
    result["reason"] = "scalp_symbol_not_enabled"
    return result

  processed_key = f"scalp:processed:{symbol.upper()}:M1:{int(bar_ts)}"
  if await client.set(processed_key, "1", nx=True, ex=7 * 24 * 3600) is None:
    result["reason"] = "duplicate_bar_event"
    return result

  t0 = time.perf_counter()
  source = ohlc_source or RedisOHLCSource(client)
  now = int(bar_ts)

  t_ctx = time.perf_counter()
  context, analysis_labels_ms = await _ensure_context(
    client, source, symbol=symbol, now=now, cfg=cfg,
  )
  context_ms = (time.perf_counter() - t_ctx) * 1000.0
  if context is None:
    result["reason"] = "scalp_context_missing"
    await incr(client, symbol, "opportunity_blocked:scalp_context_missing")
    return result
  await incr(client, symbol, f"htf_bias:{context.htf_bias}")
  ctx_cfg = getattr(_scalping_cfg(cfg), "context", None)
  max_age = int(getattr(ctx_cfg, "maximum_m5_age_seconds", 420) or 420)
  soft_age = max(max_age * 2, max_age + 300)
  context_age = int(now) - int(getattr(context, "m5_bar_ts", now) or now)
  if context_age > soft_age:
    # Truly ancient context — fail closed.
    result["reason"] = "scalp_context_stale"
    await incr(client, symbol, "opportunity_blocked:scalp_context_stale")
    return result
  if not is_context_fresh(context, now, max_age, cfg):
    # Soft stale: M5 feed lag at bar boundaries used to abort every cycle
    # (26 soft-stales in one prod window). Keep discovering off the last
    # good snapshot and record telemetry.
    result["context_soft_stale"] = True
    result["context_age_seconds"] = context_age
    await incr(client, symbol, "opportunity_soft_stale:scalp_context")

  lookback = int(getattr(ctx_cfg, "m1_lookback_bars", 60) or 60)
  t_micro = time.perf_counter()
  m1 = await source.window(symbol, "M1", lookback)
  pip = _pip_size(symbol, cfg)
  # build_micro_structure/discover_all are pandas/CPU-heavy, same as
  # build_context/build_map/build_scalp_context_snapshot elsewhere in this
  # codebase - _ensure_context right above already offloads its own heavy
  # call via asyncio.to_thread, but these two ran inline on the shared
  # event loop that also runs Telegram polling. Fires on every M1 bar
  # close for every HFS-enabled symbol (once a minute, all symbols'
  # bars closing in sync) - a real, frequent blocking cost.
  micro = await asyncio.to_thread(
    build_micro_structure,
    m1,
    equal_tol=0.5 * pip,
    price_digits=int(
      getattr(
        getattr(cfg, "units", None),
        "price_digits",
        pip_price_digits(pip),
      )
    ),
  )
  micro_ms = (time.perf_counter() - t_micro) * 1000.0

  t_strat = time.perf_counter()
  opportunities = await asyncio.to_thread(
    discover_all, context, micro, m1, cfg, pip_size=pip, now=now,
  )
  idle_reasons = (
    idle_discovery_reasons(context, m1, cfg, pip_size=pip)
    if not opportunities
    else []
  )
  strat_ms = (time.perf_counter() - t_strat) * 1000.0

  # Per-reason breakout telemetry every cycle (quiet archetype diagnosis).
  try:
    breakout_reason = diagnose_breakout_reject(
      context, m1, cfg, pip_size=pip,
    )
    if breakout_reason:
      await incr(client, symbol, f"breakout:{breakout_reason}")
  except Exception:
    log.exception("breakout reject telemetry failed symbol=%s", symbol)

  quote = await _load_quote(client, symbol)
  if quote is None:
    result["reason"] = "scalp_quote_missing"
    return result
  bid, ask, qts = quote

  risk_state = await load_risk(client, symbol)
  risk_state = apply_daily_reset(
    risk_state, cfg, now=now, session=context.session
  )
  risk_state = apply_loss_streak_cooldown_reset(risk_state, cfg, now=now)
  try:
    from app.autotrade.active_exposure import load_active_exposures

    # Per-symbol book only — a live GBPJPY plan must not inflate EURUSD
    # HFS concurrent / ghost-reconcile (live 2026-08-17 cross-symbol lock).
    live = live_exposure_ids(
      await load_active_exposures(client, symbol=symbol)
    )
  except Exception:
    log.exception("hfs live exposure reconcile failed symbol=%s", symbol)
    live = set()
  risk_state = reconcile_open_positions(risk_state, live)
  await save_risk(client, symbol, risk_state)
  risk = evaluate_risk(risk_state, cfg, session=context.session, now=now)

  # Shared MAD clock for Redis / Range Edge technique only — never used to
  # rank or gate HFS opportunities (owner 2026-08-26).
  mad_payload: dict[str, Any] | None = None
  if mode in {"shadow", "paper", "live"}:
    try:
      from app.analysis.mad_phase import enrich_mad_payload_for_shadow, refresh_mad_for_symbol

      prior_m5 = await source.window(symbol, "M5", 120)
      mad_df = prior_m5 if prior_m5 is not None and not prior_m5.empty else m1
      last = m1.iloc[-1] if m1 is not None and not m1.empty else None
      mid = (float(bid) + float(ask)) / 2.0
      mad = await refresh_mad_for_symbol(
        client,
        symbol=symbol,
        ohlc=mad_df if mad_df is not None else m1,
        now=now,
        session=str(context.session or ""),
        price=mid,
        atr=float(context.atr or 0.0) or pip * 50,
        m5_structure=str(context.m5_structure or "range"),
        bar_high=None if last is None else float(last["high"]),
        bar_low=None if last is None else float(last["low"]),
        bar_close=None if last is None else float(last["close"]),
        cfg=cfg,
        pip_size=pip,
        source="m5" if mad_df is prior_m5 else "m1",
      )
      mad_payload = enrich_mad_payload_for_shadow(mad)
      result["mad"] = mad_payload
      await set_last(client, "mad", symbol, mad_payload)
    except Exception:
      log.exception("mad phase evaluation failed symbol=%s", symbol)

  # Drop stale armed/discovered HFS contexts so scalp:active cannot pile up.
  try:
    from app.scalping.lifecycle import prune_stale_active

    pruned = await prune_stale_active(client, symbol, now=now)
    if pruned:
      result["stale_active_pruned"] = pruned
  except Exception:
    log.exception("stale scalp active prune failed symbol=%s", symbol)

  # Observe-only research stamps (features + math counterfactual) on every
  # discovery. Never flips allow/block — see docs/scalping/OWN_SCALP_MECHANISM.md.
  if (
    mode in {"shadow", "paper", "live"}
    and opportunities
    and m1 is not None
    and not m1.empty
    and context.active_range_low
    and context.active_range_high
  ):
    try:
      from datetime import datetime, timezone

      from app.scalping.research_stamp import annotate_opportunities_research

      last_bar = m1.iloc[-1]
      utc_hour = datetime.fromtimestamp(int(bar_ts), tz=timezone.utc).hour
      target_min = float(
        getattr(getattr(_scalping_cfg(cfg), "target", None), "minimum_net_target_pips", 10)
        or 10
      ) * pip
      spread_price = float(ask) - float(bid)
      opportunities = annotate_opportunities_research(
        list(opportunities),
        atr=float(context.atr or 0.0) or pip * 50,
        range_low=float(context.active_range_low),
        range_high=float(context.active_range_high),
        nearest_resistance_low=context.nearest_resistance_low,
        nearest_support_high=context.nearest_support_high,
        bar_open=float(last_bar["open"]),
        bar_high=float(last_bar["high"]),
        bar_low=float(last_bar["low"]),
        bar_close=float(last_bar["close"]),
        spread=spread_price,
        target_min_price=target_min,
        session=str(context.session or ""),
        utc_hour=utc_hour,
      )
    except Exception:
      log.exception("scalp research stamp failed symbol=%s", symbol)

  scored: list = []
  for opportunity in opportunities:
    await incr(client, symbol, "opportunity_discovered")
    await set_last(client, "opportunity", symbol, json.loads(opportunity.to_json()))
    try:
      from app.autotrade.reaction_funnel import (
        STAGE_DISCOVERED,
        bump_funnel,
      )
      from app.scalping.models import STRATEGY_DISPLAY

      await bump_funnel(
        client,
        symbol=symbol,
        stage=STAGE_DISCOVERED,
        strategy=STRATEGY_DISPLAY.get(
          opportunity.archetype, opportunity.archetype,
        ),
        family="scalp",
        strategy_mode="scalp_m1",
        archetype=opportunity.archetype,
        once_key=f"discover:{opportunity.opportunity_id}:{bar_ts}",
      )
    except Exception:
      log.exception("hfs discover funnel bump failed")
    existing = await load_lifecycle(client, symbol, opportunity.opportunity_id)
    if existing is None:
      record = ScalpLifecycleRecord(
        opportunity_id=opportunity.opportunity_id,
        episode_id=opportunity.episode_id,
        state=DISCOVERED,
        context_id=context.context_id,
        updated_at=now,
        reason_code="discovered",
      )
      record = transition(record, ARMED, reason="context_valid", now=now)
      await save_lifecycle(client, symbol, record)

    decision = evaluate_scalp_activation(
      opportunity,
      context,
      quote_bid=bid,
      quote_ask=ask,
      quote_ts=qts,
      now=now,
      pip_size=pip,
      cfg=cfg,
    )
    if decision.reason_code == "scalp_missed_chase":
      if existing is not None:
        missed = transition(existing, MISSED, reason="scalp_missed_chase", now=now)
        await save_lifecycle(client, symbol, missed)
      await incr(client, symbol, "opportunity_missed")

    if not risk.allowed:
      decision = risk
    if not decision.allowed:
      await incr(client, symbol, f"opportunity_blocked:{decision.reason_code}")
      result["blocked"].append({
        "opportunity_id": opportunity.opportunity_id,
        "reason": decision.reason_code,
      })
      await set_last(client, "decision", symbol, {
        "allowed": False,
        "reason_code": decision.reason_code,
        "opportunity_id": opportunity.opportunity_id,
        "mode": mode,
      })
      continue

    if mad_payload is not None:
      from dataclasses import replace as _dc_replace

      decision = _dc_replace(
        decision,
        measured={**decision.measured, "mad": mad_payload},
      )
    score = score_opportunity(
      opportunity, context, decision, spread_pips=float(decision.measured.get("spread_pips") or 0),
    )
    scored.append((opportunity, decision, score))

  policy = getattr(_scalping_cfg(cfg), "policy", None)
  maximum = int(getattr(policy, "maximum_opportunities_per_cycle", 3) or 3)
  ranked = rank_opportunities(scored, maximum=maximum)

  t_persist = time.perf_counter()
  for opportunity, decision, score in ranked:
    await incr(
      client,
      symbol,
      f"bias_alignment:{_bias_alignment(context.htf_bias, opportunity.direction)}",
    )
    await incr(client, symbol, "opportunity_allowed")
    try:
      from app.autotrade.reaction_funnel import (
        STAGE_ACTIVATION_ALLOWED,
        bump_funnel,
      )
      from app.scalping.models import STRATEGY_DISPLAY

      await bump_funnel(
        client,
        symbol=symbol,
        stage=STAGE_ACTIVATION_ALLOWED,
        strategy=STRATEGY_DISPLAY.get(
          opportunity.archetype, opportunity.archetype,
        ),
        family="scalp",
        strategy_mode="scalp_m1",
        archetype=opportunity.archetype,
        once_key=f"activate:{opportunity.opportunity_id}:{bar_ts}",
      )
    except Exception:
      log.exception("hfs activation funnel bump failed")
    signal = ScalpSignal(
      signal_id=deterministic_id("signal", opportunity.opportunity_id, bar_ts),
      opportunity_id=opportunity.opportunity_id,
      mode=mode,
      decision=decision,
      opportunity=opportunity,
      created_at=now,
      measured={"score": score.total, "penalties": list(score.penalties)},
    )
    await client.set(
      f"scalp:signal:{symbol.upper()}:{signal.signal_id}",
      signal.to_json(),
      ex=7 * 24 * 3600,
    )
    if mode == "paper":
      await client.set(
        f"scalp:paper:{symbol.upper()}:{opportunity.opportunity_id}",
        signal.to_json(),
        ex=7 * 24 * 3600,
      )
    existing = await load_lifecycle(client, symbol, opportunity.opportunity_id)
    if existing is not None:
      moved = transition(existing, EXECUTABLE, reason="activation_allowed", now=now)
      await save_lifecycle(client, symbol, moved)

    published_status = None
    if mode == "live":
      match = build_scalp_strategy_match(
        opportunity,
        context,
        bar_ts=bar_ts,
        quote_bid=bid,
        quote_ask=ask,
        location_reason=str(decision.measured.get("location_reason") or ""),
        cfg=cfg,
      )
      publish_result = await publish_scalp_live(
        client, match, symbol=symbol, bar_ts=bar_ts,
      )
      if publish_result is None:
        published_status = "lifecycle_advance_failed"
        await incr(client, symbol, "opportunity_blocked:lifecycle_advance_failed")
      else:
        published_status = publish_result.status
        if publish_result.status in {
          worker.PUBLISH_STATUS_PUBLISHED,
          worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
        }:
          await incr(client, symbol, "opportunity_live_published")
          latest = await load_lifecycle(client, symbol, opportunity.opportunity_id)
          if latest is not None:
            done = transition(
              latest, PUBLISHED, reason=publish_result.reason_code or "live_published", now=now,
            )
            await save_lifecycle(client, symbol, done)
          # One live position at a time — stop after first successful handoff.
          if publish_result.status == worker.PUBLISH_STATUS_PUBLISHED:
            try:
              from app.autotrade.reaction_funnel import (
                STAGE_PLAN_PUBLISHED,
                bump_funnel,
              )
              from app.scalping.models import STRATEGY_DISPLAY

              await bump_funnel(
                client,
                symbol=symbol,
                stage=STAGE_PLAN_PUBLISHED,
                strategy=STRATEGY_DISPLAY.get(
                  opportunity.archetype, opportunity.archetype,
                ),
                family="scalp",
                strategy_mode="scalp_m1",
                archetype=opportunity.archetype,
                once_key=f"publish:{opportunity.opportunity_id}",
              )
            except Exception:
              log.exception("hfs publish funnel bump failed")
            result["allowed"].append({
              "opportunity_id": opportunity.opportunity_id,
              "archetype": opportunity.archetype,
              "direction": opportunity.direction,
              "score": score.total,
              "publish_status": published_status,
              "plan_id": publish_result.plan_id,
            })
            await set_last(client, "decision", symbol, {
              "allowed": True,
              "reason_code": decision.reason_code,
              "opportunity_id": opportunity.opportunity_id,
              "mode": mode,
              "score": score.total,
              "publish_status": published_status,
              "plan_id": publish_result.plan_id,
            })
            break
        else:
          await incr(
            client,
            symbol,
            f"opportunity_blocked:live_{publish_result.reason_code or publish_result.status}",
          )

    result["allowed"].append({
      "opportunity_id": opportunity.opportunity_id,
      "archetype": opportunity.archetype,
      "direction": opportunity.direction,
      "score": score.total,
      "publish_status": published_status,
    })
    await set_last(client, "decision", symbol, {
      "allowed": True,
      "reason_code": decision.reason_code,
      "opportunity_id": opportunity.opportunity_id,
      "mode": mode,
      "score": score.total,
      "publish_status": published_status,
    })
  persist_ms = (time.perf_counter() - t_persist) * 1000.0
  total_ms = (time.perf_counter() - t0) * 1000.0

  await record_cycle(client, symbol, {
    "bar_ts": bar_ts,
    "context_id": context.context_id,
    "session": context.session,
    "htf_bias": context.htf_bias,
    "m5_structure": context.m5_structure,
    "regime": context.regime,
    "discovered": len(opportunities),
    "allowed": len(ranked),
    "blocked": len(result["blocked"]),
    "mode": mode,
    "idle_reasons": idle_reasons,
    "context_soft_stale": bool(result.get("context_soft_stale")),
    "context_age_seconds": result.get("context_age_seconds"),
    "context_load_ms": round(context_ms, 3),
    "analysis_labels_ms": round(analysis_labels_ms, 3),
    "microstructure_ms": round(micro_ms, 3),
    "strategy_evaluation_ms": round(strat_ms, 3),
    "persistence_ms": round(persist_ms, 3),
    "cycle_total_ms": round(total_ms, 3),
  })
  result["cycle_total_ms"] = total_ms
  result["context_id"] = context.context_id
  result["discovered"] = len(opportunities)
  result["idle_reasons"] = list(idle_reasons)

  # Mathematical shadow sidecar — records X_t gates; never publishes itself.
  # Live mode records observe-only (ControlledLivePolicy.enabled defaults false
  # so would_execute stays false). Prefer per-opp range_sweep stamps above;
  # cycle sidecar still evaluates both edge directions for density.
  if (
    mode in {"shadow", "paper", "live"}
    and context.active_range_low
    and context.active_range_high
  ):
    try:
      from datetime import datetime, timezone

      from app.scalping.rollout import evaluate_math_shadow

      last = m1.iloc[-1] if m1 is not None and not m1.empty else None
      if last is not None:
        mid = (float(quote[0]) + float(quote[1])) / 2.0
        utc_hour = datetime.fromtimestamp(int(bar_ts), tz=timezone.utc).hour
        target_min = float(
          getattr(getattr(_scalping_cfg(cfg), "target", None), "minimum_net_target_pips", 10)
          or 10
        ) * pip
        spread_price = float(quote[1]) - float(quote[0])
        atr = float(context.atr or 0.0) or pip * 50
        common = dict(
          mode=mode,  # type: ignore[arg-type]
          price=mid,
          atr=atr,
          range_low=float(context.active_range_low),
          range_high=float(context.active_range_high),
          bar_open=float(last["open"]),
          bar_high=float(last["high"]),
          bar_low=float(last["low"]),
          bar_close=float(last["close"]),
          spread=spread_price,
          target_min_price=target_min,
          utc_hour=utc_hour,
        )
        buy_shadow = evaluate_math_shadow(
          **common,
          direction="BUY",
          liquidity_level=float(context.active_range_low),
          barrier=context.nearest_resistance_low,
        )
        sell_shadow = evaluate_math_shadow(
          **common,
          direction="SELL",
          liquidity_level=float(context.active_range_high),
          barrier=context.nearest_support_high,
        )
        from app.scalping.research_stamp import research_agree_rows

        agree_rows = research_agree_rows(
          list(opportunities),
          session=str(context.session or ""),
          bar_ts=int(bar_ts),
        )
        payload = {
          "buy": buy_shadow.to_dict(),
          "sell": sell_shadow.to_dict(),
          "bar_ts": int(bar_ts),
          "session": str(context.session or ""),
          "mad": mad_payload,
          "range_sweep_annotated": sum(
            1
            for o in opportunities
            if (o.measured or {}).get("math_liquidity_sweep") is not None
          ),
          "research": {
            "live_discovered": len(opportunities),
            "agree_rows": agree_rows,
            "math_agree_true": sum(
              1 for r in agree_rows if r.get("math_agree") is True
            ),
            "math_agree_false": sum(
              1 for r in agree_rows if r.get("math_agree") is False
            ),
            "math_agree_unknown": sum(
              1 for r in agree_rows if r.get("math_agree") is None
            ),
          },
        }
        result["math_shadow"] = payload
        await set_last(client, "math_shadow", symbol, payload)
    except Exception:
      log.exception("math shadow evaluation failed symbol=%s", symbol)

  return result


async def handle_closed_bar(
  data: object,
  *,
  client: Any,
  source: RedisOHLCSource,
) -> None:
  """HFS handler for one closed-bar event (M5 context refresh, M1 cycle)."""
  if _mode() == "off":
    return
  parsed = _parse_bar_event(data)
  if parsed is None:
    return
  symbol, tf, bar_ts = parsed
  if not is_scalping_symbol(symbol):
    return
  if tf == "M5":
    await _ensure_context(
      client,
      source,
      symbol=symbol,
      now=bar_ts,
      cfg=runtime_config,
      force=True,
    )
    return
  if tf != "M1":
    return
  summary = await process_m1_bar(
    client,
    symbol=symbol,
    bar_ts=bar_ts,
    ohlc_source=source,
  )
  allowed_n = len(summary.get("allowed") or ())
  blocked_n = len(summary.get("blocked") or ())
  discovered_n = int(summary.get("discovered") or 0)
  log_fn = log.info if (allowed_n or blocked_n or discovered_n) else log.debug
  log_fn(
    "scalp m1 cycle symbol=%s bar_ts=%s mode=%s allowed=%s blocked=%s "
    "discovered=%s idle=%s block_reasons=%s ms=%s",
    symbol,
    bar_ts,
    summary.get("mode"),
    allowed_n,
    blocked_n,
    discovered_n,
    ",".join(summary.get("idle_reasons") or ()) or "-",
    ",".join(
      str(item.get("reason") or "?")
      for item in (summary.get("blocked") or ())
    ) or "-",
    summary.get("cycle_total_ms"),
  )


async def scalp_m1_event_loop() -> None:
  """Deprecated: closed bars are owned by bar_event_dispatcher_loop."""
  if _mode() == "off":
    log.info("M1 scalping disabled: strategies.scalping.mode=off")
    return
  log.info(
    "scalp_m1_event_loop idle; bar_event_dispatcher_loop owns live HFS symbols"
  )
