"""The real bot must not silently resume Python technical authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis_client.startup_gate import require_go_technical_authority


pytestmark = pytest.mark.no_database


def runtime(*, mode="go", consumer=True, kafka=True, brokers=("kafka:9092",)):
  return SimpleNamespace(
    analysis=SimpleNamespace(technical_authority=SimpleNamespace(
      mode=mode, consumer_enabled=consumer,
    )),
    transport=SimpleNamespace(kafka=SimpleNamespace(
      enabled=kafka, brokers=list(brokers),
    )),
  )


def test_go_technical_authority_requires_consumer_and_kafka():
  require_go_technical_authority(runtime())


@pytest.mark.parametrize("case", [
  {"mode": "python"},
  {"mode": "go_shadow"},
  {"consumer": False},
  {"kafka": False},
  {"brokers": ()},
])
def test_any_legacy_or_disconnected_configuration_fails_before_bot_starts(case):
  with pytest.raises(RuntimeError):
    require_go_technical_authority(runtime(**case))
