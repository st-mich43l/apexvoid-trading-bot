import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from app.persistence import redis_state, store
from app.signals.manual_intent import build_intent, publish_intent


def _signal(**overrides) -> dict:
  base = {
    "id": 47,
    "ts": 1_800_000_000,
    "action": "SELL",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0, 4090.0, 4080.0],
    "setup_type": "golden-fib",
    "confluence": 2,
    "trade_date": "2024-01-15",
  }
  base.update(overrides)
  return base


def _end_of_trade_day(trade_date: str) -> int:
  tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  day = datetime.fromisoformat(trade_date).date()
  return int(datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).timestamp())


def test_build_intent_maps_fields_and_formats_intent_id():
  intent = build_intent(_signal(), revision=0)

  assert intent.intent_id == "manual:47:0"
  assert intent.manual_signal_id == 47
  assert intent.revision == 0
  assert intent.direction == "SELL"
  assert intent.entry_low == pytest.approx(4100.0)
  assert intent.entry_high == pytest.approx(4105.0)
  assert intent.sl == pytest.approx(4110.0)
  assert intent.tps == (4095.0, 4090.0, 4080.0)
  assert intent.created_at == 1_800_000_000
  assert intent.expires_at == _end_of_trade_day("2024-01-15")
  assert intent.setup_type == "golden-fib"
  assert intent.confluence == 2
  assert intent.execution_mode == "algo"


def test_build_intent_expires_at_end_of_trade_day_local_tz():
  intent = build_intent(_signal(trade_date="2024-03-01"))

  tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  expiry = datetime.fromtimestamp(intent.expires_at, tz=tz)
  assert expiry == datetime(2024, 3, 2, 0, 0, tzinfo=tz)


def test_build_intent_falls_back_to_today_when_trade_date_missing():
  signal = _signal()
  del signal["trade_date"]

  intent = build_intent(signal)

  tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  today = datetime.now(tz).date()
  assert intent.expires_at == _end_of_trade_day(today.isoformat())


def test_build_intent_respects_revision():
  intent = build_intent(_signal(), revision=3)

  assert intent.intent_id == "manual:47:3"
  assert intent.revision == 3


def test_build_intent_defaults_created_at_to_signal_ts():
  # Correct for the very first arm, built moments after the signal itself
  # is created (fallback.py::_arm_algo_intent) - the only caller that
  # relies on this default.
  intent = build_intent(_signal(ts=1_800_000_000))

  assert intent.created_at == 1_800_000_000


def test_build_intent_created_at_override_ignores_stale_signal_ts():
  # Owner-reported 2026-09: /trade_modify re-arming a bumped revision with
  # the ORIGINAL signal.ts (typed long before the modify) produced a
  # candidate AutoTradeEngine.cs rejected outright as "stale candidate" -
  # no broker fill, no owner-visible error beyond that bare reject reason.
  # A re-arm must be able to stamp "now" instead.
  intent = build_intent(
    _signal(ts=1_800_000_000), revision=1, created_at=1_800_050_000,
  )

  assert intent.created_at == 1_800_050_000


def test_build_intent_handles_missing_optional_setup_metadata():
  signal = _signal()
  del signal["setup_type"]
  del signal["confluence"]

  intent = build_intent(signal)

  assert intent.setup_type is None
  assert intent.confluence is None


@pytest.mark.asyncio
async def test_publish_intent_xadds_full_payload_to_configured_stream(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_intent_stream": "manual_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_intent_stream_maxlen": 100})
  client = redis_state.get_client()
  intent = build_intent(_signal())

  await publish_intent(intent)

  entries = await client.xrange("manual_trade:test")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {
    "intent_id": "manual:47:0",
    "manual_signal_id": 47,
    "revision": 0,
    "direction": "SELL",
    "symbol": "XAU",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0, 4090.0, 4080.0],
    "created_at": 1_800_000_000,
    "expires_at": _end_of_trade_day("2024-01-15"),
    "setup_type": "golden-fib",
    "confluence": 2,
    "execution_mode": "algo",
  }


@pytest.mark.asyncio
async def test_publish_intent_is_one_xadd_per_call(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_intent_stream": "manual_trade:test2"})
  client = redis_state.get_client()

  await publish_intent(build_intent(_signal(id=1), revision=0))
  await publish_intent(build_intent(_signal(id=1), revision=1))

  entries = await client.xrange("manual_trade:test2")
  assert len(entries) == 2
  ids = [json.loads(e[1]["payload"])["intent_id"] for e in entries]
  assert ids == ["manual:1:0", "manual:1:1"]


@pytest.mark.asyncio
async def test_set_execution_intent_updates_row_and_returns_it():
  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )

  updated = await store.set_execution_intent(
    rec["id"], intent_id="manual:%d:0" % rec["id"], status="armed", revision=0,
  )

  assert updated is not None
  assert updated["execution_intent_id"] == f"manual:{rec['id']}:0"
  assert updated["execution_status"] == "armed"
  assert updated["algo_armed"] is True
  assert updated["execution_revision"] == 0

  row = await store.get_manual_signal(rec["id"])
  assert row["execution_intent_id"] == f"manual:{rec['id']}:0"
  assert row["execution_status"] == "armed"
  assert row["algo_armed"] is True


@pytest.mark.asyncio
async def test_set_execution_intent_returns_none_for_missing_signal():
  await store.init_db()

  result = await store.set_execution_intent(
    999999, intent_id="manual:999999:0", status="armed", revision=0,
  )

  assert result is None


@pytest.mark.asyncio
async def test_set_execution_status_updates_status_and_error():
  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )

  updated = await store.set_execution_status(rec["id"], "error", error="boom")

  assert updated is not None
  assert updated["execution_status"] == "error"
  assert updated["execution_error"] == "boom"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "expired", "rejected"])
