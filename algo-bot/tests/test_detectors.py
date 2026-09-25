from dataclasses import replace
from typing import Callable

import pandas as pd
import pytest

from app.analysis.confluence_zone import BandKind, classify_band_kind
from app.analysis.engine import Regime
from app.analysis import detectors
from app.analysis.types import Break, DealingRange, Grab, Pool, SessionLevel
from app.analysis.regime import BoxBreak
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange
from app.analysis.structural_reaction_support import (
  CONFIRM_ENGULFING,
  CONFIRM_REJECTION_CHOCH,
  CONFIRM_STRONG_RECLAIM,
  CONFIRM_SWEEP_RECLAIM,
  CONFIRM_WICK_REJECTION,
  box_structural_id,
  key_level_structural_id,
  trendline_structural_id,
)

_RANGE_EDGE_CONFIRMATION_TYPES = frozenset({
  CONFIRM_WICK_REJECTION,
  CONFIRM_SWEEP_RECLAIM,
  CONFIRM_REJECTION_CHOCH,
  CONFIRM_STRONG_RECLAIM,
  CONFIRM_ENGULFING,
})
from app.analysis.structure import Level, Swing, Zone
from app.analysis.trendlines import Trendline


def _df(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=index,
  )


def _series(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _indicators(df: pd.DataFrame, *, atr: float = 3) -> detectors.IndicatorSet:
  return detectors.IndicatorSet(atr=_series(df, atr))


def _ctx(
  df: pd.DataFrame,
  *,
  bias: str = "up",
  levels: list[Level] | None = None,
  equal_levels: list[Level] | None = None,
  zones: list[Zone] | None = None,
  swings: list[Swing] | None = None,
  breaks: list[Break] | None = None,
  grabs: list[Grab] | None = None,
  session_levels: list[SessionLevel] | None = None,
  dealing_range: DealingRange | None = None,
  indicator_set: detectors.IndicatorSet | None = None,
  regime: Regime | None = None,
  trendlines: list[Trendline] | None = None,
  box_break: BoxBreak | None = None,
  liquidity_pools: list[Pool] | None = None,
  scalp_barriers: list[ScalpBarrier] | None = None,
  scalp_range: ScalpRange | None = None,
  settings: detectors.DetectorSettings | None = None,
) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=swings or [],
    bias=bias,
    levels=levels or [],
    equal_levels=equal_levels or [],
    fvg_zones=[],
    order_blocks=[],
    breaks=breaks or [],
    zones=zones or [],
    liquidity_grabs=grabs or [],
    session_levels=session_levels or [],
    dealing_range=dealing_range,
    trendlines=trendlines or [],
    box_break=box_break,
    liquidity_pools=liquidity_pools or [],
    scalp_barriers=scalp_barriers or [],
    scalp_range=scalp_range,
  )
  return detectors.DetectionContext(
    symbol="XAU",
    tf=tf,
    frames={tf: df},
    indicators={tf: indicator_set or _indicators(df)},
    structures={tf: structure},
    htf_bias=bias,
    settings=settings or detectors.DetectorSettings(confluence_floor=2),
    regime=regime,
  )


def _buy_rejection_df() -> pd.DataFrame:
  return _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 102, 103, 100),
    (106, 110, 101, 109, 100),
  ])


def _sell_rejection_df() -> pd.DataFrame:
  return _df([
    (110, 112, 108, 110, 100),
    (109, 110, 101, 102, 100),
    (102, 107, 100, 106, 100),
    (106, 108, 104, 107, 100),
    (107, 112, 101, 103, 100),
  ])


def _no_rejection_df() -> pd.DataFrame:
  return _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 102, 103, 100),
    (108.5, 110, 104, 108, 100),
  ])


def _uptrend_df(bars: int) -> pd.DataFrame:
  # A staircase of higher highs/higher lows (two-bar up-leg, one-bar
  # pullback, repeating) so find_swings/market_structure reliably reads
  # "up" - a plain monotonic line has no fractal swings to detect.
  rows = []
  price = 4000.0
  for i in range(bars):
    if i % 3 == 2:
      rows.append((price, price + 0.5, price - 1.5, price - 1.0, 100))
      price -= 1.0
    else:
      rows.append((price, price + 3.0, price - 0.3, price + 2.5, 100))
      price += 2.5
  return _df(rows)


def _break_retest_ctx() -> detectors.DetectionContext:
  df = _df([
    (100, 102, 98, 100, 100),
    (100, 104, 99, 104, 100),
    (104, 108, 103, 107, 100),
    (107, 109, 105, 108, 100),
    (106, 110, 102, 109, 100),
  ])
  return _ctx(df, levels=[Level(105, "reaction")])


