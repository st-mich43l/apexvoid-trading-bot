import inspect
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade import worker
from app.core import instrument_geometry
from app.persistence import redis_state
from app.analysis import scanner
from app.autotrade.gate import AutoScalpBox, AutoScalpDecision, AutoScalpRail
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
  strategy_match_key,
)
from app.analysis.types import Level, Zone
from app.analysis.market_map import MapEntry, MarketMap


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=20, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [4016.8] * 20,
    "high": [4017.4] * 20,
    "low": [4016.2] * 20,
    "close": [4017.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)


def _decision() -> AutoScalpDecision:
  support = AutoScalpRail(
    "support",
    4016.5,
    4017.1,
    4016.8,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-low", "M15 range-low"),
  )
  resistance = AutoScalpRail(
    "resistance",
    4024.8,
    4025.4,
    4025.1,
    3,
    8.0,
    ("M5", "M15"),
    ("M5 swing-high", "M15 range-high"),
  )
  box = AutoScalpBox("xau-8034-8050", support, resistance, 77.0)
  return AutoScalpDecision(
    "candidate",
    direction="BUY",
    trigger="range_rejection",
    rail=support,
    target=resistance,
    target_room_pips=76.0,
    full_tp_pips=50,
    box=box,
    confluence=3,
    reasons=("M1 range rejection", "support rail"),
    rail_count=4,
    sweep_low=4015.9,
  )


def _strategy_match(now: int) -> StrategyMatch:
  return StrategyMatch(
    STRATEGY_MATCH_VERSION,
    strategy_match_id(
      "XAU", "M5", str(now), "Liquidity Sweep", "BUY", 4016.5, 4017.4,
    ),
    "XAU",
    "M5",
    str(now),
    now,
    now + 420,
    "Liquidity Sweep",
    "with_trend",
    "BUY",
    4016.8,
    4016.5,
    4017.4,
    4017.0,
    3,
    ("sell-side liquidity swept", "bullish reclaim"),
    1.2,
    4014.8,
    (30, 60, 90),
  )


def _range_strategy_match(now: int) -> StrategyMatch:
  return replace(
    _strategy_match(now),
    match_id=strategy_match_id(
      "XAU", "M5", str(now), "Range Edge Scalp", "BUY", 4016.5, 4017.4,
    ),
    strategy="Range Edge Scalp",
    strategy_mode="range_scalp",
    reasons=("two-sided local range", "lower-edge rejection"),
    targets_pips=(70,),
    range_id="xau-strategy-range-4016.80-4025.10",
    range_low=4016.8,
    range_high=4025.1,
    full_take_profit_pips=70,
  )


async def _seed_scanner_range_for_match(match: StrategyMatch, now: int) -> None:
  from app.autotrade.range_context import (
    RangeBarrier,
    RangeContext,
    persist_scanner_range_observation,
  )

  low = RangeBarrier(
    float(match.range_low),
    float(match.range_low) - 0.1,
    float(match.range_low) + 0.1,
    touches=3,
  )
  high = RangeBarrier(
    float(match.range_high),
    float(match.range_high) - 0.1,
    float(match.range_high) + 0.1,
    touches=3,
  )
  width = float(match.range_high) - float(match.range_low)
  context = RangeContext(
    version=1,
    range_id=str(match.range_id),
    symbol="XAU",
    state="confirmed",
    source="scanner",
    execution_timeframe="M5",
    context_timeframes=("M5",),
    lower=float(match.range_low),
    upper=float(match.range_high),
    equilibrium=(float(match.range_low) + float(match.range_high)) / 2,
    width_price=width,
    width_pips=width / 0.1,
    width_atr=width / 2.0,
    lower_barrier=low,
    upper_barrier=high,
    supports=(low,),
    resistances=(high,),
    quality=5.0,
    generated_at=now,
    expires_at=now + 660,
  )
  await persist_scanner_range_observation(
    redis_state.get_client(),
    symbol="XAU",
    context=context,
  )


@pytest.mark.asyncio
async def test_worker_ignores_forming_timeframe_and_scanner_still_ignores_m1(
  monkeypatch,
):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  assert await worker._handle_event(
    "XAU:M5:1784552400",
    client=client,
  ) is None

  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  assert await scanner._handle_event(
    "XAU:M1:1784552400",
    client=client,
  ) == []
  assert await client.xlen("auto_trade:candidates") == 0


