"""First-class structural reaction detectors and identity."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from app.analysis import detectors
from app.analysis.structural_reaction_support import (
  STRUCTURAL_SETUPS,
  evaluate_structural_reaction,
  structural_thesis_id,
)
from app.analysis.structure import Level, Zone
from app.analysis.trendlines import Trendline
from app.analysis.types import Break, DealingRange, Grab, Pool, SessionLevel
from app.autotrade.execution_policy import strategy_family
from app.autotrade.multi_match import dedupe_matches, same_thesis
from app.autotrade.strategy_match import StrategyMatch


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


def _ctx(
  df: pd.DataFrame,
  *,
  bias: str = "up",
  levels: list[Level] | None = None,
  zones: list[Zone] | None = None,
  breaks: list[Break] | None = None,
  grabs: list[Grab] | None = None,
  session_levels: list[SessionLevel] | None = None,
  trendlines: list[Trendline] | None = None,
  dealing_range: DealingRange | None = None,
) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=[],
    bias=bias,
    levels=levels or [],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    breaks=breaks or [],
    zones=zones or [],
    liquidity_grabs=grabs or [],
    session_levels=session_levels or [],
    dealing_range=dealing_range,
    trendlines=trendlines or [],
  )
  return detectors.DetectionContext(
    symbol="XAU",
    tf=tf,
    frames={tf: df},
    indicators={tf: _indicators(df)},
    structures={tf: structure},
    htf_bias=bias,
    settings=detectors.DetectorSettings(confluence_floor=2),
  )


def test_default_detectors_exclude_zone_reaction():
  names = [item.__name__ for item in detectors.DEFAULT_DETECTORS]
  assert "zone_reaction" not in names
  for required in (
    "key_level_reaction",
    "demand_zone_reaction",
    "supply_zone_reaction",
    "session_level_reaction",
    "trendline_reaction",
  ):
    assert required in names


def test_demand_zone_reaction_buy():
  df = _buy_rejection_df()
  zone = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  result = detectors.demand_zone_reaction(_ctx(df, bias="down", zones=[zone]))
  assert result is not None
  assert result.setup == "Demand Zone Reaction"
  assert result.direction == "BUY"
  assert result.structural_source == "supply_demand"
  assert result.structural_kind == "demand"
  assert result.structural_id
  assert result.bias_relationship == "counter_bias"
  assert result.confirmation_type in {
    "wick_rejection", "strong_reclaim", "sweep_reclaim", "rejection_choch",
  }


def test_supply_zone_reaction_sell():
  df = _sell_rejection_df()
  zone = Zone(107, 112, "supply", source="supply_demand", score=10, touches=0)
  result = detectors.supply_zone_reaction(_ctx(df, bias="up", zones=[zone]))
  assert result is not None
  assert result.setup == "Supply Zone Reaction"
  assert result.direction == "SELL"
  assert result.structural_kind == "supply"
  assert result.bias_relationship == "counter_bias"


def test_key_level_support_buy_and_resistance_sell():
  buy_df = _buy_rejection_df()
  support = Level(105, "support", touches=3, strength=3)
  buy = detectors.key_level_reaction(
    _ctx(buy_df, bias="down", levels=[support]),
  )
  assert buy is not None
  assert buy.setup == "Key Level Reaction"
  assert buy.direction == "BUY"
  assert buy.structural_source == "key_level"

  sell_df = _sell_rejection_df()
  resistance = Level(107, "resistance", touches=3, strength=3)
  sell = detectors.key_level_reaction(
    _ctx(sell_df, bias="up", levels=[resistance]),
  )
  assert sell is not None
  assert sell.direction == "SELL"


def test_session_level_pdl_buy_and_pdh_sell():
  buy_df = _buy_rejection_df()
  pdl = SessionLevel("PDL", 105.0, buy_df.index[-1], swept=False)
  buy = detectors.session_level_reaction(
    _ctx(buy_df, bias="range", session_levels=[pdl]),
  )
  assert buy is not None
  assert buy.setup == "Session Level Reaction"
  assert buy.direction == "BUY"
  assert buy.structural_kind == "PDL"

  sell_df = _sell_rejection_df()
  pdh = SessionLevel("PDH", 107.0, sell_df.index[-1], swept=False)
  sell = detectors.session_level_reaction(
    _ctx(sell_df, bias="range", session_levels=[pdh]),
  )
  assert sell is not None
  assert sell.direction == "SELL"
  assert sell.structural_kind == "PDH"


def test_trendline_unbroken_support_and_resistance():
  buy_df = _buy_rejection_df()
  support = Trendline(
    "support", (0, 2, 4), 0.0, 105.0, touches=3, broken=False, break_index=None,
  )
  buy = detectors.trendline_reaction(
    _ctx(buy_df, bias="down", trendlines=[support]),
  )
  assert buy is not None
  assert buy.setup == "Trendline Reaction"
  assert buy.direction == "BUY"

  sell_df = _sell_rejection_df()
  resistance = Trendline(
    "resistance",
    (0, 2, 4),
    0.0,
    107.0,
    touches=3,
    broken=False,
    break_index=None,
  )
  sell = detectors.trendline_reaction(
    _ctx(sell_df, bias="up", trendlines=[resistance]),
  )
  assert sell is not None
  assert sell.direction == "SELL"


def test_broken_trendline_is_not_trendline_reaction():
  df = _buy_rejection_df()
  broken = Trendline(
    "support",
    (0, 2),
    0.0,
    105.0,
    touches=3,
    broken=True,
    break_index=2,
  )
  assert detectors.trendline_reaction(_ctx(df, trendlines=[broken])) is None


def test_no_confirmation_yields_no_match():
  # Price never revisits demand in the lookback window.
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (110, 112, 109, 111, 100),
    (111, 113, 110, 112, 100),
    (112, 114, 111, 113, 100),
  ])
  zone = Zone(101, 106, "demand", source="supply_demand", score=10)
  conf = evaluate_structural_reaction(
    df, direction="BUY", low=101, high=106, lookback_bars=3,
  )
  assert conf is None
  assert detectors.demand_zone_reaction(_ctx(df, zones=[zone])) is None


def test_touch_prior_bar_confirmation_within_lookback():
  # Touch on bar -2 (deep into demand), confirmation on last bar.
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 100.5, 103, 100),  # touch demand
    (106, 110, 101, 109, 100),  # bullish rejection confirmation
  ])
  conf = evaluate_structural_reaction(
    df,
    direction="BUY",
    low=100,
    high=106,
    lookback_bars=3,
  )
  assert conf is not None
  assert conf.touch_index <= conf.confirmation_index
  assert conf.confirmation_type
  # Prior bar also touched; confirmation may land on the same closed bar.
  assert any(
    conf.touch_index == idx or idx < conf.confirmation_index
    for idx in range(max(0, len(df) - 3), len(df))
  )
  zone = Zone(100, 106, "demand", source="supply_demand", score=10)
  result = detectors.demand_zone_reaction(
    replace(
      _ctx(df, zones=[zone]),
      settings=detectors.DetectorSettings(
        confluence_floor=2,
        structural_reaction_lookback_bars=3,
        max_entry_atr=5.0,
      ),
    )
  )
  assert result is not None
  assert result.setup == "Demand Zone Reaction"


def test_confirmation_older_than_lookback_rejected():
  df = _df([
    (106, 110, 101, 109, 100),  # old rejection
    (109, 111, 108, 110, 100),
    (110, 112, 109, 111, 100),
    (111, 113, 110, 112, 100),
    (112, 114, 111, 113, 100),
  ])
  conf = evaluate_structural_reaction(
    df,
    direction="BUY",
    low=101,
    high=106,
    lookback_bars=2,
  )
  assert conf is None


def test_strategy_family_and_stable_thesis_identity():
  assert strategy_family("Key Level Reaction") == "key_level"
  assert strategy_family("Demand Zone Reaction") == "supply_demand"
  assert strategy_family("Supply Zone Reaction") == "supply_demand"
  assert strategy_family("Session Level Reaction") == "session_level"
  assert strategy_family("Trendline Reaction") == "trendline"
  assert strategy_family("Mapped Zone Reaction") == "mapped_zone_reaction"

  first = structural_thesis_id(
    symbol="XAU",
    strategy="Demand Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="abc",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  moved_entry = structural_thesis_id(
    symbol="XAU",
    strategy="Demand Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="abc",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  other = structural_thesis_id(
    symbol="XAU",
    strategy="Demand Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="xyz",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  assert first == moved_entry
  assert first != other


def _match(**kwargs) -> StrategyMatch:
  base = dict(
    version=1,
    match_id="m1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-10T00:00:00+00:00",
    issued_at=1,
    expires_at=1000,
    strategy="Demand Zone Reaction",
    strategy_mode="counter_bias",
    direction="BUY",
    key_level=105.0,
    entry_low=104.0,
    entry_high=106.0,
    current_price=105.5,
    confluence=3,
    reasons=("demand",),
    atr=2.0,
    structure_swing=104.0,
    targets_pips=(30, 60),
    family="supply_demand",
    structural_source="supply_demand",
    zone_id="sid-1",
    structural_zone_id="sid-1",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  base.update(kwargs)
  return StrategyMatch(**base)


def test_cross_strategy_dedup_prefers_first_class():
  demand = _match(match_id="demand")
  pullback = _match(
    match_id="pullback",
    strategy="Trend Pullback",
    family="trend_pullback",
    structural_source="Trend Pullback",
    zone_id="float:104:106",
    structural_zone_id=None,
    confirmation_bar_ts="c1",
  )
  assert same_thesis(demand, pullback, atr=2.0)
  kept, events = dedupe_matches([pullback, demand], atr=2.0)
  assert len(kept) == 1
  assert kept[0].strategy == "Demand Zone Reaction"
  assert any(item["event"] == "merged_confluence" for item in events)


def test_independent_sources_remain_separate():
  demand = _match(match_id="d", structural_zone_id="demand-1", zone_id="demand-1")
  key = _match(
    match_id="k",
    strategy="Key Level Reaction",
    family="key_level",
    structural_source="key_level",
    structural_zone_id="key-1",
    zone_id="key-1",
    entry_low=110.0,
    entry_high=112.0,
    key_level=111.0,
  )
  assert not same_thesis(demand, key, atr=2.0)
  kept, _ = dedupe_matches([demand, key], atr=2.0)
  assert len(kept) == 2


def test_structural_setups_constant():
  assert STRUCTURAL_SETUPS == {
    "Key Level Reaction",
    "Demand Zone Reaction",
    "Supply Zone Reaction",
    "Session Level Reaction",
    "Trendline Reaction",
  }
