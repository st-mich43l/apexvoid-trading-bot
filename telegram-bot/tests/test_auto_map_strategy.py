from types import SimpleNamespace

import pandas as pd
import pytest

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


def _map(
  *entries: MapEntry,
  bias: str = "down",
  price: float = 4149.0,
  eq: float = 4149.0,
  box_low: float = 4144.0,
  box_high: float = 4153.0,
) -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=price,
    eq=eq,
    box_low=box_low,
    box_high=box_high,
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


def _cfg(**overrides) -> SimpleNamespace:
  values = {
    "auto_trade_market_map_strategy_enabled": True,
    "auto_trade_max_entry_distance_pips": 10,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_tp_pips": "30,60,90,120,200",
    "auto_trade_map_zone_min_width_atr": 0.15,
    "auto_trade_map_zone_min_width_abs": 1.0,
    "auto_trade_map_counter_bias_enabled": True,
    "auto_trade_map_counter_bias_min_score": 6.0,
    "auto_trade_map_counter_bias_min_confluence": 2,
    "atr_length": 14,
    "proximal_band_atr": 0.5,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


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
  cfg = _cfg(
    auto_trade_tp_pips="30,60,90",
    auto_trade_map_zone_min_width_abs=0.3,
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
    _cfg(auto_trade_map_zone_min_width_abs=0.3),
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
    _cfg(auto_trade_map_zone_min_width_abs=0.3),
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


def test_degenerate_zone_is_filtered_and_warned(caplog):
  entry = MapEntry(
    "sell",
    4102.10,
    4102.13,
    4102,
    4103,
    "zone",
    ["supply", "fresh"],
    8.0,
  )

  with caplog.at_level("WARNING", logger=map_strategy.__name__):
    selected, state, reasons = map_strategy._select_reaction(
      _map(entry),
      _m1_bar(high=4102.2, low=4101.8, close=4101.9),
      4102.0,
      3.0,
      0.5,
      _cfg(),
    )

  assert selected is None
  assert state == "waiting_for_zone"
  assert "degenerate_width=1" in reasons[0]
  assert "lo=4102.10000" in caplog.text
  assert "hi=4102.13000" in caplog.text
  assert "tier=zone" in caplog.text
  assert "tags=['supply', 'fresh']" in caplog.text
  assert "score=8.00" in caplog.text


def test_normal_zone_and_inclusive_width_threshold_are_actionable():
  normal = MapEntry(
    "sell", 4087.0, 4095.0, 4087, 4095,
    "zone", ["supply", "fresh"], 8.0,
  )
  exact = MapEntry(
    "sell", 4100.0, 4101.0, 4100, 4101,
    "zone", ["supply"], 6.0,
  )

  assert map_strategy._actionable(normal, 3.0, _cfg())
  assert map_strategy._actionable(exact, 3.0, _cfg())


def test_unreachable_zone_reports_distance_limit_and_filters():
  far = MapEntry(
    "sell", 4087.0, 4095.0, 4087, 4095,
    "zone", ["supply", "fresh"], 8.0,
  )

  selected, state, reasons = map_strategy._select_reaction(
    _map(far),
    _m1_bar(high=4073.2, low=4071.9, close=4072.88),
    4072.88,
    3.0,
    0.5,
    _cfg(),
  )

  assert selected is None
  assert state == "waiting_for_touch"
  assert "no mapped SELL zone within reach" in reasons[0]
  assert "at 14.1 price" in reasons[0]
  assert "1.5×ATR = 4.5" in reasons[0]
  assert "side=0" in reasons[0]
  assert "actionable=0" in reasons[0]
  assert "degenerate_width=0" in reasons[0]
  assert "distance=1" in reasons[0]


def test_nearest_absent_from_rendered_map_is_flagged():
  live = MapEntry(
    "sell", 4087.0, 4095.0, 4087, 4095,
    "zone", ["supply", "fresh"], 8.0,
  )
  displayed = _map(
    MapEntry(
      "sell", 4108.0, 4116.0, 4108, 4116,
      "major", ["supply", "FVG"], 12.0,
    ),
  )

  _, _, reasons = map_strategy._select_reaction(
    _map(live),
    _m1_bar(high=4081.0, low=4079.0, close=4080.0),
    4080.0,
    3.0,
    0.5,
    _cfg(),
    displayed,
  )

  assert "absent from rendered Market Map" in reasons[0]


def _worked_counter_bias_map(
  *,
  tags: list[str] | None = None,
  score: float = 6.5,
  tier: str = "zone",
  include_level: bool = True,
) -> MarketMap:
  entries = [
    MapEntry(
      "buy",
      4066.0,
      4073.0,
      4066,
      4073,
      tier,
      tags or ["breaker", "demand", "FVG", "fresh"],
      score,
      contains_price=True,
    ),
  ]
  if include_level:
    entries.append(MapEntry(
      "buy",
      4065.7,
      4066.0,
      4065,
      4066,
      "level",
      ["TL support ×3", "support ×9"],
      9.0,
    ))
  entries.extend([
    MapEntry(
      "sell",
      4087.0,
      4095.0,
      4087,
      4095,
      "zone",
      ["OB", "supply", "fresh", "resistance ×9"],
      9.0,
    ),
    MapEntry(
      "sell",
      4102.10,
      4102.13,
      4102,
      4103,
      "zone",
      ["supply", "fresh"],
      8.0,
    ),
  ])
  return _map(
    *entries,
    bias="down",
    price=4072.88,
    eq=4084.0,
    box_low=4073.0,
    box_high=4095.0,
  )


def _counter_rejection_bar() -> pd.DataFrame:
  return _m1_bar(
    open_=4069.5,
    high=4073.0,
    low=4069.0,
    close=4072.88,
  )


def test_counter_bias_flag_off_keeps_opposite_zone_ignored():
  selected, state, _ = map_strategy._select_reaction(
    _worked_counter_bias_map(),
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=False),
  )

  assert selected is None
  assert state == "waiting_for_touch"


def test_worked_counter_bias_zone_produces_tagged_eq_capped_match(monkeypatch):
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([3.0]),
  )
  market_map = _worked_counter_bias_map()

  decision = map_strategy.evaluate_market_map_strategy(
    {"M1": _counter_rejection_bar()},
    symbol="XAU",
    event_ts="1784806680",
    spot_price=4072.88,
    cfg=_cfg(auto_trade_map_counter_bias_enabled=True),
    market_map=market_map,
    now=1784806680,
  )

  assert decision.state == "candidate"
  assert decision.mapped_zone == (4066.0, 4073.0)
  assert decision.match is not None
  assert decision.match.direction == "BUY"
  assert decision.match.tags == ("counter_bias",)
  assert decision.match.target_price == 4084.0
  assert decision.match.targets_pips == (30, 60, 90, 111)
  assert "counter_bias" in decision.match.reasons[0]
  assert "target capped at box EQ 4084.00" in decision.match.reasons[-1]


