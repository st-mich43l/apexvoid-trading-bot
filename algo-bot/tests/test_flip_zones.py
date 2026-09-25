from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analysis.market_map import build_map
from app.analysis.engine import AnalysisSettings
from app.analysis.types import Break, DealingRange, Level
from app.analysis import zones as zones_module
from app.analysis.zones import flip_zones
from tests.configuration.canonical_fixtures import market_map_cfg

pytestmark = pytest.mark.no_database


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range(
    "2026-08-31T08:00:00Z", periods=len(rows), freq="5min",
  )
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  ).assign(volume=100)


def _break(df: pd.DataFrame, index: int, direction: str = "up") -> Break:
  return Break(
    "bos",
    direction,
    100.0,
    index,
    df.index[index],
  )


def test_flip_break_requires_acceptance_run():
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 102.0, 99.0, 99.5),
  ])
  events = []

  zones = flip_zones(
    [Level(100.0, band=2.0)],
    [_break(df, 1)],
    df,
    accept_bars=2,
    max_break_age_bars=None,
    metric_sink=lambda name, symbol, labels: events.append((name, symbol, labels)),
    symbol="XAUUSD",
    timeframe="M5",
  )

  assert zones == []
  assert events == [("flip_zone_break_not_accepted", "XAUUSD", {"tf": "M5"})]


def test_flip_break_acceptance_run_mints_one_zone():
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
  ])

  zones = flip_zones(
    [Level(100.0, band=2.0)],
    [_break(df, 1)],
    df,
    accept_bars=2,
    max_break_age_bars=None,
  )

  assert len(zones) == 1
  assert zones[0].side == "demand"


def test_demand_flip_band_is_anchored_at_level():
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
  ])
  level = Level(100.0, band=2.0)

  zone = flip_zones([level], [_break(df, 1)], df, max_break_age_bars=None)[0]

  assert zone.bottom >= level.price
  assert zone.top > level.price


def test_supply_flip_band_is_anchored_at_level():
  df = _frame([
    (102.0, 103.0, 101.0, 102.0),
    (100.0, 101.0, 98.0, 99.0),
    (99.0, 100.0, 97.0, 98.0),
  ])
  level = Level(100.0, band=2.0)

  zone = flip_zones(
    [level],
    [_break(df, 1, "down")],
    df,
    max_break_age_bars=None,
  )[0]

  assert zone.top <= level.price
  assert zone.bottom < level.price


def test_zero_level_band_uses_break_body_width_floor():
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 105.0, 99.0, 104.0),
  ])

  zone = flip_zones(
    [Level(100.0, band=0.0)],
    [_break(df, 1)],
    df,
    accept_bars=1,
    max_break_age_bars=None,
    band_body_fraction=0.5,
  )[0]

  assert zone.top - zone.bottom == pytest.approx(2.0)


def test_old_flip_break_is_expired():
  df = _frame([(99.0, 102.0, 98.0, 101.0)] * 200)
  events = []

  zones = flip_zones(
    [Level(100.0, band=2.0)],
    [_break(df, 0)],
    df,
    accept_bars=1,
    max_break_age_bars=48,
    metric_sink=lambda name, symbol, labels: events.append((name, symbol, labels)),
    symbol="XAUUSD",
    timeframe="M5",
  )

  assert zones == []
  assert events == [("flip_zone_break_expired", "XAUUSD", {"tf": "M5"})]


def test_flip_break_on_final_bar_has_no_provisional_zone():
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
  ])
  events = []

  zones = flip_zones(
    [Level(100.0, band=2.0)],
    [_break(df, 1)],
    df,
    accept_bars=2,
    max_break_age_bars=None,
    metric_sink=lambda name, symbol, labels: events.append((name, symbol, labels)),
    symbol="XAUUSD",
    timeframe="M5",
  )

  assert zones == []
  assert events == [("flip_zone_break_not_accepted", "XAUUSD", {"tf": "M5"})]