def _snap_back_ctx() -> detectors.DetectionContext:
  df = _buy_rejection_df()
  return _ctx(
    df,
    zones=[Zone(103, 105, "demand", source="supply_demand")],
    grabs=[
      Grab(Pool("sell", 103, 0.1, 2), 4, "bull", df.index[4], "B"),
    ],
    indicator_set=_indicators(df, atr=2.5),
  )


def _momentum_ride_ctx() -> detectors.DetectionContext:
  df = _df([
    (100, 102, 98, 100, 100),
    (101, 104, 100, 103, 100),
    (103, 106, 102, 105, 100),
    (105, 108, 104, 107, 100),
    (107, 111, 106.2, 110.5, 100),
  ])
  return _ctx(
    df,
    levels=[Level(108.8, "reaction", band=0.1)],
    swings=[Swing(3, "high", 108), Swing(2, "low", 102)],
    indicator_set=_indicators(df, atr=1.0),
  )


def _fade_scalp_ctx() -> detectors.DetectionContext:
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 102, 103, 100),
    (106, 110, 101, 109, 100),
  ])
  return _ctx(
    df,
    levels=[Level(105, "reaction")],
    equal_levels=[Level(105, "equal_low", touches=2)],
    grabs=[
      Grab(Pool("sell", 105, 0.1, 2), 4, "bull", df.index[4], "B"),
    ],
  )


def _chop_regime(low: float = 100, high: float = 112) -> Regime:
  return Regime("chop", high, low, 3.0, ["fixture chop"])


SETUPS: list[
  tuple[
    Callable[[detectors.DetectionContext], detectors.DetectionResult | None],
    Callable[[], detectors.DetectionContext],
    str,
  ]
] = [
  (detectors.break_retest, _break_retest_ctx, "Break & Retest"),
  (detectors.snap_back, _snap_back_ctx, "Snap-Back"),
  (detectors.momentum_ride, _momentum_ride_ctx, "Momentum Ride"),
  (detectors.fade_scalp, _fade_scalp_ctx, "Fade Scalp"),
]


def _assert_correct_side(result: detectors.DetectionResult) -> None:
  if result.direction == "BUY":
    assert result.entry_zone.low <= result.current_price
    assert result.key_level <= result.current_price
  else:
    assert result.entry_zone.high >= result.current_price
    assert result.key_level >= result.current_price


@pytest.mark.parametrize(("detector", "ctx_factory", "setup"), SETUPS)
def test_named_setup_triggers_only_when_confirmed_and_correct_side(
  detector,
  ctx_factory,
  setup,
):
  result = detector(ctx_factory())

  assert result is not None
  assert result.setup == setup
  assert result.direction == "BUY"
  assert result.current_price == pytest.approx(
    float(ctx_factory().frames["M5"]["close"].iloc[-1])
  )
  assert result.confluence >= 2
  _assert_correct_side(result)


@pytest.mark.parametrize(
  ("detector", "ctx_factory"),
  [
    (detectors.break_retest, _break_retest_ctx),
    (detectors.momentum_ride, _momentum_ride_ctx),
  ],
)
def test_chop_regime_silences_trend_continuation_setups(detector, ctx_factory):
  ctx = replace(ctx_factory(), regime=_chop_regime())
  disabled = replace(
    ctx,
    settings=replace(ctx.settings, chop_filter_enabled=False),
  )

  assert detector(disabled) is not None
  assert detector(ctx) is None


def test_sell_impulse_at_range_bottom_is_muted_in_chop():
  df = _df([
    (110, 112, 108, 110, 100),
    (109, 110, 104, 105, 100),
    (105, 106, 102, 104, 100),
    (104, 105, 101, 103, 100),
    (103, 104, 98, 99, 100),
  ])
  ctx = _ctx(
    df,
    bias="down",
    levels=[Level(100, "reaction", band=0.1)],
    swings=[Swing(3, "low", 101), Swing(2, "high", 112)],
    indicator_set=_indicators(df, atr=1.0),
    regime=_chop_regime(98, 112),
  )

  assert detectors.momentum_ride(
    replace(ctx, settings=replace(ctx.settings, chop_filter_enabled=False))
  ) is not None
  assert detectors.momentum_ride(ctx) is None


def test_chop_fade_scalp_requires_edge_and_grade_a_sweep():
  df = _sell_rejection_df()
  top_edge = _ctx(
    df,
    bias="down",
    equal_levels=[Level(109, "equal_high", touches=2)],
    grabs=[Grab(Pool("buy", 109, 0.1, 2), 4, "bear", df.index[4], "A")],
    regime=_chop_regime(100, 112),
  )
  grade_b = _ctx(
    df,
    bias="down",
    equal_levels=[Level(109, "equal_high", touches=2)],
    grabs=[Grab(Pool("buy", 109, 0.1, 2), 4, "bear", df.index[4], "B")],
    regime=_chop_regime(100, 112),
  )
  mid_range = _ctx(
    df,
    bias="down",
    equal_levels=[Level(106, "equal_high", touches=2)],
    grabs=[Grab(Pool("buy", 106, 0.1, 2), 4, "bear", df.index[4], "A")],
    regime=_chop_regime(100, 112),
  )

  result = detectors.fade_scalp(top_edge)

  assert result is not None
  assert result.direction == "SELL"
  assert result.reasons[0] == "HTF bias down"
  assert "sweep A" in result.reasons
  assert "range 100-112" in result.reasons
  assert "TP anchor range low 100" in result.reasons
  assert detectors.fade_scalp(grade_b) is None
  assert detectors.fade_scalp(mid_range) is None


