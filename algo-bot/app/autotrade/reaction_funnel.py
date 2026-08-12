"""Scalp vs reaction funnel counters for ZoneWatch / trade lifecycle visibility.

Reaction fills can vanish from short-TTL Redis event streams while HFS
dominates the journal. These counters (and compact complete lifecycle
events) keep the scalp/reaction split measurable without relying on stream
retention.

HFS also writes per-archetype keys so Impulse vs Momentum vs Range can be
read without joining Postgres.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.autotrade.lifecycle import increment_metric
from app.autotrade.strategy_taxonomy import is_scalp_strategy
from app.scalping.models import STRATEGY_DISPLAY

log = logging.getLogger(__name__)

BUCKET_SCALP = "scalp"
BUCKET_REACTION = "reaction"

STAGE_ZONE_DISCOVERED = "zone_discovered"
STAGE_WAITING_REACTION_TRIGGER = "waiting_reaction_trigger"
STAGE_ACTIVATION_ALLOWED = "activation_allowed"
STAGE_DISCOVERED = "discovered"
STAGE_PLAN_PUBLISHED = "plan_published"
STAGE_FILL = "fill"
STAGE_SL = "sl"
STAGE_TP = "tp"

_FUNNEL_TTL_SECONDS = 7 * 86400
_LOG_MIN_INTERVAL_SECONDS = 60
_last_funnel_log_at: dict[str, float] = {}

_STRATEGY_TO_ARCHETYPE = {
  display: archetype for archetype, display in STRATEGY_DISPLAY.items()
}

# Legacy / executor aliases seen in auto_trade_fills.setup_type.
_SETUP_TYPE_ALIASES = {
  "momentum": "HFS Momentum Chase",
  "hfs momentum chase": "HFS Momentum Chase",
  "impulse pullback": "HFS Impulse Pullback",
  "hfs impulse pullback": "HFS Impulse Pullback",
  "range sweep": "HFS Range Sweep",
  "hfs range sweep": "HFS Range Sweep",
  "breakout retest": "HFS Breakout Retest",
  "hfs breakout retest": "HFS Breakout Retest",
  "key-level": "Key Level Reaction",
  "key level": "Key Level Reaction",
  "key level reaction": "Key Level Reaction",
  "flip-zone": "Flip Zone",
  "flip zone": "Flip Zone",
  "trendline": "Trendline Reaction",
  "trendline reaction": "Trendline Reaction",
  "session level": "Session Level Reaction",
  "session-level": "Session Level Reaction",
  "zone reaction": "Zone Reaction",
  "trend pullback": "Trend Pullback",
}


def normalize_setup_type(raw: str | None) -> str | None:
  """Canonical display name for fills/stats (fixes legacy aliases)."""
  if raw is None:
    return None
  text = str(raw).strip()
  if not text:
    return None
  # Scale-in tags: "Key Level Reaction · add_momentum"
  base = text.split("·", 1)[0].strip()
  mapped = _SETUP_TYPE_ALIASES.get(base.casefold())
  if mapped is not None:
    if "·" in text:
      return f"{mapped} ·{text.split('·', 1)[1]}"
    return mapped
  if base in _STRATEGY_TO_ARCHETYPE or base.startswith("HFS "):
    return text if "·" in text else base
  return text


def archetype_from_strategy(strategy: str | None) -> str | None:
  if not strategy:
    return None
  normalized = normalize_setup_type(strategy) or str(strategy)
  base = normalized.split("·", 1)[0].strip()
  return _STRATEGY_TO_ARCHETYPE.get(base)


def funnel_bucket(
  strategy: str | None,
  *,
  family: str | None = None,
  strategy_mode: str | None = None,
) -> str:
  if is_scalp_strategy(
    str(strategy or ""),
    family=str(family or ""),
    strategy_mode=str(strategy_mode or ""),
  ):
    return BUCKET_SCALP
  return BUCKET_REACTION


def funnel_key(symbol: str, bucket: str) -> str:
  return f"auto_trade:funnel:{str(symbol or 'XAU').upper()}:{bucket}"


def hfs_archetype_funnel_key(symbol: str, archetype: str) -> str:
  return (
    f"auto_trade:funnel:{str(symbol or 'XAU').upper()}:"
    f"hfs:{str(archetype or 'unknown')}"
  )


async def bump_funnel(
  client: Any,
  *,
  symbol: str,
  stage: str,
  strategy: str | None = None,
  family: str | None = None,
  strategy_mode: str | None = None,
  reason_code: str | None = None,
  once_key: str | None = None,
  archetype: str | None = None,
) -> str:
  """Increment a durable per-symbol scalp/reaction funnel counter.

  When ``once_key`` is set, only the first bump for that key counts
  (e.g. one waiting_reaction_trigger per zone watch episode).
  HFS strategies also increment ``auto_trade:funnel:{sym}:hfs:{archetype}``.
  """
  if once_key:
    try:
      created = await client.set(
        f"auto_trade:funnel_once:{once_key}",
        "1",
        nx=True,
        ex=_FUNNEL_TTL_SECONDS,
      )
    except Exception:
      created = True
    if not created:
      return funnel_bucket(
        strategy, family=family, strategy_mode=strategy_mode,
      )
  bucket = funnel_bucket(
    strategy, family=family, strategy_mode=strategy_mode,
  )
  key = funnel_key(symbol, bucket)
  try:
    pipe = client.pipeline()
    pipe.hincrby(key, stage, 1)
    if reason_code:
      pipe.hincrby(key, f"{stage}:{reason_code}", 1)
    pipe.expire(key, _FUNNEL_TTL_SECONDS)
    resolved_archetype = archetype or archetype_from_strategy(strategy)
    if bucket == BUCKET_SCALP and resolved_archetype:
      arch_key = hfs_archetype_funnel_key(symbol, resolved_archetype)
      pipe.hincrby(arch_key, stage, 1)
      if reason_code:
        pipe.hincrby(arch_key, f"{stage}:{reason_code}", 1)
      pipe.expire(arch_key, _FUNNEL_TTL_SECONDS)
    await pipe.execute()
  except Exception:
    log.exception(
      "funnel bump failed symbol=%s bucket=%s stage=%s",
      symbol, bucket, stage,
    )
  try:
    await increment_metric(
      client,
      f"funnel_{stage}",
      symbol=symbol,
      dimensions={"bucket": bucket, "strategy": str(strategy or "")},
    )
  except Exception:
    log.exception(
      "funnel metric failed symbol=%s stage=%s", symbol, stage,
    )
  await _maybe_log_funnel_snapshot(client, symbol=symbol, bucket=bucket)
  return bucket


async def _maybe_log_funnel_snapshot(
  client: Any,
  *,
  symbol: str,
  bucket: str,
) -> None:
  sym = str(symbol or "XAU").upper()
  now = time.monotonic()
  last = _last_funnel_log_at.get(sym, 0.0)
  if now - last < _LOG_MIN_INTERVAL_SECONDS:
    return
  _last_funnel_log_at[sym] = now
  try:
    reaction = await client.hgetall(funnel_key(sym, BUCKET_REACTION)) or {}
    scalp = await client.hgetall(funnel_key(sym, BUCKET_SCALP)) or {}
    impulse = await client.hgetall(
      hfs_archetype_funnel_key(sym, "impulse_pullback"),
    ) or {}
    momentum = await client.hgetall(
      hfs_archetype_funnel_key(sym, "momentum_chase"),
    ) or {}
  except Exception:
    return

  def _decode(raw: dict[Any, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in raw.items():
      name = key.decode() if isinstance(key, bytes) else str(key)
      try:
        out[name] = int(value)
      except (TypeError, ValueError):
        continue
    return out

  log.info(
    "reaction funnel snapshot symbol=%s trigger_bucket=%s reaction=%s "
    "scalp=%s hfs_impulse=%s hfs_momentum=%s",
    sym,
    bucket,
    json.dumps(_decode(reaction), sort_keys=True),
    json.dumps(_decode(scalp), sort_keys=True),
    json.dumps(_decode(impulse), sort_keys=True),
    json.dumps(_decode(momentum), sort_keys=True),
  )


async def emit_plan_complete_event(
  client: Any,
  *,
  symbol: str,
  strategy: str | None,
  reason_code: str,
  outcome: str,
  group_id: str | None = None,
  candidate_id: str | None = None,
  family: str | None = None,
  strategy_mode: str | None = None,
  measured: dict[str, Any] | None = None,
) -> None:
  """Record funnel stage only — never publish to the Telegram event stream.

  Broker fill/close cards already own owner-facing copy. Emitting a second
  ``order_filled`` / ``closed`` lifecycle with ``publish_status=True``
  overwrites the reply body with internal funnel text.
  """
  del measured  # retained for call-site compatibility; counters are enough
  stage = STAGE_SL if outcome == "sl" else STAGE_TP if outcome == "tp" else STAGE_FILL
  once = None
  if group_id or candidate_id:
    once = f"{group_id or candidate_id}:{outcome}"
  if outcome not in {"sl", "tp", "fill"}:
    return
  await bump_funnel(
    client,
    symbol=symbol,
    stage=stage,
    strategy=normalize_setup_type(strategy) or strategy,
    family=family,
    strategy_mode=strategy_mode,
    reason_code=reason_code,
    once_key=once,
  )
