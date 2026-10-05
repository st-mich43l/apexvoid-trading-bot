"""Channel ids are deployment identity and come from the environment."""

from __future__ import annotations

import pytest

from app.core import config as core_config

pytestmark = pytest.mark.no_database


def _document() -> dict:
  return {"telegram": {"telegram_channel_id": -100123456789, "signal_public_channel_id": -1004468199035}}


def test_vip_channel_comes_from_the_environment(monkeypatch):
  monkeypatch.setenv("SIGNAL_VIP_CHANNEL_ID", "-1009876543210")
  monkeypatch.delenv("SIGNAL_PUBLIC_CHANNEL_ID", raising=False)
  document = _document()

  core_config._apply_secrets(document)

  assert document["telegram"]["telegram_channel_id"] == -1009876543210
  assert document["telegram"]["signal_public_channel_id"] == -1004468199035


def test_public_channel_env_overrides_the_yaml_value(monkeypatch):
  monkeypatch.delenv("SIGNAL_VIP_CHANNEL_ID", raising=False)
  monkeypatch.setenv("SIGNAL_PUBLIC_CHANNEL_ID", "-1001112223334")
  document = _document()

  core_config._apply_secrets(document)

  assert document["telegram"]["signal_public_channel_id"] == -1001112223334
  assert document["telegram"]["telegram_channel_id"] == -100123456789


def test_unset_environment_leaves_the_yaml_ids_alone(monkeypatch):
  monkeypatch.delenv("SIGNAL_VIP_CHANNEL_ID", raising=False)
  monkeypatch.delenv("SIGNAL_PUBLIC_CHANNEL_ID", raising=False)
  document = _document()

  core_config._apply_secrets(document)

  assert document["telegram"]["telegram_channel_id"] == -100123456789
  assert document["telegram"]["signal_public_channel_id"] == -1004468199035
