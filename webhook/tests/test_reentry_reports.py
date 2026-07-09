import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import dedup, telegram, trade_ops
from app.reports import _round_lines, build_stats, format_review, format_stats, sparkline


@pytest.mark.asyncio
async def test_reopen_inherits_original_stop_not_moved_sl():
  await dedup.init_db()
  src = await dedup.store_manual_signal(
    1, "BUY", 4100.0, 4105.0, 4088.0, [4130.0], symbol="XAU",
  )
  # TP1 → move stop to break-even (entry mid). sl is overwritten in place.
  await dedup.update_sl(src["id"], 4102.5)
  moved = await dedup.get_manual_signal(src["id"])
  assert moved["sl"] == 4102.5 and moved["original_sl"] == 4088.0

  await dedup.close_leg(src["id"], 0)  # round must end before it can reopen
  result = await trade_ops.do_reopen({
    "sid": src["id"], "symbol": "XAU", "entry_override": None,
  })
  round2 = await dedup.get_manual_signal(result["record"]["id"])
  # Round 2 must start from the ORIGINAL stop, not the break-even one.
  assert round2["sl"] == 4088.0
  assert round2["original_sl"] == 4088.0


@pytest.mark.asyncio
async def test_reopen_rejects_open_signal():
  await dedup.init_db()
  src = await dedup.store_manual_signal(
    1, "BUY", 4100.0, 4105.0, 4088.0, [4130.0], symbol="XAU",
  )  # left OPEN on purpose
  result = await trade_ops.do_reopen({
    "sid": src["id"], "symbol": "XAU", "entry_override": None,
  })
  assert result["ok"] is False
  assert result["error"] == "still_open"
  # No re-entry round was created; the cluster is still just the source.
  assert len(await dedup.get_signal_cluster(src["id"])) == 1


def test_reports_risk_uses_original_stop():
  # Stop moved to BE (= entry mid); realized R must still use the original stop.
  signal = {
    "id": 1, "daily_seq": 1, "action": "BUY", "symbol": "XAU",
    "entry": 4100.0, "entry_end": 4105.0,
    "sl": 4102.5, "original_sl": 4088.0,
    "tps": [4130.0], "result_pips": 145, "legs": [],
  }
  lines = "\n".join(_round_lines(signal, 1))
  # risk = |4102.5 - 4088| = 14.5 → 145 pips; net 145 → realized ~1.0R.
  assert "~1.0R" in lines


async def _new_signal(
  ts: int,
  setup_type: str = "ob-retest",
  confluence: int = 3,
) -> dict:
  return await dedup.store_manual_signal(
    ts,
    "BUY",
    4100.0,
    4105.0,
    4088.0,
    [4195.0, 4190.0, 4180.0],
    setup_type=setup_type,
    confluence=confluence,
  )


@pytest.mark.asyncio
async def test_reopen_creates_independent_root_linked_rounds(
  tmp_path,
  monkeypatch,
):
  await dedup.init_db()
  source_rec = await _new_signal(1)
  await dedup.close_leg(source_rec["id"], 70)
  source_before = await dedup.get_manual_signal(source_rec["id"])

  send = AsyncMock(return_value=SimpleNamespace(message_id=801))
  monkeypatch.setattr(telegram, "_send_with_retry", send)
  first = await telegram._reopen_signal(source_rec["id"], None, None)
  first_row = await dedup.get_manual_signal(first[0]["id"])
  source_after = await dedup.get_manual_signal(source_rec["id"])

  assert source_after == source_before
  assert first_row["daily_seq"] == source_rec["daily_seq"] + 1
  assert first_row["parent_id"] == source_rec["id"]
  assert first_row["fill_state"] == "pending"
  assert first_row["setup_type"] == "ob-retest"
  assert "round 2 from #1" in first[1]

  await dedup.close_leg(first_row["id"], 40)  # close round 2 before reopening it
  send.return_value = SimpleNamespace(message_id=802)
  second = await telegram._reopen_signal(first_row["id"], 4101.0, 4104.0)
  second_row = await dedup.get_manual_signal(second[0]["id"])

  assert second_row["parent_id"] == source_rec["id"]
  assert second_row["daily_seq"] == first_row["daily_seq"] + 1
  assert "round 3 from #2" in second[1]
  assert len(await dedup.get_signal_cluster(second_row["id"])) == 3


def test_entry_setup_segment_parses_like_tag():
  signal = telegram._parse_manual(
    "gold sell 4100-4105 / sl 4110 / tp 95/90/80 "
    "/ setup OB-Retest ***"
  )

  assert signal["setup_type"] == "ob-retest"
  assert signal["confluence"] == 3


def test_entry_scalp_option_sets_internal_setup():
  signal = telegram._parse_manual(
    "gold sell 4100-4105 / sl 4110 / tp 95/90/80 / scalp"
  )

  assert signal["setup_type"] == "scalp"
  assert signal["confluence"] is None
  assert signal["visibility"] == "both"


def test_entry_scalp_nhanh_option_and_setup_override():
  base = "gold buy 4100-4105 / sl 4090 / tp 10/20"

  assert telegram._parse_manual(
    base + " / scalp nhanh / vip"
  )["setup_type"] == "scalp"
  parsed = telegram._parse_manual(
    base + " / scalp / setup breakout-retest **"
  )
  assert parsed["setup_type"] == "breakout-retest"
  assert parsed["confluence"] == 2


