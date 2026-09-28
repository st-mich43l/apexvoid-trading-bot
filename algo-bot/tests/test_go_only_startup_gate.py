"""Go-only startup: automatic trading needs Go as the effective technical authority.

The gate fails closed on an effective python/go_shadow config or a missing
Kafka transport, but never blocks a Manual-Algo-only deployment
(auto_trade disabled). Go mode must not install a Python ZoneWatch publisher.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import main  # noqa: E402
from app.analysis_client import authority  # noqa: E402
from app.analysis_client.startup_gate import require_go_technical_authority  # noqa: E402
from app.core import config as config_module  # noqa: E402
from tests.configuration.canonical_fixtures import install_runtime_overrides  # noqa: E402


pytestmark = pytest.mark.no_database

GO = {
  "analysis.technical_authority.mode": "go",
  "analysis.technical_authority.consumer_enabled": True,
}


def _cfg(*, auto_trade=True, mode="go", consumer=True, kafka=True, brokers=("kafka:9092",)):
  return SimpleNamespace(
    runtime=SimpleNamespace(auto_trade=SimpleNamespace(enabled=auto_trade)),
    analysis=SimpleNamespace(
      technical_authority=SimpleNamespace(
        mode=mode,
        consumer_enabled=consumer,
        consumer_group="apexvoid-algo-bot-analysis-opportunity-v1",
      ),
    ),
    transport=SimpleNamespace(kafka=SimpleNamespace(enabled=kafka, brokers=list(brokers))),
  )


@pytest.fixture(autouse=True)
def _reset_authority_snapshot():
  # main() calls get_snapshot().require(); keep that out of later tests.
  yield
  authority.reset_snapshot_for_tests()


def test_go_mode_with_consumer_and_kafka_passes():
  require_go_technical_authority(_cfg())


@pytest.mark.parametrize(
  "mode,consumer",
  [("python", False), ("python", True), ("go_shadow", True), ("go", False)],
)
def test_python_or_shadow_authority_fails_closed(mode, consumer):
  with pytest.raises(RuntimeError, match="Go technical authority required") as exc:
    require_go_technical_authority(_cfg(mode=mode, consumer=consumer))
  assert f"effective mode={mode!r}" in str(exc.value)


@pytest.mark.parametrize("kafka,brokers", [(False, ("kafka:9092",)), (True, ())])
def test_missing_kafka_fails_closed(kafka, brokers):
  with pytest.raises(RuntimeError, match="Kafka transport"):
    require_go_technical_authority(_cfg(kafka=kafka, brokers=brokers))


@pytest.mark.parametrize(
  "mode,consumer,kafka",
  [("python", False, False), ("go_shadow", True, True), ("python", False, True)],
)
def test_manual_only_deployment_is_never_gated(mode, consumer, kafka):
  """auto_trade disabled = Manual Algo / Telegram / journaling only: no
  automatic technical-opportunity producer exists, so nothing to refuse."""
  require_go_technical_authority(
    _cfg(auto_trade=False, mode=mode, consumer=consumer, kafka=kafka),
  )


def test_gate_reads_the_real_config_model_paths(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  with pytest.raises(RuntimeError, match="Go technical authority required"):
    require_go_technical_authority(config_module.runtime_config)
  install_runtime_overrides(monkeypatch, GO)
  require_go_technical_authority(config_module.runtime_config)
  install_runtime_overrides(monkeypatch, {"transport.kafka.enabled": False})
  with pytest.raises(RuntimeError, match="Kafka transport"):
    require_go_technical_authority(config_module.runtime_config)


def _startup_doubles(monkeypatch) -> dict[str, object]:
  doubles = {
    "init_db": AsyncMock(),
    "reconcile_startup_state": AsyncMock(),
    "backfill_retained_auto_trade_stats": AsyncMock(return_value="0-0"),
    "setup_commands": AsyncMock(),
    "start_telegram_actor": Mock(),
    "_spawn_supervised": Mock(),
    "verify_mounted_runtime_manifest_or_raise": Mock(),
    "publish_python_manifest": AsyncMock(return_value={"state": "ok"}),
  }
  for name, double in doubles.items():
    monkeypatch.setattr(main, name, double)
  monkeypatch.setattr(main.scanner_bot.session, "close", AsyncMock())
  monkeypatch.setattr(main.redis_state, "wait_until_ready", AsyncMock())
  monkeypatch.setattr(main.redis_state, "get_client", Mock(return_value=SimpleNamespace()))
  monkeypatch.setattr(authority, "refresh_authority_snapshot", AsyncMock())
  monkeypatch.setattr(
    authority,
    "audit_authority_runtime",
    AsyncMock(return_value={"event": "runtime_boot", "go_bound_scopes": [], "detail": "test"}),
    raising=False,
  )
  return doubles


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "overrides,message",
  [
    ({}, "Go technical authority required"),
    (
      {
        "analysis.technical_authority.mode": "go_shadow",
        "analysis.technical_authority.consumer_enabled": True,
      },
      "Go technical authority required",
    ),
    ({**GO, "transport.kafka.enabled": False}, "Kafka transport"),
  ],
  ids=["python", "go_shadow", "kafka_disabled"],
)
async def test_main_fails_closed_before_any_side_effect(monkeypatch, overrides, message):
  install_runtime_overrides(
    monkeypatch, overrides, legacy_overrides={"auto_trade_enabled": True},
  )
  doubles = _startup_doubles(monkeypatch)
  polling = AsyncMock()
  monkeypatch.setattr(main.dp, "start_polling", polling)

  with pytest.raises(RuntimeError, match=message):
    await main.main()

  doubles["init_db"].assert_not_awaited()
  doubles["_spawn_supervised"].assert_not_called()
  polling.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_in_go_mode_has_no_python_technical_publisher(monkeypatch):
  """Go mode starts the durable consumer and worker only.

  ZoneWatch activation and scanner monkeypatches are intentionally absent from
  the production composition root; stale Python matches are rejected by the
  worker's final origin fence.
  """
  install_runtime_overrides(monkeypatch, GO, legacy_overrides={"auto_trade_enabled": True})
  doubles = _startup_doubles(monkeypatch)

  monkeypatch.setattr(main.dp, "start_polling", AsyncMock())

  await main.main()

  spawned = {call.args[0]: call.args[1] for call in doubles["_spawn_supervised"].call_args_list}
  assert spawned["bar_event_dispatcher_loop"] is main.bar_event_dispatcher_loop
  assert "analysis_opportunity_consumer_loop" in spawned
  assert "zone_watch_execution_loop" not in spawned
