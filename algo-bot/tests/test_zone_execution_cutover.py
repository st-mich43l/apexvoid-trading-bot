from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.autotrade.strategy_match import StrategyMatch
from app.autotrade import worker
from app.autotrade import zone_execution_cutover as cutover
from app.autotrade import zone_watch as zw


pytestmark = [pytest.mark.no_database, pytest.mark.real_redis]


@pytest.mark.asyncio
async def test_latest_spot_dispatcher_coalesces_per_symbol(monkeypatch):
  first_started = asyncio.Event()
  release_first = asyncio.Event()
  calls: list[tuple[str, str]] = []

  async def evaluate(_client, *, symbol, event_ts, **_kwargs):
    calls.append((symbol, event_ts))
    if event_ts == "1":
      first_started.set()
      await release_first.wait()

  monkeypatch.setattr(cutover, "_evaluate_spot_zone_event", evaluate)
  dispatcher = cutover._LatestSpotZoneDispatcher(SimpleNamespace())
  try:
    assert dispatcher.submit("XAU", "1") is True
    await asyncio.wait_for(first_started.wait(), timeout=1)
    dispatcher.submit("XAU", "2")
    dispatcher.submit("XAU", "3")
    dispatcher.submit("EURUSD", "4")
    await asyncio.sleep(0)
    assert ("EURUSD", "4") in calls
    assert ("XAU", "2") not in calls

    release_first.set()
    for _ in range(20):
      if ("XAU", "3") in calls:
        break
      await asyncio.sleep(0)
    assert calls.count(("XAU", "1")) == 1
    assert ("XAU", "2") not in calls
    assert calls.count(("XAU", "3")) == 1
  finally:
    release_first.set()
    await dispatcher.close()


@pytest.fixture(autouse=True)
def _freeze_technique_killzone_hour(monkeypatch):
  """Cutover stays under technique.enforce; freeze UTC hour to NY open."""
  from app.autotrade import killzone as kz

  real = kz.evaluate_killzone_gate
  real_win = kz.evaluate_reaction_publish_window

  def _gated(*, ts=None, hour=None, cfg=None, require=True):
    return real(ts=None, hour=14, cfg=cfg, require=require)

  def _window(*, ts=None, hour=None, cfg=None, require=True):
    return real_win(ts=None, hour=14, cfg=cfg, require=require)

  monkeypatch.setattr(kz, "evaluate_killzone_gate", _gated)
  monkeypatch.setattr(kz, "evaluate_reaction_publish_window", _window)


@pytest_asyncio.fixture
async def client():
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.skip("REAL_REDIS_URL is required for ZoneWatch cutover tests")
  redis = Redis.from_url(url, decode_responses=True)
  await redis.ping()
  await redis.flushdb()
  try:
    yield redis
  finally:
    await redis.flushdb()
    await redis.aclose()


def _match(*, strategy: str = "Key Level") -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id="setup-1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy=strategy,
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4114.5,
    entry_low=4113.0,
    entry_high=4116.0,
    current_price=4114.5,
    confluence=3,
    reasons=("supply rejection",),
    atr=4.0,
    structure_swing=4116.0,
    targets_pips=(300,),
    family="key_level" if strategy != "Range Edge Scalp" else "range",
    structural_source="key_level",
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
    structural_zone_low=4113.0,
    structural_zone_high=4116.0,
    touch_bar_ts="1785390000",
    confirmation_bar_ts="1785390300",
    reaction_type="rejection_choch",
  )


def _result(
  *,
  low: float = 4113.0,
  high: float = 4116.0,
  setup="Key Level",
  structural_source: str = "key_level",
):
  return SimpleNamespace(
    setup=setup,
    direction="SELL",
    entry_zone=SimpleNamespace(low=low, high=high),
    structural_low=low,
    structural_high=high,
    structural_timeframe="M15",
    structural_source=structural_source,
    structural_id="zone-1",
    confluence_zone_id="zone-1",
    confluence_tags=("key_level", "supply"),
    confluence=3,
    source_score=12.0,
    confirmation_type="rejection_choch",
    confirmation="rejection_choch",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-1"),
  )


def test_grade_policy_is_explicit():
  assert cutover._grade(_result(), "M15") == zw.GRADE_A
  assert cutover._grade(_result(setup="Range Edge Scalp"), "M5") == zw.GRADE_B