@pytest.mark.parametrize(
  ("tags", "score"),
  [
    (["breaker", "demand", "FVG"], 6.5),
    (["breaker", "demand", "FVG", "fresh"], 4.0),
    (["demand", "fresh"], 6.5),
  ],
)
def test_counter_bias_rejects_missing_fresh_low_score_or_confluence(
  tags,
  score,
):
  selected, state, reasons = map_strategy._select_reaction(
    _worked_counter_bias_map(
      tags=tags,
      score=score,
      include_level=False,
    ),
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=True),
  )

  assert selected is None
  assert state == "waiting_for_touch"
  assert "actionable=1" in reasons[0]


def test_nearby_trendline_level_satisfies_counter_bias_confluence():
  market_map = _worked_counter_bias_map(
    tags=["demand", "fresh"],
    include_level=True,
  )

  selected, state, _ = map_strategy._select_reaction(
    market_map,
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=True),
  )

  assert state == "candidate"
  assert selected is not None
  assert selected[0].tier == "zone"
  assert selected[1] == "BUY"


def test_counter_bias_tier_is_not_a_quality_criterion():
  market_map = _worked_counter_bias_map(tier="level", include_level=False)

  selected, state, _ = map_strategy._select_reaction(
    market_map,
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=True),
  )

  assert state == "candidate"
  assert selected is not None
  assert selected[0].tier == "level"


def test_replay_1938_filters_dead_band_then_selects_counter_bias(monkeypatch):
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([3.0]),
  )
  market_map = _worked_counter_bias_map()
  aligned_only = map_strategy.evaluate_market_map_strategy(
    {"M1": _counter_rejection_bar()},
    symbol="XAU",
    event_ts="1784806680",
    spot_price=4072.88,
    cfg=_cfg(auto_trade_map_counter_bias_enabled=False),
    market_map=market_map,
    now=1784806680,
  )
  counter_enabled = map_strategy.evaluate_market_map_strategy(
    {"M1": _counter_rejection_bar()},
    symbol="XAU",
    event_ts="1784806680",
    spot_price=4072.88,
    cfg=_cfg(auto_trade_map_counter_bias_enabled=True),
    market_map=market_map,
    now=1784806680,
  )

  assert aligned_only.state == "waiting_for_touch"
  assert "nearest 4087.00-4095.00" in aligned_only.reasons[0]
  assert "degenerate_width=1" in aligned_only.reasons[0]
  assert counter_enabled.state == "candidate"
  assert counter_enabled.match is not None
  assert counter_enabled.match.tags == ("counter_bias",)
