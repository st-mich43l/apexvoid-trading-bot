from dataclasses import replace
from datetime import datetime, timezone
import random
from types import SimpleNamespace

import pandas as pd

from app.market_map import (
  MapEntry,
  MarketMap,
  ScalpRail,
  _merge_display_entries,
  build_map,
  map_materially_changed,
  market_map_from_payload,
  market_map_payload,
  render_market_map,
)
from app.pa_types import DealingRange, Level, SessionLevel, Zone
from app.regime import BoxBreak
from app.scalp_ranges import ScalpBarrier
from app.trendlines import Trendline


def _cfg(**overrides):
  values = {
    "map_max_per_side": 4,
    "map_major_score": 12.0,
    "map_max_touches": 2,
    "map_min_zone_score": 6.0,
    "map_min_level_touches": 4,
    "map_max_distance_atr": 15.0,
    "map_band_max_atr": 2.0,
    "map_min_per_side": 2,
    "map_fallback_radius": 30.0,
    "map_scalp_radius": 15.0,
    "map_scalp_tol": 1.0,
    "map_scalp_max": 5,
    "map_scalp_fractal_n": 1,
    "round_step": 5.0,
    "scanner_exec_tf": "M5",
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
  scalp_barriers=None,
  box_break=None,
  regime=None,
  df=None,
  structure="range",
  momentum="neutral",
):
  if df is None:
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
    atr=pd.Series([2.0] * len(df), index=df.index),
    zones=zones or [],
    key_levels=levels or [],
    session_levels=sessions or [],
    trendlines=trendlines or [],
    scalp_barriers=scalp_barriers or [],
    box_break=box_break,
    regime=regime,
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
  zones = [Zone(4034.5, 4036, "demand", source="supply_demand", score=8)]

  market_map = build_map(
    _ctx({"M5": _item(zones, sessions=sessions, trendlines=lines)}),
    4041,
    _cfg(),
  )

  assert any(entry.tier == "level" and "PDL" in entry.tags for entry in market_map.buys)
  assert any("TL support ×3" in entry.tags for entry in market_map.buys)
  assert any(
    entry.tier == "level" and {"PDH", "swept"} <= set(entry.tags)
    for entry in market_map.sells
  )
  assert all("TL resistance ×3" not in entry.tags for entry in market_map.entries)


def test_overlapping_zones_merge_under_width_cap_and_keep_higher_tier():
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

  market_map = build_map(
    _ctx({"M5": _item(zones)}),
    4041,
    _cfg(map_min_per_side=0),
  )

  assert len(market_map.sells) == 1
  merged = market_map.sells[0]
  assert merged.tier == "major"
  assert {"supply", "flip", "breakout-retest"} <= set(merged.tags)
  assert (merged.label_lo, merged.label_hi) == (4063, 4067)


def test_map_drops_weak_and_far_zones():
  zones = [
    Zone(4045, 4048, "supply", source="supply_demand", score=8),
    Zone(4050, 4052, "supply", source="supply_demand", score=5),
    Zone(4190, 4194, "supply", source="supply_demand", score=20),
  ]

  market_map = build_map(
    _ctx({"M5": _item(zones)}),
    4033,
    _cfg(map_min_per_side=0),
  )

  assert [(entry.lo, entry.hi) for entry in market_map.sells] == [(4045, 4048)]


def test_distance_gate_uses_current_atr_not_old_window_median():
  item = _item([
    Zone(4075, 4078, "supply", source="supply_demand", score=10),
  ])
  item.atr = pd.Series([10.0, 10.0, 2.0])

  market_map = build_map(
    _ctx({"M5": item}),
    4033,
    _cfg(map_min_per_side=0),
  )

  assert market_map.sells == []


def test_transitive_overlap_does_not_create_an_oversized_zone():
  zones = [
    Zone(4040, 4050, "supply", source="supply_demand", score=8),
    Zone(4048, 4060, "supply", source="order_block", score=9),
    Zone(4058, 4070, "supply", source="flip_zone", score=10),
  ]

  market_map = build_map(
    _ctx({"M5": _item(zones)}),
    4033,
    _cfg(map_min_per_side=0),
  )

  assert len(market_map.sells) == 3
  assert max(entry.hi - entry.lo for entry in market_map.sells) <= 5
  assert all((entry.lo, entry.hi) != (4040, 4070) for entry in market_map.sells)


def test_key_level_adds_confluence_without_widening_entry_zone():
  zones = [Zone(4048, 4051, "supply", source="order_block", score=9)]
  levels = [Level(4050, "reaction", touches=8, band=10, strength=8)]

  market_map = build_map(
    _ctx({"M5": _item(zones, levels=levels)}),
    4033,
    _cfg(),
  )

  entry = market_map.sells[0]
  assert (entry.lo, entry.hi) == (4048, 4051)
  assert "resistance ×8" in entry.tags