@pytest.mark.asyncio
async def test_structural_zone_width_rejects_when_gate_enabled(client, monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": True})
  eligible = await cutover._record_width_telemetry(
    client,
    symbol="XAU",
    zone_id="narrow",
    result=_result(
      low=4113.0, high=4113.5, structural_source="supply_demand",
    ),
    source_tf="M5",
  )
  assert eligible is False
  raw = await client.get(cutover.width_telemetry_key("XAU", "narrow"))
  assert raw is not None
  assert '"rejection_reason":"zone_too_narrow"' in raw


@pytest.mark.asyncio
async def test_structural_zone_width_gate_disabled_flag_actually_disables_it(
  client, monkeypatch,
):
  """Section 4: SCANNER_ZONE_WIDTH_GATE_ENABLED=false must actually disable
  scanner-stage structural width rejection - the cutover previously ignored
  this flag entirely ("the cutover makes the width contract canonical"),
  silently re-enabling a gate the operator explicitly turned off.
  """
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": False})
  eligible = await cutover._record_width_telemetry(
    client,
    symbol="XAU",
    zone_id="narrow",
    result=_result(
      low=4113.0, high=4113.5, structural_source="supply_demand",
    ),
    source_tf="M5",
  )
  # Telemetry still records the true (failing) width result...
  raw = await client.get(cutover.width_telemetry_key("XAU", "narrow"))
  assert raw is not None
  assert '"rejection_reason":"zone_too_narrow"' in raw
  assert '"eligible":false' in raw
  # ...but with the gate off, this must not be able to reject the zone.
  assert eligible is True


@pytest.mark.asyncio
async def test_level_band_is_never_width_rejected_regardless_of_the_gate(
  client, monkeypatch,
):
  """Section 4: a key level / session level / trendline reaction is a
  LEVEL_BAND, not a merged STRUCTURAL_ZONE - it must never be rejected only
  for being narrower than XAU_ZONE_MIN_WIDTH_PRICE, whether the width gate
  is on or off.
  """
  for gate_enabled in (True, False):
    install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": gate_enabled,})
    eligible = await cutover._record_width_telemetry(
      client,
      symbol="XAU",
      zone_id=f"level-{gate_enabled}",
      result=_result(
        low=4113.0, high=4114.2, structural_source="key_level",
      ),
      source_tf="M5",
    )
    assert eligible is True


@pytest.mark.asyncio
async def test_zone_presence_counts_one_touch_per_visit(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    now=100,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  first, entered = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=200, htf_evidence=True,
  )
  second, entered_again = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=260, htf_evidence=True,
  )
  assert entered is True
  assert entered_again is False
  assert first.touch_count == second.touch_count == 1
  assert first.episode_id == second.episode_id


@pytest.mark.asyncio
async def test_rediscovery_refreshes_metadata_without_resetting_touch(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=100,
  )
  touched = await zw.record_zone_touch(client, record.zone_id, now=150)
  refreshed, created = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4112.9,
    high=4116.1,
    source_timeframe="M15",
    structural_sources=("key_level", "supply_demand"),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    score=14.0,
    now=300,
  )
  assert created is False
  assert refreshed.touch_count == touched.touch_count == 1
  assert refreshed.last_confirmed_at == 300
  assert refreshed.low == pytest.approx(4112.9)
  assert refreshed.score == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_active_plan_state_race_is_reconciled(monkeypatch):
  match = _match()
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
      plan_id="v8:setup-1",
      reason_code="zone_watching_retest",
      zone_id="zone-1",
      setup_id="setup-1",
    )),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v8_state",
    AsyncMock(return_value=worker.ExistingV8State(
      plan_id="v8:setup-1",
      setup_state="plan_published",
      plan_state="armed",
      plan_exists=True,
      already_published=True,
      already_terminal=False,
      owner_matches=True,
    )),
  )
  result = await cutover._safe_direct_publish(
    SimpleNamespace(), match, symbol="XAU",
  )
  assert result.status == worker.PUBLISH_STATUS_DUPLICATE_RECONCILED


@pytest.mark.asyncio
async def test_direct_exception_returns_durable_fallback(monkeypatch):
  match = _match()
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(side_effect=RuntimeError("redis blip")),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v8_state",
    AsyncMock(return_value=worker.ExistingV8State(
      plan_id="v8:setup-1",
      setup_state="worker_acknowledged",
      plan_state=None,
      plan_exists=False,
      already_published=False,
      already_terminal=False,
      owner_matches=False,
    )),
  )
  result = await cutover._safe_direct_publish(
    SimpleNamespace(), match, symbol="XAU",
  )
  assert result.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
  assert result.reason_code == "direct_publish_failed_durable_fallback"


@pytest.mark.asyncio
async def test_waiting_zone_creates_no_setup_or_ready_event(client, monkeypatch):
  from app.analysis import scanner

  result = _result()
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(), None, {}),
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4100.0, 4100.2, 1_785_390_000)),
  )

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is None
  assert await client.get("analysis:setup:setup-1") is None
  assert await client.xlen("auto_trade:strategy_match_ready") == 0
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.WATCHING_RETEST