def test_worker_source_has_no_direct_scanner_market_map_or_telegram_import():
  """Worker must not pull scanner/detectors/market_map/Telegram client.

  Catches static imports, function-body imports, importlib.import_module,
  and __import__ string references to the forbidden modules.
  """
  import ast
  from pathlib import Path

  forbidden = frozenset({
    "app.analysis.scanner",
    "app.analysis.detectors",
    "app.analysis.market_map",
    "app.bot.client",
  })
  source_path = Path(inspect.getsourcefile(worker) or worker.__file__)
  source = source_path.read_text(encoding="utf-8")
  tree = ast.parse(source, filename=str(source_path))
  found: set[str] = set()

  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        name = alias.name
        if name in forbidden or any(
          name.startswith(f"{mod}.") for mod in forbidden
        ):
          found.add(name)
    elif isinstance(node, ast.ImportFrom):
      module = node.module or ""
      if module in forbidden or any(
        module.startswith(f"{mod}.") for mod in forbidden
      ):
        found.add(module)
      # from app.bot import client
      if module == "app.bot" and any(
        alias.name == "client" for alias in node.names
      ):
        found.add("app.bot.client")
      if module == "app.analysis" and any(
        alias.name in {"scanner", "detectors", "market_map"}
        for alias in node.names
      ):
        found.add(f"app.analysis.{next(a.name for a in node.names if a.name in {'scanner', 'detectors', 'market_map'})}")
    elif isinstance(node, ast.Call):
      func = node.func
      is_import_module = (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and (
          (isinstance(func.value, ast.Name) and func.value.id == "importlib")
          or (
            isinstance(func.value, ast.Attribute)
            and func.value.attr == "importlib"
          )
        )
      )
      is_builtin_import = (
        isinstance(func, ast.Name) and func.id == "__import__"
      )
      if (is_import_module or is_builtin_import) and node.args:
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
          name = arg0.value
          if name in forbidden or any(
            name.startswith(f"{mod}.") for mod in forbidden
          ):
            found.add(name)

  # String-literal import targets (e.g. importlib.import_module("app.bot.client"))
  # already covered via AST; also fail closed on explicit "from app… import"
  # substrings that somehow evade parse (encoding tricks).
  for needle in (
    "from app.analysis.scanner",
    "from app.analysis.detectors",
    "from app.analysis.market_map",
    "from app.bot.client",
    "importlib.import_module(\"app.bot.client\")",
    "importlib.import_module('app.bot.client')",
    "__import__(\"app.bot.client\")",
    "__import__('app.bot.client')",
  ):
    if needle in source:
      found.add(needle)

  assert not found, f"worker forbidden layer imports: {sorted(found)}"


# --- A1: entry-location guard -----------------------------------------------

# --- A3: HTF supply/demand veto ---------------------------------------------

def test_htf_veto_rejects_sell_below_untested_supply_and_allows_at_supply():
  zone = Zone(4131.0, 4133.0, "supply", touches=0)

  below = worker._htf_veto_reason("SELL", 4127.18, zone)
  at_supply = worker._htf_veto_reason("SELL", 4132.0, zone)

  assert below is not None
  assert at_supply is None


def test_htf_veto_ignores_already_tested_zones():
  tested_zone = Zone(4131.0, 4133.0, "supply", touches=1)
  assert worker._htf_veto_reason("SELL", 4127.18, tested_zone) is None


@pytest.mark.no_database
def test_nearest_directional_zone_picks_supply_for_sell_demand_for_buy():
  supply = Zone(4131.0, 4133.0, "supply", touches=0)
  demand = Zone(4100.0, 4102.0, "demand", touches=0)
  zones = [supply, demand]

  assert worker._nearest_directional_zone("SELL", 4127.18, zones) is supply
  assert worker._nearest_directional_zone("BUY", 4105.0, zones) is demand


@pytest.mark.no_database
def test_nearest_directional_zone_skips_entry_structure_same_wall():
  """SELL inside supply must not treat that supply as stop-side opposing."""
  entry_supply = Zone(4125.0, 4130.0, "supply", touches=1)
  higher_supply = Zone(4140.0, 4142.0, "supply", touches=0)
  zones = [entry_supply, higher_supply]

  assert worker._nearest_directional_zone(
    "SELL", 4127.5, zones,
  ) is higher_supply
  assert worker._nearest_directional_zone(
    "SELL",
    4127.5,
    zones,
    candidate_entry_low=4125.0,
    candidate_entry_high=4130.0,
    atr=4.0,
    pip_size=0.1,
  ) is higher_supply
  # Only the entry wall present → no opposing attachment.
  assert worker._nearest_directional_zone(
    "SELL",
    4127.5,
    [entry_supply],
    candidate_entry_low=4125.0,
    candidate_entry_high=4130.0,
    atr=4.0,
    pip_size=0.1,
  ) is None


