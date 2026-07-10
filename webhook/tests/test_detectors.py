from dataclasses import replace
from typing import Callable

import pandas as pd
import pytest

from app import detectors
from app.structure import Level, Zone


def _df(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=index,
  )


def _series(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _wae(
  df: pd.DataFrame,
  *,
  up_last: float = 2,
  up_prev: float = 1,
  down_last: float = 0,
  down_prev: float = 0,
  explosion: float = 2,
  dead_zone: float = 1,
) -> pd.DataFrame:
  up = [0.0] * len(df)
  down = [0.0] * len(df)
  if len(df) >= 2:
    up[-2], up[-1] = up_prev, up_last
    down[-2], down[-1] = down_prev, down_last
  return pd.DataFrame({
    "trend_up": up,
    "trend_down": down,
    "explosion": [explosion] * len(df),
    "dead_zone": [dead_zone] * len(df),
  }, index=df.index)


def _indicators(
  df: pd.DataFrame,
  *,
  ema_fast: float = 103,
  ema_slow: float = 102,
  atr: float = 2,
  mfi: float = 60,
  wae: pd.DataFrame | None = None,
) -> detectors.IndicatorSet:
  return detectors.IndicatorSet(
    ema_fast=_series(df, ema_fast),
    ema_slow=_series(df, ema_slow),
    atr=_series(df, atr),
    mfi=_series(df, mfi),
    bbands=pd.DataFrame(index=df.index),
    wae=wae if wae is not None else _wae(df),
  )


def _ctx(
  df: pd.DataFrame,
  *,
  bias: str = "up",
  levels: list[Level] | None = None,
  equal_levels: list[Level] | None = None,
  indicator_set: detectors.IndicatorSet | None = None,
) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=[],
    bias=bias,
    levels=levels or [],
    equal_levels=equal_levels or [],
    fvg_zones=[],
    order_blocks=[],
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


def _trend_pullback_ctx() -> detectors.DetectionContext:
  df = _df([
    (110, 120, 100, 112, 100),
    (112, 113, 107, 108, 100),
    (108, 109, 104, 105, 100),
    (105, 106, 101, 103, 100),
    (102, 104, 101, 102.2, 100),
  ])
  ind = _indicators(df, ema_fast=103, ema_slow=102.1, atr=1)
  return _ctx(df, levels=[Level(102, "reaction")], indicator_set=ind)


def _break_retest_ctx() -> detectors.DetectionContext:
  df = _df([
    (99, 120, 95, 99, 100),
    (100, 101, 98, 100, 100),
    (101, 101.5, 99, 101, 100),
    (101, 101.8, 100, 101.5, 100),
    (101.5, 104, 101.3, 103.5, 100),
    (103.5, 105, 103, 104, 100),
    (102.5, 103, 101.95, 102.1, 100),
  ])
  return _ctx(df, levels=[Level(102, "reaction")])


def _snap_back_ctx() -> detectors.DetectionContext:
  df = _df([
    (120, 130, 90, 120, 100),
    (118, 121, 116, 118, 100),
    (116, 118, 114, 116, 100),
    (114, 116, 112, 114, 100),
    (101, 103, 100, 102, 100),
  ])
  ind = _indicators(df, ema_fast=111, ema_slow=110, atr=2)
  return _ctx(df, levels=[Level(102, "reaction")], indicator_set=ind)


def _momentum_ride_ctx() -> detectors.DetectionContext:
  rows = [(110, 112, 90, 110, 100)] * 24
  rows.append((122, 130, 121, 124, 220))
  df = _df(rows)
  ind = _indicators(
    df,
    mfi=65,
    wae=_wae(df, up_last=10, up_prev=4, explosion=5, dead_zone=2),
  )
  return _ctx(df, levels=[Level(122, "reaction")], indicator_set=ind)


def _fade_scalp_ctx() -> detectors.DetectionContext:
  df = _df([
    (120, 130, 90, 120, 100),
    (110, 112, 105, 108, 100),
    (105, 106, 101, 103, 100),
    (103, 104, 100.5, 102, 100),
    (100.5, 102, 99.5, 101, 100),
  ])
  ind = _indicators(df, wae=_wae(df, up_last=1, up_prev=2))
  return _ctx(
    df,
    levels=[Level(100, "reaction")],
    equal_levels=[Level(100, "equal_low", touches=2)],
    indicator_set=ind,
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


@pytest.mark.parametrize(("detector", "ctx_factory", "setup"), SETUPS)
def test_named_setup_triggers_with_htf_aligned_context(
  detector,
  ctx_factory,
  setup,
):
  result = detector(ctx_factory())

  assert result is not None
  assert result.setup == setup
  assert result.direction == "BUY"
  assert result.confluence >= 2


@pytest.mark.parametrize(("detector", "ctx_factory", "_setup"), SETUPS)
def test_named_setup_returns_none_in_mid_range(detector, ctx_factory, _setup):
  mid = _df([
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
    (100, 110, 90, 100, 100),
  ])
  ctx = replace(ctx_factory(), frames={"M5": mid})

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


def test_detectors_module_has_no_delivery_or_redis_imports():
  assert not hasattr(detectors, "redis_state")
  assert not hasattr(detectors, "send_with_retry")
  assert not hasattr(detectors, "broadcast_entry")
  assert not hasattr(detectors, "store_manual_signal")
