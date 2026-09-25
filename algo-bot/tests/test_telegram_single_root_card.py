"""Telegram one-root-card minimum: silent plan_armed + retain-on-terminal."""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import pytest

from app.autotrade import delivery
from app.autotrade import setup_card


pytestmark = pytest.mark.no_database


def test_plan_armed_event_stays_silent():
  text = delivery.render_auto_trade_event({
    "type": "plan_armed",
    "message": "PLAN ARMED Trend Pullback BUY (market_watch)",
  })
  assert text is None
  assert "plan_armed" in delivery.TELEGRAM_SILENT_LIFECYCLE_TYPES


def test_generic_plan_published_is_silent():
  text = delivery.render_auto_trade_event({
    "type": "plan_published",
    "message": "PLAN PUBLISHED",
  })
  assert text is None


def test_should_delete_root_follows_the_delete_root_on_terminal_flag(monkeypatch):
  """Reject/expire delete the root card when the flag is on, retain when off."""
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_telegram_delete_root_on_terminal": True})
  assert setup_card.should_delete_root_on_terminal() is True

  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_telegram_delete_root_on_terminal": False})
  assert setup_card.should_delete_root_on_terminal() is False
