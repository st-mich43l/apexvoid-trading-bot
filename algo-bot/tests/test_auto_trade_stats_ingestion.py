import json
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import pytest

from app.autotrade import stats_ingestion
from app.persistence import redis_state, store
from app.signals.reports import build_stats


@pytest.mark.asyncio
async def test_terminal_result_inherits_setup_from_labelled_fill(sql):
  """Regression for the Aug 6-14 result rows that lost their fill label."""
  await store.init_db()
  gid = "v6:terminal-result-setup"
  await store.record_auto_trade_event({
    "type": "opened",
    "timestamp": 10,
    "position_id": 99000,
    "group_id": gid,
    "candidate_id": gid,
    "stream": "algo_auto",
    "symbol": "XAU",
    "setup": "Flip Zone",
    "direction": "BUY",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "group_result",
    "timestamp": 20,
    "position_id": 99000,
    "group_id": gid,
    "group_realized_pips": 35.0,
  })

  assert await sql.val(
    "SELECT setup_type FROM auto_trade_results WHERE group_id = $1", gid,
  ) == "Flip Zone"


@pytest.mark.asyncio
async def test_no_tp_archived_without_price_records_stop_distance_loss():
  """Bare 'no TP archived' closes used to vanish from /trade_stats."""
  await store.init_db()
  gid = "v8:loss-no-price"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 10,
    "position_id": 99001,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 20,
    "position_id": 99001,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "message": "PLAN CLOSED · no TP archived",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 50


@pytest.mark.asyncio
async def test_no_tp_archived_parses_losing_pips_from_message():
  await store.init_db()
  gid = "v8:loss-from-message"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 30,
    "position_id": 99002,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "SELL",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4055.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 40,
    "position_id": 99002,
    "group_id": gid,
    "candidate_id": gid,
    "message": "PLAN CLOSED · no TP archived · losing -47 pips",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 47


@pytest.mark.asyncio
async def test_highest_tp_then_be_exit_is_not_a_loss():
  """TP booked then residual SL/BE must stay a win (highest TP archived)."""
  await store.init_db()
  gid = "v8:tp-then-be"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 50,
    "position_id": 99003,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 60,
    "position_id": 99003,
    "group_id": gid,
    "candidate_id": gid,
    # Residual exit below entry would look like a loss if we used price alone.
    "price": 4049.0,
    "target_pips": 41,
    "group_realized_pips": -10,
    "message": "PLAN CLOSED · highest TP archived TP1 · @ 4049.00",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "+"
  assert hit[0]["pips"] == 41


@pytest.mark.asyncio
async def test_deferred_tp_stopout_records_loss_not_false_archive(sql):
  await store.init_db()
  gid = "v8:deferred-tp-stopout"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 70,
    "position_id": 99004,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Trend Pullback",
    "symbol": "XAU",
    "price": 4636.98,
    "stop_loss": 4631.04,
    "volume": 600,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 80,
    "position_id": 99004,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "symbol": "XAU",
    "price": 4630.96,
    "stop_loss": 4631.04,
    "target_pips": 31,
    "group_realized_pips": -60,
    "previous_state": "fully_open",
    "reason_code": "manual_or_external_close",
    "message": "PLAN CLOSED · highest TP archived TP1 · @ 4630.96",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 60
  assert await sql.val(
    "SELECT booked_tp_count FROM auto_trade_results WHERE group_id = $1",
    gid,
  ) == 0


