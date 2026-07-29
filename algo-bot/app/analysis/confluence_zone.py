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


def _bucket(mid: float, width: float, *, atr: float, pip_size: float) -> tuple[float, float]:
  # Mirrors reaction_identity.py's canonicalize_zone_bucket exactly, so a
  # merged zone's identity survives the same bar-to-bar coordinate jitter a
  # single structure's structural_zone_id already tolerates.
  bucket = max(float(pip_size) * 10.0, max(0.0, float(atr)) * 0.25, 1.0)
  mid_bucket = round(mid / bucket) * bucket
  width_bucket = round(width / bucket) * bucket
  return mid_bucket, width_bucket


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
  """Stable id for a merged band: bucketed mid/width + sorted tag union.

  Deliberately independent of any single member's 5dp boundary or which
  specific members happen to be present - two overlapping structures that
  merge into the same band always produce the same id regardless of merge
  order, member count, or per-bar jitter in each member's own coordinates.
  """
  mid = (float(low) + float(high)) / 2.0
  width = max(0.0, float(high) - float(low))
  mid_bucket, width_bucket = _bucket(mid, width, atr=atr, pip_size=pip_size)
  tag_text = ",".join(sorted({str(tag).casefold() for tag in tags}))
  return _sha(
    f"cz|{symbol.upper()}|{side.lower()}|{source_tf.upper()}|"
    f"{mid_bucket:.2f}|{width_bucket:.2f}|{tag_text}"
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
