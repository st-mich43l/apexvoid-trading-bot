"""refactor/p0-direct-zone-signal-execution: XAU zone-width contract (section 4).

Width is expressed in actual XAU price units, never pips/digits. A normal
tradable zone must land inside [XAU_ZONE_MIN_WIDTH_PRICE,
XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE] (or the wider XAU_MAJOR_ZONE_MAX_WIDTH_PRICE
ceiling for a major/H1 zone); a weak isolated narrow level must never be
artificially stretched to pass, and an excessive band must be rejected rather
than silently kept.
"""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import pytest

from app.analysis import scanner
from app.analysis.confluence_zone import (
  ConfluenceMember,
  ZONE_TOO_NARROW,
  ZONE_TOO_WIDE,
  merge_confluence_zones,
  validate_zone_width,
)
from app.analysis.detectors import DetectionResult
from app.analysis.types import Zone


pytestmark = pytest.mark.no_database


def _key_level_result(
  low: float, high: float, *, direction: str = "SELL",
  structural_id: str = "kl-narrow",
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
    structural_kind="resistance",
  )


def _structural_zone_result(
  low: float, high: float, *, direction: str = "SELL",
  structural_id: str = "sd-narrow",
) -> DetectionResult:
  # A genuine STRUCTURAL_ZONE source (BandKind.classify_band_kind), unlike
  # _key_level_result above - key levels/session levels/trendlines are
  # LEVEL_BAND and exempt from the 3.0-price minimum entirely (see
  # confluence_zone.classify_band_kind); only a real supply/demand/OB/FVG
  # band is subject to it.
  return DetectionResult(
    setup="Demand Zone Reaction" if direction == "BUY" else "Supply Zone Reaction",
    direction=direction,
    key_level=(low + high) / 2,
    entry_zone=Zone(low, high, "demand" if direction == "BUY" else "supply"),
    current_price=(low + high) / 2,
    confluence=2,
    reasons=["zone reaction"],
    structural_source="supply_demand",
    structural_id=structural_id,
    structural_kind="demand" if direction == "BUY" else "supply",
  )


def test_supply_zone_4113_4116_width_3_is_eligible():
  result = validate_zone_width(
    raw_width=4116.0 - 4113.0,
    merged_width=4116.0 - 4113.0,
    merge_sources=("supply",),
  )
  assert result.eligible
  assert result.rejection_reason is None
  assert result.merged_zone_width == pytest.approx(3.0)


def test_bullish_ob_4084_4088_width_4_is_eligible():
  result = validate_zone_width(
    raw_width=4088.0 - 4084.0,
    merged_width=4088.0 - 4084.0,
    merge_sources=("ob",),
  )
  assert result.eligible
  assert result.rejection_reason is None
  assert result.merged_zone_width == pytest.approx(4.0)


def test_half_price_isolated_zone_is_rejected_as_too_narrow():
  result = validate_zone_width(
    raw_width=0.5,
    merged_width=0.5,
    merge_sources=("key_level",),
  )
  assert not result.eligible
  assert result.rejection_reason == ZONE_TOO_NARROW
  assert result.min_required_width == pytest.approx(3.0)


def test_two_related_narrow_structures_merge_into_a_valid_3_to_6_zone():
  members = [
    ConfluenceMember("kl-1", "sell", 4113.0, 4114.5, "key_level", score=4.0),
    ConfluenceMember("ob-1", "sell", 4114.2, 4116.0, "ob", score=4.0),
  ]
  zones = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.01, source_tf="M5",
  )
  assert len(zones) == 1
  zone = zones[0]
  result = validate_zone_width(
    raw_width=4114.5 - 4113.0,
    merged_width=zone.high - zone.low,
    merge_sources=zone.tags,
  )
  assert result.eligible
  assert 3.0 <= result.merged_zone_width <= 6.0


def test_excessively_wide_normal_zone_is_rejected_as_too_wide():
  result = validate_zone_width(
    raw_width=12.0,
    merged_width=12.0,
    merge_sources=("supply", "ob"),
    is_major=False,
  )
  assert not result.eligible
  assert result.rejection_reason == ZONE_TOO_WIDE


def test_major_h1_zone_gets_a_wider_validated_ceiling():
  # A width that would be rejected as "too wide" for a normal zone must be
  # allowed for a major (H1-sourced) zone, up to the major ceiling.
  normal = validate_zone_width(
    raw_width=8.0, merged_width=8.0, merge_sources=("supply",), is_major=False,
  )
  major = validate_zone_width(
    raw_width=8.0, merged_width=8.0, merge_sources=("supply",), is_major=True,
  )
  assert not normal.eligible
  assert normal.rejection_reason == ZONE_TOO_WIDE
  assert major.eligible


def test_width_is_not_confused_with_atr_or_pip_units():
  # A tiny ATR-derived band must never override the explicit XAU minimum-
  # width contract - width here is raw XAU price distance, period.
  result = validate_zone_width(
    raw_width=0.05,  # ~5 "pips" if this were mistaken for a pip distance
    merged_width=0.05,
    merge_sources=("key_level",),
  )
  assert not result.eligible
  assert result.rejection_reason == ZONE_TOO_NARROW


def test_custom_width_overrides_are_respected():
  result = validate_zone_width(
    raw_width=1.5,
    merged_width=1.5,
    merge_sources=("key_level",),
    min_width=1.0,
    preferred_max_width=2.0,
  )
  assert result.eligible
  assert result.min_required_width == pytest.approx(1.0)
  assert result.max_allowed_width == pytest.approx(2.0)


def test_scanner_gate_keeps_a_too_narrow_zone_as_preference_telemetry(monkeypatch):
  narrow = [_structural_zone_result(4100.0, 4100.5, structural_id="sd-narrow")]

  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": False})
  merged_off = scanner._merge_detection_confluence("XAU", "M5", narrow, atr=2.0)
  assert len(merged_off) == 1

  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": True})
  merged_on = scanner._merge_detection_confluence("XAU", "M5", narrow, atr=2.0)
  # Zone-width quality is preference telemetry — merged zone is retained.
  assert len(merged_on) == 1
  assert merged_on[0].setup == narrow[0].setup
  assert float(merged_on[0].entry_zone.low) == pytest.approx(4100.0)
  assert float(merged_on[0].entry_zone.high) == pytest.approx(4100.5)


def test_key_level_band_is_never_width_dropped_regardless_of_the_gate(
  monkeypatch,
):
  """Recovery mission section 4: a key level is a LEVEL_BAND, not a merged
  structural zone - it must never be rejected only for being narrower than
  XAU_ZONE_MIN_WIDTH_PRICE, whether the scanner-stage width gate is on or
  off. (This is the same fixture/width test_scanner_gate_drops_a_too_
  narrow_zone_only_when_enabled used before the BandKind split - key
  levels no longer belong in that test.)
  """
  narrow = [_key_level_result(4100.0, 4100.5, structural_id="kl-narrow")]

  for gate_enabled in (True, False):
    install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": gate_enabled,})
    merged = scanner._merge_detection_confluence("XAU", "M5", narrow, atr=2.0)
    assert len(merged) == 1


def test_scanner_gate_keeps_a_contract_width_zone_when_enabled(monkeypatch):
  normal = [_key_level_result(4113.0, 4116.0, structural_id="kl-normal")]

  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": True})
  merged = scanner._merge_detection_confluence("XAU", "M5", normal, atr=2.0)

  assert len(merged) == 1
  assert merged[0].confluence_zone_id
