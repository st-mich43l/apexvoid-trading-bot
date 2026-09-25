"""Technique zones keep their OWN origin (never absorbed by an older zone).

Owner-reported 2026-09-21: the bot was blind to the supply behind XAU's
-29 pt drop. The raw generator produced it (origin 13:45, 4349.3-4356.6) but
``merge_zones`` folded it into an older overlapping zone (origin 06:20) whose
history - price accepted above it for two hours - made the whole composite
"invalidated", so no S/D/OB/FVG instance ever existed there.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.analysis import technique_geometry as geometry
from app.analysis.engine import AnalysisSettings, analyze
from app.analysis.math_utils import atr_scalar
from app.analysis.types import Zone
from app.analysis.zones import as_single_zones, mark_mitigation

pytestmark = pytest.mark.no_database

FIXTURE = Path(__file__).parent / "fixtures" / "xau_m5_2026_09_21_supply_reversal.json"


def _m5_frame() -> pd.DataFrame:
  payload = json.loads(FIXTURE.read_text())
  frame = pd.DataFrame(payload["rows"], columns=payload["columns"])
  frame.index = pd.DatetimeIndex(
    pd.to_datetime(frame.pop("t"), unit="s", utc=True), name="time",
  )
  return frame


def _reference_mark_mitigation(zones, df, cutoff=None):
  """The original per-bar loop (pre-numpy), kept as the behavioural oracle."""
  stamped = []
  end = len(df) if cutoff is None else max(0, min(cutoff, len(df)))
  for zone in zones:
    touches = 0
    in_touch = False
    start_from = zone.break_index if zone.break_index is not None else zone.origin_index
    start = max(0, start_from + 1)
    for i in range(start, end):
      row = df.iloc[i]
      touched = float(row["low"]) <= zone.top and float(row["high"]) >= zone.bottom
      if touched and not in_touch:
        touches += 1
      in_touch = touched
    final = max(touches, zone.touches)
    stamped.append((final, zone.mitigated or final > 0))
  return stamped


def test_mark_mitigation_matches_the_reference_loop_on_random_data():
  rng = np.random.default_rng(7)
  closes = 4300 + np.cumsum(rng.normal(0, 2.0, 300))
  df = pd.DataFrame({
    "open": closes + rng.normal(0, 0.5, 300),
    "high": closes + np.abs(rng.normal(1.5, 1.0, 300)),
    "low": closes - np.abs(rng.normal(1.5, 1.0, 300)),
    "close": closes,
  })
  zones = []
  for _ in range(60):
    bottom = float(rng.uniform(4250, 4350))
    origin = int(rng.integers(0, 290))
    zones.append(Zone(
      bottom=bottom,
      top=bottom + float(rng.uniform(0.5, 12)),
      side="supply",
      origin_index=origin,
      touches=int(rng.integers(0, 2)),
      break_index=(origin + int(rng.integers(0, 5))) if rng.random() < 0.5 else None,
    ))
  for cutoff in (None, 299, 120, 0, 1000):
    expected = _reference_mark_mitigation(zones, df, cutoff)
    actual = [(z.touches, z.mitigated) for z in mark_mitigation(zones, df, cutoff)]
    assert actual == expected, f"cutoff={cutoff}"


def test_as_single_zones_keeps_each_zones_own_origin_and_sources():
  old = Zone(4342.6, 4355.4, "supply", origin_index=10, source="supply_demand")
  fresh = Zone(4349.3, 4356.6, "supply", origin_index=300, source="order_block")
  out = as_single_zones([old, fresh])
  assert [z.origin_index for z in out] == [10, 300]
  assert out[0].sources == ["supply_demand"] and out[1].sources == ["order_block"]


def test_fresh_supply_is_not_absorbed_into_an_older_overlapping_zone():
  analysis = analyze({"M5": _m5_frame()}, AnalysisSettings(), symbol="XAU").per_tf["M5"]
  fresh_origin = pd.Timestamp("2026-09-21 13:45", tz="UTC")

  fresh = [
    z for z in analysis.technique_zones
    if z.side == "supply" and z.source == "supply_demand"
    and z.created_ts == fresh_origin
  ]
  assert fresh, "the 13:45 supply behind the -29 pt impulse must exist on its own"
  zone = fresh[0]
  assert zone.low == pytest.approx(4349.3, abs=0.2)
  assert zone.high == pytest.approx(4356.6, abs=0.2)

  # The map-merge view folds it into an older zone (origin far earlier)...
  absorbing = [
    z for z in analysis.supply_demand_zones
    if z.side == "supply" and z.low <= zone.high and z.high >= zone.low
  ]
  assert absorbing
  assert min(z.created_ts for z in absorbing) < fresh_origin

  # ...whose history is what used to condemn it; on its own it still holds.
  atr = atr_scalar(analysis.atr)
  settings = geometry.TechniqueGeometrySettings(pip_size=0.1)
  assert geometry.not_invalidated(
    side="sell",
    low=zone.low,
    high=zone.high,
    df=analysis.df,
    origin_index=int(zone.origin_index),
    atr=atr,
    settings=settings,
  )
  assert not geometry.zone_is_spent(
    mitigated=bool(zone.mitigated),
    touches=int(zone.touches),
    side="sell",
    low=zone.low,
    high=zone.high,
    df=analysis.df,
    origin_index=int(zone.origin_index),
    atr=atr,
    settings=settings,
  )


def test_hand_built_analysis_without_technique_zones_falls_back_to_merged_views():
  from dataclasses import replace

  from app.analysis.engine import _attach_technique_instances

  analysis = analyze({"M5": _m5_frame()}, AnalysisSettings(), symbol="XAU").per_tf["M5"]
  bare = replace(analysis, technique_zones=[], technique_instances=[])
  out = _attach_technique_instances({"M5": bare}, AnalysisSettings())["M5"]
  assert out.technique_zones == []
  assert isinstance(out.technique_instances, list)
