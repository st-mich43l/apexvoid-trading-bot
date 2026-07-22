from dataclasses import replace
from typing import Callable

import pandas as pd
import pytest

from app.analysis.engine import Regime
from app.analysis import detectors
from app.analysis.types import Break, DealingRange, Grab, Pool, SessionLevel
from app.analysis.regime import BoxBreak
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange
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


def _trend_pullback_ctx() -> detectors.DetectionContext:
  df = _buy_rejection_df()
  return _ctx(
    df,
    levels=[Level(105, "reaction")],
    zones=[Zone(103, 105, "demand", source="order_block")],
  )


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
  (detectors.trend_pullback, _trend_pullback_ctx, "Trend Pullback"),
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
    (detectors.trend_pullback, _trend_pullback_ctx),
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


def test_broken_supply_zone_is_rejected():
  df = _df([
    (110, 112, 108, 110, 100),
    (109, 110, 101, 102, 100),
    (102, 107, 100, 106, 100),
    (106, 108, 104, 107, 100),
    (107, 112, 101, 103, 100),
  ])
  ctx = _ctx(
    df,
    bias="down",
    levels=[Level(102, "reaction")],
    zones=[Zone(100, 102, "supply", source="order_block")],
  )

  assert detectors.trend_pullback(ctx) is None


def test_confirmation_rejection_is_required():
  no_rejection = _ctx(
    _no_rejection_df(),
    levels=[Level(105, "reaction")],
    zones=[Zone(103, 105, "demand", source="order_block")],
  )
  confirmed = _trend_pullback_ctx()

  assert detectors.trend_pullback(no_rejection) is None
  assert detectors.trend_pullback(confirmed) is not None


def test_trend_pullback_prefers_best_scored_zone_over_nearest_zone():
  df = _buy_rejection_df()
  ctx = _ctx(
    df,
    zones=[
      Zone(
        106,
        107,
        "demand",
        source="bullish_fvg",
        score=2,
        score_reasons=["FVG"],
      ),
      Zone(
        103,
        105,
        "demand",
        source="order_block",
        break_kind="BOS",
        score=9,
        score_reasons=["fresh", "OB", "HTF zone"],
      ),
    ],
  )

  result = detectors.trend_pullback(ctx)

  assert result is not None
  assert result.entry_zone.low == 103
  assert result.entry_zone.high == 105
  assert result.confluence == 2
  assert result.reasons[1:4] == ["fresh", "OB", "HTF zone"]


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


def test_live_spot_is_used_for_entry_validation():
  df = _sell_rejection_df()
  base = _ctx(
    df,
    bias="down",
    zones=[Zone(104, 106, "supply", source="order_block")],
  )
  live_wrong_side = replace(base, spot_price=107.0)

  assert detectors.trend_pullback(base) is not None
  assert detectors.trend_pullback(live_wrong_side) is None


def test_star_score_remap_and_mitigated_cap():
  fresh_two = Zone(100, 101, "demand", score=9, touches=0)
  fresh_three = Zone(100, 101, "demand", score=13, touches=0)
  mitigated = Zone(100, 101, "demand", score=13, touches=1)

  assert detectors._confluence_from_zone(fresh_two, []) == 2
  assert detectors._confluence_from_zone(fresh_three, []) == 3
  assert detectors._confluence_from_zone(mitigated, []) == 2


