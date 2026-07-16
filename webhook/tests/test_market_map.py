from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.market_map import (
  MapEntry,
  MarketMap,
  build_map,
  map_materially_changed,
  market_map_from_payload,
  market_map_payload,
  render_market_map,
)
from app.pa_types import DealingRange, Level, SessionLevel, Zone
from app.regime import BoxBreak
from app.trendlines import Trendline


def _cfg(**overrides):
  values = {
    "map_max_per_side": 4,
    "map_major_score": 12.0,
    "map_max_touches": 2,
    "map_change_min": 1.0,
    "proximal_band_atr": 0.5,
    "session_asia_start": 22,
    "session_london_start": 7,
    "session_ny_start": 13,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def _item(
  zones=None,
  *,
  levels=None,
  sessions=None,
  trendlines=None,
  box_break=None,
  structure="range",
  momentum="neutral",
):
  df = pd.DataFrame(
    {
      "open": [4040.0],
      "high": [4042.0],
      "low": [4039.0],
      "close": [4041.0],
      "volume": [100],
    },
    index=pd.date_range("2026-07-16", periods=1, freq="5min", tz="UTC"),
  )
  return SimpleNamespace(
    df=df,
    atr=pd.Series([2.0], index=df.index),
    zones=zones or [],
    key_levels=levels or [],
    session_levels=sessions or [],
    trendlines=trendlines or [],
    box_break=box_break,
    structure=structure,
    momentum=momentum,
  )


def _ctx(per_tf, *, bias="down"):
  return SimpleNamespace(
    per_tf=per_tf,
    htf_bias=bias,
    dealing_range=DealingRange(4062, 4032, 4047, 0.3, "discount"),
    regime=SimpleNamespace(range_low=4032, range_high=4062),
  )


def test_map_is_both_sided_major_tags_ob_and_drops_spent_zone():
  zones = [
    Zone(4025.31, 4027.8, "demand", source="order_block", score=9),
    Zone(
      4018.2,
      4021.1,
      "demand",
      source="supply_demand",
      score=13,
      score_reasons=["HTF zone"],
    ),
    Zone(4063.2, 4065.8, "supply", source="supply_demand", score=8),
    Zone(4049.2, 4052.1, "supply", source="flip_zone", score=10),
    Zone(4008, 4010, "demand", source="supply_demand", score=14, touches=2),
  ]

  market_map = build_map(_ctx({"M5": _item(zones)}), 4041, _cfg())

  assert len(market_map.buys) == 2
  assert len(market_map.sells) == 2
  assert any(entry.tier == "major" for entry in market_map.buys)
  assert any("OB" in entry.tags for entry in market_map.buys)
  assert any("breakout-retest" in entry.tags for entry in market_map.sells)
  assert all(entry.lo != 4008 for entry in market_map.entries)


def test_human_rounding_and_thin_level_integer_pair():
  zones = [Zone(4025.31, 4027.8, "demand", source="supply_demand", score=8)]
  levels = [Level(4035.5, "reaction", touches=4, band=0.2, strength=4)]

  market_map = build_map(
    _ctx({"M5": _item(zones, levels=levels)}, bias="up"),
    4041,
    _cfg(),
  )

  zone = next(entry for entry in market_map.buys if "demand" in entry.tags)
  level = next(entry for entry in market_map.buys if "support ×4" in entry.tags)
  assert (zone.label_lo, zone.label_hi) == (4025, 4028)
  assert (level.label_lo, level.label_hi) == (4035, 4036)


def test_map_keeps_unswept_sessions_and_unbroken_trendlines_only():
  ts = pd.Timestamp("2026-07-15T21:00:00Z")
  sessions = [
    SessionLevel("PDL", 4030, ts, False),
    SessionLevel("PDH", 4060, ts, True, ts),
  ]
  lines = [
    Trendline("support", (0, 1, 2), 0.0, 4035, 3, False, None),
    Trendline("resistance", (0, 1, 2), 0.0, 4065, 3, True, 3),
  ]

  market_map = build_map(
    _ctx({"M5": _item(sessions=sessions, trendlines=lines)}),
    4041,
    _cfg(),
  )

  assert any(
    entry.tier == "major" and "PDL" in entry.tags
    for entry in market_map.buys
  )
  assert any("TL support ×3" in entry.tags for entry in market_map.buys)
  assert all("PDH" not in entry.tags for entry in market_map.entries)
  assert all("TL resistance ×3" not in entry.tags for entry in market_map.entries)


def test_overlapping_rounded_bands_merge_tags_and_higher_tier():
  zones = [
    Zone(4063.2, 4065.1, "supply", source="supply_demand", score=8),
    Zone(
      4065.05,
      4066.2,
      "supply",
      source="flip_zone",
      score=13,
      score_reasons=["HTF zone"],
    ),
  ]

  market_map = build_map(_ctx({"M5": _item(zones)}), 4041, _cfg())

  assert len(market_map.sells) == 1
  merged = market_map.sells[0]
  assert merged.tier == "major"
  assert {"supply", "flip", "breakout-retest"} <= set(merged.tags)
  assert (merged.label_lo, merged.label_hi) == (4063, 4067)


def test_cap_selects_major_then_score_before_proximity():
  zones = [
    Zone(4000 + index * 5, 4002 + index * 5, "demand", source="supply_demand", score=score)
    for index, score in enumerate((13, 11, 10, 9, 8, 7))
  ]

  market_map = build_map(
    _ctx({"M5": _item(zones)}, bias="up"),
    4041,
    _cfg(map_max_per_side=4),
  )

  assert len(market_map.buys) == 4
  assert market_map.buys[0].tier == "major"
  assert [entry.score for entry in market_map.buys] == [13, 11, 10, 9]


def test_render_payload_and_material_change_are_deterministic():
  entry = MapEntry("buy", 4025.31, 4027.8, 4025, 4028, "zone", ["OB", "fresh"], 9)
  market_map = MarketMap([entry], 4041, 4047, 4032, 4062, "down", "M30")

  text = render_market_map(
    market_map,
    "XAU",
    datetime(2026, 7, 16, 8, 46, tzinfo=timezone.utc),
    _cfg(),
  )
  restored = market_map_from_payload(market_map_payload(market_map))

  assert "<pre>" in text
  assert "XAU Market Map" in text
  assert "4,025–4,028" in text
  assert restored == market_map
  assert not map_materially_changed(market_map, restored, 1.0)
  moved_small = replace(
    market_map,
    entries=[replace(entry, lo=4025.8, hi=4028.2)],
  )
  moved_large = replace(
    market_map,
    entries=[replace(entry, lo=4026.31, hi=4028.8)],
  )
  assert not map_materially_changed(market_map, moved_small, 1.0)
  assert map_materially_changed(market_map, moved_large, 1.0)


def test_operator_example_replay_builds_tiered_board():
  m5 = _item(
    [
      Zone(4025, 4028, "demand", source="order_block", score=9),
      Zone(4035, 4038, "demand", source="supply_demand", score=8),
      Zone(4049, 4053, "supply", source="flip_zone", score=10),
      Zone(4063, 4066, "supply", source="supply_demand", score=9),
      Zone(4074, 4077, "supply", source="supply_demand", score=12),
    ],
    box_break=BoxBreak(4050, 4032, "up", 0, False, "2 closes"),
    structure="down",
  )
  m30 = _item([
    Zone(
      4018,
      4021,
      "demand",
      source="supply_demand",
      score=13,
      score_reasons=["HTF zone"],
    ),
  ], structure="down")

  market_map = build_map(_ctx({"M5": m5, "M30": m30}), 4041, _cfg())

  assert len(market_map.buys) >= 2
  assert len(market_map.sells) >= 2
  assert any("breakout-retest" in entry.tags for entry in market_map.sells)
  assert any(entry.tier == "major" for entry in market_map.buys)