def test_cap_selects_major_then_score_before_proximity():
  zones = [
    Zone(
      4000 + index * 5,
      4002 + index * 5,
      "demand",
      source="supply_demand",
      score=score,
      score_reasons=["HTF zone"] if index == 0 else [],
    )
    for index, score in enumerate((13, 11, 10, 9, 8, 7))
  ]

  market_map = build_map(
    _ctx({"M5": _item(zones)}, bias="up"),
    4041,
    _cfg(map_max_per_side=4, map_max_distance_atr=25),
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
  assert "ZONE · OB · fresh" in text
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


def test_legacy_scalp_arrows_restore_as_actions():
  market_map = MarketMap(
    [],
    4000,
    None,
    None,
    None,
    "range",
    None,
    [
      ScalpRail(4005, 4004, 4006, 4005, "↑", ["micro ×3"], 3),
      ScalpRail(3995, 3994, 3996, 3995, "↓", ["micro ×3"], 3),
    ],
  )

  restored = market_map_from_payload(market_map_payload(market_map))

  assert [rail.direction for rail in restored.rails] == ["SELL", "BUY"]


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

  market_map = build_map(
    _ctx({"M5": m5, "M30": m30}),
    4041,
    _cfg(map_max_distance_atr=20),
  )

  assert len(market_map.buys) >= 2
  assert len(market_map.sells) >= 2
  assert any("breakout-retest" in entry.tags for entry in market_map.sells)
  assert any(entry.tier == "major" for entry in market_map.buys)


def test_render_compacts_production_style_tag_inflation():
  entry = MapEntry(
    "sell",
    4048,
    4051,
    4048,
    4051,
    "major",
    [
      "resistance ×4",
      "resistance ×17",
      "OB",
      "FVG",
      "flip",
      "supply",
      "PDH",
      "HTF M30",
    ],
    21,
  )
  market_map = MarketMap([entry], 4033, 4032, 4028, 4036, "down", "M30")

  text = render_market_map(
    market_map,
    "XAU",
    datetime(2026, 7, 16, 11, 8, tzinfo=timezone.utc),
    _cfg(),
  )

  assert "MAJOR · OB · flip · supply · FVG" in text
  assert "resistance ×17" not in text
  assert "HTF M30" not in text


def test_screenshot_replay_drops_oversized_container_and_keeps_core():
  zones = [
    Zone(4016, 4045, "supply", source="supply_demand", score=18),
    Zone(4024, 4028, "supply", source="order_block", score=9),
  ]

  market_map = build_map(
    _ctx({"M5": _item(zones)}),
    3992,
    _cfg(map_min_per_side=0, map_max_distance_atr=20),
  )

  assert [(entry.lo, entry.hi) for entry in market_map.sells] == [(4024, 4028)]
  assert all(entry.hi - entry.lo <= 5 for entry in market_map.sells)


def test_screenshot_replay_resolves_partial_display_overlap():
  zones = [
    Zone(4007, 4009, "supply", source="order_block", score=10),
    Zone(4008, 4015, "supply", source="supply_demand", score=8),
  ]

  market_map = build_map(
    _ctx({"M5": _item(zones)}),
    3992,
    _cfg(map_min_per_side=0),
  )
  ordered = sorted(market_map.sells, key=lambda entry: entry.lo)

  assert len(ordered) in {1, 2}
  assert all(
    first.hi <= second.lo and first.label_hi <= second.label_lo
    for first, second in zip(ordered, ordered[1:])
  )


def test_screenshot_replay_fills_empty_buy_side_from_fallback_ladder():
  ts = pd.Timestamp("2026-07-16T06:00:00Z")
  item = _item(
    [
      Zone(
        3988,
        3990,
        "demand",
        source="supply_demand",
        score=8,
        touches=2,
      ),
    ],
    sessions=[SessionLevel("ASIA_L", 3985, ts, True, ts)],
  )

  market_map = build_map(_ctx({"M5": item}), 3992, _cfg())
  tags = {tag.casefold() for entry in market_map.buys for tag in entry.tags}

  assert len(market_map.buys) >= 2
  assert {"revisit", "swept"} <= tags
  assert all(entry.tier == "level" for entry in market_map.buys)


def test_screenshot_replay_deduplicates_tags_case_insensitively():
  entry = MapEntry(
    "sell",
    4025,
    4026,
    4025,
    4026,
    "zone",
    ["key 4025.71 x12", "KEY 4025.71 X12"],
    12,
  )
  market_map = MarketMap([entry], 3992, None, None, None, "range", None)

  text = render_market_map(
    market_map,
    "XAU",
    datetime(2026, 7, 17, 5, 0, tzinfo=timezone.utc),
    _cfg(),
  )

  assert text.casefold().count("key 4025.71 x12") == 1


def test_display_merge_property_is_capped_non_overlapping_and_deterministic():
  rng = random.Random(20260717)
  for _ in range(100):
    candidates = []
    for index in range(rng.randint(1, 40)):
      lo = rng.uniform(3980, 4040)
      hi = lo + rng.uniform(0.05, 30)
      candidates.append(MapEntry(
        "sell",
        lo,
        hi,
        int(lo),
        int(hi) + 1,
        rng.choice(["level", "zone", "major"]),
        [f"source {index % 5}"],
        rng.uniform(1, 20),
      ))

    first = _merge_display_entries(candidates, 5.0)
    second = _merge_display_entries(list(reversed(candidates)), 5.0)
    ordered = sorted(first, key=lambda entry: entry.lo)

    assert first == second
    assert all(entry.hi - entry.lo <= 5.0 + 1e-9 for entry in ordered)
    assert all(entry.label_hi - entry.label_lo <= 5 for entry in ordered)
    assert all(
      left.hi <= right.lo and left.label_hi <= right.label_lo
      for left, right in zip(ordered, ordered[1:])
    )


def test_scalp_rails_are_near_sorted_deduplicated_and_rendered():
  df = pd.DataFrame(
    {
      "open": [3991, 3992, 3994, 3992, 3989, 3991, 3992],
      "high": [3993, 3994, 3997, 3994, 3991, 3993, 3994],
      "low": [3989, 3990, 3992, 3990, 3987, 3989, 3990],
      "close": [3992, 3993, 3993, 3991, 3990, 3992, 3993],
      "volume": [100] * 7,
    },
    index=pd.date_range("2026-07-17", periods=7, freq="5min", tz="UTC"),
  )
  regime = SimpleNamespace(range_low=3988, range_high=3996)
  item = _item(
    sessions=[SessionLevel("ASIA_L", 3988, df.index[0], False)],
    regime=regime,
    df=df,
  )
  ctx = _ctx({"M5": item})
  ctx.regime = regime

  market_map = build_map(ctx, 3992, _cfg())
  distances = [abs(rail.price - market_map.price) for rail in market_map.rails]

  assert 1 <= len(market_map.rails) <= 5
  assert distances == sorted(distances)
  assert all(abs(rail.price - market_map.price) <= 15 for rail in market_map.rails)
  assert all(
    rail.direction == ("SELL" if rail.price > market_map.price else "BUY")
    for rail in market_map.rails
  )
  assert any(any(tag.startswith("micro") for tag in rail.tags) for rail in market_map.rails)
  assert any(any(tag.startswith("box-") for tag in rail.tags) for rail in market_map.rails)
  assert any("round" in rail.tags for rail in market_map.rails)
  text = render_market_map(
    market_map,
    "XAU",
    datetime(2026, 7, 17, 5, 0, tzinfo=timezone.utc),
    _cfg(),
  )
  assert "\nSCALP\n" in text
  assert {rail.direction for rail in market_map.rails} == {"BUY", "SELL"}
  assert "↑" not in text and "↓" not in text


def test_scalp_rails_reuse_detector_barriers():
  barrier = ScalpBarrier(
    side="resistance",
    level=3998.25,
    low=3998.0,
    high=3998.5,
    touches=4,
    wick_rejections=3,
    accepted_closes=0,
    last_touch_index=10,
    tags=["micro ×4", "wick ×3", "session LONDON_H"],
    score=11.5,
  )
  item = _item(scalp_barriers=[barrier])

  market_map = build_map(_ctx({"M5": item}), 3992, _cfg())
  rail = next(rail for rail in market_map.rails if rail.price == 3998.25)

  assert set(rail.tags) == set(barrier.tags)
  assert rail.score == barrier.score


def test_major_requires_htf_plus_fresh_or_score_and_pdh_only_stays_zone():
  ts = pd.Timestamp("2026-07-16T21:00:00Z")
  zones = [
    Zone(
      4020,
      4022,
      "demand",
      source="supply_demand",
      score=8,
      score_reasons=["HTF zone"],
    ),
    Zone(4030, 4032, "demand", source="order_block", score=14),
  ]
  item = _item(zones, sessions=[SessionLevel("PDL", 4031, ts, False)])

  market_map = build_map(
    _ctx({"M5": item}),
    4040,
    _cfg(map_min_per_side=0),
  )
  htf = next(entry for entry in market_map.buys if "HTF" in entry.tags)
  pdh_only = next(entry for entry in market_map.buys if "PDL" in entry.tags)

  assert htf.tier == "major"
  assert pdh_only.tier == "zone"