@pytest.mark.asyncio
async def test_grade_b_zone_publishes_without_waiting_for_an_m1_trigger(
  client, monkeypatch,
):
  """Shadow/default activation mode preserves legacy zone-touch publish.

  Enforce mode (separate tests) requires a fresh episode-scoped M1 reaction
  before StrategyMatch persistence / direct publication.
  """
  from app.analysis import scanner

  result = _result(setup="Range Edge Scalp")
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(strategy="Range Edge Scalp"), None, {}),
  )
  # Quote is inside the 4113-4116 zone: this candidate is executable now.
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, 1_785_390_000)),
  )
  monkeypatch.setattr(
    cutover, "_m1_trigger_for_zone", AsyncMock(return_value=None),
  )
  direct_publish = AsyncMock(return_value=worker.PublishResult(
    status=worker.PUBLISH_STATUS_PUBLISHED,
    plan_id="v8:setup-1",
    reason_code="candidate_published",
    zone_id="zone-1",
    setup_id="setup-1",
  ))
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )

  assert published is not None
  direct_publish.assert_awaited_once()
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.PUBLISHED_LOCKED
  assert watched.last_plan_id == "v8:setup-1"


@pytest.mark.asyncio
async def test_enforce_reaction_waits_for_m1_without_persisting(
  client, monkeypatch,
):
  """Quote-inside-zone alone must not activate reaction setups in enforce."""
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "shadow",
    },
  )
  result = _result(setup="Key Level")
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(), None, {}),
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, 1_785_390_100)),
  )
  monkeypatch.setattr(
    cutover, "_m1_trigger_for_zone", AsyncMock(return_value=None),
  )
  direct_publish = AsyncMock()
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)
  persist = AsyncMock(side_effect=AssertionError("must not persist"))
  monkeypatch.setattr(cutover, "_persist_match", persist)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is None
  direct_publish.assert_not_awaited()
  persist.assert_not_awaited()
  assert await client.get("analysis:setup:setup-1") is None
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state not in zw.TERMINAL_ZONE_WATCH_STATES | zw.LOCKED_ZONE_WATCH_STATES
  last = await client.get("auto_trade:last_entry_activation:XAU")
  assert last is not None
  assert "reaction_trigger_missing" in last


@pytest.mark.asyncio
async def test_enforce_reaction_activates_with_fresh_m1_once(
  client, monkeypatch,
):
  from dataclasses import replace
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "shadow",
    },
  )
  result = _result(setup="Key Level")
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  match = replace(
    _match(),
    range_low=4100.0,
    range_high=4200.0,
    confirmation_bar_ts=None,
    reaction_type=None,
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (match, None, {}),
  )
  entered_at = 1_785_390_050
  now_ts = 1_785_390_200
  await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=entered_at - 10,
  )
  await zw.record_zone_presence(
    client, "zone-1", inside=True, now=entered_at, htf_evidence=True,
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, now_ts)),
  )
  trigger = M1TriggerResult(
    "body_close", "SELL", 4116.0, entered_at + 30, "sell rejection",
  )
  monkeypatch.setattr(
    cutover, "_m1_trigger_for_zone", AsyncMock(return_value=trigger),
  )
  direct_publish = AsyncMock(return_value=worker.PublishResult(
    status=worker.PUBLISH_STATUS_PUBLISHED,
    plan_id="v8:setup-1",
    reason_code="candidate_published",
    zone_id="zone-1",
    setup_id="setup-1",
  ))
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:02:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is not None
  direct_publish.assert_awaited_once()
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.PUBLISHED_LOCKED