# --- opposing-barrier veto (22 Jul incident: strategy_match BUY filled 20
# pips below a published round-number supply level with no check at all) ---


def test_opposing_barrier_reason_ahead_of_nearby_supply_zone_is_telemetry_only():
  # "opposing_barrier" is a PREFERENCE_TELEMETRY_REASONS condition
  # (execution_policy.py) - _opposing_barrier_reason's wrapper only
  # surfaces a non-None reason for classify_guard_severity's hard_block
  # path, which this "ahead, not inside" relationship never reaches. Only
  # entry literally INSIDE an opposing barrier still hard-blocks via the
  # hard_geometry=True path (see
  # test_opposing_barrier_reason_vetoes_buy_inside_opposing_supply below).
  supply = [Zone(4017.5, 4018.0, "supply", touches=2)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, supply, [], 0.5,
  )
  assert reason is None


def test_opposing_barrier_reason_buy_ignores_supply_outside_buffer():
  far_supply = [Zone(4020.0, 4020.5, "supply", touches=2)]
  reason = worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, far_supply, [], 0.5,
  )
  assert reason is None


def test_opposing_barrier_reason_ignores_zone_behind_entry():
  # A supply zone below current price is behind a BUY, not ahead of it.
  behind = [Zone(4010.0, 4011.0, "supply", touches=0)]
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, behind, [], 0.5,
  ) is None


def test_opposing_barrier_reason_round_number_level_is_telemetry_either_direction():
  # A round-number level isn't sided like a Zone: it can cap a BUY from below
  # or a SELL from above, unlike supply/demand - but same as the Zone case,
  # "ahead of, not inside" is preference telemetry, not a hard veto.
  round_level = [Level(price=4020.0, kind="round", touches=3, band=0.3)]
  buy_reason = worker._opposing_barrier_reason(
    "BUY", 4019.5, 1.2, [], round_level, 0.5,
  )
  sell_reason = worker._opposing_barrier_reason(
    "SELL", 4020.5, 1.2, [], round_level, 0.5,
  )
  assert buy_reason is None
  assert sell_reason is None


def test_opposing_barrier_reason_respects_disabled_atr_or_buffer():
  supply = [Zone(4017.5, 4018.0, "supply", touches=2)]
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, None, supply, [], 0.5,
  ) is None
  assert worker._opposing_barrier_reason(
    "BUY", 4017.2, 1.2, supply, [], 0.0,
  ) is None


# --- A5: rejection counters --------------------------------------------------

@pytest.mark.asyncio
async def test_record_gate_reject_increments_condition_counter():
  client = redis_state.get_client()
  await worker._record_gate_reject(client, "XAU", "waiting_for_box")
  await worker._record_gate_reject(client, "XAU", "waiting_for_box")

  count = await client.hget(
    "auto_trade:gate_reject:XAU:waiting_for_box", "count",
  )
  assert int(count) == 2


# --- Fix 1: opposing-barrier containment gap --------------------------------

def _map_entry(side: str, lo: float, hi: float, *, score: float = 5.0) -> MapEntry:
  return MapEntry(
    side=side, lo=lo, hi=hi, label_lo=int(lo), label_hi=int(hi),
    tier="major", tags=[], score=score,
  )


def _market_map(entries: list[MapEntry], *, price: float = 4118.0) -> MarketMap:
  return MarketMap(
    entries=entries, price=price, eq=None, box_low=None, box_high=None,
    bias="up", bias_tf="M30",
  )