def test_wrong_side_level_fallback_is_gone_for_sell_and_buy():
  sell_df = _sell_rejection_df()
  sell = _ctx(
    sell_df,
    bias="down",
    levels=[Level(99, "reaction")],
    swings=[Swing(3, "low", 104), Swing(2, "high", 112)],
  )
  buy_df = _buy_rejection_df()
  buy = _ctx(
    buy_df,
    levels=[Level(112, "reaction")],
    swings=[Swing(3, "high", 108), Swing(2, "low", 102)],
  )

  assert detectors.momentum_ride(sell) is None
  assert detectors.momentum_ride(buy) is None
  assert detectors._nearest_level(sell.structures["M5"].levels, 103, "SELL") is None
  assert detectors._nearest_level(buy.structures["M5"].levels, 109, "BUY") is None


def test_wide_zone_uses_proximal_band_slice():
  zone = Zone(60, 100, "demand", source="order_block", score=9)
  selected = detectors._best_valid_zone(
    [zone],
    price=108,
    atr=4,
    direction="BUY",
    settings=detectors.DetectorSettings(
      max_zone_width_atr=1.5,
      proximal_band_atr=0.5,
    ),
  )

  assert selected is not None
  proximal, sliced = selected
  assert sliced is True
  assert proximal.low == 98
  assert proximal.high == 100
  assert "proximal of wide zone" in detectors._add_proximal_reason([], sliced)


@pytest.mark.no_database
def test_star_score_remap_and_mitigated_cap():
  fresh_two = Zone(100, 101, "demand", score=9, touches=0)
  fresh_three = Zone(100, 101, "demand", score=13, touches=0)
  mitigated = Zone(100, 101, "demand", score=13, touches=1)
  max_zone = Zone(100, 101, "demand", score=detectors._ZONE_SCORE_MAX, touches=0)

  assert detectors._confluence_from_zone(fresh_two, []) == 1
  assert detectors._confluence_from_zone(fresh_three, []) == 2
  assert detectors._confluence_from_zone(mitigated, []) == 2
  assert detectors._confluence_from_zone(max_zone, []) == 3


@pytest.mark.no_database
def test_normalised_factor_threshold_matches_legacy_cut_points():
  """PR-E regression: 12/max and 8/max factor scores still map to 3★/2★."""
  assert detectors._FACTOR_SCORE_MAX == (
    detectors._FACTOR_HTF_ALIGN_WEIGHT
    + detectors._FACTOR_TOUCH_CAP * detectors._FACTOR_TOUCH_UNIT_WEIGHT
    + detectors._FACTOR_WICK_REJECTION_WEIGHT
    + detectors._FACTOR_DISPLACEMENT_WEIGHT
    + detectors._FACTOR_SESSION_CONTEXT_WEIGHT
    + detectors._FACTOR_STRUCTURAL_AGREEMENT_WEIGHT
    + detectors._FACTOR_FIB_TOUCH_WEIGHT
  )
  assert detectors._FACTOR_SCORE_MAX == 20.5
  assert detectors._ZONE_SCORE_MAX == 24.5
  three_star = detectors.ConfluenceFactors(
    htf_aligned=True,
    touches=3,
    wick_rejection=True,
    displacement_grade=True,
    structural_agreement=True,
  )
  two_star = detectors.ConfluenceFactors(
    htf_aligned=True,
    wick_rejection=True,
    displacement_grade=True,
  )
  assert detectors._raw_factor_score(three_star) >= 12.0
  assert detectors._confluence_from_factors(three_star) == 3
  assert 7.0 <= detectors._raw_factor_score(two_star) < 12.0
  assert detectors._confluence_from_factors(two_star) == 2


@pytest.mark.no_database
def test_key_level_reaction_confluence_varies_with_htf_alignment_and_touches():
  zone = Zone(100, 101, "demand", score=0.0)
  minimal = detectors.ConfluenceFactors(
    wick_rejection=True,
    structural_agreement=True,
    touches=2,
  )
  strong = detectors.ConfluenceFactors(
    htf_aligned=True,
    wick_rejection=True,
    structural_agreement=True,
    touches=3,
  )
  assert detectors._confluence_from_zone(zone, minimal) == 2
  assert detectors._confluence_from_zone(zone, strong) == 3
  assert detectors._confluence_from_zone(
    zone, strong,
  ) > detectors._confluence_from_zone(zone, minimal)