@pytest.mark.asyncio
async def test_enforce_location_blocks_buy_after_premium_rally(
  client, monkeypatch,
):
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "enforce",
    },
  )
  buy_result = SimpleNamespace(
    setup="Zone Reaction",
    direction="BUY",
    entry_zone=SimpleNamespace(low=4078.0, high=4082.0),
    structural_low=4078.0,
    structural_high=4082.0,
    structural_timeframe="M15",
    structural_source="key_level",
    structural_id="zone-buy",
    confluence_zone_id="zone-buy",
    confluence_tags=("key_level", "demand"),
    confluence=3,
    source_score=12.0,
    confirmation_type="body_close",
    confirmation="body_close",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-1"),
  )
  buy_match = StrategyMatch(
    version=1,
    match_id="setup-buy",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy="Zone Reaction",
    strategy_mode="with_bias",
    direction="BUY",
    key_level=4080.0,
    entry_low=4078.0,
    entry_high=4082.0,
    current_price=4080.0,
    confluence=3,
    reasons=("demand",),
    atr=4.0,
    structure_swing=4078.0,
    targets_pips=(300,),
    family="key_level",
    structural_source="key_level",
    confluence_zone_id="zone-buy",
    structural_zone_id="zone-buy",
    structural_zone_low=4078.0,
    structural_zone_high=4082.0,
    range_low=None,
    range_high=None,
  )
  dealing = SimpleNamespace(low=4000.0, high=4100.0)
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
    analysis=SimpleNamespace(
      per_tf={
        "M15": SimpleNamespace(dealing_range=dealing, regime=None),
        "H1": SimpleNamespace(dealing_range=None, regime=None),
        "M5": SimpleNamespace(dealing_range=None, regime=None),
      },
      dealing_range=dealing,
      regime=None,
    ),
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (buy_match, None, {}),
  )
  now_ts = 1_785_390_200
  # Ask in premium (~0.80) inside the zone band for geometry test.
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4079.5, 4080.5, now_ts)),
  )
  # Force zone presence "inside" even though dealing range says premium.
  monkeypatch.setattr(
    cutover,
    "_quote_evidence",
    lambda record, quote: SimpleNamespace(
      inside=True,
      executable_quote=quote[1],
      side="ask",
    ),
  )
  monkeypatch.setattr(
    cutover,
    "_m1_trigger_for_zone",
    AsyncMock(return_value=M1TriggerResult(
      "body_close", "BUY", 4078.0, now_ts - 30, "buy rejection",
    )),
  )
  direct_publish = AsyncMock()
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [buy_result],
    require_static_eligibility=True,
  )
  assert published is None
  direct_publish.assert_not_awaited()
  last = await client.get("auto_trade:last_entry_location:XAU")
  assert last is not None
  assert "buy_in_premium" in last or "buy_at_range_extreme" in last


@pytest.mark.asyncio
async def test_prod_replay_null_match_range_no_longer_context_missing(
  client, monkeypatch,
):
  """Prod 2026-08-05 16:07 UTC: Zone Reaction SELL in-zone blocked under enforce.

  Evidence:
  - Redis candidate range_low/high = null (Zone Reaction never sets scalp box)
  - Scanner discovery had M15 dealing 4183.27-4265.13 → entry_location_allowed
  - Cutover wired match.range_* as M15 → entry_location_context_missing

  Replay must use discovery dealing ranges and allow location (then wait M1).
  """
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "enforce",
    },
  )
  zone_low = 4229.88
  zone_high = 4234.017794180076
  m15_low = 4183.27
  m15_high = 4265.13
  result = SimpleNamespace(
    setup="Zone Reaction",
    direction="SELL",
    entry_zone=SimpleNamespace(low=zone_low, high=zone_high),
    structural_low=zone_low,
    structural_high=zone_high,
    structural_timeframe="M5",
    structural_source="supply",
    structural_id="zone-prod-sell",
    confluence_zone_id="zone-prod-sell",
    confluence_tags=("supply",),
    confluence=3,
    source_score=12.0,
    confirmation_type="strong_reclaim",
    confirmation="strong_reclaim",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-prod"),
  )
  match = StrategyMatch(
    version=1,
    match_id="583c6151086405b8aa1551140f7a4d63",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-08-05T16:05:07+00:00",
    issued_at=1_785_946_000,
    expires_at=1_785_946_420,
    strategy="Zone Reaction",
    strategy_mode="counter_bias",
    direction="SELL",
    key_level=4232.0,
    entry_low=zone_low,
    entry_high=zone_high,
    current_price=4225.95,
    confluence=3,
    reasons=("premium", "strong_reclaim"),
    atr=8.275588360151538,
    structure_swing=zone_high,
    targets_pips=(300,),
    family="supply_demand",
    structural_source="supply",
    confluence_zone_id="zone-prod-sell",
    structural_zone_id="zone-prod-sell",
    structural_zone_low=zone_low,
    structural_zone_high=zone_high,
    range_low=None,
    range_high=None,
  )
  dealing = SimpleNamespace(low=m15_low, high=m15_high)
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([8.27]))},
    analysis=SimpleNamespace(
      per_tf={
        "M15": SimpleNamespace(dealing_range=dealing, regime=None),
        "H1": SimpleNamespace(dealing_range=None, regime=None),
        "M5": SimpleNamespace(
          dealing_range=SimpleNamespace(low=4224.74, high=4265.13),
          regime=None,
        ),
      },
      dealing_range=dealing,
      regime=None,
    ),
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.1)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (match, None, {}),
  )
  now_ts = 1_785_946_222
  # Bid inside zone (prod executable_quote=4231.01).
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4231.01, 4231.10, now_ts)),
  )
  monkeypatch.setattr(
    cutover,
    "_quote_evidence",
    lambda record, quote: SimpleNamespace(
      inside=True,
      executable_quote=quote[0],
      side="bid",
    ),
  )
  # No M1 yet — location must still evaluate with dealing ranges (not missing).
  monkeypatch.setattr(
    cutover,
    "_m1_trigger_for_zone",
    AsyncMock(return_value=None),
  )
  direct_publish = AsyncMock()
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-08-05T16:07:02+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is None
  direct_publish.assert_not_awaited()

  last_loc = await client.get("auto_trade:last_entry_location:XAU")
  assert last_loc is not None
  assert "entry_location_context_missing" not in last_loc
  assert "entry_location_allowed" in last_loc
  assert "4183.27" in last_loc
  assert "4265.13" in last_loc

  snap = await client.get("analysis:zone_watch_location_ranges:zone-prod-sell")
  assert snap is not None
  assert "4183.27" in snap

  last_act = await client.get("auto_trade:last_entry_activation:XAU")
  assert last_act is not None
  assert "reaction_trigger_missing" in last_act


