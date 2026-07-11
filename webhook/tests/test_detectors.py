from dataclasses import replace
from typing import Callable

import pandas as pd
import pytest

from app import detectors
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


@pytest.mark.parametrize(("detector", "ctx_factory", "_setup"), SETUPS)
def test_named_setup_returns_none_in_recent_mid_range(detector, ctx_factory, _setup):
  mid = _df([
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
  ])
  ctx = ctx_factory()
  ctx = replace(ctx, frames={"M5": mid}, indicators={"M5": _indicators(mid)})

  assert detector(ctx) is None


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


def test_detectors_module_has_no_delivery_or_redis_imports():
  assert not hasattr(detectors, "redis_state")
  assert not hasattr(detectors, "send_with_retry")
  assert not hasattr(detectors, "broadcast_entry")
  assert not hasattr(detectors, "store_manual_signal")
