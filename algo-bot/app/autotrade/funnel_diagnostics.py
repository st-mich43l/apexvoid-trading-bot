"""Opportunity → plan → fill funnel view over ``auto_trade:metrics:{symbol}``."""

from __future__ import annotations

from typing import Any

from app.persistence import redis_state


FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
  ("checked", "strategy_match_checking"),
  ("candidate_published", "strategy_match_candidate_published"),
  ("plan_published", "v8_plan_published"),
  ("filled", "funnel_fill"),
)

# Where opportunities leave the funnel without a plan.
EXIT_METRICS: tuple[tuple[str, str], ...] = (
  ("waiting", "strategy_match_waiting"),
  ("blocked", "strategy_match_blocked"),
  ("arbitration_suppressed", "strategy_match_arbitration_suppressed"),
  ("expired", "strategy_match_expired"),
  ("target_room_rejected", "target_room_rejected"),
)

# Reason-coded counters (``strategy_match_blocked:{reason}``) listed under blocked.
BLOCK_PREFIX = "strategy_match_blocked:"


def _decode_metrics(raw: dict[Any, Any]) -> dict[str, int]:
  out: dict[str, int] = {}
  for key, value in raw.items():
    name = key.decode() if isinstance(key, bytes) else str(key)
    try:
      out[name] = int(value)
    except (TypeError, ValueError):
      continue
  return out


def _top_block_reasons(metrics: dict[str, int], *, limit: int = 5) -> list[tuple[str, int]]:
  scored = [
    (key[len(BLOCK_PREFIX):], count)
    for key, count in metrics.items()
    if key.startswith(BLOCK_PREFIX) and count > 0
  ]
  scored.sort(key=lambda item: (-item[1], item[0]))
  return scored[:limit]


async def auto_trade_funnel_text(symbol: str = "XAU") -> str:
  client = redis_state.get_client()
  sym = str(symbol or "XAU").upper()
  raw = await client.hgetall(f"auto_trade:metrics:{sym}") or {}
  metrics = _decode_metrics(raw)

  lines = [f"<b>Algo funnel — {sym}</b>", ""]
  previous = None
  for stage, metric_key in FUNNEL_STAGES:
    total = metrics.get(metric_key, 0)
    suffix = ""
    if previous is not None and previous > 0:
      suffix = f" ({100.0 * total / previous:.0f}% of prior)"
    lines.append(f"{stage}: <b>{total}</b>{suffix}")
    if total > 0:
      previous = total
  lines.append("")
  lines.append("exits without a plan:")
  for name, metric_key in EXIT_METRICS:
    lines.append(f"  {name}: <b>{metrics.get(metric_key, 0)}</b>")
  blocks = _top_block_reasons(metrics)
  if blocks:
    lines.append("  top blocks:")
    for reason, count in blocks:
      lines.append(f"    • {reason}: {count}")
  return "\n".join(lines)