async def test_terminal_unfilled_execution_status_clears_algo_armed(status):
  await store.init_db()
  rec = await store.store_manual_signal(
    ts=1_800_000_000,
    action="SELL",
    entry=4100.0,
    entry_end=4105.0,
    sl=4110.0,
    tps=[4095.0],
    execution_mode="algo",
  )
  await store.set_execution_intent(
    rec["id"], intent_id=f"manual:{rec['id']}:0", status="armed", revision=0,
  )

  updated = await store.set_execution_status(rec["id"], status)

  assert updated["algo_armed"] is False


@pytest.mark.asyncio
async def test_set_execution_status_returns_none_for_missing_signal():
  await store.init_db()

  result = await store.set_execution_status(999999, "error", error="boom")

  assert result is None


@pytest.mark.asyncio
async def test_manual_signal_defaults_to_notify_execution_mode():
  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
  )

  row = await store.get_manual_signal(rec["id"])

  assert row["execution_mode"] == "notify"
  assert row["execution_status"] is None
  assert row["execution_revision"] == 0
  assert row["execution_intent_id"] is None


@pytest.mark.asyncio
async def test_set_execution_fill_records_broker_position_and_price():
  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )

  updated = await store.set_execution_fill(
    rec["id"], broker_position_id=555, broker_fill_price=4100.25,
  )

  assert updated is not None
  assert updated["execution_status"] == "filled"
  assert updated["broker_position_id"] == "555"
  assert updated["broker_fill_price"] == pytest.approx(4100.25)

  row = await store.get_manual_signal(rec["id"])
  assert row["execution_status"] == "filled"
  assert row["broker_position_id"] == "555"
  assert row["broker_fill_price"] == pytest.approx(4100.25)


@pytest.mark.asyncio
async def test_set_execution_fill_keeps_shallow_ladder_risk_reference():
  """SELL: lower fill wins; BUY: higher fill wins; deep cannot shrink risk."""
  await store.init_db()
  sell = await store.store_manual_signal(
    1_800_000_001, "SELL", 4500, 4503, 4506, [4497, 4494, 4490],
    execution_mode="algo",
  )
  await store.set_execution_fill(
    sell["id"], broker_position_id=1, broker_fill_price=4500.0,
  )
  await store.set_execution_fill(
    sell["id"], broker_position_id=2, broker_fill_price=4503.0,
  )
  await store.set_execution_fill(
    sell["id"], broker_position_id=3, broker_fill_price=4501.5,
  )
  sell_row = await store.get_manual_signal(sell["id"])
  assert sell_row["broker_fill_price"] == pytest.approx(4500.0)
  assert sell_row["broker_position_id"] == "1"

  buy = await store.store_manual_signal(
    1_800_000_002, "BUY", 4500, 4503, 4497, [4506, 4509, 4512],
    execution_mode="algo",
  )
  await store.set_execution_fill(
    buy["id"], broker_position_id=10, broker_fill_price=4503.0,
  )
  await store.set_execution_fill(
    buy["id"], broker_position_id=11, broker_fill_price=4500.0,
  )
  await store.set_execution_fill(
    buy["id"], broker_position_id=12, broker_fill_price=4501.0,
  )
  buy_row = await store.get_manual_signal(buy["id"])
  assert buy_row["broker_fill_price"] == pytest.approx(4503.0)


@pytest.mark.asyncio
async def test_set_execution_fill_returns_none_for_missing_signal():
  await store.init_db()

  result = await store.set_execution_fill(
    999999, broker_position_id=1, broker_fill_price=1.0,
  )

  assert result is None


@pytest.mark.asyncio
async def test_get_signal_by_execution_intent_id_matches_full_id():
  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )
  intent_id = f"manual:{rec['id']}:0"
  await store.set_execution_intent(
    rec["id"], intent_id=intent_id, status="armed", revision=0,
  )

  found = await store.get_signal_by_execution_intent_id(intent_id)

  assert found is not None
  assert found["id"] == rec["id"]


@pytest.mark.asyncio
async def test_get_signal_by_execution_intent_id_matches_truncated_token():
  # AutoTradeEngine.cs only embeds the first 10 characters of the intent_id
  # in a broker comment (CandidateToken) - this must still resolve.
  await store.init_db()
  rec = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )
  intent_id = f"manual:{rec['id']}:0"
  await store.set_execution_intent(
    rec["id"], intent_id=intent_id, status="armed", revision=0,
  )

  found = await store.get_signal_by_execution_intent_id(intent_id[:10])

  assert found is not None
  assert found["id"] == rec["id"]


@pytest.mark.asyncio
async def test_get_signal_by_execution_intent_id_returns_none_when_unmatched():
  await store.init_db()

  found = await store.get_signal_by_execution_intent_id("manual:999999:0")

  assert found is None


@pytest.mark.asyncio
async def test_count_pending_algo_signals_counts_only_still_resting_orders():
  await store.init_db()
  pending = await store.store_manual_signal(
    1_800_000_000, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )
  await store.set_execution_status(pending["id"], "pending")

  filled = await store.store_manual_signal(
    1_800_000_001, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )
  await store.set_execution_fill(
    filled["id"], broker_position_id=1, broker_fill_price=4100.0,
  )

  cancelled = await store.store_manual_signal(
    1_800_000_002, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
    execution_mode="algo",
  )
  await store.set_execution_status(cancelled["id"], "pending")
  await store.cancel_manual_signal(cancelled["id"])

  await store.store_manual_signal(
    1_800_000_003, "SELL", 4100, 4105, 4110, [4095, 4090, 4080],
  )

  count = await store.count_pending_algo_signals()

  assert count == 1
