from datetime import datetime, timezone
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

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


def test_from_json_normalizes_a_stale_pre_rename_strategy_name():
  """Redis candidates carry a 7d TTL and can outlive a strategy rename.

  A candidate written before "Key Level Reaction" -> "Key Level" (#507) must
  still read back as the current canonical name, not the retired one it was
  saved with.
  """
  match, reason, measured = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(), [_result(setup="Key Level")], now=NOW,
  )
  assert match is not None
  stale_json = match.to_json().replace(
    '"strategy":"Key Level"', '"strategy":"Key Level Reaction"',
  )
  reloaded = StrategyMatch.from_json(stale_json)
  assert reloaded is not None
  assert reloaded.strategy == "Key Level"


def test_scanner_transports_strongest_strategy_without_regime_routing(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_tp_pips": "30,60,90"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_max_age_seconds": 420,})

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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_tp_pips": "30,60,90"})
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


def test_insufficient_target_room_falls_through_to_configured_targets(
  monkeypatch,
):
  lower = ScalpBarrier(
    "support", 4113.0, 4112.8, 4113.2, 4, 3, 0, 18, ["micro ×4"], 9.0,
  )
  upper = ScalpBarrier(
    "resistance", 4116.0, 4115.8, 4116.2, 5, 4, 0, 17, ["micro ×5"], 10.0,
  )
  # Only ~2.5 pips of room to the opposite edge -- no target-room-selected
  # take-profit can fit inside the range.
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

  # Target-room is preference telemetry, not a hard gate (see
  # _build_strategy_match's own comment: "fall through to configured
  # strategy targets instead of refusing the match") - insufficient room
  # for the range's own scaled target must not silently drop a
  # confirmed, wick-rejection-backed setup. It still publishes, just with
  # the largest configured target standing in for the one that didn't fit.
  assert match is not None
  assert reason is None
  assert match.full_take_profit_pips == max(match.targets_pips)


@pytest.mark.asyncio
async def test_scanner_syncs_and_clears_strategy_match(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_max_age_seconds": 420,})

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


def test_range_edge_identity_is_stable_across_bars_not_event_ts_derived():
  """K (partial - identity): range_edge_scalp has no structural_id, so it
  used to fall through to strategy_match_id, which folds in event_ts -
  every re-detection of the same range edge got a brand new match_id.
  Now it must resolve to the same identity across different event_ts
  values, the same way confluence_zone_id already does for Zone Reaction.
  """
  scalp_range = _range()
  result = _result("Range Edge Scalp", mode="range_scalp")

  first, reason_a, _ = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(scalp_range=scalp_range), [result],
    now=NOW,
  )
  second, reason_b, _ = scanner._build_strategy_match(
    "XAU", "M5", "1784724900", _context(scalp_range=scalp_range), [result],
    now=NOW + 3600,
  )

  assert first is not None and reason_a is None
  assert second is not None and reason_b is None
  assert first.match_id == second.match_id


def test_range_edges_get_distinct_identities_for_the_same_range():
  """K: one range episode owns both edges, but each edge is its own setup
  - a BUY-at-lower-edge match must never collide with a SELL-at-upper-edge
  match for the same range.
  """
  scalp_range = _range()
  buy_result = _result("Range Edge Scalp", mode="range_scalp")
  sell_result = DetectionResult(
    "Range Edge Scalp",
    "SELL",
    4122.0,
    Zone(4121.8, 4122.2, "supply", score=8.0),
    4121.9,
    3,
    ["upper barrier rejection"],
    mode="range_scalp",
    confirmation="wick_rejection",
  )

  buy_match, buy_reason, _ = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(scalp_range=scalp_range),
    [buy_result], now=NOW,
  )
  sell_match, sell_reason, _ = scanner._build_strategy_match(
    "XAU", "M5", "1784721300", _context(scalp_range=scalp_range),
    [sell_result], now=NOW,
  )

  assert buy_match is not None and buy_reason is None
  assert sell_match is not None and sell_reason is None
  assert buy_match.match_id != sell_match.match_id