def test_opposing_barrier_side_unclear_zone_containment_is_telemetry_only():
  # Bug since classify_barrier_relationship's introduction (13414b7): both
  # branches of "overlapping_ambiguous" if barrier.side == "neutral" or not
  # opposing else "overlapping_ambiguous" returned the identical literal,
  # so a zone whose side couldn't be cleanly classified as opposing this
  # direction was hard-blocked exactly like a confirmed, unambiguous one
  # (test_opposing_barrier_reason_vetoes_buy_inside_opposing_supply, still
  # unchanged below). Live 2026-08-13: this was the dominant blocker in
  # production (entry_inside_opposing_zone, ~58% of all v8 plan rejections
  # over 12h) on zones logged as "supply_demand" -- a side that matches
  # neither {supply,resistance} nor {demand,support} for any direction.
  neutral = [Zone(4116.0, 4127.0, "neutral", touches=8)]
  source = worker._structural_source_identity(
    strategy="legacy", family="", structural_source="legacy",
    low=4200.0, high=4200.0, key_level=None,
  )
  decision = worker._opposing_barrier_decision(
    "BUY", 4116.25, None, 1.2, neutral, [], 0.5,
    source=source, guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert decision.hard_block is False
  assert decision.reason_code == "entry_inside_ambiguous_zone"
  assert decision.measured["relationship"] == "overlapping_neutral"

  reason = worker._opposing_barrier_reason(
    "BUY", 4116.25, 1.2, neutral, [], 0.5,
  )
  assert reason is None  # hard_block-only wrapper: nothing to veto on


def test_opposing_barrier_ahead_distance_math_unchanged_when_not_contained():
  # Regression guard: an entry genuinely ahead of (not inside) the barrier
  # still uses the pre-existing ATR/buffer tolerance logic to DETECT the
  # barrier - that math is unchanged. Only the severity changed
  # ("opposing_barrier" moved to PREFERENCE_TELEMETRY_REASONS, so
  # _opposing_barrier_reason's hard_block-only wrapper now returns None
  # here - see test_opposing_barrier_reason_ahead_of_nearby_supply_zone_is_telemetry_only).
  # Go one level down to _opposing_barrier_decision to prove the distance
  # detection itself still fires exactly as before.
  # distance = 4116.0 - 4115.5 = 0.5, within buffer_atr(0.5) * atr(1.2) = 0.6.
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  source = worker._structural_source_identity(
    strategy="legacy", family="", structural_source="legacy",
    low=4115.5, high=4115.5, key_level=None,
  )
  decision = worker._opposing_barrier_decision(
    "BUY", 4115.5, None, 1.2, supply, [], 0.5,
    source=source, guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert decision.reason_code == "opposing_barrier"
  assert decision.measured["relationship"] == "opposing_ahead"
  assert decision.measured["distance"] == pytest.approx(0.5)
  assert decision.hard_block is False

  # And still respects the buffer: too far away, no barrier detected at all.
  far = worker._opposing_barrier_decision(
    "BUY", 4110.0, None, 1.2, supply, [], 0.5,
    source=source, guard_mode=worker.GUARD_MODE_STRICT,
  )
  assert far.reason_code == "no_opposing_barrier"


def test_opposing_barrier_reason_containment_is_boundary_inclusive():
  supply = [Zone(4116.0, 4127.0, "supply", touches=8)]
  low_edge = worker._opposing_barrier_reason("BUY", 4116.0, 1.2, supply, [], 0.5)
  high_edge = worker._opposing_barrier_reason("BUY", 4127.0, 1.2, supply, [], 0.5)
  assert low_edge is not None and "inside opposing" in low_edge
  assert high_edge is not None and "inside opposing" in high_edge


def test_instrument_currencies_splits_six_letter_fx_pair():
  assert worker._instrument_currencies("GBPJPY") == ("GBP", "JPY")
  assert worker._instrument_currencies("eurusd") == ("EUR", "USD")
  assert worker._instrument_currencies("XAU") is None
  assert worker._instrument_currencies("XA1JPY") is None


@pytest.mark.asyncio
async def test_event_cluster_guard_noop_when_disabled(monkeypatch):
  install_runtime_overrides(
    monkeypatch,
    overrides={"actionability.gates.event_cluster_guard_enabled": False},
  )
  blow_up = AsyncMock(side_effect=AssertionError("must not query when disabled"))
  monkeypatch.setattr(worker, "nearest_currency_event", blow_up)

  hit = await worker._event_cluster_guard("GBPJPY", 1_780_000_000)

  assert hit is None
  blow_up.assert_not_called()


@pytest.mark.asyncio
async def test_event_cluster_guard_fires_when_both_currencies_have_events(
  monkeypatch,
):
  # 2026 dig: a BoE print and a BoJ statement in the same 48h window
  # compound GBPJPY volatility rather than adding it.
  install_runtime_overrides(
    monkeypatch,
    overrides={
      "actionability.gates.event_cluster_guard_enabled": True,
      "actionability.gates.event_cluster_span_hours": 48,
      "actionability.gates.event_cluster_guard_minutes": 180,
    },
  )
  now = 1_780_000_000
  gbp_event = {"ts_utc": now + 3600, "currency": "GBP", "title": "BoE CPI"}
  jpy_event = {"ts_utc": now - 1800, "currency": "JPY", "title": "BoJ Policy"}

  async def fake_nearest(currency, start, end, anchor):
    return gbp_event if currency == "GBP" else jpy_event

  monkeypatch.setattr(worker, "nearest_currency_event", fake_nearest)

  hit = await worker._event_cluster_guard("GBPJPY", now)

  assert hit == jpy_event  # nearer to `now` than the GBP event


@pytest.mark.asyncio
async def test_event_cluster_guard_noop_when_only_one_currency_has_an_event(
  monkeypatch,
):
  install_runtime_overrides(
    monkeypatch,
    overrides={"actionability.gates.event_cluster_guard_enabled": True},
  )
  now = 1_780_000_000

  async def fake_nearest(currency, start, end, anchor):
    return {"ts_utc": now, "currency": "GBP"} if currency == "GBP" else None

  monkeypatch.setattr(worker, "nearest_currency_event", fake_nearest)

  hit = await worker._event_cluster_guard("GBPJPY", now)

  assert hit is None


@pytest.mark.asyncio
async def test_news_guard_hit_falls_back_to_single_event_window(monkeypatch):
  install_runtime_overrides(
    monkeypatch,
    overrides={"actionability.gates.event_cluster_guard_enabled": False},
  )
  single_event = {"ts_utc": 1_780_000_000, "currency": "USD"}
  monkeypatch.setattr(
    worker, "event_in_window", AsyncMock(return_value=single_event),
  )

  hit = await worker._news_guard_hit("EURUSD", 1_780_000_000)

  assert hit == single_event


# --- Fix 3: post-stop-out cooldown ------------------------------------------

@pytest.mark.asyncio
async def test_zone_cooldown_reason_ignores_legacy_ambiguous_marker():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )

  assert reason is None


@pytest.mark.asyncio
async def test_zone_cooldown_reason_vetoes_confirmed_stop_loss(monkeypatch):
  client = redis_state.get_client()
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_zone_cooldown_enabled": True,})
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({
      "entry_price": 4116.25,
      "stop_price": 4111.54,
      "closed_at": 1000,
      "reason": "stop_loss",
      "confidence": "confirmed",
    }),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )

  assert reason is not None
  assert "zone cooldown" in reason