def test_confluence_rubric_is_shared_across_detectors():
  """Two different detectors observing the same factor set must derive the
  same confluence - the rubric, not the calling detector, is the source of
  truth (B2). Exercised through the real _finish/_confluence_from_zone path
  each of the DEFAULT_DETECTORS funnels through.
  """
  df = _buy_rejection_df()
  ctx = _ctx(df, indicator_set=_indicators(df, atr=1.0))
  zone = Zone(99, 101, "demand")  # score=0.0 -> factors path
  same_factors = detectors.ConfluenceFactors(
    htf_aligned=True,
    touches=3,
    wick_rejection=True,
    displacement_grade=True,
  )

  from_setup_a = detectors._finish(
    ctx, "Snap-Back", "BUY", 100.0, zone, 100.5, 1.0,
    ["reason from detector A"], factors=same_factors,
  )
  from_setup_b = detectors._finish(
    ctx, "Momentum Ride", "BUY", 100.0, zone, 100.5, 1.0,
    ["a completely different reason string from detector B"],
    factors=same_factors,
  )

  assert from_setup_a is not None
  assert from_setup_b is not None
  assert from_setup_a.confluence == from_setup_b.confluence == 3


def test_reasons_list_length_no_longer_influences_confluence():
  """B2: delete the len(reasons) fallback. Adding reason strings must not
  move the score - only ConfluenceFactors does.
  """
  df = _buy_rejection_df()
  ctx = _ctx(df, indicator_set=_indicators(df, atr=1.0))
  zone = Zone(99, 101, "demand")  # score=0.0 -> factors path
  factors = detectors.ConfluenceFactors(
    htf_aligned=True, wick_rejection=True, displacement_grade=True,
  )

  short = detectors._finish(
    ctx, "Fade Scalp", "BUY", 100.0, zone, 100.5, 1.0,
    ["one reason"], factors=factors,
  )
  long = detectors._finish(
    ctx, "Fade Scalp", "BUY", 100.0, zone, 100.5, 1.0,
    ["one reason", "two", "three", "four", "five", "six"], factors=factors,
  )

  assert short is not None
  assert long is not None
  assert short.confluence == long.confluence


@pytest.mark.parametrize(("detector", "ctx_factory", "_setup"), SETUPS)
def test_named_setup_returns_none_in_dealing_range_eq(detector, ctx_factory, _setup):
  ctx = ctx_factory()
  structure = replace(
    ctx.structures["M5"],
    dealing_range=DealingRange(high=110, low=90, eq=100, position=0.5, zone="eq"),
  )
  ctx = replace(ctx, structures={"M5": structure})

  assert detector(ctx) is None


def test_pd_gate_strict_rejects_buy_at_upper_eq_edge():
  st = detectors.StructureSet(
    swings=[],
    bias="up",
    levels=[],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    dealing_range=DealingRange(high=200, low=100, eq=150, position=0.55, zone="eq"),
  )

  assert not detectors._pd_gate(
    st,
    "BUY",
    detectors.DetectorSettings(strict_pd_gate=True),
  )


@pytest.mark.parametrize(("detector", "ctx_factory", "_setup"), SETUPS)
def test_named_setup_returns_none_when_counter_htf_bias(
  detector,
  ctx_factory,
  _setup,
):
  ctx = replace(ctx_factory(), htf_bias="down")
  ctx = replace(
    ctx,
    settings=replace(ctx.settings, allow_counter_trend=False),
  )

  assert detector(ctx) is None


@pytest.mark.parametrize(("detector", "ctx_factory", "_setup"), SETUPS)
def test_named_setup_returns_none_below_confluence_floor(
  detector,
  ctx_factory,
  _setup,
):
  ctx = ctx_factory()
  ctx = replace(ctx, settings=replace(ctx.settings, confluence_floor=4))

  assert detector(ctx) is None


def test_entry_valid_rejects_wrong_side_broken_and_far_zones():
  atr = 2.0

  assert detectors._entry_valid(Zone(105, 106, "supply"), 104, atr, "SELL")
  assert not detectors._entry_valid(Zone(101, 102, "supply"), 104, atr, "SELL")
  assert not detectors._entry_valid(Zone(100, 102, "supply"), 103, atr, "SELL")
  assert not detectors._entry_valid(Zone(111, 112, "supply"), 104, atr, "SELL")

  assert detectors._entry_valid(Zone(102, 103, "demand"), 104, atr, "BUY")
  assert not detectors._entry_valid(Zone(105, 106, "demand"), 104, atr, "BUY")
  assert not detectors._entry_valid(Zone(105, 106, "demand"), 104, atr, "BUY")
  assert not detectors._entry_valid(Zone(97, 98, "demand"), 104, atr, "BUY")


