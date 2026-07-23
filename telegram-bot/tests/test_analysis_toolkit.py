import pandas as pd
import pytest

from app.analysis.engine import (
  AnalysisSettings,
  Regime,
  TimeframeAnalysis,
  _apply_mtf_zone_scores,
  _htf_bias,
  analyze,
  regime,
)
from app.analysis.dealing_range import dealing_range
from app.analysis.levels import key_levels
from app.analysis.liquidity import liquidity_grabs, liquidity_pools
from app.analysis.momentum import momentum
from app.analysis.types import Break, DealingRange, Leg, Level, Pool, SessionLevel, Swing, Zone
from app.analysis.session_liquidity import previous_week_levels, session_levels
from app.analysis.structure import market_structure, structure_breaks
from app.analysis.swings import find_swings
from app.analysis.zones import (
  ZONE_RECONCILED_TAG_PREFIX,
  breaker_blocks,
  mark_mitigation,
  merge_zones,
  order_blocks,
  reconcile_opposing,
  score_zones,
)


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  ).assign(volume=100)


def _df_with_index(
  index: pd.DatetimeIndex,
  rows: list[tuple[float, float, float, float]],
) -> pd.DataFrame:
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  ).assign(volume=100)


def test_hybrid_swings_alternate_and_filter_subthreshold_wiggle():
  df = _df([
    (100, 101, 99, 100),
    (100, 105, 100, 104),
    (104, 104, 98, 99),
    (99, 108, 103, 107),
    (107, 107.2, 100, 102),
    (102, 111, 104, 110),
    (110, 110.5, 102, 105),
    (105, 112, 105, 111),
  ])
  atr = pd.Series([2.0] * len(df), index=df.index)

  pivots = find_swings(df, fractal_n=1, zigzag_atr_mult=0.5, atr=atr)

  assert [s.kind for s in pivots] == ["high", "low", "high", "low", "high", "low"]
  assert [s.label for s in pivots if s.kind == "high"][-2:] == ["HH", "HH"]

  noisy = _df([
    (100, 101, 99, 100),
    (104.9, 105, 104.9, 105),
    (104.6, 104.8, 104.2, 104.6),
    (104.5, 104.7, 104.5, 104.6),
  ])
  noisy_atr = pd.Series([2.0] * len(noisy), index=noisy.index)

  assert [
    s.kind for s in find_swings(noisy, fractal_n=1, zigzag_atr_mult=1.0, atr=noisy_atr)
  ] == ["high"]


def test_structure_breaks_classify_bos_and_choch_in_uptrend():
  index = pd.date_range("2026-07-10", periods=8, freq="5min", tz="UTC")
  swings = [
    Swing(1, "high", 105, "HH", index[1]),
    Swing(2, "low", 100, "HL", index[2]),
    Swing(3, "high", 110, "HH", index[3]),
    Swing(4, "low", 104, "HL", index[4]),
  ]
  up_df = _df([
    (100, 101, 99, 100),
    (104, 106, 103, 105),
    (101, 102, 99, 100),
    (108, 111, 107, 110),
    (105, 106, 103, 104),
    (110, 112, 109, 111),
  ])
  down_df = _df([
    (100, 101, 99, 100),
    (104, 106, 103, 105),
    (101, 102, 99, 100),
    (108, 111, 107, 110),
    (105, 106, 103, 104),
    (104, 105, 99, 103),
  ])

  assert market_structure(swings) == "up"
  assert any(
    item.kind == "BOS" and item.direction == "up"
    for item in structure_breaks(swings, up_df)
  )
  assert any(
    item.kind == "CHoCH" and item.direction == "down"
    for item in structure_breaks(swings, down_df)
  )