@pytest.mark.asyncio
async def test_m1_path_uses_persisted_location_range_snapshot(
  client, monkeypatch,
):
  """M1 cutover must reuse discovery range snapshot when match.range_* is null."""
  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "enforce",
    },
  )
  zone_id = "zone-m1-snap"
  await cutover._save_location_ranges(
    client,
    zone_id,
    {
      "m15_range_low": 4183.27,
      "m15_range_high": 4265.13,
      "h1_range_low": None,
      "h1_range_high": None,
      "m5_range_low": 4224.74,
      "m5_range_high": 4265.13,
    },
  )

  async def _fake_reload(*_a, **_k):
    raise AssertionError("must not reload market context when snapshot exists")

  monkeypatch.setattr(
    "app.analysis.scanner._load_market_context_for_symbol",
    _fake_reload,
  )
  monkeypatch.setattr(
    "app.analysis.market_map_delivery.get_cached_analysis",
    lambda _symbol: None,
  )

  ranges = await cutover._resolve_location_range_bounds(
    client,
    symbol="XAU",
    zone_id=zone_id,
    analysis_context=None,
  )
  assert cutover._ranges_usable(ranges)
  assert ranges["m15_range_low"] == pytest.approx(4183.27)
  assert ranges["m15_range_high"] == pytest.approx(4265.13)

  match = StrategyMatch(
    version=1,
    match_id="m1-snap",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-08-05T16:05:07+00:00",
    issued_at=1_785_946_000,
    expires_at=1_785_946_420,
    strategy="Zone Reaction",
    strategy_mode="counter_bias",
    direction="SELL",
    key_level=4232.0,
    entry_low=4229.88,
    entry_high=4234.02,
    current_price=4231.01,
    confluence=3,
    reasons=("premium",),
    atr=8.0,
    structure_swing=4234.02,
    targets_pips=(300,),
    family="supply_demand",
    range_low=None,
    range_high=None,
  )
  record = SimpleNamespace(
    symbol="XAU",
    direction="SELL",
    low=4229.88,
    high=4234.02,
    zone_entered_at=1_785_946_100,
    grade="A",
    zone_id=zone_id,
  )
  quote = (4231.01, 4231.10, 1_785_946_222)
  evidence = SimpleNamespace(inside=True, executable_quote=4231.01, side="bid")
  location, _activation, context = cutover._location_and_activation_for_record(
    match=match,
    record=record,
    quote=quote,
    evidence=evidence,
    trigger=None,
    now=quote[2],
    range_bounds=ranges,
  )
  assert context.effective_range_source == "M15"
  assert location.reason_code == "entry_location_allowed"
  assert location.allowed is True



