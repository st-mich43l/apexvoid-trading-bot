"""Fail closed when the effective Algo Bot config still enables Python analysis.

Go is the sole technical-opportunity producer. TradePlan ownership is a
different, per-scope fence: this guard must not grant a scope or submit an order.
The operator's Ansible-rendered trading-bot.yml, not config/analysis.yml,
is the actual Python runtime configuration.

The guard only applies while automatic trading is enabled. A deployment that
runs with ``runtime.auto_trade.enabled=false`` (Manual Algo ``/algo`` commands,
Telegram, journaling, trade stats) has no automatic technical-opportunity
producer to protect, so it must keep starting whatever the authority mode is.
"""

from __future__ import annotations

from typing import Any


def require_go_technical_authority(runtime_config: Any) -> None:
  if not runtime_config.runtime.auto_trade.enabled:
    return
  authority = runtime_config.analysis.technical_authority
  if authority.mode != "go" or not authority.consumer_enabled:
    raise RuntimeError(
      "Go technical authority required: effective trading-bot.yml must set "
      "analysis.technical_authority.mode=go and consumer_enabled=true "
      f"(effective mode={authority.mode!r}, "
      f"consumer_enabled={authority.consumer_enabled!r}). "
      "Python scanners must not be used as a fallback."
    )
  kafka = runtime_config.transport.kafka
  if (
    not kafka.enabled
    or not kafka.brokers
    or not str(getattr(authority, "consumer_group", "") or "").strip()
  ):
    raise RuntimeError(
      "Go technical authority requires enabled Kafka transport, at least one broker, "
      "and a non-empty consumer group."
    )