@pytest.mark.parametrize(
  ("zone", "expected"),
  [
    ("4168-71", (4168.0, 4171.0)),
    ("4198-02", (4198.0, 4202.0)),
    ("4102-98", (4098.0, 4102.0)),
    ("4168-4171", (4168.0, 4171.0)),
  ],
)
def test_short_entry_endpoint_expands_near_anchor(zone, expected):
  signal = telegram._parse_manual(
    f"gold sell {zone} / sl 4210 / tp 60"
  )

  assert (signal["entry"], signal["entry_end"]) == expected


@pytest.mark.asyncio
async def test_tag_command_updates_metadata(tmp_path, monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  await dedup.init_db()
  rec = await _new_signal(1, setup_type=None, confluence=None)
  msg = SimpleNamespace(
    text=f"/trade_tag #{rec['daily_seq']} ob-retest ***",
    from_user=SimpleNamespace(id=42),
    answer=AsyncMock(),
  )
  monkeypatch.setattr(
    telegram,
    "post_result",
    AsyncMock(return_value="tagged"),
  )

  await telegram.handle_trade_tag(msg)

  row = await dedup.get_manual_signal(rec["id"])
  assert row["setup_type"] == "ob-retest"
  assert row["confluence"] == 3


@pytest.mark.asyncio
async def test_linked_accounting_stats_and_cluster_review(tmp_path, monkeypatch):
  await dedup.init_db()
  source = await _new_signal(1)
  await dedup.set_note(source["id"], "Retest held at the key level")

  await dedup.close_leg(source["id"], 50, 0.5)
  await telegram._book_leg(source["id"], 90, None, -1001)

  round_two = await dedup.store_manual_signal(
    2,
    "BUY",
    4100.0,
    4105.0,
    4088.0,
    [4195.0, 4190.0, 4180.0],
    parent_id=source["id"],
    setup_type="ob-retest",
    confluence=3,
  )
  await telegram._book_leg(round_two["id"], -30, None, -1001)

  records = await dedup.get_pips_records(0, 4_000_000_000)
  signals = await dedup.get_all_signals()
  stats = build_stats(
    records,
    signals,
    "Asia/Ho_Chi_Minh",
    22,
    7,
    13,
  )
  report = format_stats(stats, "all")
  review = format_review(await dedup.get_signal_cluster(round_two["id"]))

  assert [row["signal_id"] for row in records] == [
    source["id"],
    round_two["id"],
  ]
  assert "Trades: 2" in report
  assert "Net: <b>+40 pips</b>" in report
  assert "Expectancy: +20p per trade" in report
  assert "zone 4100–4105 BUY: 2 rounds, 1W/1L, +40p" in report
  assert "Round 1 · #1" in review
  assert "Round 2 · #2" in review
  assert "Cluster:</b> 2 rounds · 1W / 1L · net +40p" in review
  assert review.count("st_mich43l · auto-map") == 2
  assert "Result: 70 pips win" in review
  assert "Result: 30 pips loss" in review
  assert "+70 pips" not in review
  assert "execution" not in review.lower()
  assert "mechanics" not in review.lower()


def test_stats_groups_sessions_and_sparkline():
  tz = ZoneInfo("Asia/Ho_Chi_Minh")
  base = datetime(2026, 7, 3, tzinfo=tz)
  signals = [
    {
      "id": 1, "parent_id": None, "entry": 4100, "entry_end": 4105,
      "action": "BUY",
    },
    {
      "id": 2, "parent_id": 1, "entry": 4100, "entry_end": 4105,
      "action": "BUY",
    },
    {
      "id": 3, "parent_id": None, "entry": 4110, "entry_end": 4112,
      "action": "SELL",
    },
  ]
  records = [
    {
      "sign": "+", "pips": 70, "signal_id": 1,
      "setup_type": "ob-retest",
      "signal_ts": int(base.replace(hour=23).timestamp()),
    },
    {
      "sign": "-", "pips": 30, "signal_id": 2,
      "setup_type": "ob-retest",
      "signal_ts": int(base.replace(hour=8).timestamp()),
    },
    {
      "sign": "+", "pips": 20, "signal_id": 3,
      "setup_type": "breakout-retest",
      "signal_ts": int(base.replace(hour=14).timestamp()),
    },
  ]

  stats = build_stats(
    records,
    signals,
    "Asia/Ho_Chi_Minh",
    22,
    7,
    13,
  )
  report = format_stats(stats, "week")

  assert "ob-retest: 2 · 1W/1L · +40p · 50%" in report
  assert "Asia: 1" in report
  assert "London: 1" in report
  assert "NY: 1" in report
  assert len(sparkline([70, 40, 60])) == 3


def test_review_map_renders_all_tp_tiers_and_last_branch():
  signal = {
    "id": 1,
    "daily_seq": 1,
    "action": "BUY",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4088.0,
    "tps": [4110.0, 4120.0, 4130.0, 4140.0, 4150.0],
    "status": "open",
    "result_pips": None,
    "legs": [],
    "symbol": "XAU",
  }

  review = format_review([signal])

  assert "├ 🥉 TP1 4110" in review
  assert "├ 🥈 TP2 4120" in review
  assert "├ 🥇 TP3 4130" in review
  assert "├ 🎯 TP4 4140" in review
  assert "└ 🎯 TP5 4150" in review