def test_order_block_created_by_bos_and_later_mitigated():
  df = _df([
    (100, 102, 99, 101),
    (101, 102, 98, 99),
    (99, 105, 99, 104),
    (104, 111, 103, 110),
    (110, 112, 100, 101),
  ])
  zones = order_blocks(
    df,
    [Leg(2, 3, "up", 11)],
    [Break("BOS", "up", 108, 3, df.index[3])],
  )

  assert len(zones) == 1
  zone = zones[0]
  assert zone.side == "demand"
  assert zone.bottom == 99
  assert zone.top == 101
  assert zone.break_kind == "BOS"

  stamped = mark_mitigation(zones, df)[0]
  assert stamped.mitigated is True
  assert stamped.touches == 1


def test_mark_mitigation_respects_asof_cutoff():
  df = _df([
    (100, 104, 99, 103),
    (103, 105, 102, 104),
    (104, 106, 100, 105),
  ])
  zone = Zone(100, 101, "demand", origin_index=0, source="order_block")

  as_of_previous = mark_mitigation([zone], df, cutoff=len(df) - 1)[0]
  full_history = mark_mitigation([zone], df)[0]

  assert as_of_previous.touches == 0
  assert as_of_previous.mitigated is False
  assert full_history.touches == 1
  assert full_history.mitigated is True


def test_merge_zones_combines_overlapping_same_side_sources():
  merged = merge_zones([
    Zone(
      100,
      104,
      "demand",
      origin_index=2,
      source="order_block",
      break_kind="BOS",
      break_index=4,
    ),
    Zone(102, 105, "demand", origin_index=3, source="bullish_fvg"),
    Zone(110, 112, "supply", origin_index=1, source="supply_demand"),
  ])

  demand = [zone for zone in merged if zone.side == "demand"]
  assert len(demand) == 1
  assert demand[0].low == 100
  assert demand[0].high == 105
  assert demand[0].sources == ["order_block", "bullish_fvg"]
  assert demand[0].source == "order_block"
  assert demand[0].break_kind == "BOS"
  assert [zone.side for zone in merged].count("supply") == 1


def test_merge_zones_keeps_chain_separate_when_band_would_exceed_cap():
  merged = merge_zones(
    [
      Zone(100, 104, "demand", source="supply_demand"),
      Zone(102, 106, "demand", source="bullish_fvg"),
      Zone(104, 108, "demand", source="order_block", break_kind="BOS"),
    ],
    min_overlap=0.5,
    max_width=6,
  )

  assert len([zone for zone in merged if zone.side == "demand"]) == 2
  assert max(zone.high - zone.low for zone in merged) <= 6


