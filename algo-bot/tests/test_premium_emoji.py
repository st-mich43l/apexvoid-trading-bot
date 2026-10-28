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


def test_the_shipped_configuration_is_off():
  from app.core.config import runtime_config

  assert configured_ids(runtime_config.telegram) == {}


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
