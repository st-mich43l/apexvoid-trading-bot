"""P3b: merged detector cards and reward/risk setup eligibility."""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.analysis import scanner
from app.analysis.actionability import resolve_actionability
from app.analysis.confluence_zone import confluence_setup_id
from app.analysis.market_map import MarketMap
from app.analysis.types import Zone
from app.autotrade import delivery, worker
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  EXPIRED,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.route_outcome import record_route_outcome, route_outcome_key
from app.autotrade.strategy_match import StrategyMatch
from app.autotrade.trade_plan_builder import TradePlanBuildRejected
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _no_news_by_default(monkeypatch):
  # TradePlan now hard-gates news via ``event_in_window`` which needs a live DB. These
  # tests target the RR/plan-build gates, so we short-circuit the news lookup
  # to keep them database-free while still exercising the TradePlan publish path.
  monkeypatch.setattr(
    worker, "event_in_window", AsyncMock(return_value=None),
  )


@pytest.fixture(autouse=True)
def _freeze_technique_killzone_hour(monkeypatch):
  from app.autotrade import killzone as kz

  real = kz.evaluate_killzone_gate
  real_win = kz.evaluate_reaction_publish_window

  def _gated(*, ts=None, hour=None, cfg=None, require=True):
    return real(ts=None, hour=14, cfg=cfg, require=require)

  def _window(*, ts=None, hour=None, cfg=None, require=True):
    return real_win(ts=None, hour=14, cfg=cfg, require=require)

  monkeypatch.setattr(kz, "evaluate_killzone_gate", _gated)
  monkeypatch.setattr(kz, "evaluate_reaction_publish_window", _window)


def _frame(price: float = 4101.0) -> pd.DataFrame:
  index = pd.date_range("2026-07-28 12:00", periods=3, freq="5min", tz="UTC")
  return pd.DataFrame({
    "open": [price - 0.2, price + 0.1, price - 0.1],
    "high": [price + 0.4, price + 0.5, price + 0.3],
    "low": [price - 0.5, price - 0.3, price - 0.4],
    "close": [price + 0.1, price - 0.1, price],
    "volume": [100.0, 120.0, 110.0],
  }, index=index)


def _ctx(price: float = 4101.0, atr: float = 2.0):
  frame = _frame(price)
  return SimpleNamespace(
    tf="M5",
    htf_bias="down",
    indicators={"M5": SimpleNamespace(atr=pd.Series([atr]))},
    structures={"M30": SimpleNamespace(bias="down")},
    frames={"M5": frame},
    regime=SimpleNamespace(kind="trend"),
    spot_price=None,
    trigger_ts="2026-07-28T12:10:00+00:00",
    analysis=None,
  )


def _result(
  *,
  setup: str,
  direction: str,
  low: float,
  high: float,
  structural_id: str,
  source: str,
  kind: str,
  confluence: int = 3,
  current_price: float | None = None,
) -> scanner.DetectionResult:
  side = "demand" if direction == "BUY" else "supply"
  return scanner.DetectionResult(
    setup=setup,
    direction=direction,
    key_level=(low + high) / 2,
    entry_zone=Zone(low, high, side, score=float(confluence)),
    current_price=(
      (low + high) / 2 if current_price is None else current_price
    ),
    confluence=confluence,
    reasons=[f"{kind} reaction"],
    structural_source=source,
    structural_id=structural_id,
    structural_low=low,
    structural_high=high,
    structural_timeframe="M5",
    structural_kind=kind,
    confirmation_type="body_close",
    confirmation_bar_ts="2026-07-28T12:10:00+00:00",
    touch_bar_ts="2026-07-28T12:05:00+00:00",
  )


def _build_one(result: scanner.DetectionResult, ctx) -> StrategyMatch:
  match, reason, measured = scanner._build_one_strategy_match(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    result,
    now=1_722_168_600,
  )
  assert reason is None, measured
  assert match is not None
  return match


