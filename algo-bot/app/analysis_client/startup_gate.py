"""Fail closed when automatic trading lacks the live Go/Kafka consumer.

Go is the sole technical-opportunity producer. This guard validates the
process-wide Go/Kafka path; it does not submit an order.
The operator's Ansible-rendered trading-bot.yml, not config/analysis.yml,
is the actual Python runtime configuration.

The guard only applies while automatic trading is enabled. A deployment that
runs with ``runtime.auto_trade.enabled=false`` (Manual Algo ``/algo`` commands,
Telegram, journaling, trade stats) has no automatic technical-opportunity
producer to protect, so it must keep starting without the automatic Go/Kafka check.
"""

from __future__ import annotations

from typing import Any


def require_live_go_consumer(runtime_config: Any) -> None:
  if not runtime_config.runtime.auto_trade.enabled:
    return
  analysis_config = runtime_config.analysis.technical_authority
  if analysis_config.mode != "go" or not analysis_config.consumer_enabled:
    raise RuntimeError(
      "Live Go analysis consumer required: effective trading-bot.yml must set "
      "analysis.technical_authority.mode=go and consumer_enabled=true "
      f"(effective mode={analysis_config.mode!r}, "
      f"consumer_enabled={analysis_config.consumer_enabled!r}). "
      "Python scanners must not be used as a fallback."
    )
  missing_go_ownership = [
    name for name in (
      "arbitration_mode",
      "thesis_correlation_mode",
      "stop_envelope_mode",
    )
    if getattr(analysis_config, name, "go") != "go"
  ]
  if missing_go_ownership:
    raise RuntimeError(
      "Live Go analysis requires AE ownership of "
      + ", ".join(missing_go_ownership)
      + "; Python technical fallbacks are not permitted."
    )
  scanner_gates = runtime_config.actionability.scanner_gates
  if not getattr(scanner_gates, "use_quality_ranking", True):
    raise RuntimeError(
      "Live Go analysis requires scanner_gates.use_quality_ranking=true; "
      "Python evidence-count ranking is not permitted."
    )
  kafka = runtime_config.transport.kafka
  if (
    not kafka.enabled
    or not kafka.brokers
    or not str(getattr(analysis_config, "consumer_group", "") or "").strip()
  ):
    raise RuntimeError(
      "Live Go analysis requires enabled Kafka transport, at least one broker, "
      "and a non-empty consumer group."
    )