def test_match_range_fields_are_not_used_as_dealing_bounds():
  """Scalp box on StrategyMatch must never become M15 dealing range."""
  match = StrategyMatch(
    version=1,
    match_id="box-misuse",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-08-05T16:05:07+00:00",
    issued_at=1_785_946_000,
    expires_at=1_785_946_420,
    strategy="Zone Reaction",
    strategy_mode="counter_bias",
    direction="SELL",
    key_level=4232.0,
    entry_low=4229.88,
    entry_high=4234.02,
    current_price=4231.01,
    confluence=3,
    reasons=("premium",),
    atr=8.0,
    structure_swing=4234.02,
    targets_pips=(300,),
    family="supply_demand",
    range_low=4229.88,  # zone edges — wrong if used as dealing range
    range_high=4234.02,
  )
  record = SimpleNamespace(
    symbol="XAU",
    direction="SELL",
    low=4229.88,
    high=4234.02,
    zone_entered_at=1_785_946_200,
    grade="A",
  )
  quote = (4231.01, 4231.10, 1_785_946_222)
  evidence = SimpleNamespace(inside=True, executable_quote=4231.01, side="bid")
  location, _activation, context = cutover._location_and_activation_for_record(
    match=match,
    record=record,
    quote=quote,
    evidence=evidence,
    trigger=None,
    now=quote[2],
    range_bounds={
      "m15_range_low": 4183.27,
      "m15_range_high": 4265.13,
      "h1_range_low": None,
      "h1_range_high": None,
      "m5_range_low": None,
      "m5_range_high": None,
    },
  )
  assert context.effective_range_low == pytest.approx(4183.27)
  assert context.effective_range_high == pytest.approx(4265.13)
  assert location.reason_code == "entry_location_allowed"
  # If match.range_* had been used, position would be nonsense near 0.5 of a 4pt box.


@pytest.mark.asyncio
async def test_safe_direct_publish_ensures_plan_published_root_card(monkeypatch):
  """Direct publish must ensure the PLAN PUBLISHED root card exists.

  ZoneWatch cutover suppresses SETUP FORMING until publish and the M1
  publish path never calls scanner._notify_digest_once, so without this
  ensure the owner can trade a setup with no Telegram root card.
  """
  match = _match()
  ensure = AsyncMock()
  monkeypatch.setattr(cutover, "_ensure_published_root_card", ensure)
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_PUBLISHED,
      plan_id="v8:setup-1",
      reason_code="candidate_published",
      zone_id="zone-1",
      setup_id="setup-1",
    )),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v8_state",
    AsyncMock(return_value=SimpleNamespace(
      already_published=True,
      plan_id="v8:setup-1",
    )),
  )

  cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)
  result = await cutover._safe_direct_publish(
    object(),
    match,
    symbol="XAU",
    event_ts=match.event_ts,
  )

  assert result.status in {
    worker.PUBLISH_STATUS_PUBLISHED,
    worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
  }
  assert match.match_id in cutover._PUBLISHED_SETUP_IDS
  ensure.assert_awaited_once()
  assert ensure.await_args.args[1] is match


def test_format_detection_cutover_suppresses_the_card_before_publication(
  monkeypatch,
):
  """Section 12 of the direct-publish spec: no new card may show SETUP
  FORMING/QUEUED/worker-acknowledgement-pending - the first real card is
  PLAN PUBLISHED. Under the cutover there is no worker/preflight/armed
  queue left to describe, so the old formatter's pre-publication text
  (still generated by _ORIGINAL_FORMAT, unmodified) must never reach
  Telegram; _notify_digest_once skips sending on empty text.
  """
  monkeypatch.setattr(
    cutover, "_ORIGINAL_FORMAT", lambda *a, **k: "SETUP FORMING stub",
  )
  match = _match()
  # _PUBLISHED_SETUP_IDS is a module-level set other tests also touch with
  # this same match_id ("setup-1") - start from a clean slate.
  cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)

  unpublished_text = cutover._format_detection_cutover(
    "XAU", "M5", None, None, [], [], None, match,
  )
  assert unpublished_text == ""

  cutover._PUBLISHED_SETUP_IDS.add(match.match_id)
  try:
    monkeypatch.setattr(
      cutover,
      "_ORIGINAL_FORMAT",
      lambda *a, **k: (
        "🟡 <b>QUEUED</b> · worker acknowledgement pending\n"
        "→ Review confirmation, SL &amp; TP before posting."
      ),
    )
    published_text = cutover._format_detection_cutover(
      "XAU", "M5", None, None, [], [], None, match,
    )
  finally:
    cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)

  assert "PLAN PUBLISHED" not in published_text
  assert "QUEUED" in published_text
  assert "Executor owns mechanical entry" in published_text


def _eligibility_with_stop_error(
  *,
  planned_stop_error: str,
  allowed: bool = True,
):
  from app.analysis.execution_eligibility import (
    ANALYSIS_ONLY,
    EXECUTION_ELIGIBILITY_VERSION,
    STATIC_ELIGIBLE,
    ExecutionEligibility,
  )

  return ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=allowed,
    state=STATIC_ELIGIBLE if allowed else ANALYSIS_ONLY,
    reason_code="ok" if allowed else planned_stop_error,
    message="",
    hard_block=not allowed,
    direction="SELL",
    entry_low=4113.0,
    entry_high=4116.0,
    planned_entry_price=4114.5,
    measured={"planned_stop_error": planned_stop_error},
  )