def test_rejection_helper_requires_directional_closed_bar():
  assert detectors._rejection(_buy_rejection_df(), "BUY")
  assert detectors._rejection(_sell_rejection_df(), "SELL")
  assert not detectors._rejection(_no_rejection_df(), "BUY")


def _scalp_range(
  *,
  lower_touches: int = 3,
  upper_touches: int = 3,
  lower_accepted: int = 0,
  upper_accepted: int = 0,
) -> ScalpRange:
  lower = ScalpBarrier(
    "support",
    100,
    99.7,
    100.3,
    lower_touches,
    lower_touches,
    lower_accepted,
    3,
    [f"micro ×{lower_touches}", f"wick ×{lower_touches}"],
    13,
  )
  upper = ScalpBarrier(
    "resistance",
    110,
    109.7,
    110.3,
    upper_touches,
    upper_touches,
    upper_accepted,
    3,
    [f"micro ×{upper_touches}", f"wick ×{upper_touches}"],
    13,
  )
  return ScalpRange(lower, upper, 105, 5, 18)


def _range_sell_df() -> pd.DataFrame:
  return _df([
    (105, 107, 103, 106, 100),
    (106, 108, 104, 105, 100),
    (105, 107, 103, 106, 100),
    (106, 108, 104, 106, 100),
    (108, 111, 105, 106, 100),
  ])


def _range_buy_df() -> pd.DataFrame:
  return _df([
    (105, 107, 103, 104, 100),
    (104, 106, 102, 105, 100),
    (105, 107, 103, 104, 100),
    (104, 106, 102, 104, 100),
    (102, 105, 99, 104, 100),
  ])


@pytest.mark.parametrize(
  ("df", "direction"),
  [
    (_range_sell_df(), "SELL"),
    (_range_buy_df(), "BUY"),
  ],
)
def test_range_edge_scalp_fires_both_directions_with_range_htf_bias(df, direction):
  scalp_range = _scalp_range()
  ctx = _ctx(
    df,
    bias="range",
    scalp_barriers=[scalp_range.lower, scalp_range.upper],
    scalp_range=scalp_range,
    indicator_set=_indicators(df, atr=2),
  )

  result = detectors.range_edge_scalp(ctx)

  assert result is not None
  assert result.setup == "Range Edge Scalp"
  assert result.direction == direction
  assert result.mode == "range_scalp"
  assert result.confirmation in _RANGE_EDGE_CONFIRMATION_TYPES
  assert result.touch_bar_ts
  assert result.confirmation_bar_ts
  assert any(reason.startswith("TP1 EQ") for reason in result.reasons)
  assert any(reason.startswith("TP2 edge") for reason in result.reasons)


def test_range_edge_scalp_waits_in_middle_and_rejects_accepted_breakout():
  middle = _df([
    (105, 107, 103, 106, 100),
    (106, 108, 104, 105, 100),
    (105, 107, 103, 106, 100),
    (106, 108, 104, 105, 100),
    (105, 107, 103, 106, 100),
  ])
  scalp_range = _scalp_range()
  middle_ctx = _ctx(
    middle,
    bias="range",
    scalp_barriers=[scalp_range.lower, scalp_range.upper],
    scalp_range=scalp_range,
    indicator_set=_indicators(middle, atr=2),
  )
  broken = _scalp_range(upper_accepted=2)
  broken_ctx = _ctx(
    _range_sell_df(),
    bias="range",
    scalp_barriers=[broken.lower, broken.upper],
    scalp_range=broken,
    indicator_set=_indicators(_range_sell_df(), atr=2),
  )

  assert detectors.range_edge_scalp(middle_ctx) is None
  assert detectors.range_edge_scalp(broken_ctx) is None


def test_two_touch_barrier_accepts_scored_edge_rejection_or_grade_a_sweep():
  df = _range_sell_df()
  scalp_range = _scalp_range(upper_touches=2)
  base = _ctx(
    df,
    bias="range",
    scalp_barriers=[scalp_range.lower, scalp_range.upper],
    scalp_range=scalp_range,
    indicator_set=_indicators(df, atr=2),
  )
  grab = Grab(Pool("buy", 110, 0.1, 2), 4, "bear", df.index[-1], "A")
  with_grab = replace(
    base,
    structures={
      "M5": replace(base.structures["M5"], liquidity_grabs=[grab]),
    },
  )

  assert detectors.range_edge_scalp(base) is not None
  assert detectors.range_edge_scalp(with_grab) is not None