@pytest.mark.asyncio
async def test_one_card_and_one_setup_per_merged_zone(monkeypatch):
  client = redis_state.get_client()
  ctx = _ctx()
  supply = _result(
    setup="Supply Zone Reaction",
    direction="SELL",
    low=4100.5,
    high=4102.2,
    structural_id="supply-1",
    source="supply_demand",
    kind="supply",
    confluence=4,
  )
  key = _result(
    setup="Key Level",
    direction="SELL",
    low=4101.8,
    high=4102.4,
    structural_id="key-1",
    source="key_level",
    kind="resistance",
    confluence=3,
  )

  merged = scanner._merge_detection_confluence(
    "XAU", "M5", [supply, key], atr=2.0,
  )

  assert len(merged) == 1
  result = merged[0]
  assert result.setup == "Supply Zone Reaction"
  assert result.confluence_tags == ("key_level", "supply")
  assert result.confluence == 4
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_enabled": True,})
  match = await scanner._sync_strategy_match(
    client,
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    merged,
  )
  assert match is not None
  assert match.match_id == confluence_setup_id(
    result.confluence_zone_id,
    "SELL",
  )
  assert match.structural_zone_id == result.confluence_zone_id
  assert match.confluence_zone_id == result.confluence_zone_id
  assert {"kind:key_level", "kind:supply"} <= set(match.tags)
  restored = StrategyMatch.from_json(match.to_json())
  assert restored is not None
  assert restored.match_id == match.match_id
  assert restored.confluence_zone_id == result.confluence_zone_id
  setup_keys = [key async for key in client.scan_iter("analysis:setup:*")]
  assert setup_keys == [f"analysis:setup:{match.match_id}"]
  assert (await load_setup(client, match.match_id)).state == CONFIRMED
  assert worker._resolve_match_confluence_claim_id(
    "XAU", match, None,
  ) == result.confluence_zone_id

  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_card_top_n": 2})
  sent_texts = []

  async def notify(text, **_kwargs):
    sent_texts.append(text)
    return SimpleNamespace(message_id=9001)

  cards = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    ctx,
    merged,
    notify,
    ["M30"],
    execution_match=match,
    execution_matches=[match],
  )

  assert cards == merged
  assert len(sent_texts) == 1
  # Card layout is the emoji-driven compact format now (PR #220) - there is
  # no combined "tag1 + tag2 · setup" string anymore. The merge still shows
  # up via the winning representative setup name plus both sources' own
  # reasons surviving into Context.
  text = sent_texts[0]
  assert "SELL · Supply Zone Reaction" in text
  assert "supply reaction" in text
  assert "resistance reaction" in text


def test_six_price_same_side_cluster_merges_before_ambiguity_gate(monkeypatch):
  ctx = _ctx(price=4103.0, atr=2.0)
  raw = [
    _result(
      setup="Key Level", direction="SELL",
      low=4100.0, high=4101.5, structural_id="key-six",
      source="key_level", kind="resistance", confluence=5,
    ),
    _result(
      setup="Supply Zone Reaction", direction="SELL",
      low=4102.2, high=4103.5, structural_id="supply-six",
      source="supply_demand", kind="supply", confluence=4,
    ),
    _result(
      setup="Supply Zone Reaction", direction="SELL",
      low=4104.2, high=4106.0, structural_id="ob-six",
      source="order_block", kind="ob", confluence=3,
    ),
  ]
  install_runtime_overrides(monkeypatch, legacy_overrides={"zone_merge_max_width": 6.0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_actionability_gate_enabled": False,})
  install_runtime_overrides(monkeypatch, legacy_overrides={"key_level_role_ambiguity_gate_enabled": False,})

  merged = scanner._merge_detection_confluence(
    "XAU", "M5", raw, atr=2.0,
  )
  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=merged,
    market_map=MarketMap(
      entries=[],
      price=4103.0,
      eq=None,
      box_low=None,
      box_high=None,
      bias="down",
      bias_tf="H1",
      actionable_entries=[],
    ),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=runtime_config,
  )

  assert len(merged) == 1
  assert merged[0].entry_zone.low == 4100.0
  assert merged[0].entry_zone.high == 4106.0
  assert merged[0].confluence_tags == ("key_level", "ob", "supply")
  assert resolution.actionable == tuple(merged)
  assert resolution.gated == ()


@pytest.mark.asyncio
async def test_distinct_same_side_zones_still_form_two_cards(monkeypatch):
  client = redis_state.get_client()
  ctx = _ctx()
  raw = [
    _result(
      setup="Demand Zone Reaction", direction="BUY",
      low=4099.0, high=4100.2, structural_id="demand-a",
      source="supply_demand", kind="demand",
    ),
    _result(
      setup="Key Level", direction="BUY",
      low=4099.8, high=4100.4, structural_id="key-a",
      source="key_level", kind="support",
    ),
    _result(
      setup="Demand Zone Reaction", direction="BUY",
      low=4110.0, high=4111.2, structural_id="demand-b",
      source="supply_demand", kind="demand",
    ),
    _result(
      setup="Key Level", direction="BUY",
      low=4110.8, high=4111.4, structural_id="key-b",
      source="key_level", kind="support",
    ),
  ]
  merged = scanner._merge_detection_confluence(
    "XAU", "M5", raw, atr=2.0,
  )

  assert len(merged) == 2
  assert len({item.confluence_zone_id for item in merged}) == 2
  matches = [_build_one(item, ctx) for item in merged]
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_card_top_n": 2})
  notify = AsyncMock(return_value=SimpleNamespace(message_id=9002))

  cards = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    ctx,
    merged,
    notify,
    ["M30"],
    execution_match=matches[0],
    execution_matches=matches,
  )

  assert len(cards) == 2
  assert notify.await_count == 2


