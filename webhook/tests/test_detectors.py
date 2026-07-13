from dataclasses import replace
from typing import Callable

import pandas as pd
import pytest

from app import detectors
from app.pa_types import DealingRange, Grab, Pool, SessionLevel
from app.structure import Level, Swing, Zone


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
  grabs: list[Grab] | None = None,
  session_levels: list[SessionLevel] | None = None,
  dealing_range: DealingRange | None = None,
  indicator_set: detectors.IndicatorSet | None = None,
) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=swings or [],
    bias=bias,
    levels=levels or [],
    equal_levels=equal_levels or [],
    fvg_zones=[],
    order_blocks=[],
    zones=zones or [],
    liquidity_grabs=grabs or [],
    session_levels=session_levels or [],
    dealing_range=dealing_range,
  )
  return detectors.DetectionContext(
    symbol="XAU",
    tf=tf,
    frames={tf: df},
    indicators={tf: indicator_set or _indicators(df)},
    structures={tf: structure},
    htf_bias=bias,
    settings=detectors.DetectorSettings(confluence_floor=2),
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


def _counter_ctx(
  *,
  zone: Zone | None = None,
  zones: list[Zone] | None = None,
  levels: list[Level] | None = None,
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


def test_detectors_module_has_no_delivery_or_redis_imports():
  assert not hasattr(detectors, "redis_state")
  assert not hasattr(detectors, "send_with_retry")
  assert not hasattr(detectors, "broadcast_entry")
  assert not hasattr(detectors, "store_manual_signal")
