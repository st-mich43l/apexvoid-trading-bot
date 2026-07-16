"""Pure two-sided market-map assembly and rendering."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math

from app.pa_math import atr_scalar
from app.trendlines import value_at

MAP_MAX_PER_SIDE = 4
MAP_MAJOR_SCORE = 12.0
MAP_MAX_TOUCHES = 2
MAP_MIN_ZONE_SCORE = 6.0
MAP_MIN_LEVEL_TOUCHES = 4
MAP_MAX_DISTANCE_ATR = 15.0
MAP_CHANGE_MIN = 1.0
SESSION_BAND_ATR = 0.1
MAP_TAG_LIMIT = 3
_TIER_RANK = {"level": 1, "zone": 2, "major": 3}
_MAJOR_SESSION_LEVELS = {"PDH", "PDL", "PWH", "PWL"}


@dataclass(frozen=True)
class MapEntry:
  side: str
  lo: float
  hi: float
  label_lo: int
  label_hi: int
  tier: str
  tags: list[str]
  score: float


@dataclass(frozen=True)
class MarketMap:
  entries: list[MapEntry]
  price: float
  eq: float | None
  box_low: float | None
  box_high: float | None
  bias: str
  bias_tf: str | None

  @property
  def buys(self) -> list[MapEntry]:
    return [entry for entry in self.entries if entry.side == "buy"]

  @property
  def sells(self) -> list[MapEntry]:
    return [entry for entry in self.entries if entry.side == "sell"]

  @property
  def majors(self) -> list[MapEntry]:
    return [entry for entry in self.entries if entry.tier == "major"]


def build_map(ctx_or_per_tf, price: float, cfg) -> MarketMap:
  per_tf = getattr(ctx_or_per_tf, "per_tf", ctx_or_per_tf)
  if not isinstance(per_tf, dict):
    per_tf = {}
  zone_candidates: list[MapEntry] = []
  level_candidates: list[MapEntry] = []
  trendline_candidates: list[MapEntry] = []
  major_score = float(getattr(cfg, "map_major_score", MAP_MAJOR_SCORE))
  max_touches = max(1, int(getattr(cfg, "map_max_touches", MAP_MAX_TOUCHES)))
  min_zone_score = max(
    0.0,
    float(getattr(cfg, "map_min_zone_score", MAP_MIN_ZONE_SCORE)),
  )
  min_level_touches = max(
    3,
    int(getattr(cfg, "map_min_level_touches", MAP_MIN_LEVEL_TOUCHES)),
  )
  reference_atr = _reference_atr(per_tf)
  max_distance = (
    max(0.0, float(getattr(cfg, "map_max_distance_atr", MAP_MAX_DISTANCE_ATR)))
    * reference_atr
    if reference_atr > 0
    else math.inf
  )
  proximal_band = (
    max(0.0, float(getattr(cfg, "proximal_band_atr", 0.5)))
    * reference_atr
  )
  for tf, item in sorted(per_tf.items(), key=lambda pair: (_tf_rank(pair[0]), pair[0])):
    session_levels = list(getattr(item, "session_levels", []) or [])
    for zone in getattr(item, "zones", []) or []:
      if int(getattr(zone, "touches", 0)) >= max_touches:
        continue
      if float(getattr(zone, "score", 0.0)) < min_zone_score:
        continue
      entry = _zone_entry(zone, tf, per_tf, session_levels, price, major_score)
      if entry is not None and _within_distance(entry, price, max_distance):
        zone_candidates.append(entry)
    for level in getattr(item, "key_levels", []) or []:
      if int(getattr(level, "touches", 0)) < min_level_touches:
        continue
      band = max(0.0, float(getattr(level, "band", 0.0)))
      if proximal_band > 0:
        band = min(band, proximal_band)
      level_candidates.extend(_nearby_entries(_level_entry(
        float(level.price),
        band,
        price,
        f"support ×{level.touches}",
        f"resistance ×{level.touches}",
        float(getattr(level, "strength", level.touches)),
      ), price, max_distance))
    session_band = max(0.0, SESSION_BAND_ATR * reference_atr)
    for level in session_levels:
      if bool(getattr(level, "swept", False)):
        continue
      level_candidates.extend(_nearby_entries(_level_entry(
        float(level.price),
        session_band,
        price,
        str(level.name),
        str(level.name),
        major_score if level.name in _MAJOR_SESSION_LEVELS else 4.0,
        major=level.name in _MAJOR_SESSION_LEVELS,
      ), price, max_distance))
    current_bar = max(0, len(getattr(item, "df", [])) - 1)
    for line in getattr(item, "trendlines", []) or []:
      if line.broken:
        continue
      line_price = value_at(line, current_bar)
      trendline_candidates.extend(_nearby_entries(_level_entry(
        line_price,
        proximal_band,
        price,
        f"TL support ×{line.touches}",
        f"TL resistance ×{line.touches}",
        float(line.touches),
        support=line.kind == "support",
        resistance=line.kind == "resistance",
      ), price, max_distance))
    box_break = getattr(item, "box_break", None)
    if box_break is not None:
      edge = (
        float(box_break.box_high)
        if box_break.direction == "up"
        else float(box_break.box_low)
      )
      zone_candidates.extend(_nearby_entries(_level_entry(
        edge,
        proximal_band,
        price,
        "breakout-retest",
        "breakout-retest",
        8.0,
        tier="zone",
        support=box_break.direction == "up",
        resistance=box_break.direction == "down",
      ), price, max_distance))

  zones = _merge_display_entries(zone_candidates)
  levels = _merge_display_entries(level_candidates)
  zones, levels = _attach_confluence(zones, levels)
  entries = [*zones, *levels]
  entries, _ = _attach_confluence(entries, trendline_candidates)
  capped: list[MapEntry] = []
  max_per_side = max(1, int(getattr(cfg, "map_max_per_side", MAP_MAX_PER_SIDE)))
  for side in ("sell", "buy"):
    ranked = sorted(
      (entry for entry in entries if entry.side == side),
      key=lambda entry: (
        -_TIER_RANK[entry.tier],
        -entry.score,
        _distance(entry, price),
        entry.label_lo,
        entry.label_hi,
      ),
    )
    capped.extend(ranked[:max_per_side])

  regime = getattr(ctx_or_per_tf, "regime", None)
  dealing_range = getattr(ctx_or_per_tf, "dealing_range", None)
  bias = str(getattr(ctx_or_per_tf, "htf_bias", "range"))
  return MarketMap(
    entries=capped,
    price=float(price),
    eq=float(dealing_range.eq) if dealing_range is not None else None,
    box_low=float(regime.range_low) if regime is not None else None,
    box_high=float(regime.range_high) if regime is not None else None,
    bias=bias,
    bias_tf=_bias_timeframe(per_tf, bias),
  )


def render_market_map(
  market_map: MarketMap,
  symbol: str,
  now: datetime,
  cfg,
) -> str:
  clock = now.strftime("%H:%M")
  bias = market_map.bias
  if market_map.bias_tf:
    bias += f" ({market_map.bias_tf})"
  context = _session_context(now, cfg)
  summary = f"bias {bias} · {context}"
  if market_map.box_low is not None and market_map.box_high is not None:
    summary += (
      f" · box {_format_number(market_map.box_low)}"
      f"–{_format_number(market_map.box_high)}"
    )
  if market_map.eq is not None:
    summary += f" · EQ ~{_format_number(market_map.eq)}"
  lines = [
    f"🗺 {symbol.upper()} Market Map · {clock} · price {_format_number(market_map.price)}",
    summary,
    "",
    "SELL",
    *_render_side(market_map.sells, market_map.price),
    "",
    "BUY",
    *_render_side(market_map.buys, market_map.price),
  ]
  return f"<pre>{escape(chr(10).join(lines))}</pre>"


def map_reference(
  market_map: MarketMap,
  direction: str,
  lo: float,
  hi: float,
) -> str | None:
  side = "buy" if direction.upper() == "BUY" else "sell"
  matches = [
    entry for entry in market_map.entries
    if entry.side == side and _bands_overlap(entry.lo, entry.hi, lo, hi)
  ]
  if not matches:
    return None
  entry = min(matches, key=lambda item: (_distance(item, (lo + hi) / 2), -item.score))
  tags = "·".join(_compact_tags(entry.tags, 2))
  return (
    f"map: {side.upper()} {_format_band(entry.label_lo, entry.label_hi)}"
    f" ({tags})"
  )


def market_map_payload(market_map: MarketMap) -> str:
  return json.dumps(asdict(market_map), separators=(",", ":"), sort_keys=True)


def market_map_from_payload(payload: str) -> MarketMap:
  data = json.loads(payload)
  return MarketMap(
    entries=[MapEntry(**entry) for entry in data.get("entries", [])],
    price=float(data["price"]),
    eq=_optional_float(data.get("eq")),
    box_low=_optional_float(data.get("box_low")),
    box_high=_optional_float(data.get("box_high")),
    bias=str(data.get("bias", "range")),
    bias_tf=data.get("bias_tf"),
  )


def map_materially_changed(
  previous: MarketMap | None,
  current: MarketMap,
  minimum: float,
) -> bool:
  if previous is None:
    return True
  old = _entry_groups(previous.entries)
  new = _entry_groups(current.entries)
  if set(old) != set(new):
    return True
  threshold = max(0.0, float(minimum))
  for key in old:
    old_bands = sorted(old[key])
    new_bands = sorted(new[key])
    if len(old_bands) != len(new_bands):
      return True
    for first, second in zip(old_bands, new_bands):
      if (
        abs(first[0] - second[0]) >= threshold
        or abs(first[1] - second[1]) >= threshold
      ):
        return True
  return False


def _zone_entry(
  zone,
  tf: str,
  per_tf: dict,
  session_levels: list,
  price: float,
  major_score: float,
) -> MapEntry | None:
  side = _geometry_side(zone.side, (float(zone.low) + float(zone.high)) / 2, price)
  if side is None:
    return None
  score = float(getattr(zone, "score", 0.0))
  score_reasons = list(getattr(zone, "score_reasons", []) or [])
  major_level = next(
    (
      level.name for level in session_levels
      if level.name in _MAJOR_SESSION_LEVELS
      and float(zone.low) <= float(level.price) <= float(zone.high)
    ),
    None,
  )
  htf = "HTF zone" in score_reasons
  tier = "major" if htf or major_level or score >= major_score else "zone"
  tags = _zone_tags(zone, side)
  if htf:
    tags.append(_htf_tag(zone, tf, per_tf))
  if major_level:
    tags.append(major_level)
  tags.extend(_score_tags(score_reasons))
  return _entry(side, float(zone.low), float(zone.high), tier, tags, score)


def _zone_tags(zone, side: str) -> list[str]:
  sources = list(getattr(zone, "sources", []) or [])
  if not sources and getattr(zone, "source", ""):
    sources = [zone.source]
  tags: list[str] = []
  if "order_block" in sources:
    tags.append("OB")
  else:
    tags.append("demand" if side == "buy" else "supply")
  source_tags = {
    "breaker": "breaker",
    "flip_zone": "flip",
    "supply_demand": "demand" if side == "buy" else "supply",
    "bullish_fvg": "FVG",
    "bearish_fvg": "FVG",
    "box_breakout": "breakout-retest",
  }
  for source in sources:
    tag = source_tags.get(source)
    if tag:
      tags.append(tag)
  if "flip_zone" in sources:
    tags.append("breakout-retest")
  if int(getattr(zone, "touches", 0)) == 0:
    tags.append("fresh")
  return _unique(tags)


def _score_tags(reasons: list[str]) -> list[str]:
  tags: list[str] = []
  for reason in reasons:
    if reason == "liquidity pool":
      tags.append("sweep pool")
    elif reason == "sweep A":
      tags.append("sweep A")
    elif reason in _MAJOR_SESSION_LEVELS or reason.endswith(("_H", "_L")):
      tags.append(reason)
  return tags


def _htf_tag(zone, tf: str, per_tf: dict) -> str:
  for other_tf, item in sorted(
    per_tf.items(),
    key=lambda pair: (-_tf_rank(pair[0]), pair[0]),
  ):
    if _tf_rank(other_tf) <= _tf_rank(tf):
      continue
    for higher in getattr(item, "zones", []) or []:
      if (
        higher.side == zone.side
        and float(zone.low) >= float(higher.low)
        and float(zone.high) <= float(higher.high)
      ):
        return f"HTF {other_tf}"
  return "HTF"


def _level_entry(
  value: float,
  band: float,
  price: float,
  buy_tag: str,
  sell_tag: str,
  score: float,
  *,
  major: bool = False,
  tier: str = "level",
  support: bool = True,
  resistance: bool = True,
) -> list[MapEntry]:
  lo = value - band
  hi = value + band
  entries: list[MapEntry] = []
  if support and value < price:
    entries.append(_entry(
      "buy",
      lo,
      hi,
      "major" if major else tier,
      [buy_tag],
      score,
    ))
  if resistance and value > price:
    entries.append(_entry(
      "sell",
      lo,
      hi,
      "major" if major else tier,
      [sell_tag],
      score,
    ))
  return entries


def _entry(
  side: str,
  lo: float,
  hi: float,
  tier: str,
  tags: list[str],
  score: float,
) -> MapEntry:
  lo, hi = sorted((float(lo), float(hi)))
  label_lo, label_hi = _rounded_band(lo, hi)
  return MapEntry(
    side,
    lo,
    hi,
    label_lo,
    label_hi,
    tier,
    _unique(tags),
    float(score),
  )


def _rounded_band(lo: float, hi: float) -> tuple[int, int]:
  label_lo = math.floor(lo)
  label_hi = math.ceil(hi)
  if hi - lo < 1.0 and label_hi <= label_lo:
    label_hi = label_lo + 1
  return label_lo, label_hi


def _merge_display_entries(entries: list[MapEntry]) -> list[MapEntry]:
  """Collapse only genuine confluence and keep the actionable intersection."""
  merged: list[MapEntry] = []
  for side in ("buy", "sell"):
    ordered = sorted(
      (entry for entry in entries if entry.side == side),
      key=lambda entry: (entry.lo, entry.hi, -entry.score),
    )
    for entry in ordered:
      if (
        not merged
        or merged[-1].side != side
        or not _bands_overlap(merged[-1].lo, merged[-1].hi, entry.lo, entry.hi)
      ):
        merged.append(entry)
        continue
      previous = merged[-1]
      tier = max((previous.tier, entry.tier), key=lambda item: _TIER_RANK[item])
      merged[-1] = _entry(
        side,
        max(previous.lo, entry.lo),
        min(previous.hi, entry.hi),
        tier,
        [*previous.tags, *entry.tags],
        max(previous.score, entry.score),
      )
  return merged


def _attach_confluence(
  entries: list[MapEntry],
  references: list[MapEntry],
) -> tuple[list[MapEntry], list[MapEntry]]:
  attached = list(entries)
  unmatched: list[MapEntry] = []
  for reference in references:
    matches = [
      index for index, entry in enumerate(attached)
      if entry.side == reference.side
      and _bands_overlap(entry.lo, entry.hi, reference.lo, reference.hi)
    ]
    if not matches:
      unmatched.append(reference)
      continue
    center = (reference.lo + reference.hi) / 2
    index = min(matches, key=lambda item: _distance(attached[item], center))
    entry = attached[index]
    tier = max((entry.tier, reference.tier), key=lambda item: _TIER_RANK[item])
    attached[index] = _entry(
      entry.side,
      entry.lo,
      entry.hi,
      tier,
      [*entry.tags, *reference.tags],
      max(entry.score, reference.score),
    )
  return attached, unmatched


def _nearby_entries(
  entries: list[MapEntry],
  price: float,
  max_distance: float,
) -> list[MapEntry]:
  return [
    entry for entry in entries
    if _within_distance(entry, price, max_distance)
  ]


def _within_distance(entry: MapEntry, price: float, max_distance: float) -> bool:
  return (
    math.isfinite(entry.lo)
    and math.isfinite(entry.hi)
    and _distance(entry, price) <= max_distance
    and entry.hi - entry.lo <= max_distance
  )


def _reference_atr(per_tf: dict) -> float:
  for _, item in sorted(per_tf.items(), key=lambda pair: (_tf_rank(pair[0]), pair[0])):
    atr = getattr(item, "atr", None)
    if hasattr(atr, "dropna"):
      clean = atr.dropna()
      value = float(clean.iloc[-1]) if not clean.empty else 0.0
    else:
      value = atr_scalar(atr, fallback=0.0)
    if math.isfinite(value) and value > 0:
      return value
  return 0.0


def _render_side(entries: list[MapEntry], price: float) -> list[str]:
  ordered = sorted(entries, key=lambda entry: (_distance(entry, price), entry.label_lo))
  if not ordered:
    return ["└ no mapped levels"]
  lines = []
  for index, entry in enumerate(ordered):
    branch = "└" if index == len(ordered) - 1 else "├"
    details = " · ".join(_compact_tags(entry.tags, MAP_TAG_LIMIT))
    tags = entry.tier.upper()
    if details:
      tags += f" · {details}"
    suffix = " ⭐" if "breakout-retest" in entry.tags else ""
    lines.append(
      f"{branch} {_format_band(entry.label_lo, entry.label_hi)}  {tags}{suffix}"
    )
  return lines


def _compact_tags(tags: list[str], limit: int) -> list[str]:
  strongest: dict[str, tuple[int, str]] = {}
  regular: list[str] = []
  for tag in _unique(tags):
    group = _touch_group(tag)
    if group is None:
      regular.append(tag)
      continue
    touches = _touch_count(tag)
    if group not in strongest or touches > strongest[group][0]:
      strongest[group] = (touches, tag)
  regular.extend(value[1] for value in strongest.values())
  ordered = sorted(
    enumerate(regular),
    key=lambda item: (_tag_priority(item[1]), item[0]),
  )
  return [tag for _, tag in ordered[:max(0, limit)]]


def _touch_group(tag: str) -> str | None:
  prefixes = (
    "TL support ×",
    "TL resistance ×",
    "support ×",
    "resistance ×",
  )
  return next((prefix for prefix in prefixes if tag.startswith(prefix)), None)


def _touch_count(tag: str) -> int:
  try:
    return int(tag.rsplit("×", 1)[1])
  except (IndexError, ValueError):
    return 0


def _tag_priority(tag: str) -> int:
  if tag in {"OB", "demand", "supply"}:
    return 0
  if tag in {"flip", "breaker", "breakout-retest"}:
    return 1
  if tag in _MAJOR_SESSION_LEVELS:
    return 2
  if tag.startswith("HTF"):
    return 3
  if tag == "fresh":
    return 4
  if tag.startswith("sweep"):
    return 5
  if tag == "FVG":
    return 6
  if tag.endswith(("_H", "_L")):
    return 7
  if tag.startswith("TL "):
    return 8
  return 9


def _session_context(now: datetime, cfg) -> str:
  current = now.astimezone(timezone.utc)
  opens = [
    ("Asia", int(getattr(cfg, "session_asia_start", 22))),
    ("London", int(getattr(cfg, "session_london_start", 7))),
    ("NY", int(getattr(cfg, "session_ny_start", 13))),
  ]
  points = []
  for name, hour in opens:
    candidate = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate > current:
      candidate -= timedelta(days=1)
    points.append((candidate, name))
  _, name = max(points)
  order = [item[0] for item in opens]
  next_name = order[(order.index(name) + 1) % len(order)]
  return f"{name} → {next_name}"


def _bias_timeframe(per_tf: dict, bias: str) -> str | None:
  if bias not in {"up", "down"}:
    return None
  for tf, item in sorted(per_tf.items(), key=lambda pair: (-_tf_rank(pair[0]), pair[0])):
    structure = str(getattr(item, "structure", "range"))
    momentum = str(getattr(item, "momentum", "neutral"))
    if structure == bias or (bias == "up" and momentum == "bull"):
      return tf
    if bias == "down" and momentum == "bear":
      return tf
  return None


def _geometry_side(kind: str, anchor: float, price: float) -> str | None:
  if kind in {"demand", "support"} and anchor < price:
    return "buy"
  if kind in {"supply", "resistance"} and anchor > price:
    return "sell"
  return None


def _distance(entry: MapEntry, price: float) -> float:
  if entry.lo <= price <= entry.hi:
    return 0.0
  return min(abs(price - entry.lo), abs(price - entry.hi))


def _bands_overlap(first_lo: float, first_hi: float, lo: float, hi: float) -> bool:
  return min(first_hi, hi) >= max(first_lo, lo)


def _format_band(lo: int, hi: int) -> str:
  return f"{lo:,}–{hi:,}"


def _format_number(value: float) -> str:
  return f"{int(round(float(value))):,}"


def _entry_groups(entries: list[MapEntry]) -> dict[tuple, list[tuple[float, float]]]:
  groups: dict[tuple, list[tuple[float, float]]] = {}
  for entry in entries:
    key = (entry.side, entry.tier, tuple(entry.tags))
    groups.setdefault(key, []).append((entry.lo, entry.hi))
  return groups


def _optional_float(value) -> float | None:
  return None if value is None else float(value)


def _unique(items: list[str]) -> list[str]:
  result: list[str] = []
  for item in items:
    if item and item not in result:
      result.append(item)
  return result


def _tf_rank(tf: str) -> int:
  tf = tf.upper()
  if len(tf) < 2 or not tf[1:].isdigit():
    return 0
  value = int(tf[1:])
  if tf.startswith("M"):
    return value
  if tf.startswith("H"):
    return value * 60
  if tf.startswith("D"):
    return value * 1440
  return 0
