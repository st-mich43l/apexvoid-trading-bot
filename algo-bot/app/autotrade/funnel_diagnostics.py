"""Discovery → activation funnel view over ``auto_trade:metrics:{symbol}``."""

from __future__ import annotations

from typing import Any

from app.persistence import redis_state


FUNNEL_STAGES: tuple[tuple[str, str | None], ...] = (
  ("detected", None),
  ("actionable", "scanner_setup_actionable"),
  ("match_published", "candidate_published"),
  ("zonewatch_armed", "funnel_zone_discovered"),
  ("activation_allowed", "activation_allowed"),
  ("plan_published", "v8_plan_published"),
)

STAGE_BLOCK_PREFIXES: dict[str, tuple[str, ...]] = {
  "detected": ("structure_gated",),
  "actionable": ("scanner_actionability_gated:",),
  "match_published": ("strategy_match_blocked:",),
  "zonewatch_armed": (
    "static_eligibility_blocked",
    "scanner_match_build_blocked:",
  ),
  "activation_allowed": ("activation_blocked:",),
  "plan_published": (
    "target_room_rejected",
    "v8_plan_build_incomplete",
  ),
}


def _decode_metrics(raw: dict[Any, Any]) -> dict[str, int]:
  out: dict[str, int] = {}
  for key, value in raw.items():
    name = key.decode() if isinstance(key, bytes) else str(key)
    try:
      out[name] = int(value)
    except (TypeError, ValueError):
      continue
  return out


def _detected_total(metrics: dict[str, int]) -> int:
  return sum(count for name, count in metrics.items() if name.endswith("_detected"))


def _stage_total(stage: str, metric_key: str | None, metrics: dict[str, int]) -> int:
  if stage == "detected":
    return _detected_total(metrics)
  if metric_key is None:
    return 0
  return int(metrics.get(metric_key, 0))


def _top_block_reasons(
  metrics: dict[str, int],
  prefixes: tuple[str, ...],
  *,
  limit: int = 5,
) -> list[tuple[str, int]]:
  scored: list[tuple[str, int]] = []
  for key, count in metrics.items():
    if count <= 0:
      continue
    for prefix in prefixes:
      if prefix.endswith(":"):
        if key.startswith(prefix):
          scored.append((key[len(prefix):], count))
          break
      elif key == prefix:
        scored.append((key, count))
        break
      elif key.startswith(f"{prefix}:"):
        scored.append((key[len(prefix) + 1:], count))
        break
  scored.sort(key=lambda item: (-item[1], item[0]))
  deduped: list[tuple[str, int]] = []
  seen: set[str] = set()
  for reason, count in scored:
    if reason in seen:
      continue
    seen.add(reason)
    deduped.append((reason, count))
  return deduped[:limit]


async def auto_trade_funnel_text(symbol: str = "XAU") -> str:
  client = redis_state.get_client()
  sym = str(symbol or "XAU").upper()
  raw = await client.hgetall(f"auto_trade:metrics:{sym}") or {}
  metrics = _decode_metrics(raw)

  lines = [f"<b>Algo funnel — {sym}</b>", ""]
  previous = None
  for stage, metric_key in FUNNEL_STAGES:
    total = _stage_total(stage, metric_key, metrics)
    suffix = ""
    if previous is not None and previous > 0:
      suffix = f" ({100.0 * total / previous:.0f}% of prior)"
    lines.append(f"{stage}: <b>{total}</b>{suffix}")
    blocks = _top_block_reasons(
      metrics,
      STAGE_BLOCK_PREFIXES.get(stage, ()),
      limit=5,
    )
    if blocks:
      lines.append("  top blocks:")
      for reason, count in blocks:
        lines.append(f"    • {reason}: {count}")
    if total > 0:
      previous = total
  return "\n".join(lines)
