import json

import pytest

from app.core.config import settings
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
  }
  base.update(overrides)
  return base


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
  assert intent.expires_at is None
  assert intent.setup_type == "golden-fib"
  assert intent.confluence == 2
  assert intent.execution_mode == "algo"


def test_build_intent_respects_revision():
  intent = build_intent(_signal(), revision=3)

  assert intent.intent_id == "manual:47:3"
  assert intent.revision == 3


def test_build_intent_handles_missing_optional_setup_metadata():
  signal = _signal()
  del signal["setup_type"]
  del signal["confluence"]

  intent = build_intent(signal)

  assert intent.setup_type is None
  assert intent.confluence is None


@pytest.mark.asyncio
async def test_publish_intent_xadds_full_payload_to_configured_stream(monkeypatch):
  monkeypatch.setattr(settings, "manual_trade_intent_stream", "manual_trade:test")
  monkeypatch.setattr(settings, "manual_trade_intent_stream_maxlen", 100)
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
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0, 4090.0, 4080.0],
    "created_at": 1_800_000_000,
    "expires_at": None,
    "setup_type": "golden-fib",
    "confluence": 2,
    "execution_mode": "algo",
  }


@pytest.mark.asyncio
async def test_publish_intent_is_one_xadd_per_call(monkeypatch):
  monkeypatch.setattr(settings, "manual_trade_intent_stream", "manual_trade:test2")
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
