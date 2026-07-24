from datetime import datetime, timezone

import pandas as pd
import pytest

from app.analysis import scanner
from app.analysis.detectors import (
  DetectionContext,
  DetectionResult,
  DetectorSettings,
  IndicatorSet,
  StructureSet,
)
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange
from app.analysis.types import Zone
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
  strategy_match_key,
)
from app.persistence import redis_state


NOW = int(datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc).timestamp())


def _context(*, scalp_range: ScalpRange | None = None) -> DetectionContext:
  index = pd.date_range("2026-07-22 10:00", periods=20, freq="5min", tz="UTC")
  frame = pd.DataFrame({
    "open": [4116.0] * 20,
    "high": [4121.5] * 20,
    "low": [4113.1] * 20,
    "close": [4116.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)
  indicators = IndicatorSet(pd.Series([1.2] * 20, index=index))
  structure = StructureSet(
    swings=[],
    bias="up",
    levels=[],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    scalp_range=scalp_range,
  )
  return DetectionContext(
    symbol="XAU",
    tf="M5",
    frames={"M5": frame},
    indicators={"M5": indicators},
    structures={"M5": structure},
    htf_bias="up",
    settings=DetectorSettings(),
  )


def _result(
  setup: str = "Liquidity Sweep",
  *,
  mode: str = "with_trend",
  confluence: int = 3,
) -> DetectionResult:
  return DetectionResult(
    setup,
    "BUY",
    4113.0,
    Zone(4112.8, 4113.4, "demand", score=8.0),
    4113.2,
    confluence,
    ["sell-side liquidity swept", "bullish reclaim"],
    mode=mode,
    confirmation="sweep_reclaim",
  )


def _range() -> ScalpRange:
  lower = ScalpBarrier(
    "support", 4113.0, 4112.8, 4113.2, 4, 3, 0, 18,
    ["micro ×4"], 9.0,
  )
  upper = ScalpBarrier(
    "resistance", 4122.0, 4121.8, 4122.2, 5, 4, 0, 17,
    ["micro ×5"], 10.0,
  )
  return ScalpRange(lower, upper, 4117.5, 7.5, 9.0)


def test_strategy_match_contract_round_trips_and_rejects_wrong_version():
  match, reason, measured = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(), [_result()], now=NOW,
  )

  assert match is not None
  assert reason is None
  assert measured.get("matches", 1) >= 1
  assert StrategyMatch.from_json(match.to_json()) == match
  assert StrategyMatch.from_json("not-json") is None
  assert StrategyMatch.from_json(
    match.to_json().replace(
      f'"version":{STRATEGY_MATCH_VERSION}',
      f'"version":{STRATEGY_MATCH_VERSION + 1}',
    )
  ) is None


def test_scanner_transports_strongest_strategy_without_regime_routing(
  monkeypatch,
):
  monkeypatch.setattr(scanner.settings, "auto_trade_tp_pips", "30,60,90")
  monkeypatch.setattr(
    scanner.settings, "auto_trade_strategy_match_max_age_seconds", 420,
  )

  match, reason, measured = scanner._build_strategy_match(
    "XAU",
    "M5",
    "1784721300",
    _context(),
    [_result("Range Edge Scalp", mode="range_scalp", confluence=2), _result()],
    now=NOW,
  )

  assert match is not None
  assert reason is None
  assert match.strategy == "Liquidity Sweep"
  assert match.strategy_mode == "with_trend"
  assert match.source_tf == "M5"
  assert match.targets_pips == (30, 60, 90)
  assert match.structure_swing == 4112.8
  assert match.expires_at == NOW + 420
  assert match.match_id == strategy_match_id(
    "XAU", "M5", "1784721300", "Liquidity Sweep", "BUY", 4112.8, 4113.4,
  )


def test_range_edge_is_a_strategy_with_its_own_full_tp_plan(monkeypatch):
  monkeypatch.setattr(scanner.settings, "auto_trade_tp_pips", "30,60,90")
  match, reason, measured = scanner._build_strategy_match(
    "XAU",
    "M5",
    "1784721300",
    _context(scalp_range=_range()),
    [_result("Range Edge Scalp", mode="range_scalp")],
    now=NOW,
  )

  assert match is not None
  assert reason is None
  # 88 pips of room to the opposite edge: largest of the default
  # 20/30/40/50/70 ladder that fits with the 3-pip buffer is 70 (73 <= 88).
  assert match.strategy == "Range Edge Scalp"
  assert match.is_range_edge
  assert match.range_low == 4113.0
  assert match.range_high == 4122.0
  assert match.full_take_profit_pips == 70
  assert match.targets_pips == (70,)


def test_range_edge_selects_40_pip_target_from_40_to_49_pip_room(monkeypatch):
  # 23 Jul incident: Telegram showed a Range Edge Scalp BUY with ~40-49
  # pips of room, but no autonomous order ever opened. Root cause: the old
  # hardcoded {50,70} ladder required >=55 pips of room just to reach the
  # smallest configured target, so this room band always fell through to a
  # silent `return None` with zero telemetry. It must now select 40.
  lower = ScalpBarrier(
    "support", 4113.0, 4112.8, 4113.2, 4, 3, 0, 18, ["micro ×4"], 9.0,
  )
  # 1 pip = 0.1 price for XAU: 4118.5 -> 4123.0 is 4.5 price = 45 pips room.
  upper = ScalpBarrier(
    "resistance", 4123.0, 4122.8, 4123.2, 5, 4, 0, 17, ["micro ×5"], 10.0,
  )
  narrow_range = ScalpRange(lower, upper, 4118.0, 10.0, 9.0)
  result = DetectionResult(
    "Range Edge Scalp",
    "BUY",
    4113.0,
    Zone(4112.8, 4113.4, "demand", score=8.0),
    4118.5,
    2,
    ["sell-side liquidity swept", "bullish reclaim"],
    mode="range_scalp",
    confirmation="sweep_reclaim",
  )

  match, reason, measured = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(scalp_range=narrow_range), [result],
    now=NOW,
  )

  # 45 pips of room: 50 needs 55 (too tight), 40 needs 45 (fits exactly).
  assert match is not None
  assert reason is None
  assert match.full_take_profit_pips == 40