def test_reconcile_opposing_trims_lower_scored_demand_side():
  # 23 Jul 2026 incident numbers: published SELL 4,116-4,127 (score 8) and
  # BUY 4,112-4,122 (score 5) overlapped 4,116-4,122.
  supply = Zone(4116, 4127, "supply", origin_index=1, score=8.0)
  demand = Zone(4112, 4122, "demand", origin_index=2, score=5.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  result_supply = next(zone for zone in result if zone.side == "supply")
  result_demand = next(zone for zone in result if zone.side == "demand")
  assert (result_supply.low, result_supply.high) == (4116, 4127)
  assert (result_demand.low, result_demand.high) == (4112, 4116)
  assert any(
    tag.startswith(ZONE_RECONCILED_TAG_PREFIX) for tag in result_demand.score_reasons
  )
  assert not any(
    tag.startswith(ZONE_RECONCILED_TAG_PREFIX) for tag in result_supply.score_reasons
  )


def test_reconcile_opposing_trims_lower_scored_supply_side():
  supply = Zone(4116, 4127, "supply", origin_index=1, score=5.0)
  demand = Zone(4112, 4122, "demand", origin_index=2, score=8.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  result_supply = next(zone for zone in result if zone.side == "supply")
  result_demand = next(zone for zone in result if zone.side == "demand")
  assert (result_supply.low, result_supply.high) == (4122, 4127)
  assert (result_demand.low, result_demand.high) == (4112, 4122)
  assert any(
    tag.startswith(ZONE_RECONCILED_TAG_PREFIX) for tag in result_supply.score_reasons
  )


def test_reconcile_opposing_drops_remainder_narrower_than_min_width():
  # High overlap ratio (~0.91, well above the 0.5 threshold) so this tests
  # the min_width drop specifically, not the overlap-ratio gate below.
  supply = Zone(4116, 4127, "supply", origin_index=1, score=8.0)
  demand = Zone(4115, 4126, "demand", origin_index=2, score=5.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  assert [zone.side for zone in result] == ["supply"]
  assert (result[0].low, result[0].high) == (4116, 4127)


def test_reconcile_opposing_leaves_marginal_overlap_untouched():
  # 0.5-wide overlap on a 4.5-wide zone -> ratio ~0.11, well below the 0.5
  # threshold: partial overlap between adjacent zones is normal market
  # structure, not a contradiction (22 Jul 2026 regression).
  supply = Zone(4124, 4130, "supply", origin_index=1, score=8.0)
  demand = Zone(4120, 4124.5, "demand", origin_index=2, score=5.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  assert supply in result
  assert demand in result


def test_reconcile_opposing_full_containment_reconciles():
  # A zone entirely inside an opposing zone normalises to ratio 1.0
  # (_overlap_ratio divides by the narrower zone) - full containment is a
  # genuine conflict and must still reconcile even under the new threshold.
  supply = Zone(4120, 4122, "supply", origin_index=1, score=10.0)
  demand = Zone(4110, 4130, "demand", origin_index=2, score=5.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  demand_zones = [zone for zone in result if zone.side == "demand"]
  assert len(demand_zones) == 1
  assert (demand_zones[0].low, demand_zones[0].high) != (4110, 4130)


def test_reconcile_opposing_trims_a_zone_at_most_once_per_pass():
  # One demand zone overlaps three supply zones above threshold. Without
  # the single-trim cap this cascades: each pass re-trims the same
  # (shrinking) demand zone against the next supply zone until it clears
  # min_width and is dropped entirely (the 22 Jul regression). With the
  # cap, the demand zone is trimmed by whichever supply zone it compares
  # against first and then exhausted as a trim target - it survives if
  # that single remainder clears min_width.
  demand = Zone(4100, 4140, "demand", origin_index=1, score=1.0)
  supply_a = Zone(4095, 4108, "supply", origin_index=2, score=9.0)
  supply_b = Zone(4130, 4145, "supply", origin_index=3, score=9.0)
  supply_c = Zone(4108, 4132, "supply", origin_index=4, score=9.0)

  result = reconcile_opposing(
    [demand, supply_a, supply_b, supply_c], min_width=2.0,
  )

  demand_zones = [zone for zone in result if zone.side == "demand"]
  assert len(demand_zones) == 1, (
    "demand must survive as exactly one zone, not be trimmed away by "
    "repeated comparisons against multiple opposing zones"
  )
  original_width = 4140 - 4100
  survivor_width = demand_zones[0].high - demand_zones[0].low
  assert survivor_width < original_width
  assert any(
    tag.startswith(ZONE_RECONCILED_TAG_PREFIX)
    for tag in demand_zones[0].score_reasons
  )


def test_reconcile_opposing_circuit_breaker_fails_open():
  # A synthetic map where every pair overlaps heavily - far more than the
  # 20% default max_fraction - must discard the whole reconciliation pass
  # and return the input completely unchanged, rather than let a cascade
  # (or even a series of well-formed single trims) strip most of the map.
  zones = []
  for i in range(6):
    base = 4100 + i * 20
    zones.append(Zone(base, base + 12, "supply", origin_index=2 * i, score=8.0))
    zones.append(
      Zone(base + 1, base + 11, "demand", origin_index=2 * i + 1, score=5.0),
    )

  stats: dict = {}
  result = reconcile_opposing(zones, min_width=2.0, stats=stats)

  key = lambda zone: (zone.side, zone.low, zone.high, zone.origin_index)
  assert sorted(result, key=key) == sorted(zones, key=key)
  assert stats["aborted"] is True
  assert stats["dropped"] == 0
  assert stats["trimmed"] == 0
  assert stats["output"] == len(zones)


def test_reconcile_opposing_realistic_fvg_density_retains_most_zones():
  """Regression guard: dense M5-style FVG output must not get stripped by
  reconciliation. This is the test that would have caught the 22 Jul
  regression before merge: the pre-fix code had a zero-overlap-ratio test
  plus an uncapped cascade and no circuit breaker at all, so on a fixture
  this dense it would have driven retention toward zero with no possible
  fail-open outcome. Real production zone maps mix several construction
  sources (supply/demand, order blocks, FVGs) merged together and rarely
  run this hot; a pure-FVG synthetic fixture like this one is a worst case,
  so either outcome below is healthy - the property that must always hold
  is that the map is never silently gutted the way it was in the incident.
  """
  from app.analysis.zones import fvg, merge_zones, score_zones

  rows = []
  price = 4100.0
  for i in range(150):
    direction = 1 if i % 6 < 4 else -1
    step = direction * (1.2 + (i % 3) * 0.4)
    open_ = price
    close = price + step
    high = max(open_, close) + 0.3
    low = min(open_, close) - 0.3
    rows.append((open_, high, low, close))
    price = close
  df = _df(rows)

  zones = score_zones(merge_zones(fvg(df)), [], [], 5.0)
  assert len(zones) > 10, "fixture must actually produce a dense FVG map"

  stats: dict = {}
  reconciled = reconcile_opposing(zones, min_width=2.0, stats=stats)

  retained_fraction = len(reconciled) / len(zones)
  assert stats["aborted"] or retained_fraction >= 0.8, (
    f"reconciliation retained only {retained_fraction:.0%} of {len(zones)} "
    "zones without failing open - expected either >=80% retention or the "
    "circuit breaker to discard the pass and leave the map unchanged"
  )
  if stats["aborted"]:
    assert reconciled == zones


def test_analyze_skips_reconcile_opposing_when_disabled_by_settings(monkeypatch):
  import app.analysis.engine as engine_module

  calls: list[int] = []
  original = engine_module.reconcile_opposing

  def _spy(*args, **kwargs):
    calls.append(1)
    return original(*args, **kwargs)

  monkeypatch.setattr(engine_module, "reconcile_opposing", _spy)

  m5 = _df([
    (100, 101, 99, 100),
    (100, 105, 100, 104),
    (104, 104.5, 98, 99),
    (99, 108, 99, 107),
    (107, 107.5, 101, 102),
    (102, 111, 102, 110),
    (110, 110.5, 104, 105),
  ])

  analyze(
    {"M5": m5},
    AnalysisSettings(
      zigzag_atr_mult=0.0, key_level_min_touches=1, zone_reconcile_enabled=False,
    ),
    [],
  )
  assert calls == [], "reconcile_opposing must not run when the flag is off"

  analyze(
    {"M5": m5},
    AnalysisSettings(
      zigzag_atr_mult=0.0, key_level_min_touches=1, zone_reconcile_enabled=True,
    ),
    [],
  )
  assert calls == [1], "reconcile_opposing must run when the flag is on"


def test_reconcile_opposing_split_keeps_larger_remainder_only():
  # keep (supply) sits strictly inside trim (demand) - splitting would
  # produce two disjoint demand fragments; only the larger side survives.
  supply = Zone(4120, 4122, "supply", origin_index=1, score=10.0)
  demand = Zone(4110, 4130, "demand", origin_index=2, score=5.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  demand_zones = [zone for zone in result if zone.side == "demand"]
  assert len(demand_zones) == 1
  assert (demand_zones[0].low, demand_zones[0].high) == (4110, 4120)
  supply_zones = [zone for zone in result if zone.side == "supply"]
  assert len(supply_zones) == 1
  assert (supply_zones[0].low, supply_zones[0].high) == (4120, 4122)


def test_reconcile_opposing_is_deterministic_regardless_of_input_order():
  zones = [
    Zone(4116, 4127, "supply", origin_index=1, score=8.0),
    Zone(4112, 4122, "demand", origin_index=2, score=5.0),
    Zone(4200, 4210, "supply", origin_index=3, score=6.0),
    Zone(4000, 4010, "demand", origin_index=4, score=6.0),
  ]

  first = reconcile_opposing(zones, min_width=2.0)
  second = reconcile_opposing(list(reversed(zones)), min_width=2.0)

  key = lambda zone: (zone.side, zone.low, zone.high)
  assert sorted(first, key=key) == sorted(second, key=key)
  # Running twice over the same (unmutated) input is also stable.
  assert reconcile_opposing(zones, min_width=2.0) == first


def test_reconcile_opposing_leaves_non_overlapping_zones_untouched():
  supply = Zone(4200, 4210, "supply", origin_index=1, score=8.0)
  demand = Zone(4000, 4010, "demand", origin_index=2, score=5.0)

  result = reconcile_opposing([supply, demand], min_width=2.0)

  assert supply in result
  assert demand in result


def test_reconcile_opposing_does_not_touch_same_side_overlaps():
  # Same-side overlaps are merge_zones's job, which already ran before this
  # is called - reconcile_opposing only ever compares supply against demand.
  first = Zone(100, 104, "supply", origin_index=1, score=5.0)
  second = Zone(102, 106, "supply", origin_index=2, score=5.0)

  result = reconcile_opposing([first, second], min_width=2.0)

  assert first in result
  assert second in result


def test_score_zones_prefers_fresh_ob_round_level_liquidity_and_htf():
  strong = Zone(
    4099,
    4101,
    "demand",
    source="order_block",
    break_kind="BOS",
    touches=0,
  )
  weak = Zone(4104, 4105, "demand", source="bullish_fvg", touches=2)
  htf = Zone(4098, 4102, "demand", source="supply_demand")

  scored = score_zones(
    [weak, strong],
    [Level(4100, "reaction", touches=3, band=1.0)],
    [Pool("sell", 4098.8, 0.2, touches=2)],
    round_step=5,
    htf_zones=[htf],
  )

  assert scored[0].low == 4099
  assert scored[0].score > scored[1].score
  assert scored[0].score >= 13
  assert {"fresh", "OB", "key level", "round 4100", "liquidity pool", "HTF zone"} <= set(
    scored[0].score_reasons
  )


def _tf_item(
  zones: list[Zone],
  *,
  structure: str = "range",
  momentum_value: str = "neutral",
) -> TimeframeAnalysis:
  df = _df([(100, 101, 99, 100)])
  return TimeframeAnalysis(
    df=df,
    atr=pd.Series([1.0], index=df.index),
    swings=[],
    structure=structure,
    breaks=[],
    key_levels=[],
    legs=[],
    supply_demand_zones=[],
    order_blocks=[zone for zone in zones if "order_block" in zone.sources],
    flip_zones=[],
    fvg_zones=[],
    zones=zones,
    liquidity_pools=[],
    liquidity_grabs=[],
    momentum=momentum_value,
  )


def test_mtf_second_pass_adds_htf_zone_score_to_lower_tf():
  lower = Zone(
    100,
    101,
    "demand",
    source="order_block",
    break_kind="BOS",
  )
  higher = Zone(99, 102, "demand", source="supply_demand")

  updated = _apply_mtf_zone_scores(
    {
      "M5": _tf_item([lower]),
      "M30": _tf_item([higher]),
    },
    AnalysisSettings(round_step=0),
  )

  scored = updated["M5"].zones[0]
  assert scored.score == 9
  assert "HTF zone" in scored.score_reasons
  assert updated["M5"].order_blocks == [scored]


def test_htf_bias_fallback_is_deterministic_by_timeframe_rank():
  low_tf = _tf_item([], structure="up", momentum_value="bull")
  high_tf = _tf_item([], structure="down", momentum_value="bear")

  assert _htf_bias({"M5": low_tf, "M30": high_tf}, []) == "down"
  assert _htf_bias({"M30": high_tf, "M5": low_tf}, []) == "down"


def test_key_levels_cluster_repeated_swings_and_drop_lone_touch():
  swings = [
    Swing(1, "high", 100.0),
    Swing(3, "high", 100.4),
    Swing(5, "high", 100.8),
    Swing(7, "low", 110.0),
  ]

  levels = key_levels(swings, atr=2.0, level_cluster_atr=0.5, min_touches=2)

  assert len(levels) == 1
  assert levels[0].touches == 3
  assert 100.0 <= levels[0].price <= 100.8


def test_liquidity_pool_and_grab_from_equal_highs():
  df = _df([
    (99, 100, 98, 99),
    (99, 100.1, 98, 99.5),
    (99.5, 100.4, 98, 99.8),
  ])
  swings = [
    Swing(0, "high", 100.0),
    Swing(1, "high", 100.05),
  ]

  pools = liquidity_pools(swings, df, equal_tol_atr=0.2, atr=pd.Series([1, 1, 1]))
  grabs = liquidity_grabs(df, pools)

  assert any(pool.side == "buy" and pool.touches == 2 for pool in pools)
  assert any(grab.direction == "bear" for grab in grabs)


def test_session_levels_bucket_sessions_sweeps_and_rollover():
  index = pd.date_range(
    "2026-07-09 00:00",
    "2026-07-12 02:00",
    freq="5min",
    tz="UTC",
  )
  rows = []
  for ts in index:
    high = 101.0
    low = 99.0
    if ts == pd.Timestamp("2026-07-10 01:00", tz="UTC"):
      high = 120.0
    if ts == pd.Timestamp("2026-07-10 02:00", tz="UTC"):
      low = 90.0
    if ts == pd.Timestamp("2026-07-10 08:00", tz="UTC"):
      high = 121.0
    if ts == pd.Timestamp("2026-07-10 09:00", tz="UTC"):
      high = 115.0
    if ts == pd.Timestamp("2026-07-10 10:00", tz="UTC"):
      low = 95.0
    if ts == pd.Timestamp("2026-07-10 15:00", tz="UTC"):
      high = 118.0
    if ts == pd.Timestamp("2026-07-10 16:00", tz="UTC"):
      low = 94.0
    if ts == pd.Timestamp("2026-07-10 22:30", tz="UTC"):
      high = 130.0
    rows.append((100.0, high, low, 100.5))
  df = _df_with_index(index, rows)

  levels = session_levels(df, AnalysisSettings())
  asia_high = next(
    level for level in levels
    if level.name == "ASIA_H" and level.price == 120.0
  )
  asia_low = next(
    level for level in levels
    if level.name == "ASIA_L" and level.price == 90.0
  )
  pdh = next(level for level in levels if level.name == "PDH")

  assert asia_high.swept is True
  assert asia_high.swept_ts == pd.Timestamp("2026-07-10 08:00", tz="UTC")
  assert asia_low.swept is False
  assert any(level.name == "LONDON_H" and level.price == 121.0 for level in levels)
  assert any(level.name == "LONDON_L" and level.price == 95.0 for level in levels)
  assert any(level.name == "NY_H" and level.price == 118.0 for level in levels)
  assert any(level.name == "NY_L" and level.price == 94.0 for level in levels)
  assert pdh.price == 130.0
  assert pdh.ts == pd.Timestamp("2026-07-10 22:30", tz="UTC")
  assert previous_week_levels(df) == []


def test_dealing_range_classifies_discount_and_eq():
  swings = [
    Swing(0, "low", 100.0),
    Swing(1, "high", 200.0),
  ]

  discount = dealing_range(swings, 130.0)
  eq = dealing_range(swings, 150.0)

  assert discount is not None
  assert discount.position == 0.3
  assert discount.zone == "discount"
  assert eq is not None
  assert eq.zone == "eq"


def test_regime_marks_tight_exec_range_as_chop():
  df = _df([(105, 106, 104, 105)] * 24)
  range_ = DealingRange(high=110, low=100, eq=105, position=0.5, zone="eq")

  result = regime(
    df,
    pd.Series([3.0] * len(df), index=df.index),
    [],
    "up",
    range_,
    AnalysisSettings(chop_range_atr=4.0, chop_lookback=24),
  )

  assert result == Regime(
    "chop",
    110,
    100,
    result.height_atr,
    result.reasons,
    legacy_kind="chop",
    new_kind="chop",
  )
  assert result.height_atr == pytest.approx(10 / 3)
  assert any(reason.startswith("range height") for reason in result.reasons)


def test_regime_marks_contained_range_structure_as_chop():
  rows = [
    (105, 108, 102, close)
    for close in ([104, 106, 105, 103, 107, 106] * 4)
  ]
  df = _df(rows)
  range_ = DealingRange(high=110, low=100, eq=105, position=0.5, zone="eq")

  result = regime(
    df,
    pd.Series([1.0] * len(df), index=df.index),
    [],
    "range",
    range_,
    AnalysisSettings(chop_range_atr=4.0, chop_lookback=24),
  )

  assert result.kind == "chop"
  assert any("range structure held" in reason for reason in result.reasons)


def test_regime_keeps_expanded_breakout_as_trend():
  rows = [(105, 108, 102, 105)] * 22 + [
    (109, 113, 108, 111),
    (111, 116, 110, 114),
  ]
  df = _df(rows)
  range_ = DealingRange(high=110, low=100, eq=105, position=1.0, zone="premium")

  result = regime(
    df,
    pd.Series([1.0] * len(df), index=df.index),
    [],
    "range",
    range_,
    AnalysisSettings(chop_range_atr=4.0, chop_lookback=24),
  )

  assert result.kind == "trend"
  assert result.reasons == ["range expanded or broke edge"]


def test_liquidity_grab_grade_a_and_inducement_score_bonus():
  df = _df([
    (102, 103, 101, 102),
    (100, 103, 99, 102),
    (102, 104, 101, 103),
    (103, 107, 102, 106),
  ])
  atr = pd.Series([1.0] * len(df), index=df.index)
  pool = Pool("sell", 100.0, 0.1, 2)
  zone = Zone(99.5, 101.0, "demand", source="supply_demand")
  grabs = liquidity_grabs(
    df,
    [pool],
    [Leg(2, 3, "up", 5.0)],
    [zone],
    atr,
    sweep_body_frac=0.5,
    sweep_react_bars=3,
    inducement_band_atr=0.3,
  )

  assert len(grabs) == 1
  assert grabs[0].grade == "A"
  assert grabs[0].displacement is True
  assert grabs[0].inducement is True

  scored = score_zones([zone], [], [pool], round_step=0, grabs=grabs)[0]
  assert "sweep A" in scored.score_reasons


def test_liquidity_grab_grade_b_without_displacement():
  df = _df([
    (102, 103, 101, 102),
    (100, 103, 99, 102),
    (102, 103, 101, 102),
  ])
  pool = Pool("sell", 100.0, 0.1, 2)

  grabs = liquidity_grabs(df, [pool], legs=[])

  assert len(grabs) == 1
  assert grabs[0].grade == "B"
  assert grabs[0].displacement is False


def test_breaker_blocks_close_through_flips_and_wick_does_not():
  ob = Zone(
    100,
    102,
    "demand",
    origin_index=0,
    source="order_block",
    break_kind="BOS",
  )
  violated = _df([
    (101, 102, 100, 101),
    (101, 103, 99, 101),
    (101, 102, 99, 99.5),
  ])
  wick_only = _df([
    (101, 102, 100, 101),
    (101, 103, 99, 101),
  ])

  zones = breaker_blocks([ob], violated)
  dead = next(zone for zone in zones if zone.source == "order_block")
  breaker = next(zone for zone in zones if zone.source == "breaker")

  assert dead.mitigated is True
  assert dead.touches == 1
  assert breaker.side == "supply"
  assert breaker.low == 100
  assert breaker.high == 102
  assert breaker.origin_index == 2
  assert [zone.source for zone in breaker_blocks([ob], wick_only)] == ["order_block"]


def test_score_zones_rewards_session_level_and_discount_position():
  zone = Zone(100, 101, "demand", source="supply_demand")
  ts = pd.Timestamp("2026-07-10 02:00", tz="UTC")

  strong = score_zones(
    [zone],
    [],
    [],
    round_step=0,
    session_levels=[SessionLevel("ASIA_L", 100.2, ts, swept=False)],
    dealing_range=DealingRange(high=120, low=90, eq=105, position=0.33, zone="discount"),
  )[0]
  weak = score_zones(
    [zone],
    [],
    [],
    round_step=0,
    dealing_range=DealingRange(high=105, low=80, eq=92.5, position=0.8, zone="premium"),
  )[0]

  assert strong.score > weak.score
  assert "ASIA_L" in strong.score_reasons
  assert "discount" in strong.score_reasons


def test_price_only_momentum_bull_and_neutral():
  bull = _df([
    (100, 103, 99.8, 102.8),
    (102.8, 106, 102.5, 105.8),
    (105.8, 109, 105.5, 108.8),
    (108.8, 112, 108.5, 111.8),
  ])
  choppy = _df([
    (100, 102, 98, 100.2),
    (100.2, 102, 98, 99.9),
    (99.9, 102, 98, 100.1),
    (100.1, 102, 98, 100.0),
  ])

  assert momentum(bull, pd.Series([1, 2, 3, 4]), lookback=4) == "bull"
  assert momentum(choppy, pd.Series([1, 1, 1, 1]), lookback=4) == "neutral"


def test_analyze_assembles_per_tf_outputs_and_htf_bias():
  m5 = _df([
    (100, 101, 99, 100),
    (100, 105, 100, 104),
    (104, 104.5, 98, 99),
    (99, 108, 99, 107),
    (107, 107.5, 101, 102),
    (102, 111, 102, 110),
    (110, 110.5, 104, 105),
  ])
  m15 = _df([
    (100, 103, 99.8, 102.8),
    (102.8, 106, 102.5, 105.8),
    (105.8, 109, 105.5, 108.8),
    (108.8, 112, 108.5, 111.8),
  ])

  ctx = analyze(
    {"M5": m5, "M15": m15},
    AnalysisSettings(zigzag_atr_mult=0.0, key_level_min_touches=1),
    ["M15"],
  )

  assert set(ctx.per_tf) == {"M5", "M15"}
  assert ctx.htf_bias in {"up", "down", "range"}
  assert all(hasattr(zone, "mitigated") for item in ctx.per_tf.values() for zone in item.zones)


def test_analysis_modules_have_no_delivery_or_state_imports():
  import app.analysis.engine as engine
  import app.analysis.dealing_range as dealing_range_module
  import app.analysis.levels as levels
  import app.analysis.liquidity as liquidity
  import app.analysis.momentum as momentum_module
  import app.analysis.market_map as market_map
  import app.analysis.math_utils as math_utils
  import app.analysis.types as types
  import app.analysis.regime as regime_module
  import app.analysis.scalp_ranges as scalp_ranges_module
  import app.analysis.session_liquidity as session_liquidity
  import app.analysis.structure as structure
  import app.analysis.swings as swings_module
  import app.analysis.trendlines as trendlines_module
  import app.analysis.zones as zones

  forbidden = {
    "redis_state",
    "send_with_retry",
    "broadcast_entry",
    "store_manual_signal",
  }
  modules = [
    engine,
    dealing_range_module,
    levels,
    liquidity,
    momentum_module,
    market_map,
    math_utils,
    types,
    regime_module,
    scalp_ranges_module,
    session_liquidity,
    structure,
    swings_module,
    trendlines_module,
    zones,
  ]

  for module in modules:
    assert forbidden.isdisjoint(vars(module))