def test_match_planned_stop_hard_error_reads_measured_even_when_allowed():
  from dataclasses import replace

  match = replace(
    _match(),
    execution_eligibility=_eligibility_with_stop_error(
      planned_stop_error="stop_exceeds_envelope_furthest_leg",
      allowed=True,
    ),
  )
  assert (
    cutover._match_planned_stop_hard_error(match)
    == "stop_exceeds_envelope_furthest_leg"
  )
  assert cutover._publish_should_terminalize_zone_watch(
    status=worker.PUBLISH_STATUS_INVALIDATED,
    reason_code="structure_invalidated",
  )
  assert cutover._publish_should_terminalize_zone_watch(
    status=worker.PUBLISH_STATUS_REJECTED,
    reason_code="v8_protective_stop_unavailable",
  )
  assert not cutover._publish_should_terminalize_zone_watch(
    status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
    reason_code="waiting_retest_entry_zone",
  )


@pytest.mark.asyncio
async def test_prepare_activation_preblocks_planned_stop_envelope_error(
  monkeypatch,
):
  from dataclasses import replace

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "shadow",
    },
  )
  record = zw.ZoneWatch(
    version=zw.ZONE_WATCH_VERSION,
    zone_id="zone-stop",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    width=3.0,
    source_timeframe="M15",
    structural_sources=("session_level",),
    confluence_tags=("session_level",),
    technique_tags=(),
    grade=zw.GRADE_A,
    score=1.0,
    freshness=0,
    touch_count=0,
    discovered_at=1_785_390_000,
    last_confirmed_at=1_785_390_000,
    last_touch_at=None,
    invalidation_price=None,
    state=zw.WATCHING_RETEST,
    market_map_id="",
    structure_signature="",
    updated_at=1_785_390_000,
  )
  match = replace(
    _match(strategy="iFVG"),
    match_id="setup-stop",
    confluence_zone_id="zone-stop",
    structural_zone_id="zone-stop",
    execution_eligibility=_eligibility_with_stop_error(
      planned_stop_error="stop_exceeds_envelope_furthest_leg",
    ),
  )
  terminalize = AsyncMock()
  monkeypatch.setattr(cutover, "_terminalize_zone_watch", terminalize)
  monkeypatch.setattr(cutover, "_record_policy_telemetry", AsyncMock())
  prepared = await cutover._prepare_activation(
    SimpleNamespace(),
    record=record,
    match=match,
    quote=(4114.4, 4114.6, 1_785_390_200),
    evidence=SimpleNamespace(executable_quote=4114.4, inside=True),
  )
  assert prepared is None
  terminalize.assert_awaited_once()
  assert terminalize.await_args.kwargs["reason_code"] == (
    "stop_exceeds_envelope_furthest_leg"
  )