def test_range_edge_scalp_requires_room_to_eq():
  df = _range_sell_df()
  scalp_range = _scalp_range()
  ctx = _ctx(
    df,
    bias="range",
    scalp_barriers=[scalp_range.lower, scalp_range.upper],
    scalp_range=scalp_range,
    indicator_set=_indicators(df, atr=2),
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      range_scalp_min_room_atr=3,
    ),
  )

  assert detectors.range_edge_scalp(ctx) is None


def test_trendline_break_retest_fires_outside_chop_only():
  df = _buy_rejection_df()
  line = Trendline(
    "resistance", (0, 1, 2), 0.0, 105.0, 3, True, 3,
    fit_error_atr=0.0, violations=0, bars_since_last_touch=0, span_bars=2,
    exhausted=False,
  )
  ctx = _ctx(df, trendlines=[line])

  result = detectors.break_retest(ctx)

  assert result is not None
  assert result.setup == "Break & Retest"
  assert "TL break+retest" in result.reasons
  assert result.entry_zone.source == "trendline"
  assert detectors.break_retest(replace(ctx, regime=_chop_regime())) is None
  # Registry replay_only_reason: "not yet re-verified against band-kind
  # classification and canonical BREAKOUT_RETEST family merge". Break &
  # Retest was never wiring structural_source/structural_id at all, so it
  # was invisible to _merge_detection_confluence (which requires a truthy
  # structural_id) and never band-kind classified - reusing the exact same
  # identity trendline_reaction uses means the two can never both fire
  # unmerged on the same trendline (they're mutually exclusive on
  # broken/unbroken state anyway) and this now gets the lenient LEVEL_BAND
  # width treatment instead of silently defaulting to STRUCTURAL_ZONE.
  assert result.structural_id == trendline_structural_id(
    ctx.symbol, ctx.tf, line,
  )
  assert result.structural_source == "trendline"
  assert classify_band_kind(result.structural_source) == BandKind.LEVEL_BAND


def test_break_retest_level_path_populates_structural_identity():
  # Same gap as the trendline path above, for the horizontal-level branch
  # of break_retest (the one key_level_reaction explicitly defers
  # broken-role levels to - see its ROLE_BROKEN_SUPPORT/RESISTANCE skip).
  df = _df([
    (95, 96, 94, 95, 100),
    (95, 96, 94, 95, 100),
    (99, 102, 98, 101, 100),
    (101, 103, 100.5, 102, 100),
    (101, 102, 99.5, 101.8, 100),
  ])
  level = Level(100.0, kind="reaction", touches=3, strength=2.0)
  ctx = _ctx(df, levels=[level], indicator_set=_indicators(df, atr=1.0))

  result = detectors.break_retest(ctx)

  assert result is not None
  assert result.setup == "Break & Retest"
  assert result.direction == "BUY"
  assert result.entry_zone.source == "retest_support"
  assert result.structural_id == key_level_structural_id(
    ctx.symbol, ctx.tf, level,
  )
  assert result.structural_source == "key_level"
  assert classify_band_kind(result.structural_source) == BandKind.LEVEL_BAND


def _box_breakout_ctx(*, bias: str = "up", accept_index: int = 3):
  df = _df([
    (105, 106, 104, 105, 100),
    (105, 106, 104, 105, 100),
    (110.15, 110.5, 110.1, 110.3, 100),
    (110.3, 110.6, 110.15, 110.4, 100),
    (110.4, 111, 109.5, 110.8, 100),
  ])
  box = BoxBreak(110, 100, "up", accept_index, True, "2 closes")
  return _ctx(
    df,
    bias=bias,
    box_break=box,
    regime=_chop_regime(100, 110),
    session_levels=[SessionLevel("PDH", 115, df.index[0], swept=False)],
    indicator_set=_indicators(df, atr=1.0),
  )


def test_box_breakout_accepts_bias_aligned_retest_inside_chop():
  result = detectors.box_breakout(_box_breakout_ctx())

  assert result is not None
  assert result.setup == "Box Breakout"
  assert result.direction == "BUY"
  assert result.key_level == 100
  assert result.reasons[1:5] == [
    "box 100-110",
    "accepted (2 closes)",
    "retest 110",
    "measured +10.0",
  ]
  assert "box 100-110" in result.reasons
  assert "accepted (2 closes)" in result.reasons
  assert "retest 110" in result.reasons
  assert "measured +10.0" in result.reasons
  assert "coil" in result.reasons
  assert "TP1 PDH" in result.reasons
  assert "coil" in result.entry_zone.score_reasons
  # Same gap as Break & Retest above: box_breakout never wired
  # structural_source/structural_id, so it was invisible to
  # _merge_detection_confluence and never band-kind classified - its
  # replay_only_reason names exactly this ("not yet re-verified against
  # band-kind classification and canonical BREAKOUT_RETEST family merge").
  box = _box_breakout_ctx().structures["M5"].box_break
  assert result.structural_id == box_structural_id("XAU", "M5", box)
  assert result.structural_source == "box_breakout"
  assert (
    classify_band_kind(result.structural_source)
    == BandKind.BREAKOUT_RETEST_BAND
  )
  assert result.structural_low == result.entry_zone.low
  assert result.structural_high == result.entry_zone.high