def test_confluence_rubric_is_shared_across_detectors():
  """Two different detectors observing the same factor set must derive the
  same confluence - the rubric, not the calling detector, is the source of
  truth (B2). Exercised through the real _finish/_confluence_from_zone path
  each of the 8 DEFAULT_DETECTORS funnels through.
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
    9,
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
    9,
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


def _counter_ctx(
  *,
  zone: Zone | None = None,
  zones: list[Zone] | None = None,
  levels: list[Level] | None = None,
  breaks: list[Break] | None = None,
  grabs: list[Grab] | None = None,
  session_levels: list[SessionLevel] | None = None,
  position: float = 0.2,
  allow: bool = True,
) -> detectors.DetectionContext:
  df = _buy_rejection_df()
  ctx = _ctx(
    df,
    bias="down",
    zones=zones if zones is not None else ([zone] if zone is not None else []),
    levels=levels,
    breaks=breaks,
    grabs=grabs,
    session_levels=session_levels,
    dealing_range=DealingRange(high=150, low=100, eq=125, position=position, zone="discount"),
  )
  return replace(
    ctx,
    settings=replace(ctx.settings, allow_counter_trend=allow),
  )


def test_counter_reaction_requires_fresh_scored_zone_sweep_and_extreme_pd():
  df = _buy_rejection_df()
  zone = Zone(
    106,
    110,
    "demand",
    source="supply_demand",
    score=11,
    score_reasons=["fresh", "S/D", "sweep A"],
  )
  grab = Grab(Pool("sell", 107, 0.1, 2), 4, "bull", df.index[4], "A")

  result = detectors.zone_reaction(_counter_ctx(zone=zone, grabs=[grab]))

  assert result is not None
  assert result.setup == "Zone Reaction"
  assert result.direction == "BUY"
  assert result.mode == "counter_reaction"
  assert "sweep A" in result.reasons
  assert "PD 0.20" in result.reasons

  assert detectors.zone_reaction(_counter_ctx(zone=replace(zone, touches=1), grabs=[grab])) is None
  assert detectors.zone_reaction(_counter_ctx(zone=zone, grabs=[])) is None
  assert detectors.zone_reaction(_counter_ctx(zone=zone, grabs=[grab], position=0.4)) is None
  assert detectors.zone_reaction(_counter_ctx(zone=zone, grabs=[grab], allow=False)) is None


def test_chop_zone_reaction_requires_edge_and_grade_a_sweep():
  df = _buy_rejection_df()
  edge_zone = Zone(
    101,
    103,
    "demand",
    source="supply_demand",
    score=11,
    score_reasons=["fresh", "S/D"],
  )
  edge_grab = Grab(Pool("sell", 102, 0.1, 2), 4, "bull", df.index[4], "A")
  edge_ctx = replace(
    _counter_ctx(zone=edge_zone, grabs=[edge_grab]),
    regime=_chop_regime(100, 112),
  )

  result = detectors.zone_reaction(edge_ctx)

  assert result is not None
  assert result.direction == "BUY"
  assert "sweep A" in result.reasons
  assert "range 100-112" in result.reasons
  assert "TP anchor range high 112" in result.reasons

  mid_zone = replace(edge_zone, bottom=105, top=107)
  mid_ctx = replace(
    _counter_ctx(
      zone=mid_zone,
      grabs=[Grab(Pool("sell", 106, 0.1, 2), 4, "bull", df.index[4], "A")],
    ),
    regime=_chop_regime(100, 112),
  )
  assert detectors.zone_reaction(mid_ctx) is None

  grade_b_ctx = replace(
    _counter_ctx(
      zone=edge_zone,
      breaks=[Break("CHoCH", "up", 108, 4, df.index[4])],
      grabs=[Grab(Pool("sell", 102, 0.1, 2), 4, "bull", df.index[4], "B")],
    ),
    regime=_chop_regime(100, 112),
  )
  legacy = replace(
    grade_b_ctx,
    settings=replace(grade_b_ctx.settings, chop_filter_enabled=False),
  )
  assert detectors.zone_reaction(legacy) is not None
  assert detectors.zone_reaction(grade_b_ctx) is None


def test_counter_swing_requires_fresh_htf_order_block():
  df = _buy_rejection_df()
  zone = Zone(
    106,
    110,
    "demand",
    source="order_block",
    sources=["order_block"],
    score=13,
    score_reasons=["fresh", "OB", "HTF zone"],
    break_kind="BOS",
  )
  grab = Grab(Pool("sell", 107, 0.1, 2), 4, "bull", df.index[4], "A")

  result = detectors.zone_reaction(_counter_ctx(zone=zone, grabs=[grab]))

  assert result is not None
  assert result.mode == "counter_swing"
  assert "fresh HTF OB" in result.reasons
  assert any(reason.startswith("TP anchor EQ") for reason in result.reasons)


def test_counter_level_reaction_from_strong_bare_key_level_only_scalps():
  df = _buy_rejection_df()
  grab = Grab(Pool("sell", 105, 0.1, 2), 4, "bull", df.index[4], "A")

  result = detectors.zone_reaction(
    _counter_ctx(levels=[Level(105, "reaction", touches=4, band=0.2)], grabs=[grab])
  )

  assert result is not None
  assert result.mode == "counter_reaction"
  assert "key 105 x4" in result.reasons
  assert result.entry_zone.source == "level"

  weak = detectors.zone_reaction(
    _counter_ctx(levels=[Level(105, "reaction", touches=2, band=0.2)], grabs=[grab])
  )
  assert weak is None


def test_counter_unswept_session_level_reaction():
  df = _buy_rejection_df()
  ts = df.index[-2]
  grab = Grab(Pool("sell", 105, 0.1, 2), 4, "bull", df.index[4], "A")

  result = detectors.zone_reaction(
    _counter_ctx(
      grabs=[grab],
      session_levels=[SessionLevel("PDL", 105, ts, swept=False)],
    )
  )

  assert result is not None
  assert result.mode == "counter_reaction"
  assert "PDL" in result.reasons


def test_unbroken_trendline_support_fires_scalp_reaction_after_grade_a_sweep():
  df = _buy_rejection_df()
  line = Trendline("support", (0, 2, 4), 0.0, 105.0, 3, False, None)
  grab = Grab(Pool("sell", 105, 0.1, 3), 4, "bull", df.index[4], "A")
  ctx = _ctx(
    df,
    bias="down",
    grabs=[grab],
    trendlines=[line],
    dealing_range=DealingRange(150, 100, 125, 0.2, "discount"),
  )

  result = detectors.zone_reaction(ctx)

  assert result is not None
  assert result.mode == "counter_reaction"
  assert result.entry_zone.source == "trendline"
  assert "TL support ×3" in result.reasons
  broken = replace(line, broken=True, break_index=3)
  broken_st = replace(ctx.structures["M5"], trendlines=[broken])
  assert detectors.zone_reaction(
    replace(ctx, structures={"M5": broken_st})
  ) is None


def test_trendline_break_retest_fires_outside_chop_only():
  df = _buy_rejection_df()
  line = Trendline("resistance", (0, 1, 2), 0.0, 105.0, 3, True, 3)
  ctx = _ctx(df, trendlines=[line])

  result = detectors.break_retest(ctx)

  assert result is not None
  assert result.setup == "Break & Retest"
  assert "TL break+retest" in result.reasons
  assert result.entry_zone.source == "trendline"
  assert detectors.break_retest(replace(ctx, regime=_chop_regime())) is None


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