@pytest.mark.asyncio
async def test_startup_backfill_recovers_retained_algo_results(monkeypatch):
  await store.init_db()
  client = redis_state.get_client()
  stream = "auto_trade:test_stats_events"
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_event_stream": stream})
  fill = {
    "type": "order_filled",
    "timestamp": 100,
    "position_id": 7001,
    "group_id": "v8:stats-recovery",
    "candidate_id": "v8:stats-recovery",
    "stream": "algo_auto",
    "symbol": "XAU",
    "setup": "Key Level",
    "direction": "BUY",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  }
  closed = {
    "type": "position_closed",
    "timestamp": 200,
    "position_id": 7001,
    "group_id": "v8:stats-recovery",
    "candidate_id": "v8:stats-recovery",
    "stream": "algo_auto",
    "symbol": "XAU",
    "direction": "BUY",
    "price": 4051.0,
    "target_pips": 90,
    "message": "PLAN CLOSED · highest TP archived TP3",
  }
  await client.xadd(stream, {"payload": json.dumps(fill)})
  last_id = await client.xadd(stream, {"payload": json.dumps(closed)})

  cursor = await stats_ingestion.backfill_retained_auto_trade_stats(client)
  records = await store.get_pips_records(0, 1_000)
  stats = build_stats(records, [], "UTC", 0, 8, 13)

  assert cursor == str(last_id)
  assert len(records) == 1
  assert records[0]["stream"] == "algo_auto"
  assert records[0]["pips"] == 90
  assert stats["trades"] == 1
  assert stats["by_stream"]["algo_auto"]["total_pips"] == 90

  # A restart reuses the independent stats cursor and remains idempotent.
  assert await stats_ingestion.backfill_retained_auto_trade_stats(client) == cursor
  assert len(await store.get_pips_records(0, 1_000)) == 1

  # If the executor kept publishing while the bot was down, startup catches
  # up everything after the stored cursor before /trade_stats is registered.
  fill_2 = {
    **fill,
    "timestamp": 300,
    "position_id": 7002,
    "group_id": "v8:stats-recovery-2",
    "candidate_id": "v8:stats-recovery-2",
    "direction": "SELL",
    "price": 4060.0,
    "stop_loss": 4065.0,
  }
  closed_2 = {
    **closed,
    "timestamp": 400,
    "position_id": 7002,
    "group_id": "v8:stats-recovery-2",
    "candidate_id": "v8:stats-recovery-2",
    "direction": "SELL",
    "target_pips": 60,
    "message": "PLAN CLOSED · highest TP archived TP2",
  }
  await client.xadd(stream, {"payload": json.dumps(fill_2)})
  latest_id = await client.xadd(stream, {"payload": json.dumps(closed_2)})

  assert (
    await stats_ingestion.backfill_retained_auto_trade_stats(client)
    == str(latest_id)
  )
  recovered = await store.get_pips_records(0, 1_000)
  assert [row["pips"] for row in recovered] == [90, 60]


@pytest.mark.asyncio
async def test_close_before_fill_still_records_trade_stats():
  """Live race: position_closed can beat order_filled in the stream."""
  await store.init_db()
  gid = "v8:close-before-fill"
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 20,
    "position_id": 99101,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "symbol": "XAU",
    "setup": "Key Level",
    "stream": "algo_auto",
    "price": 4045.0,
    "entry_price": 4050.0,
    "stop_loss": 4045.0,
    "message": "PLAN CLOSED · no TP archived · losing -50 pips · @ 4045.00",
    "group_realized_pips": -50,
  })
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 10,
    "position_id": 99101,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  })
  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 50


@pytest.mark.asyncio
async def test_plan_runtime_reconcile_recovers_unknown_leg_close_orphan():
  await store.init_db()
  gid = "v8:runtime-orphan"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 100,
    "position_id": 99102,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "HFS Range Sweep",
    "symbol": "XAU",
    "price": 4642.018,
    "stop_loss": 4640.67,
    "volume": 1500,
  })
  assert not any(
    row["trade_key"] == f"algo:{gid}"
    for row in await store.get_pips_records(0, 10**12)
  )
  runtime = {
    "PlanId": gid,
    "Symbol": "XAU",
    "Direction": "BUY",
    "Stage": "Closed",
    "GroupStage": "closed",
    "PositionId": 99102,
    "EntryFillPrice": 4642.018,
    "GroupWeightedFillPrice": 4642.018,
    "RemainingVolume": 0,
    "CurrentStop": 4640.67,
    "HighestBookedTargetIndex": -1,
    "TerminalReason": "unknown_leg_close",
    "Legs": [
      {"LegId": "L1", "BrokerPositionId": 99102, "Stage": "closed", "RemainingVolume": 0},
    ],
  }
  wrote = await store.reconcile_orphan_auto_trade_result(gid, runtime, closed_at=200)
  assert wrote is True
  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 13