@pytest.mark.asyncio
async def test_prepare_activation_does_not_apply_mad_hard_gate(monkeypatch):
  """MAD must not veto activation — entry quality / structure only."""
  from dataclasses import replace
  from app.core import instrument_geometry
  from tests.test_config_effective_instrument_context import _load_production_example

  eurusd_cfg = _load_production_example().config.for_instrument("EURUSD")
  monkeypatch.setattr(
    instrument_geometry,
    "instrument_runtime",
    lambda symbol: eurusd_cfg if str(symbol).upper() == "EURUSD" else eurusd_cfg,
  )
  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "execution.technique.enforce": True,
      "execution.technique.mad_hard_gate_enabled": True,
      "actionability.entry_location.mode": "shadow",
    },
  )
  record = zw.ZoneWatch(
    version=zw.ZONE_WATCH_VERSION,
    zone_id="zone-mad",
    symbol="EURUSD",
    direction="SELL",
    low=1.0850,
    high=1.0855,
    width=0.0005,
    source_timeframe="M15",
    structural_sources=("range",),
    confluence_tags=("range",),
    technique_tags=(),
    grade=zw.GRADE_B,
    score=1.0,
    freshness=0,
    touch_count=0,
    discovered_at=1_785_390_000,
    last_confirmed_at=1_785_390_000,
    last_touch_at=None,
    invalidation_price=None,
    state=zw.WATCHING_RETEST,
    market_map_id="",
    structure_signature="",
    updated_at=1_785_390_000,
  )
  match = replace(
    _match(strategy="Range Edge Scalp"),
    match_id="setup-mad",
    symbol="EURUSD",
    confluence_zone_id="zone-mad",
    structural_zone_id="zone-mad",
    family="range",
    strategy_mode="range_scalp",
  )
  telemetry = AsyncMock()
  mad_gate = AsyncMock(
    return_value=(
      False,
      "mad_gate_reversal_avoid_expand",
      {"mad_phase": "expand"},
    )
  )
  monkeypatch.setattr(cutover, "_record_policy_telemetry", telemetry)
  monkeypatch.setattr(cutover, "_m1_trigger_for_zone", AsyncMock(return_value=None))
  monkeypatch.setattr(cutover, "_recent_m1_impulse_bars", AsyncMock(return_value=0))
  monkeypatch.setattr(
    cutover,
    "_resolve_location_range_bounds",
    AsyncMock(return_value={
      "m15_range_low": 1.0800,
      "m15_range_high": 1.0900,
    }),
  )
  monkeypatch.setattr(
    cutover,
    "_closed_bar_decisive_break",
    AsyncMock(return_value=False),
  )
  monkeypatch.setattr(
    cutover,
    "_location_and_activation_for_record",
    lambda **_kwargs: (
      SimpleNamespace(
        allowed=True,
        reason_code="location_allowed",
        would_block=False,
        measured={"mode": "shadow"},
      ),
      SimpleNamespace(
        allowed=True,
        reason_code="reaction_trigger_fresh",
        would_block=False,
        requires_trigger=True,
        measured={"mode": "enforce"},
      ),
      SimpleNamespace(
        effective_range_source="scanner",
        effective_range_low=1.0800,
        effective_range_high=1.0900,
        effective_range_position_raw=0.5,
      ),
    ),
  )
  monkeypatch.setattr(
    "app.autotrade.reaction_funnel.bump_funnel",
    AsyncMock(),
  )
  monkeypatch.setattr(
    "app.analysis.mad_phase.evaluate_technique_mad_gate",
    mad_gate,
  )

  prepared = await cutover._prepare_activation(
    SimpleNamespace(),
    record=record,
    match=match,
    quote=(1.0852, 1.0854, 1_785_390_200),
    evidence=SimpleNamespace(executable_quote=1.0852, inside=True),
  )
  assert prepared is not None
  mad_gate.assert_not_awaited()
  for call in telemetry.await_args_list:
    assert call.kwargs.get("reason_code") != "mad_gate_reversal_avoid_expand"


@pytest.mark.asyncio
async def test_activate_match_terminalizes_zone_on_v8_stop_invalidate(
  monkeypatch,
):
  from dataclasses import replace

  record = zw.ZoneWatch(
    version=zw.ZONE_WATCH_VERSION,
    zone_id="zone-thrash",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    width=3.0,
    source_timeframe="M15",
    structural_sources=("session_level",),
    confluence_tags=("session_level",),
    technique_tags=(),
    grade=zw.GRADE_A,
    score=1.0,
    freshness=0,
    touch_count=0,
    discovered_at=1_785_390_000,
    last_confirmed_at=1_785_390_000,
    last_touch_at=None,
    invalidation_price=None,
    state=zw.WATCHING_RETEST,
    market_map_id="",
    structure_signature="",
    updated_at=1_785_390_000,
  )
  match = replace(_match(strategy="Session Level"), match_id="setup-thrash")
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, 1_785_390_200)),
  )
  monkeypatch.setattr(
    cutover,
    "_scalp_access",
    lambda *_a, **_k: SimpleNamespace(
      executable=True,
      status="inside",
      chase_pips=0.0,
      maximum_chase_pips=40.0,
      evidence=SimpleNamespace(
        executable_quote=4114.4,
        inside=True,
        distance_pips=0.0,
      ),
    ),
  )
  monkeypatch.setattr(
    cutover,
    "_persist_match",
    AsyncMock(side_effect=lambda _client, m: m),
  )
  monkeypatch.setattr(
    cutover,
    "_safe_direct_publish",
    AsyncMock(
      return_value=worker.PublishResult(
        status=worker.PUBLISH_STATUS_INVALIDATED,
        plan_id="",
        reason_code="v8_protective_stop_unavailable",
        zone_id="zone-thrash",
        setup_id="setup-thrash",
      ),
    ),
  )
  terminalize = AsyncMock()
  monkeypatch.setattr(cutover, "_terminalize_zone_watch", terminalize)
  presence = AsyncMock()
  monkeypatch.setattr(cutover, "record_zone_presence", presence)

  async def _no_setup(*_a, **_k):
    return None

  monkeypatch.setattr(
    "app.autotrade.setup_lifecycle.load_setup",
    _no_setup,
  )

  published = await cutover._activate_match(
    SimpleNamespace(),
    record,
    match,
    event_ts="2026-07-30T06:02:00+00:00",
  )
  assert published is None
  terminalize.assert_awaited_once()
  assert terminalize.await_args.kwargs["reason_code"] == (
    "v8_protective_stop_unavailable"
  )
  presence.assert_not_awaited()
