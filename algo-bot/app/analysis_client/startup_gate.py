"""Fail closed when automatic trading lacks the live Go/Kafka consumer.

Go is the sole technical-opportunity producer. This guard validates the
process-wide Go/Kafka path; it does not submit an order.
The operator's selected Configuration YAML root, not a process ENV tuning set,
is the actual Python runtime configuration.

The guard only applies while automatic trading is enabled. A deployment that
runs with ``runtime.auto_trade.enabled=false`` (Manual Algo ``/algo`` commands,
Telegram, journaling, trade stats) has no automatic technical-opportunity
producer to protect, so it must keep starting without the automatic Go/Kafka check.
"""

from __future__ import annotations

from typing import Any


def require_live_go_consumer(runtime_config: Any) -> None:
  if not runtime_config.auto_algo.enabled:
    return
  analysis_config = runtime_config.analysis.technical_authority
  if not analysis_config.consumer_enabled:
    raise RuntimeError(
      "Live Go analysis consumer required: selected YAML root must set "
      "analysis.technical_authority.consumer_enabled=true "
      f"(effective consumer_enabled={analysis_config.consumer_enabled!r}). "
      "Python scanners must not be used as a fallback."
    )
  kafka = runtime_config.runtime.kafka
  if (
    not kafka.enabled
    or not kafka.brokers
    or not str(getattr(analysis_config, "consumer_group", "") or "").strip()
  ):
    raise RuntimeError(
      "Live Go analysis requires enabled Kafka transport, at least one broker, "
      "and a non-empty consumer group."
    )