@pytest.mark.asyncio
async def test_backfill_dumper_two_pass_and_bytes_payload(monkeypatch):
  await store.init_db()
  from app.scripts import backfill_auto_trade_stats as dumper

  stream = "auto_trade:test_dumper_stats"
  install_runtime_overrides(
    monkeypatch, legacy_overrides={"auto_trade_event_stream": stream},
  )
  client = redis_state.get_client()
  await client.delete(stream)
  fill = {
    "type": "order_filled",
    "timestamp": 10,
    "position_id": 99103,
    "group_id": "v8:dumper-two-pass",
    "candidate_id": "v8:dumper-two-pass",
    "stream": "algo_auto",
    "symbol": "XAU",
    "setup": "Key Level",
    "direction": "SELL",
    "price": 4050.0,
    "stop_loss": 4055.0,
    "volume": 800,
  }
  closed = {
    "type": "position_closed",
    "timestamp": 20,
    "position_id": 99103,
    "group_id": "v8:dumper-two-pass",
    "candidate_id": "v8:dumper-two-pass",
    "stream": "algo_auto",
    "symbol": "XAU",
    "direction": "SELL",
    "price": 4045.0,
    "target_pips": 50,
    "message": "PLAN CLOSED · highest TP archived TP1",
  }
  # Close inserted before fill in Redis; dumper must still fill-then-close.
  await client.xadd(stream, {b"payload": json.dumps(closed).encode()})
  await client.xadd(stream, {b"payload": json.dumps(fill).encode()})

  stats = await dumper.backfill(
    count=None, stream=stream, runtime_reconcile=False,
  )
  assert stats["fill_events"] == 1
  assert stats["result_events"] == 1
  rows = await store.get_pips_records(0, 1_000)
  hit = [row for row in rows if row["trade_key"] == "algo:v8:dumper-two-pass"]
  assert len(hit) == 1
  assert hit[0]["pips"] == 50


@pytest.mark.asyncio
async def test_algo_manual_group_result_does_not_dilute_peak(sql):
  """group_result blend must not overwrite legs_achieved peak in /trade_stats."""
  await store.init_db()
  signal = await store.store_manual_signal(
    ts=1,
    action="SELL",
    entry=4436.0,
    entry_end=4440.0,
    sl=4445.0,
    tps=[4431.0, 4426.0],
    setup_type="breakout-retest",
    execution_mode="algo",
  )
  sid = int(signal["id"])
  gid = f"manual:{sid}"
  await sql.exec(
    "UPDATE manual_signals SET trade_stream='algo_manual', "
    "execution_intent_id=$2, legs=$3, result_pips=50, status='closed' "
    "WHERE id=$1",
    sid,
    f"manual:{sid}:0",
    json.dumps([
      {"frac": 0.25, "pips": 47, "ts": 10},
      {"frac": 1.0, "pips": 50, "ts": 20},
    ]),
  )
  await store.record_auto_trade_event({
    "type": "manual_opened",
    "timestamp": 5,
    "position_id": 88001,
    "group_id": gid,
    "candidate_id": f"manual:{sid}:0",
    "stream": "algo_manual",
    "symbol": "XAU",
    "setup": "breakout-retest",
    "direction": "SELL",
    "price": 4436.02,
    "stop_loss": 4445.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 15,
    "position_id": 88001,
    "group_id": gid,
    "stream": "algo_manual",
    "symbol": "XAU",
    "direction": "SELL",
    "price": 4431.0,
    "group_realized_pips": 50,
    "message": "position closed · winning 50.0 pips",
  })
  await store.record_auto_trade_event({
    "type": "group_result",
    "timestamp": 20,
    "group_id": gid,
    "stream": "algo_manual",
    "group_realized_pips": 0.6,
    "message": f"group {gid} realised 0.6 pips",
  })
  rows = await store.get_pips_records(0, 10**12)
  algo = [row for row in rows if row["stream"] == "algo_manual" and row["trade_key"] == gid]
  assert len(algo) == 1
  assert algo[0]["pips"] == 50
  assert algo[0]["sign"] == "+"
  stored = await sql.val(
    "SELECT result_pips FROM auto_trade_results WHERE group_id=$1", gid,
  )
  assert float(stored) == 50.0


