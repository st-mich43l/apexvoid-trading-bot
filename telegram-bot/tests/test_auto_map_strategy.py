from types import SimpleNamespace

import pandas as pd

from app.analysis.market_map import MapEntry, MarketMap
from app.autotrade import map_strategy
from app.autotrade.strategy_match import StrategyMatch


def _m1_bar(
  *,
  open_: float = 4150.99,
  high: float = 4152.92,
  low: float = 4150.98,
  close: float = 4151.79,
) -> pd.DataFrame:
  return pd.DataFrame({
    "open": [open_],
    "high": [high],
    "low": [low],
    "close": [close],
    "volume": [500.0],
  }, index=pd.date_range("2026-07-22 14:45", periods=1, freq="1min", tz="UTC"))


def _map(*entries: MapEntry, bias: str = "down") -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=4149.0,
    eq=4149.0,
    box_low=4144.0,
    box_high=4153.0,
    bias=bias,
    bias_tf="M30",
  )


def _supply() -> MapEntry:
  return MapEntry(
    "sell",
    4152.97,
    4153.37,
    4152,
    4154,
    "zone",
    ["supply", "FVG", "fresh"],
    8.0,
  )


def test_incident_m1_rejection_matches_mapped_supply(monkeypatch):
  m1 = _m1_bar()
  frames = {tf: m1 for tf in ("M1", "M5", "M15", "M30")}
  market_map = _map(
    MapEntry("sell", 4150.0, 4150.0, 4150, 4151, "level", ["round"], 1.0),
    _supply(),
  )
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([1.8]),
  )
  cfg = SimpleNamespace(
    auto_trade_market_map_strategy_enabled=True,
    auto_trade_max_entry_distance_pips=10,
    auto_trade_strategy_match_max_age_seconds=420,
    auto_trade_tp_pips="30,60,90",
    atr_length=14,
    proximal_band_atr=0.5,
  )

  decision = map_strategy.evaluate_market_map_strategy(
    frames,
    symbol="XAU",
    event_ts="1784731500",
    spot_price=4151.79,
    cfg=cfg,
    market_map=market_map,
    now=1784731560,
  )

  assert decision.state == "candidate"
  assert decision.mapped_zone == (4152.97, 4153.37)
  assert decision.match is not None
  assert decision.match.strategy == "Mapped Zone Reaction"
  assert decision.match.strategy_mode == "mapped_zone_reaction"
  assert decision.match.direction == "SELL"
  assert decision.match.source_tf == "M1"
  assert decision.match.entry_low < 4152.97
  assert decision.match.structure_swing == 4153.37
  assert decision.match.confluence == 3
  assert StrategyMatch.from_json(decision.match.to_json()) == decision.match


def test_round_number_fallback_is_never_executable():
  round_only = _map(
    MapEntry("sell", 4150.0, 4150.0, 4150, 4151, "level", ["round"], 1.0),
  )

  selected, state, reasons = map_strategy._select_reaction(
    round_only,
    _m1_bar(high=4150.2, low=4148.8, close=4149.0),
    4149.0,
    1.8,
    0.5,
  )

  assert selected is None
  assert state == "waiting_for_zone"
  assert "no structural mapped SELL zone" in reasons[0]


def test_touch_without_m1_rejection_waits():
  selected, state, reasons = map_strategy._select_reaction(
    _map(_supply()),
    _m1_bar(open_=4151.5, high=4153.2, low=4151.4, close=4153.1),
    4153.1,
    1.8,
    0.5,
  )

  assert selected is None
  assert state == "waiting_for_reaction"
  assert "waiting for M1 rejection" in reasons[0]


def test_bias_selects_only_the_matching_side():
  buy = MapEntry(
    "buy",
    4145.0,
    4146.0,
    4145,
    4146,
    "major",
    ["demand", "OB"],
    12.0,
  )

  selected, state, _ = map_strategy._select_reaction(
    _map(buy, bias="down"),
    _m1_bar(high=4146.0, low=4144.9, close=4145.8),
    4145.8,
    1.0,
    0.5,
  )

  assert selected is None
  assert state == "waiting_for_zone"