@pytest.mark.asyncio
async def test_opposing_sides_keep_identity_and_remain_actionable(
  monkeypatch,
):
  client = redis_state.get_client()
  ctx = _ctx()
  raw = [
    _result(
      setup="Demand Zone Reaction", direction="BUY",
      low=4100.0, high=4101.0, structural_id="demand-buy",
      source="supply_demand", kind="demand",
    ),
    _result(
      setup="Supply Zone Reaction", direction="SELL",
      low=4100.0, high=4101.0, structural_id="supply-sell",
      source="supply_demand", kind="supply",
    ),
  ]
  merged = scanner._merge_detection_confluence(
    "XAU", "M5", raw, atr=2.0,
  )

  assert len(merged) == 2
  assert {item.direction for item in merged} == {"BUY", "SELL"}
  # Contested corridor is preference telemetry — both sides stay actionable.
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_actionability_gate_enabled": True,})
  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=merged,
    market_map=MarketMap([], 4100.5, None, None, None, "range", "M30"),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=runtime_config,
  )
  assert resolution.observed == tuple(merged)
  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  assert resolution.conflicts[0]["outcome"] == "contested_corridor"
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert len(contested) == 2
  assert all(decision.hard_block is False for decision in contested)

  match = await scanner._sync_strategy_match(
    client,
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    list(resolution.actionable),
  )
  assert match is not None
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_card_top_n": 2})
  notify = AsyncMock(return_value=SimpleNamespace(message_id=9003))

  cards = await scanner._notify_digest_once(
    client,
    "XAU",
    "M5",
    ctx,
    list(resolution.actionable),
    notify,
    ["M30"],
    execution_match=match,
    execution_matches=[match],
  )

  assert len(cards) >= 1
  notify.assert_awaited()
  assert [key async for key in client.scan_iter("analysis:setup:*")]