@pytest.mark.asyncio
async def test_two_clip_fills_use_volume_weighted_stop_for_r_multiple():
  """entry_clips=2 must not inflate R via AVG(stop_pips)."""
  await store.init_db()
  gid = "v8:two-clip-r"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 100,
    "position_id": 77001,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4000.0,
    "stop_loss": 3995.0,
    "volume": 400,
  })
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 101,
    "position_id": 77002,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level",
    "symbol": "XAU",
    "price": 4001.0,
    "stop_loss": 3995.0,
    "volume": 600,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 110,
    "position_id": 77001,
    "group_id": gid,
    "candidate_id": gid,
    "message": "PLAN CLOSED · highest TP archived TP2",
    "target_pips": 100,
    "group_realized_pips": 100,
    "break_even_applied": True,
    "highest_booked_target_index": 1,
    "planned_reward_risk": 2.0,
    "target_room_fallback_used": False,
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  # stop_pips: clip1=50, clip2=60 → VW = (50*400 + 60*600) / 1000 = 56
  assert hit[0]["stop_pips"] == pytest.approx(56.0)
  assert hit[0]["r_multiple"] == pytest.approx(100.0 / 56.0)
  assert hit[0]["planned_reward_risk"] == pytest.approx(2.0)
  assert hit[0]["target_room_fallback_used"] is False
  assert hit[0]["exit_path"] == "tp2_full"


@pytest.mark.asyncio
async def test_exit_path_and_planned_reward_risk_round_trip():
  await store.init_db()
  gid = "v8:tp1-be-path"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 200,
    "position_id": 77011,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Trend Pullback",
    "symbol": "XAU",
    "price": 4000.0,
    "stop_loss": 3995.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 210,
    "position_id": 77011,
    "group_id": gid,
    "message": "PLAN CLOSED · highest TP archived TP1 · break-even",
    "target_pips": 50,
    "break_even_applied": True,
    "highest_booked_target_index": 0,
    "planned_reward_risk": 2.0,
    "target_room_fallback_used": False,
  })
  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["exit_path"] == "tp1_be"
  assert hit[0]["planned_reward_risk"] == pytest.approx(2.0)
  assert hit[0]["target_room_fallback_used"] is False

  gid2 = "v8:one-r-fallback"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 300,
    "position_id": 77021,
    "group_id": gid2,
    "candidate_id": gid2,
    "direction": "BUY",
    "setup": "Trend Pullback",
    "symbol": "XAU",
    "price": 4000.0,
    "stop_loss": 3995.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 310,
    "position_id": 77021,
    "group_id": gid2,
    "message": "PLAN CLOSED · highest TP archived TP1",
    "target_pips": 50,
    "planned_reward_risk": 1.0,
    "target_room_fallback_used": True,
    "highest_booked_target_index": 0,
    "break_even_applied": False,
  })
  rows2 = await store.get_pips_records(0, 10**12)
  hit2 = [row for row in rows2 if row["trade_key"] == f"algo:{gid2}"]
  assert len(hit2) == 1
  assert hit2[0]["exit_path"] == "tp1_stop"
  assert hit2[0]["planned_reward_risk"] == pytest.approx(1.0)
  assert hit2[0]["target_room_fallback_used"] is True