def test_box_breakout_allows_immediate_proximal_displacement_entry():
  df = _df([
    (105, 106, 104, 105, 100),
    (105, 106, 104, 105, 100),
    (109.7, 111.2, 109.6, 110.8, 100),
  ])
  ctx = _ctx(
    df,
    box_break=BoxBreak(110, 100, "up", 2, False, "displacement"),
    regime=_chop_regime(100, 110),
    indicator_set=_indicators(df, atr=1.0),
  )

  result = detectors.box_breakout(ctx)

  assert result is not None
  assert "accepted (displacement)" in result.reasons
  assert "proximal 110" in result.reasons


def test_box_breakout_rejects_counter_bias_and_stale_acceptance():
  assert detectors.box_breakout(_box_breakout_ctx(bias="down")) is None
  assert detectors.box_breakout(_box_breakout_ctx(accept_index=-3)) is None


def test_detectors_module_has_no_delivery_or_redis_imports():
  assert not hasattr(detectors, "redis_state")
  assert not hasattr(detectors, "send_with_retry")
  assert not hasattr(detectors, "broadcast_entry")
  assert not hasattr(detectors, "store_manual_signal")


def test_build_context_htf_bias_unknown_when_h1_frame_missing():
  # H1 is scanner_htf's primary entry (H1->M15->M5 single-analysis-source
  # cutover, P2) - if it's absent entirely, HTF bias must fail closed to
  # "unknown" rather than silently falling back through the rest of
  # per_tf/_ordered_tfs to guess from M5/M15 alone.
  m5 = _uptrend_df(60)
  ctx = detectors.build_context(
    "XAU", "M5", {"M5": m5}, detectors.DetectorSettings(confluence_floor=2),
    htf_order=["H1", "M15"],
  )
  assert ctx.htf_bias == "unknown"


def test_build_context_htf_bias_unknown_when_h1_frame_too_short():
  m5 = _uptrend_df(60)
  h1 = _uptrend_df(10)  # below _MIN_PRIMARY_HTF_WARMUP_BARS
  ctx = detectors.build_context(
    "XAU", "M5", {"M5": m5, "H1": h1},
    detectors.DetectorSettings(confluence_floor=2),
    htf_order=["H1", "M15"],
  )
  assert ctx.htf_bias == "unknown"


def test_build_context_htf_bias_computed_once_h1_has_enough_bars():
  m5 = _uptrend_df(60)
  h1 = _uptrend_df(60)  # >= _MIN_PRIMARY_HTF_WARMUP_BARS
  ctx = detectors.build_context(
    "XAU", "M5", {"M5": m5, "H1": h1},
    detectors.DetectorSettings(confluence_floor=2),
    htf_order=["H1", "M15"],
  )
  assert ctx.htf_bias != "unknown"


def _technique_ctx(
  instances,
  *,
  df=None,
  bias="down",
  zone_merge_overlap=0.5,
):
  from app.analysis.engine import AnalysisContext, TimeframeAnalysis

  if df is None:
    df = _buy_rejection_df()
  tf = "M5"
  zone = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  structure = detectors.StructureSet(
    swings=[],
    bias=bias,
    levels=[],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    breaks=[],
    zones=[zone],
    liquidity_grabs=[],
  )
  ctx = detectors.DetectionContext(
    symbol="XAU",
    tf=tf,
    frames={tf: df},
    indicators={tf: _indicators(df, atr=3.0)},
    structures={tf: structure},
    htf_bias=bias,
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      zone_merge_overlap=zone_merge_overlap,
    ),
  )
  analysis = AnalysisContext(
    frames=ctx.frames,
    per_tf={
      tf: TimeframeAnalysis(
        df=df,
        atr=ctx.indicators[tf].atr,
        swings=[],
        structure="trend",
        breaks=[],
        key_levels=[],
        legs=[],
        supply_demand_zones=[],
        order_blocks=[],
        flip_zones=[],
        fvg_zones=[],
        zones=[zone],
        liquidity_pools=[],
        liquidity_grabs=[],
        momentum="neutral",
        technique_instances=instances,
      ),
    },
    htf_bias=bias,
  )
  return replace(ctx, analysis=analysis)