def test_flip_zone_anchor_violation_is_rejected_without_repair(monkeypatch):
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
  ])
  events = []
  real_zone = zones_module.Zone

  def invalid_zone(**kwargs):
    return real_zone(
      bottom=kwargs["bottom"] - 1.0,
      top=kwargs["top"],
      side=kwargs["side"],
      origin_index=kwargs["origin_index"],
      created_ts=kwargs["created_ts"],
      source=kwargs["source"],
      break_kind=kwargs["break_kind"],
      break_index=kwargs["break_index"],
    )

  monkeypatch.setattr(zones_module, "Zone", invalid_zone)
  zones = flip_zones(
    [Level(100.0, band=2.0)],
    [_break(df, 1)],
    df,
    max_break_age_bars=None,
    metric_sink=lambda name, symbol, labels: events.append((name, symbol, labels)),
    symbol="XAUUSD",
    timeframe="M5",
  )

  assert zones == []
  assert events == [("flip_zone_anchor_violation", "XAUUSD", {"tf": "M5"})]


def test_flip_zone_keeps_market_map_flip_and_breakout_retest_tags():
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
  ])
  zone = replace(
    flip_zones(
      [Level(100.0, band=2.0)],
      [_break(df, 1)],
      df,
      max_break_age_bars=None,
    )[0],
    score=10.0,
  )
  per_tf = SimpleNamespace(
    df=df,
    atr=pd.Series([1.0] * len(df), index=df.index),
    zones=[zone],
    key_levels=[],
    session_levels=[],
    trendlines=[],
    scalp_barriers=[],
    scalp_range=None,
    box_break=None,
    regime=None,
    structure="range",
    momentum="neutral",
  )
  ctx = SimpleNamespace(
    per_tf={"M5": per_tf},
    htf_bias="up",
    dealing_range=DealingRange(110.0, 90.0, 100.0, 0.5, "eq"),
    regime=SimpleNamespace(range_low=90.0, range_high=110.0),
  )

  market_map = build_map(ctx, 103.0, market_map_cfg(map_min_per_side=0))

  tags = {tag for entry in market_map.buys for tag in entry.tags}
  assert {"flip", "breakout-retest"} <= tags


def test_engine_flip_acceptance_inherits_breakout_setting(monkeypatch):
  import app.analysis.engine as engine_module

  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
  ])
  captured = {}

  def spy(levels, breaks, frame, **kwargs):
    captured.update(kwargs)
    return []

  monkeypatch.setattr(engine_module, "flip_zones", spy)
  engine_module.analyze(
    {"M5": df},
    AnalysisSettings(
      breakout_accept_bars=3,
      flip_zone_max_break_age_bars=7,
      flip_band_body_fraction=0.75,
    ),
    htf_order=[],
    symbol="XAUUSD",
  )

  assert captured["accept_bars"] == 3
  assert captured["max_break_age_bars"] == 7
  assert captured["band_body_fraction"] == 0.75
  assert captured["symbol"] == "XAUUSD"
  assert captured["timeframe"] == "M5"


def _assert_synthetic_live_shape_rejected(symbol: str) -> None:
  """Use a synthetic single-close break/retest shape, not reconstructed OHLC."""
  df = _frame([
    (98.0, 99.0, 97.0, 98.0),
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 102.0, 99.0, 99.5),
  ])
  events = []
  zones = flip_zones(
    [Level(100.0, band=2.0)],
    [_break(df, 1)],
    df,
    accept_bars=2,
    max_break_age_bars=None,
    metric_sink=lambda name, symbol_name, labels: events.append(name),
    symbol=symbol,
    timeframe="M5",
  )

  assert zones == []
  assert events == ["flip_zone_break_not_accepted"]


def test_live_flip_zone_xau_2026_08_31():
  """Synthetic XAU shape: one-close break followed by a lower-band retest."""
  _assert_synthetic_live_shape_rejected("XAUUSD")


def test_live_flip_zone_gbpusd_2026_08_31():
  """Synthetic GBPUSD shape: one-close break followed by a lower-band retest."""
  _assert_synthetic_live_shape_rejected("GBPUSD")


def test_live_flip_zone_eurusd_2026_08_31():
  """Synthetic EURUSD shape: one-close break followed by a lower-band retest."""
  _assert_synthetic_live_shape_rejected("EURUSD")
