"""Shadow/paper M1 high-frequency scalping event loop."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.analysis.ohlc_source import RedisOHLCSource
from app.autotrade import units
from app.core.config import runtime_config
from app.persistence import redis_state
from app.scalping.activation import evaluate_scalp_activation
from app.scalping.context import (
  LIVE_SYMBOL,
  build_scalp_context_snapshot,
  is_context_fresh,
  load_current_context,
  save_context,
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
from app.scalping.publish import build_hfs_strategy_match, publish_hfs_live
from app.scalping.ranking import rank_opportunities, score_opportunity
from app.scalping.risk import evaluate_risk, load_risk, save_risk
from app.scalping.strategies import discover_all, idle_discovery_reasons
from app.scalping.telemetry import incr, record_cycle, set_last
from app.autotrade import worker


log = logging.getLogger(__name__)


def _hfs(cfg: Any = None) -> Any:
  cfg = cfg or runtime_config
  return getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None)


def _mode(cfg: Any = None) -> str:
  section = _hfs(cfg)
  mode = str(getattr(section, "mode", "off") or "off").strip().lower()
  return mode if mode in {"off", "shadow", "paper", "live"} else "off"


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


async def _ensure_context(
  client: Any,
  source: RedisOHLCSource,
  *,
  symbol: str,
  now: int,
  cfg: Any,
  force: bool = False,
):
  existing = await load_current_context(client, symbol)
  ctx_cfg = getattr(_hfs(cfg), "context", None)
  max_age = int(getattr(ctx_cfg, "maximum_m5_age_seconds", 420) or 420)
  if existing is not None and not force and is_context_fresh(existing, now, max_age):
    return existing

  m5 = await source.window(symbol, "M5", 120)
  m15 = await source.window(symbol, "M15", 120)
  h1 = await source.window(symbol, "H1", 120)
  quote = await _load_quote(client, symbol)
  if quote is None or m5.empty:
    return existing
  bid, ask, _ = quote
  mid = (bid + ask) / 2.0
  pip = units.pip_size(symbol)
  atr = float(m5["high"].astype(float).tail(14).mean() - m5["low"].astype(float).tail(14).mean())
  snapshot = build_scalp_context_snapshot(
    symbol=symbol,
    m5=m5,
    m15=m15,
    h1=h1,
    price=mid,
    pip_size=pip,
    atr=atr,
    now=now,
    cfg=cfg,
  )
  if snapshot is None:
    return existing
  current_ttl = int(getattr(ctx_cfg, "current_context_ttl_seconds", 3600) or 3600)
  historic_ttl = int(getattr(ctx_cfg, "historic_context_ttl_seconds", 86400) or 86400)
  await save_context(
    client,
    snapshot,
    current_ttl=current_ttl,
    historic_ttl=historic_ttl,
  )
  await set_last(client, "context", symbol, json.loads(snapshot.to_json()))
  return snapshot


async def process_m1_bar(
  client: Any,
  *,
  symbol: str,
  bar_ts: int,
  ohlc_source: RedisOHLCSource | None = None,
  cfg: Any | None = None,
) -> dict[str, Any]:
  """Idempotent one-bar scalping cycle. Never publishes broker candidates."""
  cfg = cfg or runtime_config
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
  if symbol.upper() != LIVE_SYMBOL:
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
  context = await _ensure_context(client, source, symbol=symbol, now=now, cfg=cfg)
  context_ms = (time.perf_counter() - t_ctx) * 1000.0
  if context is None:
    result["reason"] = "scalp_context_missing"
    await incr(client, symbol, "opportunity_blocked:scalp_context_missing")
    return result
  ctx_cfg = getattr(_hfs(cfg), "context", None)
  max_age = int(getattr(ctx_cfg, "maximum_m5_age_seconds", 420) or 420)
  if not is_context_fresh(context, now, max_age):
    result["reason"] = "scalp_context_stale"
    await incr(client, symbol, "opportunity_blocked:scalp_context_stale")
    return result

  lookback = int(getattr(ctx_cfg, "m1_lookback_bars", 60) or 60)
  t_micro = time.perf_counter()
  m1 = await source.window(symbol, "M1", lookback)
  micro = build_micro_structure(m1)
  micro_ms = (time.perf_counter() - t_micro) * 1000.0

  pip = units.pip_size(symbol)
  t_strat = time.perf_counter()
  opportunities = discover_all(context, micro, m1, cfg, pip_size=pip, now=now)
  idle_reasons = (
    idle_discovery_reasons(context, m1, cfg, pip_size=pip)
    if not opportunities
    else []
  )
  strat_ms = (time.perf_counter() - t_strat) * 1000.0

  quote = await _load_quote(client, symbol)
  if quote is None:
    result["reason"] = "scalp_quote_missing"
    return result
  bid, ask, qts = quote

  risk_state = await load_risk(client, symbol)
  risk = evaluate_risk(risk_state, cfg, session=context.session, now=now)

  scored: list = []
  for opportunity in opportunities:
    await incr(client, symbol, "opportunity_discovered")
    await set_last(client, "opportunity", symbol, json.loads(opportunity.to_json()))
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

    score = score_opportunity(
      opportunity, context, decision, spread_pips=float(decision.measured.get("spread_pips") or 0),
    )
    scored.append((opportunity, decision, score))

  policy = getattr(_hfs(cfg), "policy", None)
  maximum = int(getattr(policy, "maximum_opportunities_per_cycle", 3) or 3)
  ranked = rank_opportunities(scored, maximum=maximum)

  t_persist = time.perf_counter()
  for opportunity, decision, score in ranked:
    await incr(client, symbol, "opportunity_allowed")
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
      match = build_hfs_strategy_match(
        opportunity,
        context,
        bar_ts=bar_ts,
        quote_bid=bid,
        quote_ask=ask,
        location_reason=str(decision.measured.get("location_reason") or ""),
      )
      publish_result = await publish_hfs_live(
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
    "discovered": len(opportunities),
    "allowed": len(ranked),
    "blocked": len(result["blocked"]),
    "mode": mode,
    "idle_reasons": idle_reasons,
    "context_load_ms": round(context_ms, 3),
    "microstructure_ms": round(micro_ms, 3),
    "strategy_evaluation_ms": round(strat_ms, 3),
    "persistence_ms": round(persist_ms, 3),
    "cycle_total_ms": round(total_ms, 3),
  })
  result["cycle_total_ms"] = total_ms
  result["context_id"] = context.context_id
  return result


async def scalp_m1_event_loop() -> None:
  """Subscribe to closed M1 bars for XAU scalping."""
  if _mode() == "off":
    log.info("HFS scalping disabled: strategies.high_frequency_scalp.mode=off")
    return

  log.info(
    "HFS scalping loop starting mode=%s symbol=%s",
    _mode(),
    LIVE_SYMBOL,
  )
  client = await redis_state.get_client()
  pubsub = client.pubsub()
  channel = runtime_config.market_data.ctrader_feed.bars_channel
  await pubsub.subscribe(channel)
  source = RedisOHLCSource(client)
  try:
    async for message in pubsub.listen():
      if message is None or message.get("type") != "message":
        continue
      parsed = _parse_bar_event(message.get("data"))
      if parsed is None:
        continue
      symbol, tf, bar_ts = parsed
      if symbol != LIVE_SYMBOL:
        continue
      try:
        if tf == "M5":
          await _ensure_context(
            client,
            source,
            symbol=symbol,
            now=bar_ts,
            cfg=runtime_config,
            force=True,
          )
          continue
        if tf != "M1":
          continue
        summary = await process_m1_bar(
          client,
          symbol=symbol,
          bar_ts=bar_ts,
          ohlc_source=source,
        )
        log.info(
          "scalp m1 cycle symbol=%s bar_ts=%s mode=%s allowed=%s blocked=%s ms=%s",
          symbol,
          bar_ts,
          summary.get("mode"),
          len(summary.get("allowed") or ()),
          len(summary.get("blocked") or ()),
          summary.get("cycle_total_ms"),
        )
      except Exception:
        log.exception("scalp m1 cycle failed symbol=%s tf=%s bar_ts=%s", symbol, tf, bar_ts)
  finally:
    await pubsub.unsubscribe(channel)
    await pubsub.aclose()
