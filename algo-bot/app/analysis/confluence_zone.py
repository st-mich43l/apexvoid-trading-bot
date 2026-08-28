"""Confluence-merge zone (Codex Prompt P3, Part 1).

A single price band that is simultaneously a key level, demand/supply, order
block, and/or FVG must collapse into ONE `ConfluenceZone` carrying the union
of those tags, so it produces one setup and one order - never several
strategies each ordering on the same or a nearby band.

This module is deliberately independent of `app/analysis/market_map.py`'s own
same-side display merge (`_merge_display_entries`/`_attach_confluence`),
which exists purely to shape the Telegram Market Map card and has no zone_id/
provenance/confluence-score concept. `ConfluenceZone` is the strategy/
execution-facing identity: it is what a confirmed setup's thesis claim keys
on, via `resolve_confluence_zone_id`/`claim_confluence_zone` below.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.core.config import runtime_config
from app.runtime.price_identity import price_token

CONFLUENCE_ZONE_CLAIM_KEY_PREFIX = "auto_trade:confluence_zone_claim"

# Same directional-role vocabulary reaction_identity.py already folds into a
# structural_zone_id - a bullish ("buy") member never merges with a bearish
# ("sell") one, so a merged zone is always single-direction by construction.
_MERGEABLE_KINDS = frozenset({
  "key_level",
  "demand",
  "supply",
  "ob",
  "fvg",
  "breaker",
})


def _sha(raw: str) -> str:
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


ZONE_TOO_NARROW = "zone_too_narrow"
ZONE_TOO_WIDE = "zone_too_wide"


class BandKind:
  """Section 4: the XAU_ZONE_MIN_WIDTH_PRICE structural-zone contract must
  not be applied to every reaction band uniformly. A key level, session
  high/low, or trendline reaction is a tolerance band around one price -
  it has no real "structural boundaries" the way a merged supply/demand/
  order-block/FVG zone does, so judging it by the same 3.0-price minimum
  either kills a genuinely valid narrow level or forces the detector to
  fabricate a wider band that misrepresents where the level actually is.
  """

  STRUCTURAL_ZONE = "structural_zone"
  LEVEL_BAND = "level_band"
  RANGE_EDGE_BAND = "range_edge_band"
  BREAKOUT_RETEST_BAND = "breakout_retest_band"


_LEVEL_BAND_SOURCES = frozenset({
  "key_level", "session_level", "trendline", "liquidity_pool",
})
_RANGE_EDGE_BAND_SOURCES = frozenset({"range", "range_edge", "range_scalp"})
_BREAKOUT_RETEST_BAND_SOURCES = frozenset({"box_breakout", "breakout_retest"})


def classify_band_kind(structural_source: str | None) -> str:
  """Map a DetectionResult's structural_source to its BandKind.

  Anything not recognized as a level/range-edge/breakout-retest source
  falls through to STRUCTURAL_ZONE (today: "supply_demand", covering
  demand/supply/OB/FVG/breaker evidence) - the conservative default, since
  that is the one kind of band the 3.0-price minimum is actually meant for.
  """
  source = str(structural_source or "").casefold()
  if source in _LEVEL_BAND_SOURCES:
    return BandKind.LEVEL_BAND
  if source in _RANGE_EDGE_BAND_SOURCES:
    return BandKind.RANGE_EDGE_BAND
  if source in _BREAKOUT_RETEST_BAND_SOURCES:
    return BandKind.BREAKOUT_RETEST_BAND
  return BandKind.STRUCTURAL_ZONE


@dataclass(frozen=True)
class ZoneWidthResult:
  """Section 4 XAU zone-width contract telemetry, in actual price units.

  ``raw_zone_width`` is the width of the single strongest structural member
  before any merge; ``merged_zone_width`` is the final band width after
  same-side confluence merging. A zone is eligible only when the merged
  width lands inside [min_required_width, max_allowed_width] - a weak,
  isolated narrow level is never artificially stretched to pass, and an
  excessively wide band is rejected rather than silently kept.
  """

  eligible: bool
  raw_zone_width: float
  merged_zone_width: float
  min_required_width: float
  max_allowed_width: float
  merge_sources: tuple[str, ...]
  rejection_reason: str | None


def validate_zone_width(
  *,
  raw_width: float,
  merged_width: float,
  merge_sources: Iterable[str],
  is_major: bool = False,
  min_width: float | None = None,
  preferred_max_width: float | None = None,
  major_max_width: float | None = None,
  symbol: str | None = None,
  config: Any | None = None,
) -> ZoneWidthResult:
  """Apply the configured instrument zone-width contract to one zone.

  ``is_major`` (typically an H1-sourced zone) uses the wider
  ``major_max_width`` ceiling instead of the normal ``preferred_max_width``
  cap - a major H1 supply/demand band is allowed to be wider than a normal
  M5/M15 zone without being rejected as "too broad". Callers on the hot
  scanner path should leave the *_width kwargs unset so this always reads
  the live settings; the explicit parameters exist so tests (and any
  offline width-audit tooling) can probe the contract without monkeypatching
  global settings.
  """
  root = runtime_config if config is None else config
  instrument_zones = (
    root.for_instrument(symbol).analysis.zones
    if symbol is not None
    else None
  )
  default_minimum = (
    instrument_zones.minimum_width_price
    if instrument_zones is not None
    else root.analysis.zones.symbol_contract.minimum_width_price
  )
  default_preferred_maximum = (
    instrument_zones.preferred_maximum_width_price
    if instrument_zones is not None
    else root.analysis.zones.symbol_contract.preferred_maximum_width_price
  )
  default_major_maximum = (
    instrument_zones.major_maximum_width_price
    if instrument_zones is not None
    else root.analysis.zones.symbol_contract.major_maximum_width_price
  )
  resolved_min = float(
    min_width
    if min_width is not None
    else default_minimum
  )
  resolved_max = float(
    (
      major_max_width
      if major_max_width is not None
      else default_major_maximum
    )
    if is_major
    else (
      preferred_max_width
      if preferred_max_width is not None
      else default_preferred_maximum
    )
  )
  sources = tuple(merge_sources)
  width = max(0.0, float(merged_width))
  if width < resolved_min:
    reason = ZONE_TOO_NARROW
  elif width > resolved_max:
    reason = ZONE_TOO_WIDE
  else:
    reason = None
  return ZoneWidthResult(
    eligible=reason is None,
    raw_zone_width=max(0.0, float(raw_width)),
    merged_zone_width=width,
    min_required_width=resolved_min,
    max_allowed_width=resolved_max,
    merge_sources=sources,
    rejection_reason=reason,
  )


@dataclass(frozen=True)
class ConfluenceMember:
  """One raw structure eligible to merge into a ConfluenceZone.

  `member_id` is the structure's own stable id (eg. a Zone.id, a key-level
  id, or any caller-chosen identifier) - kept only for `provenance`, never
  used for zone_id derivation (that must stay stable across which specific
  members happen to be present on a given bar).
  """

  member_id: str
  side: str  # "buy" | "sell"
  low: float
  high: float
  kind: str  # one of _MERGEABLE_KINDS, or any other tag - unmergeable kinds
             # still pass through as their own single-member zone.
  score: float = 0.0


@dataclass(frozen=True)
class ConfluenceZone:
  low: float
  high: float
  side: str
  tags: tuple[str, ...]
  confluence: int
  zone_id: str
  provenance: tuple[str, ...]
  score: float = 0.0


def _stable_atr(atr: float, pip_size: float) -> float:
  # Mirrors reaction_identity.py's _stable_atr exactly (see _bucket below).
  #
  # Bucket size must not drift with ordinary ATR noise - live ATR is a
  # rolling per-bar indicator that fluctuates constantly even when the
  # underlying zone hasn't moved at all. Bug found live: the same real
  # $4077-4081 SELL key-level band rehashed into a brand-new zone_id ~10
  # times in one hour purely because ATR ticked across the fractional
  # threshold that changes bucket size (atr=1.0 and atr=5 already produce
  # different zone_ids for an identical band) - each rehash reset touch
  # count to zero and the zone never survived long enough to execute.
  # Quantizing ATR into a coarse step before it can affect bucket size means
  # the grid only moves on a genuine regime-scale ATR change, not routine
  # bar-to-bar noise.
  step = float(pip_size) * 40.0
  return round(max(0.0, float(atr)) / step) * step


def _bucket(mid: float, *, atr: float, pip_size: float) -> float:
  # Mirrors reaction_identity.py's canonicalize_zone_bucket exactly, so a
  # merged zone's identity survives the same bar-to-bar coordinate jitter a
  # single structure's structural_zone_id already tolerates.
  #
  # Width is deliberately NOT part of this identity (bug found live: two
  # detections of literally the same resistance band measured 5.45 and 5.57
  # wide - both round to the same 1.0-unit mid bucket, but 5.45 and 5.57
  # straddle the width bucket's own rounding boundary at 5.5, so the old
  # width_bucket differed by a full unit between them and produced two
  # different zone_ids for one structural area, each publishing its own
  # TradePlan). Two overlapping/co-located structures merging into the same
  # band are the same trade idea regardless of the merged band's exact
  # measured width from one scan to the next - only where it sits matters
  # for identity.
  bucket = max(
    float(pip_size) * 10.0,
    _stable_atr(atr, pip_size) * 0.25,
  )
  return round(mid / bucket) * bucket


def confluence_zone_id(
  symbol: str,
  side: str,
  low: float,
  high: float,
  tags: Iterable[str],
  *,
  atr: float,
  pip_size: float,
  source_tf: str = "M5",
) -> str:
  """Stable id for a merged band: bucketed mid + sorted tag union.

  Deliberately independent of any single member's 5dp boundary, exact
  measured width, or which specific members happen to be present - two
  overlapping structures that merge into the same band always produce the
  same id regardless of merge order, member count, or per-bar jitter in
  each member's own coordinates (including jitter in the merged band's
  measured width - see _bucket's docstring).
  """
  mid = (float(low) + float(high)) / 2.0
  mid_bucket = _bucket(mid, atr=atr, pip_size=pip_size)
  tag_text = ",".join(sorted({str(tag).casefold() for tag in tags}))
  return _sha(
    f"cz|{symbol.upper()}|{side.lower()}|{source_tf.upper()}|"
    f"{price_token(mid_bucket, pip_size=pip_size)}|{tag_text}"
  )


def confluence_setup_id(zone_id: str, direction: str) -> str:
  """Stable setup identity shared by every detector in one merged zone."""
  return _sha(
    f"confluence-setup|{zone_id}|{direction.upper()}"
  )[:32]


def _overlaps_or_within_gap(
  a_low: float, a_high: float, b_low: float, b_high: float, gap: float,
) -> bool:
  if a_low <= b_high and b_low <= a_high:
    return True  # overlap
  nearest_gap = b_low - a_high if b_low > a_high else a_low - b_high
  return nearest_gap <= gap


def merge_confluence_zones(
  members: Sequence[ConfluenceMember],
  *,
  symbol: str,
  atr: float,
  pip_size: float,
  source_tf: str = "M5",
  max_width: float = 6.0,
  gap: float = 1.0,
) -> list[ConfluenceZone]:
  """Collapse overlapping/nearby same-side members into ConfluenceZones.

  Two bands merge if they overlap or their nearest edges are within `gap`,
  provided the resulting union band stays <= `max_width` - if absorbing a
  candidate would exceed max_width, the tighter existing cluster is kept and
  the outlier is left as its own (single-member) zone, per spec: "keep the
  tighter cluster and do not absorb the outlier". Opposing sides never merge
  - callers partition by side, so cross-direction confluence never collapses
  into one order.
  """
  clusters: list[list[ConfluenceMember]] = []
  for side in ("buy", "sell"):
    side_members = sorted(
      (member for member in members if member.side == side),
      key=lambda member: (member.low, member.high),
    )
    for member in side_members:
      placed = False
      for cluster in clusters:
        if cluster[0].side != side:
          continue
        cluster_low = min(item.low for item in cluster)
        cluster_high = max(item.high for item in cluster)
        if not _overlaps_or_within_gap(
          cluster_low, cluster_high, member.low, member.high, gap,
        ):
          continue
        union_low = min(cluster_low, member.low)
        union_high = max(cluster_high, member.high)
        if union_high - union_low > max_width:
          continue  # would exceed max width - keep the tighter cluster
        cluster.append(member)
        placed = True
        break
      if not placed:
        clusters.append([member])

  zones: list[ConfluenceZone] = []
  for cluster in clusters:
    low = min(item.low for item in cluster)
    high = max(item.high for item in cluster)
    side = cluster[0].side
    tags = tuple(sorted({item.kind for item in cluster}))
    # More distinct structural types = higher confluence, without
    # double-counting identical members (a tags set already dedupes kinds).
    confluence = len(tags)
    zone_id = confluence_zone_id(
      symbol, side, low, high, tags,
      atr=atr, pip_size=pip_size, source_tf=source_tf,
    )
    zones.append(ConfluenceZone(
      low=low,
      high=high,
      side=side,
      tags=tags,
      confluence=confluence,
      zone_id=zone_id,
      provenance=tuple(item.member_id for item in cluster),
      score=max((item.score for item in cluster), default=0.0),
    ))
  return zones


def resolve_confluence_zone_id(
  candidate_low: float,
  candidate_high: float,
  candidate_side: str,
  candidate_tags: Iterable[str],
  *,
  other_members: Sequence[ConfluenceMember],
  symbol: str,
  atr: float,
  pip_size: float,
  source_tf: str = "M5",
  max_width: float = 6.0,
  gap: float = 1.0,
  candidate_id: str = "candidate",
) -> str:
  """The merged zone_id a just-confirmed candidate resolves onto.

  Feeds the candidate itself plus every other currently-known structure
  (typically the Market Map's structural pool) through the same merge used
  to build ConfluenceZones, then returns whichever merged zone the candidate
  ended up in - so two different strategies whose confirmed bands overlap
  the same structural cluster always compute the same id, and the second one
  correctly collides on the first one's zone claim.
  """
  candidate = ConfluenceMember(
    member_id=candidate_id,
    side=candidate_side.lower(),
    low=float(candidate_low),
    high=float(candidate_high),
    kind=next(iter(candidate_tags), "candidate"),
  )
  zones = merge_confluence_zones(
    [candidate, *other_members],
    symbol=symbol, atr=atr, pip_size=pip_size, source_tf=source_tf,
    max_width=max_width, gap=gap,
  )
  for zone in zones:
    if candidate_id in zone.provenance:
      return zone.zone_id
  # Unreachable in practice (the candidate always lands in exactly one
  # cluster - its own, at minimum), but fail closed to a candidate-only id
  # rather than raising out of a hot publish path.
  return confluence_zone_id(
    symbol, candidate_side, candidate_low, candidate_high, candidate_tags,
    atr=atr, pip_size=pip_size, source_tf=source_tf,
  )


def confluence_zone_claim_key(zone_id: str) -> str:
  return f"{CONFLUENCE_ZONE_CLAIM_KEY_PREFIX}:{zone_id}"


async def claim_confluence_zone(
  client: Any,
  *,
  zone_id: str,
  owner_id: str,
  ttl: int = 86400,
) -> bool:
  """SETNX-style claim so at most one order comes from one merged zone.

  Mirrors setup_lifecycle.claim_active_thesis exactly (same semantics: True
  if this owner now holds the claim, either freshly or already; False if a
  different owner holds it) - kept as an independent claim rather than
  folded into the thesis claim because a merged zone can, in principle,
  be approached by candidates whose thesis_id differs (different structural
  tag subsets hash differently) even though they physically overlap.
  """
  key = confluence_zone_claim_key(zone_id)
  claimed = await client.set(key, owner_id, ex=ttl, nx=True)
  if claimed:
    return True
  current = await client.get(key)
  if current is None:
    return False
  current_id = current.decode() if isinstance(current, bytes) else current
  return current_id == owner_id


async def release_confluence_zone(
  client: Any,
  *,
  zone_id: str,
  owner_id: str,
) -> None:
  key = confluence_zone_claim_key(zone_id)
  current = await client.get(key)
  if current is None:
    return
  current_id = current.decode() if isinstance(current, bytes) else current
  if current_id == owner_id:
    await client.delete(key)


@dataclass(frozen=True)
class ConfluenceBand:
  """Merged price band where two or more distinct techniques overlap."""

  low: float
  high: float
  side: str
  technique_tags: tuple[str, ...]
  zone_id: str
  provenance: tuple[str, ...]
  score: float = 0.0
  touches: int = 0


def _technique_overlap_ratio(
  a_low: float,
  a_high: float,
  b_low: float,
  b_high: float,
) -> float:
  overlap = min(a_high, b_high) - max(a_low, b_low)
  if overlap <= 0:
    return 0.0
  smaller = min(a_high - a_low, b_high - b_low)
  if smaller <= 0:
    return 1.0 if a_low <= b_high and b_low <= a_high else 0.0
  return overlap / smaller


def build_confluence_bands(
  instances: Sequence[Any],
  *,
  symbol: str,
  atr: float,
  pip_size: float,
  source_tf: str = "M5",
  min_overlap: float = 0.5,
  max_width: float = 6.0,
  confluence_bonus: float = 2.5,
) -> list[ConfluenceBand]:
  """Cluster technique instances into Confluence bands (>=2 distinct techniques).

  ``instances`` must expose ``technique``, ``side``, ``low``, ``high``,
  ``instance_id``, and optional ``measured['score']``.
  """
  from app.analysis.technique_geometry import TECHNIQUE_TAGS

  clusters: list[list[Any]] = []
  for side in ("buy", "sell"):
    side_items = sorted(
      (item for item in instances if str(getattr(item, "side", "")).lower() == side),
      key=lambda item: (float(item.low), float(item.high)),
    )
    for item in side_items:
      technique = str(getattr(item, "technique", ""))
      if technique not in TECHNIQUE_TAGS:
        continue
      placed = False
      for cluster in clusters:
        if cluster[0].side != side:
          continue
        cluster_low = min(float(member.low) for member in cluster)
        cluster_high = max(float(member.high) for member in cluster)
        if _technique_overlap_ratio(
          cluster_low, cluster_high, float(item.low), float(item.high),
        ) < min_overlap:
          continue
        union_low = min(cluster_low, float(item.low))
        union_high = max(cluster_high, float(item.high))
        if union_high - union_low > max_width:
          continue
        cluster.append(item)
        placed = True
        break
      if not placed:
        clusters.append([item])

  bands: list[ConfluenceBand] = []
  for cluster in clusters:
    techniques = {str(member.technique) for member in cluster}
    if len(techniques) < 2:
      continue
    low = min(float(member.low) for member in cluster)
    high = max(float(member.high) for member in cluster)
    side = str(cluster[0].side).lower()
    tags = tuple(sorted(techniques))
    zone_id = confluence_zone_id(
      symbol, side, low, high, tags,
      atr=atr, pip_size=pip_size, source_tf=source_tf,
    )
    provenance = tuple(str(member.instance_id) for member in cluster)
    member_scores = [
      float((getattr(member, "measured", None) or {}).get("score", 0.0))
      for member in cluster
    ]
    member_touches = [
      int((getattr(member, "measured", None) or {}).get("touches", 0))
      for member in cluster
    ]
    score = max(member_scores) + confluence_bonus * (len(tags) - 1)
    bands.append(ConfluenceBand(
      low=low,
      high=high,
      side=side,
      technique_tags=tags,
      zone_id=zone_id,
      provenance=provenance,
      score=score,
      touches=max(member_touches, default=0),
    ))
  return bands


def confluence_band_covers_instance(
  band: ConfluenceBand,
  instance: Any,
  *,
  min_overlap: float = 0.5,
) -> bool:
  if str(getattr(instance, "side", "")).lower() != band.side:
    return False
  return _technique_overlap_ratio(
    band.low, band.high, float(instance.low), float(instance.high),
  ) >= min_overlap
