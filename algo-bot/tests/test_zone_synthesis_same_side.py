"""Required regression tests D, E, F for the zone/M1 simplification
refactor: same-side zone synthesis and stable episode identity.

Discovery (see docs/p0-simple-zone-m1-baseline-map.md section 7): this
infrastructure already mostly exists - app.analysis.confluence_zone's
merge_confluence_zones/confluence_zone_id/confluence_setup_id already
produce a stable, tag-order-independent, coordinate-jitter-tolerant,
timestamp-free identity, and scanner.py's _merge_detection_confluence
already runs EVERY structural detection (not only ones that happen to
co-locate with another) through it, attaching confluence_zone_id - which
scanner.py's match_id construction then prefers over the unstable
structural_thesis_id (which does include touch/confirmation timestamps)
whenever it is present. These tests prove that mechanism actually holds
for the specific scenarios the spec requires, rather than building a
parallel domain model that would duplicate it.
"""

from __future__ import annotations

from app.analysis import scanner
from app.analysis.confluence_zone import confluence_setup_id
from app.analysis.detectors import DetectionResult
from app.analysis.types import Zone


def _key_level_result(
  low: float, high: float, *, direction: str = "BUY",
  structural_id: str = "key-level-1",
) -> DetectionResult:
  return DetectionResult(
    setup="Key Level",
    direction=direction,
    key_level=(low + high) / 2,
    entry_zone=Zone(low, high, "demand" if direction == "BUY" else "supply"),
    current_price=(low + high) / 2,
    confluence=2,
    reasons=["key level reaction"],
    structural_source="key_level",
    structural_id=structural_id,
    structural_kind="support" if direction == "BUY" else "resistance",
  )


def _demand_zone_result(
  low: float, high: float, *, direction: str = "BUY",
  structural_id: str = "demand-1", kind: str | None = None,
) -> DetectionResult:
  if kind is None:
    kind = "demand" if direction == "BUY" else "supply"
  return DetectionResult(
    setup="Demand Zone Reaction" if direction == "BUY" else "Supply Zone Reaction",
    direction=direction,
    key_level=(low + high) / 2,
    entry_zone=Zone(low, high, "demand" if direction == "BUY" else "supply"),
    current_price=(low + high) / 2,
    confluence=3,
    reasons=["zone reaction"],
    structural_source="supply_demand",
    structural_id=structural_id,
    structural_kind=kind,
  )


def _ob_result(low: float, high: float, *, structural_id: str = "ob-1") -> DetectionResult:
  return _demand_zone_result(low, high, structural_id=structural_id, kind="ob")


def _fvg_result(low: float, high: float, *, structural_id: str = "fvg-1") -> DetectionResult:
  return _demand_zone_result(low, high, structural_id=structural_id, kind="fvg")


def test_same_zone_multiple_source_types_merge_into_one_buy_opportunity():
  """D: key level + demand + OB + bullish FVG, all overlapping, collapse
  into one merged result carrying every evidence tag - not four separate
  StrategyMatches for the same band.
  """
  results = [
    _key_level_result(4099.0, 4101.0, structural_id="key-100"),
    _demand_zone_result(4098.5, 4100.5, structural_id="demand-100"),
    _ob_result(4099.2, 4100.8, structural_id="ob-100"),
    _fvg_result(4099.5, 4101.2, structural_id="fvg-100"),
  ]

  merged = scanner._merge_detection_confluence("XAU", "M5", results, atr=2.0)

  assert len(merged) == 1
  result = merged[0]
  assert result.confluence_zone_id
  assert set(result.confluence_tags) == {"key_level", "demand", "ob", "fvg"}


def test_same_zone_multiple_source_types_merge_into_one_sell_opportunity():
  results = [
    _key_level_result(4099.0, 4101.0, direction="SELL", structural_id="key-200"),
    _demand_zone_result(4098.5, 4100.5, direction="SELL", structural_id="supply-200"),
    _ob_result(4099.2, 4100.8, structural_id="ob-200"),
  ]
  # OB fixture defaults to BUY-shaped construction; force SELL explicitly.
  results[2] = DetectionResult(
    setup="Supply Zone Reaction", direction="SELL", key_level=4100.0,
    entry_zone=Zone(4099.2, 4100.8, "supply"), current_price=4100.0,
    confluence=3, reasons=["ob"], structural_source="supply_demand",
    structural_id="ob-200-sell", structural_kind="ob",
  )

  merged = scanner._merge_detection_confluence("XAU", "M5", results, atr=2.0)

  assert len(merged) == 1
  result = merged[0]
  assert result.direction == "SELL"
  assert set(result.confluence_tags) == {"key_level", "supply", "ob"}


def test_coordinate_jitter_resolves_to_the_same_episode_identity():
  """E: 4007.47-4012.47 (bar A) and 4007.41-4012.53 (bar B), same
  underlying source id, must resolve to the same confluence_zone_id (and
  therefore the same setup identity via confluence_setup_id) - not two
  different episodes because of sub-pip jitter.
  """
  bar_a = [_key_level_result(4007.47, 4012.47, structural_id="jitter-src")]
  bar_b = [_key_level_result(4007.41, 4012.53, structural_id="jitter-src")]

  merged_a = scanner._merge_detection_confluence("XAU", "M5", bar_a, atr=2.0)
  merged_b = scanner._merge_detection_confluence("XAU", "M5", bar_b, atr=2.0)

  assert merged_a[0].confluence_zone_id == merged_b[0].confluence_zone_id
  assert (
    confluence_setup_id(merged_a[0].confluence_zone_id, "BUY")
    == confluence_setup_id(merged_b[0].confluence_zone_id, "BUY")
  )


def test_new_confirmation_timestamp_does_not_change_episode_identity():
  """F: same structure and direction, different touch_bar_ts/
  confirmation_bar_ts/confirmation type, must resolve to the same
  episode/setup identity - confluence_zone_id never factors timestamps
  into its hash at all, unlike the legacy structural_thesis_id path.
  """
  from dataclasses import replace

  first = _key_level_result(4100.0, 4102.0, structural_id="confirm-src")
  first = replace(
    first, touch_bar_ts="1000", confirmation_bar_ts="1000",
    confirmation_type="wick_rejection",
  )
  second = _key_level_result(4100.0, 4102.0, structural_id="confirm-src")
  second = replace(
    second, touch_bar_ts="2000", confirmation_bar_ts="2600",
    confirmation_type="sweep_reclaim",
  )

  merged_first = scanner._merge_detection_confluence("XAU", "M5", [first], atr=2.0)
  merged_second = scanner._merge_detection_confluence("XAU", "M5", [second], atr=2.0)

  assert (
    merged_first[0].confluence_zone_id == merged_second[0].confluence_zone_id
  )