@pytest.mark.asyncio
async def test_zone_cooldown_reason_allows_opposite_direction():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "SELL", 4116.90, 2.0, 1.0,
  )

  assert reason is None


@pytest.mark.asyncio
async def test_zone_cooldown_reason_none_when_marker_absent_or_expired():
  client = redis_state.get_client()
  # Never written / already expired (Redis TTL naturally removes the key) -
  # both look identical from the read side: GET returns None.
  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4116.90, 2.0, 1.0,
  )
  assert reason is None


@pytest.mark.asyncio
async def test_zone_cooldown_reason_none_outside_atr_band():
  client = redis_state.get_client()
  await client.set(
    worker._zone_cooldown_key("XAU", "BUY"),
    json.dumps({"entry_price": 4116.25, "stop_price": 4111.54, "closed_at": 1000}),
  )

  reason = await worker._zone_cooldown_reason(
    client, "XAU", "BUY", 4200.0, 2.0, 1.0,
  )

  assert reason is None


# --- Fix 4: overlapping opposing-zone veto ----------------------------------

def test_has_overlapping_zones_detects_map_self_contradiction():
  # Replaces the retired _overlapping_zone_conflict_reason's own dedicated
  # tests too (2026-09, Market Map purge stage 4): both guards read the same
  # technique-native zone scan now, and _resolve_overlap_thesis (not this
  # simple containment check) is the live per-candidate overlap veto.
  overlapping = [
    Zone(4116.0, 4127.0, "supply"),
    Zone(4112.0, 4122.0, "demand"),
  ]
  disjoint = [
    Zone(4130.0, 4140.0, "supply"),
    Zone(4100.0, 4110.0, "demand"),
  ]

  assert worker._has_overlapping_zones(overlapping) is True
  assert worker._has_overlapping_zones(disjoint) is False
  assert worker._has_overlapping_zones(None) is False
  assert worker._has_overlapping_zones([]) is False

