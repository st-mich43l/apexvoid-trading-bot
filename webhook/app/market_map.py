"""Pure two-sided market-map assembly and rendering."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math

from app.pa_math import atr_scalar
from app.swings import find_swings
from app.trendlines import value_at

MAP_MAX_PER_SIDE = 4
MAP_MAJOR_SCORE = 12.0
MAP_MAX_TOUCHES = 2
MAP_MIN_ZONE_SCORE = 6.0
MAP_MIN_LEVEL_TOUCHES = 4
MAP_MAX_DISTANCE_ATR = 15.0
MAP_CHANGE_MIN = 1.0
MAP_BAND_MAX_ATR = 2.0
MAP_MIN_PER_SIDE = 2
MAP_FALLBACK_RADIUS = 30.0
MAP_SCALP_RADIUS = 15.0
MAP_SCALP_TOL = 1.0
MAP_SCALP_MAX = 5
MAP_SCALP_FRACTAL_N = 1
SESSION_BAND_ATR = 0.1
MAP_TAG_LIMIT = 4
_TIER_RANK = {"level": 1, "zone": 2, "major": 3}
_MAJOR_SESSION_LEVELS = {"PDH", "PDL", "PWH", "PWL"}
_TODAY_SESSION_LEVELS = {
  "ASIA_H",
  "ASIA_L",
  "LONDON_H",
  "LONDON_L",
  "NY_H",
  "NY_L",
}


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
class ScalpRail:
  price: float
  lo: float
  hi: float
  label: int
  direction: str
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
  rails: list[ScalpRail] = field(default_factory=list)

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
  revisit_candidates: list[MapEntry] = []
  swept_candidates: list[MapEntry] = []
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
  band_max = max(
    5.0,
    max(0.0, float(getattr(cfg, "map_band_max_atr", MAP_BAND_MAX_ATR)))
    * reference_atr,
  )
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
      touches = int(getattr(zone, "touches", 0))
      if touches == max_touches:
        entry = _zone_entry(zone, tf, per_tf, session_levels, price, major_score)
        if entry is not None and _within_distance(entry, price, max_distance):
          revisit_candidates.append(replace(
            entry,
            tier="level",
            tags=_fallback_tags(entry.tags, "revisit"),
          ))
      if touches >= max_touches:
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
        for entry in _nearby_entries(_level_entry(
          float(level.price),
          session_band,
          price,
          str(level.name),
          str(level.name),
          3.0,
        ), price, max_distance):
          swept_candidates.append(replace(
            entry,
            tier="level",
            tags=_fallback_tags(entry.tags, "swept"),
          ))
        continue
      level_candidates.extend(_nearby_entries(_level_entry(
        float(level.price),
        session_band,
        price,
        str(level.name),
        str(level.name),
        major_score if level.name in _MAJOR_SESSION_LEVELS else 4.0,
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

  zones = _merge_display_entries(zone_candidates, band_max)
  levels = _merge_display_entries(level_candidates, band_max)
  zones, levels = _attach_confluence(zones, levels)
  entries = [*zones, *levels]
  entries, _ = _attach_confluence(entries, trendline_candidates)
  entries = _merge_display_entries(entries, band_max)
  capped: list[MapEntry] = []
  min_per_side = max(0, int(getattr(cfg, "map_min_per_side", MAP_MIN_PER_SIDE)))
  max_per_side = max(
    min_per_side,
    int(getattr(cfg, "map_max_per_side", MAP_MAX_PER_SIDE)),
  )
  fallback_radius = max(
    0.0,
    float(getattr(cfg, "map_fallback_radius", MAP_FALLBACK_RADIUS)),
  )
  round_candidates = _round_fallback_entries(
    price,
    float(getattr(cfg, "round_step", 5.0)),
    fallback_radius,
  )
  for side in ("sell", "buy"):
    ranked = _rank_entries(
      (entry for entry in entries if entry.side == side),
      price,
    )
    selected = ranked[:max_per_side]
    if len(selected) < min_per_side:
      selected = _fill_side(
        selected,
        side,
        (revisit_candidates, swept_candidates, round_candidates),
        min_per_side,
        max_per_side,
        band_max,
        price,
      )
    capped.extend(_rank_entries(selected, price)[:max_per_side])

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
    rails=_build_scalp_rails(
      ctx_or_per_tf,
      per_tf,
      float(price),
      reference_atr,
      cfg,
    ),
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
    "SCALP",
    *_render_rails(market_map.rails),
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


def rail_reference(
  market_map: MarketMap,
  lo: float,
  hi: float,
) -> str | None:
  matches = [
    rail for rail in market_map.rails
    if _bands_overlap(rail.lo, rail.hi, lo, hi)
  ]
  if not matches:
    return None
  center = (lo + hi) / 2
  rail = min(matches, key=lambda item: (abs(item.price - center), -item.score))
  tags = "·".join(_compact_rail_tags(rail.tags, 3))
  suffix = f" {tags}" if tags else ""
  return f"rail: {rail.direction} {_format_number(rail.label)}{suffix}"


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
    rails=[_rail_from_payload(rail) for rail in data.get("rails", [])],
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
  return _rails_materially_changed(previous.rails, current.rails, threshold)


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
  htf = any(reason.lower() == "htf zone" for reason in score_reasons)
  fresh = int(getattr(zone, "touches", 0)) == 0
  tier = "major" if htf and (fresh or score >= major_score) else "zone"
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
    _compact_tags(tags, MAP_TAG_LIMIT),
    float(score),
  )


def _rounded_band(lo: float, hi: float) -> tuple[int, int]:
  label_lo = math.floor(lo)
  label_hi = math.ceil(hi)
  if hi - lo < 1.0 and label_hi <= label_lo:
    label_hi = label_lo + 1
  return label_lo, label_hi


def _merge_display_entries(
  entries: list[MapEntry],
  band_max: float,
) -> list[MapEntry]:
  """Resolve same-side display bands without chain-merge blobs or overlap."""
  cap = max(0.001, float(band_max))
  resolved: list[MapEntry] = []
  for side in ("buy", "sell"):
    candidates = sorted(
      (
        entry for entry in entries
        if entry.side == side
        and math.isfinite(entry.lo)
        and math.isfinite(entry.hi)
      ),
      key=_entry_sort_key,
    )
    candidates = [
      entry for index, entry in enumerate(candidates)
      if not _is_oversized_container(entry, candidates, index, cap)
    ]
    candidates = [_cap_entry_width(entry, cap) for entry in candidates]
    merged: list[MapEntry] = []
    for entry in candidates:
      if not merged or not _bands_overlap(
        merged[-1].lo,
        merged[-1].hi,
        entry.lo,
        entry.hi,
      ):
        merged.append(entry)
        continue
      previous = merged[-1]
      union_lo = min(previous.lo, entry.lo)
      union_hi = max(previous.hi, entry.hi)
      if union_hi - union_lo <= cap:
        merged[-1] = _merged_entry(previous, entry, union_lo, union_hi)
      else:
        merged.append(entry)
    resolved.extend(_remove_display_overlaps(merged, cap))
  return resolved


def _is_oversized_container(
  entry: MapEntry,
  entries: list[MapEntry],
  index: int,
  cap: float,
) -> bool:
  if entry.hi - entry.lo <= cap:
    return False
  for other_index, other in enumerate(entries):
    if other_index == index:
      continue
    if (
      entry.lo <= other.lo
      and entry.hi >= other.hi
      and other.hi - other.lo < entry.hi - entry.lo
    ):
      return True
  return False


def _cap_entry_width(entry: MapEntry, cap: float) -> MapEntry:
  if entry.hi - entry.lo <= cap:
    return _limit_label_width(entry, cap)
  center = (entry.lo + entry.hi) / 2
  capped = _entry(
    entry.side,
    center - (cap / 2),
    center + (cap / 2),
    entry.tier,
    entry.tags,
    entry.score,
  )
  return _limit_label_width(capped, cap)


def _limit_label_width(entry: MapEntry, cap: float) -> MapEntry:
  label_cap = max(1, int(math.floor(cap)))
  if entry.label_hi - entry.label_lo <= label_cap:
    return entry
  center = int(round((entry.lo + entry.hi) / 2))
  label_lo = center - (label_cap // 2)
  return replace(entry, label_lo=label_lo, label_hi=label_lo + label_cap)


def _merged_entry(
  first: MapEntry,
  second: MapEntry,
  lo: float,
  hi: float,
) -> MapEntry:
  tier = max((first.tier, second.tier), key=lambda item: _TIER_RANK[item])
  return _entry(
    first.side,
    lo,
    hi,
    tier,
    [*first.tags, *second.tags],
    max(first.score, second.score),
  )


def _remove_display_overlaps(
  entries: list[MapEntry],
  cap: float,
) -> list[MapEntry]:
  ordered = sorted(entries, key=_entry_sort_key)
  index = 1
  while index < len(ordered):
    previous = ordered[index - 1]
    current = ordered[index]
    if previous.hi > current.lo:
      if _entry_quality(previous) >= _entry_quality(current):
        if current.hi <= previous.hi:
          ordered.pop(index)
          continue
        ordered[index] = _entry(
          current.side,
          previous.hi,
          current.hi,
          current.tier,
          current.tags,
          current.score,
        )
      else:
        if previous.lo >= current.lo:
          ordered.pop(index - 1)
          index = max(1, index - 1)
          continue
        ordered[index - 1] = _entry(
          previous.side,
          previous.lo,
          current.lo,
          previous.tier,
          previous.tags,
          previous.score,
        )
    index += 1

  ordered = [_limit_label_width(entry, cap) for entry in ordered]
  index = 1
  while index < len(ordered):
    previous = ordered[index - 1]
    current = ordered[index]
    if previous.label_hi > current.label_lo:
      if _entry_quality(previous) >= _entry_quality(current):
        ordered[index] = replace(current, label_lo=previous.label_hi)
        if ordered[index].label_lo > ordered[index].label_hi:
          ordered.pop(index)
          continue
      else:
        ordered[index - 1] = replace(previous, label_hi=current.label_lo)
        if ordered[index - 1].label_hi < ordered[index - 1].label_lo:
          ordered.pop(index - 1)
          index = max(1, index - 1)
          continue
    index += 1
  return ordered


def _entry_sort_key(entry: MapEntry) -> tuple:
  return (
    entry.lo,
    entry.hi,
    -entry.score,
    -_TIER_RANK[entry.tier],
    tuple(tag.casefold() for tag in entry.tags),
  )


def _entry_quality(entry: MapEntry) -> tuple:
  return (
    entry.score,
    _TIER_RANK[entry.tier],
    -(entry.hi - entry.lo),
    -entry.lo,
  )


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


def _rank_entries(entries, price: float) -> list[MapEntry]:
  return sorted(
    entries,
    key=lambda entry: (
      -_TIER_RANK[entry.tier],
      -entry.score,
      _distance(entry, price),
      entry.label_lo,
      entry.label_hi,
      tuple(tag.casefold() for tag in entry.tags),
    ),
  )


def _fallback_tags(tags: list[str], marker: str) -> list[str]:
  base = [
    tag for tag in _compact_tags(tags, MAP_TAG_LIMIT - 1)
    if tag.casefold() != marker.casefold()
  ]
  return [*base, marker]


def _fill_side(
  entries: list[MapEntry],
  side: str,
  ladders: tuple[list[MapEntry], ...],
  minimum: int,
  maximum: int,
  band_max: float,
  price: float,
) -> list[MapEntry]:
  selected = list(entries)
  for ladder in ladders:
    candidates = sorted(
      (entry for entry in ladder if entry.side == side),
      key=lambda entry: (
        _distance(entry, price),
        -entry.score,
        entry.lo,
        entry.hi,
      ),
    )
    for candidate in candidates:
      if len(selected) >= minimum or len(selected) >= maximum:
        break
      candidate = _cap_entry_width(replace(candidate, tier="level"), band_max)
      if any(_entries_render_overlap(candidate, entry) for entry in selected):
        continue
      selected.append(candidate)
    if len(selected) >= minimum or len(selected) >= maximum:
      break
  return selected


def _round_fallback_entries(
  price: float,
  step: float,
  radius: float,
) -> list[MapEntry]:
  if step <= 0 or radius <= 0 or not math.isfinite(price):
    return []
  entries: list[MapEntry] = []
  first = math.ceil((price - radius) / step)
  last = math.floor((price + radius) / step)
  for multiple in range(first, last + 1):
    level = multiple * step
    if level == price:
      continue
    side = "buy" if level < price else "sell"
    entries.append(_entry(side, level, level, "level", ["round"], 1.0))
  return entries


def _entries_render_overlap(first: MapEntry, second: MapEntry) -> bool:
  raw_overlap = min(first.hi, second.hi) > max(first.lo, second.lo)
  label_overlap = min(first.label_hi, second.label_hi) > max(
    first.label_lo,
    second.label_lo,
  )
  return raw_overlap or label_overlap


def _build_scalp_rails(
  ctx_or_per_tf,
  per_tf: dict,
  price: float,
  reference_atr: float,
  cfg,
) -> list[ScalpRail]:
  radius = max(0.0, float(getattr(cfg, "map_scalp_radius", MAP_SCALP_RADIUS)))
  tolerance = max(0.001, float(getattr(cfg, "map_scalp_tol", MAP_SCALP_TOL)))
  maximum = max(0, int(getattr(cfg, "map_scalp_max", MAP_SCALP_MAX)))
  if not per_tf or radius <= 0 or maximum == 0:
    return []
  exec_tf = str(getattr(cfg, "scanner_exec_tf", "")).upper()
  item = per_tf.get(exec_tf)
  if item is None:
    _, item = min(per_tf.items(), key=lambda pair: (_tf_rank(pair[0]), pair[0]))
  atr = getattr(item, "atr", None)
  atr_value = _last_atr(atr, reference_atr)
  candidates: list[tuple[float, list[str], float]] = []

  for barrier in getattr(item, "scalp_barriers", []) or []:
    candidates.append((
      float(barrier.level),
      list(barrier.tags),
      float(barrier.score),
    ))

  df = getattr(item, "df", None)
  if df is not None and not df.empty:
    fractal_n = max(
      1,
      int(getattr(cfg, "map_scalp_fractal_n", MAP_SCALP_FRACTAL_N)),
    )
    micro = find_swings(
      df,
      fractal_n=fractal_n,
      zigzag_pct=0.0,
      zigzag_atr_mult=0.0,
      atr=atr,
    )
    cluster_tolerance = max(0.0, atr_value * 0.3)
    for cluster in _cluster_swing_prices(micro, cluster_tolerance):
      level = sum(swing.price for swing in cluster) / len(cluster)
      candidates.append((level, [f"micro ×{len(cluster)}"], float(len(cluster))))

  for name, level in _latest_session_levels(
    getattr(item, "session_levels", []) or [],
  ):
    candidates.append((float(level.price), [f"session {name}"], 4.0))

  regime = getattr(ctx_or_per_tf, "regime", None) or getattr(item, "regime", None)
  if regime is not None:
    candidates.extend([
      (float(regime.range_high), ["box-top"], 5.0),
      (float(regime.range_low), ["box-bottom"], 5.0),
    ])

  current_bar = max(0, len(getattr(item, "df", [])) - 1)
  for line in getattr(item, "trendlines", []) or []:
    if bool(getattr(line, "broken", False)):
      continue
    candidates.append((
      value_at(line, current_bar),
      [f"TL {line.kind} ×{line.touches}"],
      float(line.touches),
    ))

  step = float(getattr(cfg, "round_step", 5.0))
  if step > 0:
    first = math.ceil((price - radius) / step)
    last = math.floor((price + radius) / step)
    for multiple in range(first, last + 1):
      candidates.append((multiple * step, ["round"], 1.0))

  rails: list[ScalpRail] = []
  ordered = sorted(
    (
      candidate for candidate in candidates
      if math.isfinite(candidate[0])
      and 0 < abs(candidate[0] - price) <= radius
    ),
    key=lambda candidate: (
      abs(candidate[0] - price),
      -candidate[2],
      candidate[0],
      tuple(tag.casefold() for tag in candidate[1]),
    ),
  )
  for level, tags, score in ordered:
    duplicate = next(
      (rail for rail in rails if abs(rail.price - level) <= 1.5 * tolerance),
      None,
    )
    if duplicate is not None:
      index = rails.index(duplicate)
      rails[index] = replace(
        duplicate,
        tags=_compact_rail_tags([*duplicate.tags, *tags], 4),
        score=max(duplicate.score, score),
      )
      continue
    direction = "SELL" if level > price else "BUY"
    rails.append(ScalpRail(
      price=float(level),
      lo=float(level - tolerance),
      hi=float(level + tolerance),
      label=int(round(level)),
      direction=direction,
      tags=_compact_rail_tags(tags, 4),
      score=float(score),
    ))
  return sorted(
    rails,
    key=lambda rail: (abs(rail.price - price), rail.price, rail.direction),
  )[:maximum]


def _cluster_swing_prices(swings: list, tolerance: float) -> list[list]:
  clusters: list[list] = []
  for swing in sorted(swings, key=lambda item: (item.price, int(item.index))):
    center = (
      sum(item.price for item in clusters[-1]) / len(clusters[-1])
      if clusters
      else 0.0
    )
    if clusters and abs(swing.price - center) <= tolerance:
      clusters[-1].append(swing)
    else:
      clusters.append([swing])
  return clusters


def _latest_session_levels(levels: list) -> list[tuple[str, object]]:
  latest: dict[str, object] = {}
  for level in levels:
    name = str(getattr(level, "name", "")).upper()
    if name not in _TODAY_SESSION_LEVELS:
      continue
    previous = latest.get(name)
    if previous is None or getattr(level, "ts", 0) > getattr(previous, "ts", 0):
      latest[name] = level
  return sorted(latest.items())


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


def _last_atr(atr, fallback: float) -> float:
  if hasattr(atr, "dropna"):
    clean = atr.dropna()
    value = float(clean.iloc[-1]) if not clean.empty else fallback
  else:
    value = atr_scalar(atr, fallback=fallback)
  return value if math.isfinite(value) and value > 0 else fallback


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
    suffix = " ⭐" if any(
      tag.casefold() == "breakout-retest" for tag in entry.tags
    ) else ""
    lines.append(
      f"{branch} {_format_band(entry.label_lo, entry.label_hi)}  {tags}{suffix}"
    )
  return lines


def _render_rails(rails: list[ScalpRail]) -> list[str]:
  if not rails:
    return ["└ no nearby rails"]
  lines: list[str] = []
  for index, rail in enumerate(rails):
    branch = "└" if index == len(rails) - 1 else "├"
    details = " · ".join(_compact_rail_tags(rail.tags, 3))
    suffix = f"  {details}" if details else ""
    lines.append(
      f"{branch} {rail.direction} {_format_number(rail.label)}{suffix}"
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
    "tl support ×",
    "tl resistance ×",
    "support ×",
    "resistance ×",
  )
  folded = tag.casefold()
  return next((prefix for prefix in prefixes if folded.startswith(prefix)), None)


def _touch_count(tag: str) -> int:
  try:
    return int(tag.rsplit("×", 1)[1])
  except (IndexError, ValueError):
    return 0


def _tag_priority(tag: str) -> int:
  folded = tag.casefold()
  if folded == "ob":
    return 0
  if folded == "breaker":
    return 1
  if folded == "flip":
    return 2
  if folded in {"demand", "supply"}:
    return 3
  if folded == "fvg":
    return 4
  if folded == "breakout-retest":
    return 5
  if folded in {"revisit", "swept", "round"}:
    return 6
  if tag.upper() in _MAJOR_SESSION_LEVELS:
    return 7
  if folded.startswith("htf"):
    return 8
  if folded == "fresh":
    return 9
  if folded.startswith("sweep"):
    return 10
  if folded.endswith(("_h", "_l")):
    return 11
  if folded.startswith("tl "):
    return 12
  return 13


def _compact_rail_tags(tags: list[str], limit: int) -> list[str]:
  priority = {
    "micro": 0,
    "box": 1,
    "session": 2,
    "tl": 3,
    "round": 4,
  }
  ordered = sorted(
    enumerate(_unique(tags)),
    key=lambda item: (
      next(
        (
          rank for prefix, rank in priority.items()
          if item[1].casefold().startswith(prefix)
        ),
        5,
      ),
      item[0],
    ),
  )
  return [tag for _, tag in ordered[:max(0, limit)]]


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


def _rails_materially_changed(
  previous: list[ScalpRail],
  current: list[ScalpRail],
  minimum: float,
) -> bool:
  old = sorted(previous, key=lambda rail: (rail.direction, rail.price, rail.label))
  new = sorted(current, key=lambda rail: (rail.direction, rail.price, rail.label))
  if len(old) != len(new):
    return True
  for first, second in zip(old, new):
    if (
      first.direction != second.direction
      or tuple(tag.casefold() for tag in first.tags)
      != tuple(tag.casefold() for tag in second.tags)
    ):
      return True
    difference = abs(first.price - second.price)
    if (minimum > 0 and difference >= minimum) or (minimum == 0 and difference > 0):
      return True
  return False


def _optional_float(value) -> float | None:
  return None if value is None else float(value)


def _rail_from_payload(data: dict) -> ScalpRail:
  values = dict(data)
  direction = str(values.get("direction", "")).upper()
  values["direction"] = {
    "↑": "SELL",
    "↓": "BUY",
  }.get(direction, direction)
  return ScalpRail(**values)


def _unique(items: list[str]) -> list[str]:
  result: list[str] = []
  seen: set[str] = set()
  for item in items:
    key = item.casefold() if item else ""
    if item and key not in seen:
      result.append(item)
      seen.add(key)
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
