"""Manual /algo entry card: the zone strategy reads as the zone the trade is in, and /trade_delete never claims a card is gone when it is not.

Run from ``algo-bot``:  ``python -m unittest tests.test_manual_card_label_and_delete``  (stdlib only; needs the app's runtime dependencies).
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade.setup_card import card_setup_label  # noqa: E402
from app.autotrade.strategy_names import side_zone_label  # noqa: E402
from app.signals import broadcast  # noqa: E402
from app.signals.trade_ops import render_result  # noqa: E402


def run(coro):
  return asyncio.run(coro)


class ZoneLabelTests(unittest.TestCase):
  def test_a_zone_trade_reads_as_the_zone_it_is_in(self):
    self.assertEqual(side_zone_label("Supply Demand", "BUY"), "Demand")
    self.assertEqual(side_zone_label("Supply Demand", "SELL"), "Supply")
    self.assertEqual(side_zone_label("supply demand", "sell"), "Supply")

  def test_other_names_and_missing_direction_are_unchanged(self):
    self.assertEqual(side_zone_label("Key Level", "BUY"), "Key Level")
    self.assertEqual(side_zone_label("Supply Demand", None), "Supply Demand")
    self.assertEqual(side_zone_label(None, "BUY"), "")

  def test_the_auto_root_card_uses_the_same_label(self):
    self.assertEqual(card_setup_label("Supply Demand", "BUY"), "Demand")
    self.assertEqual(card_setup_label("Supply Demand", "SELL"), "Supply")


class ManualEntryCardTests(unittest.TestCase):
  def setup_line(self, setup_type, action, confluence=None):
    return broadcast._setup_line({"setup_type": setup_type, "action": action, "confluence": confluence})

  def test_buy_is_demand_and_sell_is_supply_whatever_the_owner_typed(self):
    for typed in ("supply", "demand", "supply demand", "Supply Demand"):
      self.assertIn("<b>Demand</b>", self.setup_line(typed, "BUY"), typed)
      self.assertIn("<b>Supply</b>", self.setup_line(typed, "SELL"), typed)
      self.assertNotIn("Supply Demand", self.setup_line(typed, "BUY"))

  def test_stars_and_other_setups_are_unchanged(self):
    self.assertEqual(self.setup_line("demand", "BUY", 2), "🏷 Setup:  <b>Demand</b>  ⭐⭐")
    self.assertIn("<b>Key Level</b>", self.setup_line("key level", "SELL"))

  def test_a_setup_suffix_is_kept(self):
    self.assertIn("Demand ·", self.setup_line("demand · scalp", "BUY"))


class DeletePostsTests(unittest.TestCase):
  posts = [{"channel_id": -100, "message_id": 7, "tier": "vip"}]

  def delete(self, main, scanner, **kw):
    with mock.patch.object(broadcast, "delete_message", main), mock.patch.object(broadcast, "delete_scanner_message", scanner):
      return run(broadcast.delete_posts(self.posts, **kw))

  def test_a_deleted_card_is_not_reported(self):
    main, scanner = mock.AsyncMock(), mock.AsyncMock()
    self.assertEqual(self.delete(main, scanner), [])
    main.assert_awaited_once_with(-100, 7)
    scanner.assert_not_awaited()

  def test_the_other_bot_is_tried_when_the_first_cannot_delete_it(self):
    main = mock.AsyncMock(side_effect=RuntimeError("Bad Request: message can't be deleted for everyone"))
    scanner = mock.AsyncMock()
    self.assertEqual(self.delete(main, scanner), [])
    scanner.assert_awaited_once_with(-100, 7)

  def test_a_personal_post_tries_the_scanner_bot_first(self):
    main, scanner = mock.AsyncMock(), mock.AsyncMock()
    self.assertEqual(self.delete(main, scanner, personal=True), [])
    scanner.assert_awaited_once()
    main.assert_not_awaited()

  def test_a_message_telegram_no_longer_has_counts_as_deleted(self):
    main = mock.AsyncMock(side_effect=RuntimeError("Bad Request: message to delete not found"))
    scanner = mock.AsyncMock(side_effect=RuntimeError("Bad Request: message to delete not found"))
    self.assertEqual(self.delete(main, scanner), [])

  def test_a_card_neither_bot_can_delete_is_returned_with_its_error(self):
    main = mock.AsyncMock(side_effect=RuntimeError("Bad Request: message can't be deleted for everyone"))
    scanner = mock.AsyncMock(side_effect=RuntimeError("Forbidden: not enough rights"))
    failed = self.delete(main, scanner)
    self.assertEqual(len(failed), 1)
    self.assertEqual((failed[0]["channel_id"], failed[0]["message_id"]), (-100, 7))
    self.assertIn("can't be deleted", failed[0]["error"])

  def test_one_failure_does_not_stop_the_rest(self):
    posts = [{"channel_id": -100, "message_id": 7}, {"channel_id": -200, "message_id": 8}]
    main = mock.AsyncMock(side_effect=[RuntimeError("x"), None])
    scanner = mock.AsyncMock(side_effect=RuntimeError("y"))
    with mock.patch.object(broadcast, "delete_message", main), mock.patch.object(broadcast, "delete_scanner_message", scanner):
      failed = run(broadcast.delete_posts(posts))
    self.assertEqual([p["message_id"] for p in failed], [7])
    self.assertEqual(main.await_count, 2)


class DeleteAckTests(unittest.TestCase):
  def result(self, undeleted):
    return {"action": "delete", "ok": True, "seq": 5, "row": {}, "undeleted": undeleted}

  def test_a_clean_delete_says_so(self):
    self.assertEqual(render_result(self.result([]), "XAU", "vip"), "🗑 #5 deleted")

  def test_a_card_that_stayed_is_named_in_the_ack(self):
    text = render_result(self.result([{"channel_id": -100, "message_id": 7, "error": "boom"}]), "XAU", "vip")
    self.assertTrue(text.startswith("🗑 #5 deleted"))
    self.assertIn("could not be deleted", text)
    self.assertIn("-100 / message 7: boom", text)


if __name__ == "__main__":
  unittest.main()
