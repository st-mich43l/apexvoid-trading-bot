"""Cross-instrument price geometry must preserve the strategy in pip space."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.analysis.confluence_zone import confluence_zone_id, validate_zone_width
from app.analysis.detectors import _grab_points_into_zone
from app.analysis.liquidity import _is_inducement
from app.analysis.market_map import MapEntry, MarketMap
from app.analysis.structure import entry_zone
from app.analysis.zones import score_zones
from app.analysis.types import Grab, Pool, Zone
from app.autotrade.gate import AutoScalpBox, AutoScalpRail, _m1_rail_trigger
from app.autotrade.reaction_identity import structural_zone_id
from app.autotrade.trend import _breakout_direction_and_age
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import PythonConfigurationSourcePolicy
from app.runtime.instrument_config import instrument_runtime_view
from app.scalping.context import compute_context_id, is_scalping_symbol
from app.scalping.microstructure import build_micro_structure
from app.scalping.strategies import _select_target


pytestmark = pytest.mark.no_database


@pytest.fixture(scope="module")
def runtime_root():
  config_file = Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
  policy = PythonConfigurationSourcePolicy(config_file=str(config_file))
  return load_python_canonical_settings(
    load_python_runtime_source_bundle(policy=policy),
  ).config


@pytest.mark.parametrize(
  ("symbol", "pip_size", "digits", "map_change"),
  [
    ("XAU", 0.1, 2, 1.0),
    ("EURUSD", 0.0001, 5, 0.0001),
    ("GBPJPY", 0.01, 3, 0.01),
  ],
)
def test_effective_runtime_exposes_symbol_geometry(
  runtime_root,
  symbol,
  pip_size,
  digits,
  map_change,
):
  cfg = instrument_runtime_view(symbol, runtime_root)

  assert cfg.units.pip_size == pytest.approx(pip_size)
  assert cfg.units.price_digits == digits
  assert cfg.analysis.market_map.change_min == pytest.approx(map_change)
  # XAU hosts M1 scalping even with technique structure fixed_rr.
  # FX fixed_rr books must not opt into scalp cycles.
  from app.configuration.models.instruments import InstrumentTargetMode

  if symbol.upper() == "XAU":
    assert cfg.targeting.mode is InstrumentTargetMode.FIXED_RR
    assert is_scalping_symbol(symbol, cfg)
  elif cfg.targeting.mode is InstrumentTargetMode.FIXED_RR:
    assert not is_scalping_symbol(symbol, cfg)
  else:
    assert is_scalping_symbol(symbol, cfg)


@pytest.mark.parametrize(
  ("symbol", "entry"),
  [("XAU", 4000.0), ("EURUSD", 1.08500), ("GBPJPY", 191.250)],
)
def test_target_selection_is_identical_in_pip_space(runtime_root, symbol, entry):
  cfg = instrument_runtime_view(symbol, runtime_root)
  pip_size = cfg.units.pip_size

  selected = _select_target(
    direction="BUY",
    worst_fill=entry,
    room_pips=30.0,
    stop_pips=15.0,
    min_net=15.0,
    pip_size=pip_size,
    symbol=symbol,
    cfg=cfg,
  )

  assert selected is not None
  target, target_pips = selected
  assert target_pips == pytest.approx(30.0)
  assert target == pytest.approx(entry + 30.0 * pip_size)


@pytest.mark.parametrize(
  ("symbol", "base"),
  [("XAU", 4000.0), ("EURUSD", 1.08500), ("GBPJPY", 191.250)],
)
def test_microstructure_equal_levels_follow_symbol_precision(
  runtime_root,
  symbol,
  base,
):
  cfg = instrument_runtime_view(symbol, runtime_root)
  pip_size = cfg.units.pip_size
  index = pd.date_range("2026-08-18", periods=5, freq="1min", tz="UTC")
  frame = pd.DataFrame(
    {
      "open": [base] * 5,
      "high": [
        base + 1.0 * pip_size,
        base + 5.0 * pip_size,
        base + 2.0 * pip_size,
        base + 5.3 * pip_size,
        base + 1.0 * pip_size,
      ],
      "low": [
        base - 1.0 * pip_size,
        base - 0.5 * pip_size,
        base - 2.0 * pip_size,
        base - 0.5 * pip_size,
        base - 1.0 * pip_size,
      ],
      "close": [base] * 5,
    },
    index=index,
  )

  micro = build_micro_structure(
    frame,
    swing_lookback=1,
    equal_tol=0.5 * pip_size,
    price_digits=cfg.units.price_digits,
  )

  assert len(micro.equal_highs) == 1
  assert micro.equal_highs[0] == pytest.approx(base + 5.2 * pip_size)


def test_eurusd_context_and_zone_ids_do_not_collapse_to_two_decimals():
  first_context = compute_context_id(
    "EURUSD", 1, 1.08000, 1.09000, 1.08431, 1.08600, "range", 0.0001,
  )
  second_context = compute_context_id(
    "EURUSD", 1, 1.08000, 1.09000, 1.08441, 1.08600, "range", 0.0001,
  )
  first_zone = structural_zone_id(
    "EURUSD", "BUY", 1.0838, 1.0842,
    atr=0.0005, pip_size=0.0001, tags=("demand",),
  )
  second_zone = structural_zone_id(
    "EURUSD", "BUY", 1.0858, 1.0862,
    atr=0.0005, pip_size=0.0001, tags=("demand",),
  )
  first_confluence = confluence_zone_id(
    "EURUSD", "demand", 1.0838, 1.0842, ("demand",),
    atr=0.0005, pip_size=0.0001,
  )
  second_confluence = confluence_zone_id(
    "EURUSD", "demand", 1.0858, 1.0862, ("demand",),
    atr=0.0005, pip_size=0.0001,
  )

  assert first_context != second_context
  assert first_zone != second_zone
  assert first_confluence != second_confluence


def test_fx_zone_scoring_does_not_inherit_xau_point_one_tolerance():
  zone = Zone(1.0840, 1.0843, "demand", source="supply_demand")
  far_pool = Pool("sell", 1.0800, 0.0, touches=2)
  near_pool = Pool("sell", 1.0839, 0.0, touches=2)

  far = score_zones(
    [zone], [], [far_pool], round_step=0, pip_size=0.0001,
  )[0]
  near = score_zones(
    [zone], [], [near_pool], round_step=0, pip_size=0.0001,
  )[0]

  assert "liquidity pool" not in far.score_reasons
  assert "liquidity pool" in near.score_reasons


def test_fx_retest_and_liquidity_helpers_use_one_pip_floor():
  frame = pd.DataFrame({
    "open": [1.08500],
    "high": [1.08520],
    "low": [1.08480],
    "close": [1.08510],
  })
  fallback_zone = entry_zone(
    frame, 1.08500, "BUY", pip_size=0.0001,
  )
  demand = Zone(1.0840, 1.0843, "demand")
  far_pool = Pool("sell", 1.0800, 0.0, touches=2)
  near_pool = Pool("sell", 1.0839, 0.0, touches=2)
  atr = pd.Series([0.0005])

  assert fallback_zone.low == pytest.approx(1.08490)
  assert fallback_zone.high == pytest.approx(1.08510)
  assert not _is_inducement(far_pool, [demand], atr, 0.3, 0.0001)
  assert _is_inducement(near_pool, [demand], atr, 0.3, 0.0001)
  assert not _grab_points_into_zone(
    Grab(far_pool, 1, "bull"), demand, 0.0001,
  )
  assert _grab_points_into_zone(
    Grab(near_pool, 1, "bull"), demand, 0.0001,
  )


def test_eurusd_mapped_zone_uses_fx_minimum_width(runtime_root):
  """EURUSD zone-width gate uses FX price units, not XAU dollars."""
  narrow = validate_zone_width(
    raw_width=0.00010,
    merged_width=0.00010,
    merge_sources=("supply", "fresh"),
    is_major=False,
    symbol="EURUSD",
    config=runtime_root,
  )
  wide = validate_zone_width(
    raw_width=0.00080,
    merged_width=0.00080,
    merge_sources=("supply", "fresh"),
    is_major=False,
    symbol="EURUSD",
    config=runtime_root,
  )
  assert narrow.eligible is False
  assert wide.eligible is True


@pytest.mark.parametrize(
  ("base", "pip_size"),
  [(4000.0, 0.1), (1.08500, 0.0001), (191.250, 0.01)],
)
def test_rail_trigger_and_breakout_are_scale_invariant(base, pip_size):
  lower = AutoScalpRail(
    "support",
    base - pip_size,
    base + pip_size,
    base,
    3,
    3.0,
    ("M1", "M5"),
    ("range",),
  )
  upper = AutoScalpRail(
    "resistance",
    base + 9.0 * pip_size,
    base + 10.0 * pip_size,
    base + 9.5 * pip_size,
    3,
    3.0,
    ("M1", "M5"),
    ("range",),
  )
  reaction = pd.DataFrame({
    "open": [base + pip_size],
    "high": [base + 4.0 * pip_size],
    "low": [base - pip_size],
    "close": [base + 3.0 * pip_size],
  })
  box = AutoScalpBox("box", lower, upper, 10.0)
  breakout = pd.DataFrame({
    "close": [base + 9.0 * pip_size, base + 14.0 * pip_size],
  })

  trigger = _m1_rail_trigger(reaction, lower, 10.0 * pip_size, pip_size)
  direction, age = _breakout_direction_and_age(
    breakout, box, 10.0 * pip_size, 3, pip_size,
  )

  assert trigger is not None
  assert trigger[:2] == ("BUY", "range_rejection")
  assert direction == "up"
  assert age == 0