def test_insufficient_target_room_is_rejected_with_a_reason_not_silently(
  monkeypatch,
):
  lower = ScalpBarrier(
    "support", 4113.0, 4112.8, 4113.2, 4, 3, 0, 18, ["micro ×4"], 9.0,
  )
  upper = ScalpBarrier(
    "resistance", 4116.0, 4115.8, 4116.2, 5, 4, 0, 17, ["micro ×5"], 10.0,
  )
  # Only ~2.5 pips of room to the opposite edge -- no configured target
  # (30/40/50 with a 5-pip buffer) can ever fit.
  narrow_range = ScalpRange(lower, upper, 4114.5, 3.0, 9.0)
  result = DetectionResult(
    "Range Edge Scalp",
    "BUY",
    4113.0,
    Zone(4112.8, 4113.4, "demand", score=8.0),
    4115.75,
    2,
    ["sell-side liquidity swept", "bullish reclaim"],
    mode="range_scalp",
    confirmation="sweep_reclaim",
  )

  match, reason, measured = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(scalp_range=narrow_range), [result],
    now=NOW,
  )

  # Room to opposing edge is tiny; EQ room is also below the smallest
  # configured target + buffer (20+3), so the match stays analysis-only.
  assert match is None
  assert reason == "insufficient_target_room"
  assert measured["room_pips"] < 23


@pytest.mark.asyncio
async def test_scanner_syncs_and_clears_strategy_match(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(scanner.settings, "auto_trade_strategy_match_enabled", True)
  monkeypatch.setattr(
    scanner.settings, "auto_trade_strategy_match_max_age_seconds", 420,
  )

  match = await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721300", _context(), [_result()],
  )

  assert match is not None
  assert StrategyMatch.from_json(
    await client.get(strategy_match_key("XAU"))
  ) == match
  assert await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721600", _context(), [],
  ) is None
  assert await client.get(strategy_match_key("XAU")) is None