@pytest.mark.no_database
def test_technique_reaction_prefers_best_confluence_not_first_instance():
  from app.analysis.technique_geometry import TECHNIQUE_SD, TechniqueInstance
  from app.analysis.technique_detectors import supply_demand_technique_reaction

  df = _buy_rejection_df()
  far = TechniqueInstance(
    TECHNIQUE_SD, "buy", 106.0, 107.0, None, ("supply_demand",),
    measured={"touches": 0, "mitigated": False, "score": 4.0},
    origin_index=1,
  )
  near = TechniqueInstance(
    TECHNIQUE_SD, "buy", 101.0, 106.0, None, ("supply_demand",),
    measured={"touches": 0, "mitigated": False, "score": 10.0},
    origin_index=2,
  )
  ctx = _technique_ctx([far, near], df=df)
  result = supply_demand_technique_reaction(ctx)
  assert result is not None
  assert result.entry_zone.low == pytest.approx(101.0)
  assert result.entry_zone.high == pytest.approx(106.0)


@pytest.mark.no_database
def test_technique_reaction_covers_instance_with_band_builder_overlap():
  from unittest.mock import patch

  from app.analysis.confluence_zone import ConfluenceBand
  from app.analysis.technique_geometry import TECHNIQUE_SD, TechniqueInstance
  from app.analysis.technique_detectors import supply_demand_technique_reaction

  instance = TechniqueInstance(
    TECHNIQUE_SD, "buy", 101.0, 106.0, None, ("supply_demand",),
    measured={"touches": 0, "mitigated": False, "score": 10.0},
    origin_index=1,
  )
  ctx = _technique_ctx([instance], zone_merge_overlap=0.3)
  band = ConfluenceBand(
    low=101.0,
    high=106.0,
    side="buy",
    technique_tags=(TECHNIQUE_SD, "order_block"),
    zone_id="test-band",
    provenance=(instance.instance_id,),
  )
  seen: list[float] = []

  def _capture(_band, _inst, *, min_overlap=0.5):
    seen.append(min_overlap)
    return False

  with patch(
    "app.analysis.technique_detectors._confluence_bands_for_ctx",
    return_value=[band],
  ), patch(
    "app.analysis.technique_detectors.confluence_band_covers_instance",
    side_effect=_capture,
  ):
    supply_demand_technique_reaction(ctx)
  assert seen == [0.3]


def test_mad_cannot_add_a_discrete_star_to_v1_confluence():
  """§8: MAD must not blindly create a whole star. Old v1 bug:
  ``if mad_bonus >= 0.1: confluence_v1 += 1`` — direction-blind, so even a
  MANIP snapshot pointing the WRONG way for this BUY setup would have
  bumped a 2-star Break & Retest to 3 stars. confluence_v1 must be
  identical whether or not a strong, confident MAD phase is attached.
  """
  baseline_ctx = _break_retest_ctx()
  baseline = detectors.break_retest(baseline_ctx)
  assert baseline is not None
  assert baseline.direction == "BUY"

  mad_snapshot = {
    "phase": "manip",
    "range_quality_atr": None,
    "price_vs_asia": "above",
    "sweep_side": "high",
    "reclaim": True,
    "reason_code": "asia_sweep_reclaim",
    "measured": {},
    "asia": None,
    # High-sweep manipulation is SELL-aligned — the opposite of this BUY
    # setup — yet the v1 bug applied its bonus regardless of direction.
    "manipulation_direction": "SELL",
    "expansion_direction": None,
    "confidence": 0.95,
    "mad_version": 2,
  }
  mad_ctx = replace(baseline_ctx, mad_phase="manip", mad=mad_snapshot)
  with_mad = detectors.break_retest(mad_ctx)
  assert with_mad is not None
  assert with_mad.confluence_v1 == baseline.confluence_v1
  assert with_mad.confluence_v1 == 2

  # A perfectly direction/strategy-aligned MANIP snapshot must still leave
  # v1 untouched — v1 is fully MAD-blind by design, not merely direction-gated.
  aligned_snapshot = dict(mad_snapshot, manipulation_direction="BUY")
  aligned_ctx = replace(baseline_ctx, mad_phase="manip", mad=aligned_snapshot)
  aligned = detectors.break_retest(aligned_ctx)
  assert aligned is not None
  assert aligned.confluence_v1 == baseline.confluence_v1


def test_mad_never_pushes_confluence_v2_past_three_stars():
  """§8/§24: MAD's v2 contribution is bounded and additive to an already
  0..3-capped star scale — a maximal, perfectly-aligned affinity must never
  produce a 4th star."""
  ctx = _break_retest_ctx()
  mad_snapshot = {
    "phase": "manip",
    "range_quality_atr": None,
    "price_vs_asia": "above",
    "sweep_side": "high",
    "reclaim": True,
    "reason_code": "asia_sweep_reclaim",
    "measured": {},
    "asia": None,
    "manipulation_direction": "BUY",
    "expansion_direction": None,
    "confidence": 1.0,
    "mad_version": 2,
  }
  result = detectors.break_retest(replace(ctx, mad_phase="manip", mad=mad_snapshot))
  assert result is not None
  assert result.confluence_v2 <= 3
  assert result.confluence_v1 <= 3
