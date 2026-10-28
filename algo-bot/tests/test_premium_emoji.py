"""Premium (custom) emoji on the root card: converted only on the way to Telegram, off by default, never lost to a refusal."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.autotrade.setup_card import format_plan_published_root_card
from app.autotrade.strategy_match import StrategyMatch
from app.bot import client as bot_client
from app.bot.premium_emoji import SLOT_EMOJI, apply_premium_emoji, clean_ids, configured_ids

pytestmark = pytest.mark.no_database

IDS = {"buy": "5368324170671202286", "sell": "5368324170671202287", "entry": "5368324170671202288", "stop": "5368324170671202289",
       "target": "5368324170671202290", "star": "5368324170671202291", "brand": "5368324170671202292"}


def _match(direction="SELL") -> StrategyMatch:
  return StrategyMatch(
    version=1, match_id="setup-premium", symbol="XAU", source_tf="M5", event_ts="2026-07-31T13:03:00+00:00", issued_at=1_785_502_980,
    expires_at=1_785_503_400, strategy="Supply Demand", strategy_mode="with_bias", bias_relationship="with_bias", direction=direction,
    key_level=4050.68, entry_low=4048.73, entry_high=4052.63, current_price=4051.6, confluence=2, reasons=("zone reaction",), atr=4.0,
    structure_swing=10.0, targets_pips=(20, 40, 60), tags=("zone",), structural_source="zone", structural_kind="zone", structural_timeframe="M5",
    reaction_type="rejection", htf_bias="up (H1)",
  )


def _strip(html: str) -> str:
  return re.sub(r"</?tg-emoji[^>]*>", "", html)


def test_a_configured_emoji_is_wrapped_with_the_plain_emoji_kept_as_the_fallback():
  out = apply_premium_emoji("📉 <b>SELL · Supply</b>", IDS)
  assert out == f'<tg-emoji emoji-id="{IDS["sell"]}">📉</tg-emoji> <b>SELL · Supply</b>'


def test_the_variation_selector_stays_inside_the_tag():
  out = apply_premium_emoji("⚡️ Entry Zone:  <b>4,049 - 4,053</b>", IDS)
  assert out.startswith(f'<tg-emoji emoji-id="{IDS["entry"]}">⚡️</tg-emoji> Entry Zone')
  assert _strip(out) == "⚡️ Entry Zone:  <b>4,049 - 4,053</b>"


def test_a_slot_without_an_id_and_an_unknown_slot_stay_plain():
  out = apply_premium_emoji("🛡 SL: 💰 TP1 🔎", {"stop": IDS["stop"], "nonsense": "1234567"})
  assert out == f'<tg-emoji emoji-id="{IDS["stop"]}">🛡</tg-emoji> SL: 💰 TP1 🔎'


@pytest.mark.parametrize("bad", ["", "abc", "12", "123 456", '1234567"><b>x</b>', "-123456", None, "١٢٣٤٥٦٧"])
def test_a_malformed_id_is_ignored_never_injected(bad):
  assert clean_ids({"sell": bad}) == {}
  assert apply_premium_emoji("📉 SELL", {"sell": bad}) == "📉 SELL"


def test_a_yaml_integer_id_is_accepted():
  assert clean_ids({"buy": 5368324170671202286}) == {"buy": "5368324170671202286"}


def test_nothing_configured_returns_the_text_unchanged():
  card = "📈 <b>BUY · Demand</b> · ⭐⭐"
  assert apply_premium_emoji(card, {}) == card and apply_premium_emoji(card, None) == card and apply_premium_emoji("", IDS) == ""


def test_applying_twice_does_not_nest_and_existing_tags_are_untouched():
  once = apply_premium_emoji("📈 BUY ⭐⭐", IDS)
  assert apply_premium_emoji(once, IDS) == once and once.count("<tg-emoji") == 3
  own = '<tg-emoji emoji-id="999999999">📈</tg-emoji> 📈'
  assert apply_premium_emoji(own, IDS) == '<tg-emoji emoji-id="999999999">📈</tg-emoji> ' + f'<tg-emoji emoji-id="{IDS["buy"]}">📈</tg-emoji>'


@pytest.mark.parametrize("direction,slot", [("SELL", "sell"), ("BUY", "buy")])
def test_the_real_root_card_converts_and_strips_back_to_exactly_the_plain_card(direction, slot):
  plain = format_plan_published_root_card(_match(direction), stop_price=4060.0, target_prices=(4040.0, 4030.0, 4020.0))
  rich = apply_premium_emoji(plain, IDS)
  assert f'emoji-id="{IDS[slot]}"' in rich and f'emoji-id="{IDS["stop"]}"' in rich and f'emoji-id="{IDS["target"]}"' in rich
  assert f'emoji-id="{IDS["brand"]}"' in rich and f'emoji-id="{IDS["star"]}"' in rich
  assert _strip(rich) == plain  # the fallback text a non-Premium viewer sees is the card, unchanged
  # the other direction's icon is not used
  other = IDS["buy" if slot == "sell" else "sell"]
  assert f'emoji-id="{other}"' not in rich


def test_every_slot_is_a_distinct_emoji_the_card_prints():
  assert len(set(SLOT_EMOJI.values())) == len(SLOT_EMOJI)


# ---- configuration ----------------------------------------------------------------------------------------------------------------

def _telegram(premium):
  from app.core.config_schema import ConfigNode

  presentation = {} if premium is None else {"premium_emoji": premium}
  return ConfigNode({"presentation": presentation})


def test_it_is_off_unless_switched_on():
  assert configured_ids(_telegram(None)) == {}
  assert configured_ids(_telegram({"enabled": False, "ids": IDS})) == {}
  assert configured_ids(_telegram({"enabled": True, "ids": {}})) == {}
  assert configured_ids(_telegram({"enabled": True, "ids": IDS})) == {k: v for k, v in IDS.items()}


def test_the_shipped_configuration_is_on_with_the_chosen_ids():
  from app.core.config import runtime_config

  ids = configured_ids(runtime_config.telegram)
  assert {"brand", "forming", "buy", "sell", "entry", "stop", "target", "star", "trophy", "loss", "chest", "setup"} <= set(ids)
  assert ids["buy"] == "5429651785352501917" and ids["chest"] == "5278467510604160626"


# ---- the Telegram boundary ----------------------------------------------------------------------------------------------------------

def _bad_request(message):
  from aiogram.exceptions import TelegramBadRequest

  return TelegramBadRequest(method=SimpleNamespace(), message=message)


@pytest.mark.asyncio
async def test_with_premium_emoji_on_the_rich_card_is_sent(monkeypatch):
  monkeypatch.setattr(bot_client, "configured_ids", lambda _cfg: IDS)
  sent = []

  async def send(text):
    sent.append(text)
    return "ok"

  assert await bot_client.premium_then_plain(send, "📈 BUY") == "ok"
  assert sent == [f'<tg-emoji emoji-id="{IDS["buy"]}">📈</tg-emoji> BUY']


@pytest.mark.asyncio
async def test_with_it_off_the_plain_card_is_sent_once(monkeypatch):
  monkeypatch.setattr(bot_client, "configured_ids", lambda _cfg: {})
  sent = []

  async def send(text):
    sent.append(text)

  await bot_client.premium_then_plain(send, "📈 BUY")
  assert sent == ["📈 BUY"]


@pytest.mark.asyncio
async def test_if_telegram_refuses_the_rich_card_the_plain_card_goes_out(monkeypatch):
  monkeypatch.setattr(bot_client, "configured_ids", lambda _cfg: IDS)
  sent = []

  async def send(text):
    sent.append(text)
    if "tg-emoji" in text:
      raise _bad_request("Bad Request: can't parse entities: unsupported custom emoji")
    return "plain"

  assert await bot_client.premium_then_plain(send, "📈 BUY") == "plain"
  assert len(sent) == 2 and "tg-emoji" in sent[0] and sent[1] == "📈 BUY"


@pytest.mark.asyncio
async def test_message_is_not_modified_is_raised_not_retried(monkeypatch):
  monkeypatch.setattr(bot_client, "configured_ids", lambda _cfg: IDS)
  calls = []

  async def send(text):
    calls.append(text)
    raise _bad_request("Bad Request: message is not modified")

  with pytest.raises(Exception, match="not modified"):
    await bot_client.premium_then_plain(send, "📈 BUY")
  assert len(calls) == 1


def test_the_shipped_ids_give_the_forming_headline_its_magnet_and_keep_each_slot_distinct():
  from app.core.config import runtime_config

  ids = clean_ids(runtime_config.telegram.presentation.premium_emoji.ids)
  assert ids["forming"] == "5202106638508512484"
  assert {"brand", "forming", "buy", "sell", "entry", "stop", "target", "star"} <= set(ids)
  out = apply_premium_emoji("🔎 <b>XAU M5 · SETUP FORMING</b>", ids)
  assert out.startswith('<tg-emoji emoji-id="5202106638508512484">🔎</tg-emoji>')


@pytest.mark.asyncio
async def test_only_a_refusal_of_the_emoji_is_retried_plain_not_other_bad_requests(monkeypatch):
  monkeypatch.setattr(bot_client, "configured_ids", lambda _cfg: IDS)
  calls = []

  async def send(text):
    calls.append(text)
    raise _bad_request("Bad Request: message to be replied not found")

  with pytest.raises(Exception, match="replied not found"):
    await bot_client.premium_then_plain(send, "📈 BUY")
  assert len(calls) == 1  # a bad reply target is not about the emoji: raised, so the caller's own fallback (a standalone post) still runs


# ---- channel entry cards: sent to the owner's DM and forwarded ---------------------------------------------------------------------

class _Chat:
  """A recording stand-in for the main bot: send_with_retry (the DM source), forward_message and delete_message."""

  def __init__(self, *, forward_entities=("custom_emoji",), forward_errors=()):
    self.calls = []
    self.forward_entities = forward_entities
    self.forward_errors = list(forward_errors)

  async def send_with_retry(self, text, reply_to=None, chat_id=None, reply_markup=None):
    self.calls.append(("dm_send", chat_id, text))
    return SimpleNamespace(message_id=500)

  async def forward_message(self, chat_id, from_chat_id, message_id):
    self.calls.append(("forward", chat_id, from_chat_id, message_id))
    if self.forward_errors:
      raise self.forward_errors.pop(0)
    return SimpleNamespace(message_id=900, entities=[SimpleNamespace(type=t) for t in self.forward_entities])

  async def delete_message(self, chat_id, message_id):
    self.calls.append(("delete", chat_id, message_id))


def _wire(monkeypatch, chat, ids=IDS, flag=True):
  monkeypatch.setattr(bot_client, "bot", chat)
  monkeypatch.setattr(bot_client, "send_with_retry", chat.send_with_retry)
  monkeypatch.setattr(bot_client, "configured_ids", lambda _cfg: ids)
  monkeypatch.setattr(bot_client, "channel_forward_enabled", lambda _cfg: flag)


@pytest.mark.asyncio
async def test_the_card_is_sent_to_the_dm_forwarded_to_the_channel_and_the_dm_copy_deleted(monkeypatch):
  chat = _Chat()
  _wire(monkeypatch, chat)
  posted = await bot_client.forward_premium_card("📈 BUY", -100123, 42)
  assert posted.message_id == 900  # the forward is the channel post: its id is the one to store
  kinds = [call[0] for call in chat.calls]
  assert kinds == ["dm_send", "forward", "delete"]
  assert chat.calls[0][1] == 42 and "tg-emoji" in chat.calls[0][2]
  assert chat.calls[1] == ("forward", -100123, 42, 500)
  assert chat.calls[2] == ("delete", 42, 500)  # the source: a forward survives the deletion of its source


@pytest.mark.asyncio
@pytest.mark.parametrize("ids,flag,owner", [({}, True, 42), (IDS, False, 42), (IDS, True, None), (IDS, True, 0)])
async def test_with_nothing_to_preserve_or_the_switch_off_or_no_owner_nothing_is_sent(monkeypatch, ids, flag, owner):
  chat = _Chat()
  _wire(monkeypatch, chat, ids=ids, flag=flag)
  assert await bot_client.forward_premium_card("📈 BUY", -100123, owner) is None
  assert chat.calls == []


@pytest.mark.asyncio
async def test_a_card_with_no_emoji_to_convert_is_not_forwarded(monkeypatch):
  chat = _Chat()
  _wire(monkeypatch, chat)
  assert await bot_client.forward_premium_card("no icons here", -100123, 42) is None
  assert chat.calls == []


@pytest.mark.asyncio
async def test_a_failed_forward_returns_none_and_leaves_no_dm_copy(monkeypatch):
  chat = _Chat(forward_errors=[ConnectionError("network")])
  _wire(monkeypatch, chat)
  assert await bot_client.forward_premium_card("📈 BUY", -100123, 42) is None
  assert [c[0] for c in chat.calls] == ["dm_send", "forward", "delete"] and chat.calls[-1] == ("delete", 42, 500)


@pytest.mark.asyncio
async def test_a_forward_that_lost_the_emoji_is_deleted_not_left_with_a_header_and_no_icons(monkeypatch):
  chat = _Chat(forward_entities=("bold",))
  _wire(monkeypatch, chat)
  assert await bot_client.forward_premium_card("📈 BUY", -100123, 42) is None
  assert ("delete", -100123, 900) in chat.calls and ("delete", 42, 500) in chat.calls


@pytest.mark.asyncio
async def test_a_flood_wait_is_waited_out_and_the_forward_retried_without_duplicating(monkeypatch):
  from aiogram.exceptions import TelegramRetryAfter

  slept = []

  async def fake_sleep(seconds):
    slept.append(seconds)

  monkeypatch.setattr(bot_client.asyncio, "sleep", fake_sleep)
  chat = _Chat(forward_errors=[TelegramRetryAfter(method=SimpleNamespace(), message="flood", retry_after=2)])
  _wire(monkeypatch, chat)
  posted = await bot_client.forward_premium_card("📈 BUY", -100123, 42)
  assert posted.message_id == 900 and slept == [2]
  assert [c[0] for c in chat.calls].count("forward") == 2 and chat.calls[-1] == ("delete", 42, 500)


def test_the_channel_forward_switch_is_off_unless_the_feature_and_the_switch_are_both_on():
  from app.bot.premium_emoji import channel_forward_enabled

  assert channel_forward_enabled(_telegram({"enabled": True, "channel_forward": True, "ids": IDS})) is True
  assert channel_forward_enabled(_telegram({"enabled": True, "ids": IDS})) is False
  assert channel_forward_enabled(_telegram({"enabled": False, "channel_forward": True})) is False
  assert channel_forward_enabled(_telegram(None)) is False


def test_the_shipped_configuration_forwards_channel_cards():
  from app.bot.premium_emoji import channel_forward_enabled
  from app.core.config import runtime_config

  assert channel_forward_enabled(runtime_config.telegram) is True