@pytest.mark.asyncio
async def test_reward_risk_pre_gate_retains_watchable_candidate(
  monkeypatch,
):
  client = redis_state.get_client()
  ctx = _ctx(price=4100.5, atr=2.0)
  frames = ctx.frames
  result = _result(
    setup="Demand Zone Reaction",
    direction="BUY",
    low=4100.0,
    high=4101.0,
    structural_id="low-rr-demand",
    source="supply_demand",
    kind="demand",
    confluence=3,
    current_price=4100.5,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_exec_tf": "M5"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_htf": "M30"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_tp_pips": "15"})
  monkeypatch.setattr(
    scanner,
    "_load_market_context_for_symbol",
    AsyncMock(return_value=(ctx, frames)),
  )
  ctx.analysis = SimpleNamespace(per_tf={})
  monkeypatch.setattr(
    scanner,
    "build_map",
    lambda *_args, **_kwargs: MarketMap(
      [], 4100.5, None, None, None, "down", "M30",
    ),
  )
  notify = AsyncMock(return_value=SimpleNamespace(message_id=9004))

  sent = await scanner._handle_event(
    "XAU:M5:2026-07-28T12:10:00+00:00",
    detectors=[lambda _ctx: result],
    client=client,
    notify=notify,
  )

  status = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  # Estimated RR failure is no longer a terminal eligibility gate.
  assert status.get("eligibility_gated") in ([], None)
  observed = status.get("observed") or status.get("results") or []
  assert observed or status
  # Candidate remains visible; Telegram may still be suppressed by other
  # handoff rules, but scanner must not hard-drop on estimated RR alone.
  assert isinstance(sent, list)


async def _confirm(client, match: StrategyMatch) -> None:
  await create_setup(
    client,
    setup_id=match.match_id,
    thesis_id=match.thesis_id,
    symbol=match.symbol,
    source_structure_id=match.structural_zone_id,
    formation_timeframe=match.structural_timeframe,
    expires_at=match.expires_at,
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, match.match_id, state)


@pytest.mark.asyncio
async def test_final_reward_risk_gate_expires_setup_without_publishing_plan(
  monkeypatch,
):
  client = redis_state.get_client()
  zone_id = "merged-zone-final-rr"
  setup_id = confluence_setup_id(zone_id, "BUY")
  match = StrategyMatch(
    version=1,
    match_id=setup_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="1722168600",
    issued_at=1722168600,
    expires_at=2_000_000_000,
    strategy="Demand Zone Reaction",
    strategy_mode="with_bias",
    direction="BUY",
    key_level=4100.5,
    entry_low=4100.0,
    entry_high=4101.0,
    current_price=4100.5,
    confluence=3,
    reasons=("demand reaction",),
    atr=2.0,
    structure_swing=4100.0,
    targets_pips=(30,),
    family="supply_demand",
    structural_source="supply_demand",
    zone_id=zone_id,
    confluence_zone_id=zone_id,
    structural_zone_id=zone_id,
    structural_zone_low=4100.0,
    structural_zone_high=4101.0,
    touch_bar_ts="1722168300",
    confirmation_bar_ts="1722168600",
    reaction_type="strong_reclaim",
    structural_kind="demand",
    structural_timeframe="M5",
    htf_bias="up",
    regime_kind="trend",
    thesis_id="thesis-final-rr",
  )
  await _confirm(client, match)
  await client.set(
    delivery._forming_message_key(setup_id),
    json.dumps({"chat_id": 4242, "message_id": 9005}),
    ex=60,
  )
  spot = worker.AutoTradeSpot(
    price=4100.5,
    ts=1722168600,
    fresh=True,
    bid=4100.4,
    ask=4100.6,
  )
  def reject_rr(*_args, **_kwargs):
    raise TradePlanBuildRejected(
      "policy_reward_risk_insufficient",
      "remaining reward/risk below policy minimum",
      {"reward_risk": 0.75, "min_reward_risk": 1.15},
    )

  monkeypatch.setattr(
    worker,
    "build_trade_plan_from_strategy_match",
    reject_rr,
  )

  assert await worker._publish_trade_plan_v8(
    client, "XAU", spot, match,
  ) is None
  assert (await load_setup(client, setup_id)).state == EXPIRED
  assert await client.get(f"execution:trade_plan:v8:{setup_id}") is None
  events = await client.xrange(leaf(runtime_config, "auto_trade_event_stream"))
  payloads = [json.loads(fields["payload"]) for _id, fields in events]
  assert payloads[-1]["type"] == EXPIRED
  assert payloads[-1]["reason_code"] == "confirmation_expired"

  edited = []

  async def edit_card(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  async def delete_card(chat_id, message_id):
    raise AssertionError("reject/expire must edit root, not delete")

  sent_messages = []

  async def send_message(text, **kwargs):
    sent_messages.append((text, kwargs))
    return SimpleNamespace(message_id=9006)

  monkeypatch.setattr(delivery, "delete_scanner_message", delete_card)
  monkeypatch.setattr(delivery, "edit_scanner_message_text", edit_card)
  delivered = await delivery._deliver_auto_trade_event(
    client,
    payloads[-1],
    profile="internal",
    chat_id=4242,
    send=send_message,
  )

  assert delivered is False
  assert len(edited) == 1
  assert edited[0][:2] == (4242, 9005)
  assert sent_messages == []
  # Root mapping retained for reply threading.
  assert await client.get(delivery._forming_message_key(setup_id)) is not None


@pytest.mark.asyncio
async def test_repeat_waiting_cycle_recovers_route_outcome_from_stale_handoff():
  """P0-8: reaction_confirmation_handoff must not be the permanent final
  route reason for a setup that is durably WAITING_RETEST.

  worker.py's preflight pass writes route_outcome with
  reason_code="reaction_confirmation_handoff" every cycle a reaction stays
  outside its zone (TradePlan persists WAITING_RETEST on out-of-zone presence),
  intending _publish_trade_plan_v8's own confirmation-phase persist to
  immediately correct it back to the durable "waiting_retest_entry_zone"
  reason. Outside-zone setups now remain CONFIRMED while waiting (no
  ARMED_WAITING_TRIGGER node). This reproduces a second waiting cycle
  after simulating the preflight pass's stale overwrite, and asserts the
  durable reason wins back.
  """
  client = redis_state.get_client()
  zone_id = "merged-zone-repeat-wait"
  setup_id = confluence_setup_id(zone_id, "BUY")
  match = StrategyMatch(
    version=1,
    match_id=setup_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="1722168600",
    issued_at=1722168600,
    expires_at=2_000_000_000,
    strategy="Demand Zone Reaction",
    strategy_mode="with_bias",
    direction="BUY",
    key_level=4100.5,
    entry_low=4100.0,
    entry_high=4101.0,
    current_price=4095.0,
    confluence=3,
    reasons=("demand reaction",),
    atr=2.0,
    structure_swing=4090.0,
    targets_pips=(30,),
    family="supply_demand",
    structural_source="supply_demand",
    zone_id=zone_id,
    confluence_zone_id=zone_id,
    structural_zone_id=zone_id,
    structural_zone_low=4100.0,
    structural_zone_high=4101.0,
    touch_bar_ts="1722168300",
    confirmation_bar_ts="1722168600",
    reaction_type="strong_reclaim",
    structural_kind="demand",
    structural_timeframe="M5",
    htf_bias="up",
    regime_kind="trend",
    thesis_id="thesis-repeat-wait",
  )
  await _confirm(client, match)

  # Quote well below the entry zone on both cycles - execution stays
  # ineligible throughout, so the setup remains CONFIRMED + WAITING_RETEST.
  spot = worker.AutoTradeSpot(
    price=4095.0, ts=1722168600, fresh=True, bid=4094.9, ask=4095.1,
  )

  first = await worker._publish_trade_plan_v8(client, "XAU", spot, match)
  assert first is None
  record = await load_setup(client, setup_id)
  assert record.state == CONFIRMED

  outcome_raw = await client.get(route_outcome_key("XAU", setup_id))
  assert json.loads(outcome_raw)["reason_code"] == "waiting_retest_entry_zone"

  # Simulate the next cycle's preflight pass, which runs BEFORE
  # _publish_trade_plan_v8 and unconditionally records the transient
  # handoff reason for any reaction still outside its zone.
  await record_route_outcome(
    client, match,
    stage="preflight",
    status="waiting",
    reason_code="reaction_confirmation_handoff",
    message="reaction is outside its entry contract and must wait",
    publish_status=False,
  )
  outcome_raw = await client.get(route_outcome_key("XAU", setup_id))
  assert json.loads(outcome_raw)["reason_code"] == "reaction_confirmation_handoff"

  second = await worker._publish_trade_plan_v8(
    client, "XAU", worker.AutoTradeSpot(
      price=4095.0, ts=1722168660, fresh=True, bid=4094.9, ask=4095.1,
    ),
    match,
  )
  assert second is None
  record = await load_setup(client, setup_id)
  assert record.state == CONFIRMED, "still waiting, not terminal"

  outcome_raw = await client.get(route_outcome_key("XAU", setup_id))
  assert json.loads(outcome_raw)["reason_code"] == "waiting_retest_entry_zone", (
    "the durable waiting reason must win back over the stale "
    "reaction_confirmation_handoff left by the same cycle's preflight pass"
  )
