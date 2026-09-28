"""Fail closed when the effective Algo Bot config still enables Python analysis.

Go is the sole technical-opportunity producer. TradePlan ownership is a
different, per-scope fence: this guard must not grant a scope or submit an order.
The operator's Ansible-rendered trading-bot.yml, not config/analysis.yml,
is the actual Python runtime configuration.
"""

from __future__ import annotations

from typing import Any


def require_go_technical_authority(runtime_config: Any) -> None:
  authority = runtime_config.analysis.technical_authority
  if authority.mode != "go" or not authority.consumer_enabled:
    raise RuntimeError(
      "Go technical authority required: effective trading-bot.yml must set "
      "analysis.technical_authority.mode=go and consumer_enabled=true. "
      "Python scanners must not be used as a fallback."
    )
  kafka = runtime_config.transport.kafka
  if not kafka.enabled or not kafka.brokers:
    raise RuntimeError(
      "Go technical authority requires enabled Kafka transport and at least one broker."
    )
