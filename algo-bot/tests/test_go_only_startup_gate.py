"""Go-only startup: automatic trading needs the live Go Kafka path.

The startup check fails closed on a missing Kafka transport, but never blocks a
Manual-Algo-only deployment
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
from app.analysis_client.startup_gate import require_live_go_consumer  # noqa: E402
from app.core import config as config_module  # noqa: E402
from tests.support.canonical_fixtures import install_runtime_overrides  # noqa: E402


pytestmark = pytest.mark.no_database

GO = {"analysis.technical_authority.consumer_enabled": True}


def _cfg(*, auto_trade=True, consumer=True, kafka=True, brokers=("kafka:9092",)):
  return SimpleNamespace(
    auto_algo=SimpleNamespace(
      enabled=auto_trade,
      actionability=SimpleNamespace(
        scanner_gates=SimpleNamespace(use_quality_ranking=True),
      ),
    ),
    analysis=SimpleNamespace(
      technical_authority=SimpleNamespace(
        consumer_enabled=consumer,
        consumer_group="apexvoid-algo-bot-analysis-opportunity-v1",
      ),
    ),
    runtime=SimpleNamespace(
      kafka=SimpleNamespace(enabled=kafka, brokers=list(brokers)),
    ),
  )


def test_go_mode_with_consumer_and_kafka_passes():
  require_live_go_consumer(_cfg())


def test_go_mode_rejects_legacy_quality_ranking():
  config = _cfg()
  config.auto_algo.actionability.scanner_gates.use_quality_ranking = False
  with pytest.raises(RuntimeError, match="use_quality_ranking=true"):
    require_live_go_consumer(config)


def test_disabled_consumer_fails_closed():
  with pytest.raises(RuntimeError, match="Live Go analysis consumer required") as exc:
    require_live_go_consumer(_cfg(consumer=False))
  assert "effective consumer_enabled=False" in str(exc.value)


@pytest.mark.parametrize("kafka,brokers", [(False, ("kafka:9092",)), (True, ())])
def test_missing_kafka_fails_closed(kafka, brokers):
  with pytest.raises(RuntimeError, match="Kafka transport"):
    require_live_go_consumer(_cfg(kafka=kafka, brokers=brokers))


@pytest.mark.parametrize("consumer,kafka", [(False, False), (False, True)])
def test_manual_only_deployment_is_never_gated(consumer, kafka):
  """auto_trade disabled = Manual Algo / Telegram / journaling only: no
  automatic technical-opportunity producer exists, so nothing to refuse."""
  require_live_go_consumer(
    _cfg(auto_trade=False, consumer=consumer, kafka=kafka),
  )


def test_gate_reads_the_real_config_model_paths(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  require_live_go_consumer(config_module.runtime_config)
  install_runtime_overrides(
    monkeypatch,
    {"analysis.technical_authority.consumer_enabled": False},
  )
  with pytest.raises(RuntimeError, match="Live Go analysis consumer required"):
    require_live_go_consumer(config_module.runtime_config)
  install_runtime_overrides(monkeypatch, GO)
  require_live_go_consumer(config_module.runtime_config)
  install_runtime_overrides(monkeypatch, {"runtime.kafka.enabled": False})
  with pytest.raises(RuntimeError, match="Kafka transport"):
    require_live_go_consumer(config_module.runtime_config)


def _startup_doubles(monkeypatch) -> dict[str, object]:
  doubles = {
    "init_db": AsyncMock(),
    "reconcile_startup_state": AsyncMock(),
    "backfill_retained_auto_trade_stats": AsyncMock(return_value="0-0"),
    "setup_commands": AsyncMock(),
    "start_telegram_actor": Mock(),
    "_spawn_supervised": Mock(),
  }
  for name, double in doubles.items():
    monkeypatch.setattr(main, name, double)
  monkeypatch.setattr(main.scanner_bot.session, "close", AsyncMock())
  monkeypatch.setattr(main.redis_state, "wait_until_ready", AsyncMock())
  monkeypatch.setattr(main.redis_state, "get_client", Mock(return_value=SimpleNamespace()))
  return doubles


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "overrides,message",
  [
    (
      {"analysis.technical_authority.consumer_enabled": False},
      "Live Go analysis consumer required",
    ),
    ({**GO, "runtime.kafka.enabled": False}, "Kafka transport"),
  ],
  ids=["consumer_disabled", "kafka_disabled"],
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
