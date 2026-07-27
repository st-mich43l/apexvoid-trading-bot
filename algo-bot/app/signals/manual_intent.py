"""ManualTradeIntent — the versioned contract for owner-armed manual signals.

Built from a ``manual_signals`` row when a DM signal carries the ``/ algo``
suffix (``execution_mode == "algo"``). Publishing this contract to Redis is
the entire scope of this PR: nothing in this codebase consumes
``settings.manual_trade_intent_stream`` yet. A future ``ctrader-engine``
change (a separate, later PR) will watch live price against the intent's
absolute entry/SL/TP — the owner's exact entered stop, not a re-derived
structure stop like the existing box-scalp/trend auto-trade strategies — and
open/manage the position for real. Until that consumer exists, publishing an
intent has no broker-execution side effect whatsoever.
"""

import json
from dataclasses import asdict, dataclass

from app.core.config import settings
from app.persistence import redis_state


@dataclass(frozen=True)
class ManualTradeIntent:
  intent_id: str            # f"manual:{manual_signal_id}:{revision}"
  manual_signal_id: int
  revision: int
  direction: str             # "BUY" | "SELL"
  entry_low: float
  entry_high: float
  sl: float
  tps: tuple[float, ...]
  created_at: int             # unix ts
  expires_at: int | None
  setup_type: str | None
  confluence: int | None
  execution_mode: str         # "algo" (this contract only exists for algo-mode signals)


def build_intent(signal: dict, *, revision: int = 0) -> ManualTradeIntent:
  """Build a ManualTradeIntent from a ``manual_signals`` row dict.

  ``signal`` is the same shape ``store.get_manual_signal`` (and the row
  ``store.store_manual_signal`` inserts) produce: ``tps`` already decoded to
  a list of numbers by ``store._decode_signal`` — not a raw JSON string — so
  no extra JSON parsing happens here.
  """
  return ManualTradeIntent(
    intent_id=f"manual:{signal['id']}:{revision}",
    manual_signal_id=signal["id"],
    revision=revision,
    direction=signal["action"],
    entry_low=float(signal["entry"]),
    entry_high=float(signal["entry_end"]),
    sl=float(signal["sl"]),
    tps=tuple(float(v) for v in signal["tps"]),
    created_at=int(signal["ts"]),
    expires_at=None,
    setup_type=signal.get("setup_type"),
    confluence=signal.get("confluence"),
    execution_mode="algo",
  )


def _payload(intent: ManualTradeIntent) -> dict:
  """Flatten the dataclass to the exact JSON shape a future consumer reads."""
  payload = asdict(intent)
  payload["tps"] = list(intent.tps)
  return payload


async def publish_intent(intent: ManualTradeIntent) -> None:
  """Publish one ManualTradeIntent onto the manual-trade Redis stream.

  Mirrors the shape of the existing auto-trade candidate publisher in
  ``app.autotrade.worker``: a single JSON payload per ``XADD``, using the
  shared Redis client from ``app.persistence.redis_state`` (never a second
  connection pool), trimmed via ``xadd``'s own ``maxlen=``/``approximate=True``
  rather than a separate ``XTRIM`` call — that's the mechanism worker.py
  itself uses, so this stays consistent with it. No consumer reads this
  stream yet.
  """
  client = redis_state.get_client()
  payload = _payload(intent)
  await client.xadd(
    settings.manual_trade_intent_stream,
    {"payload": json.dumps(payload, separators=(",", ":"))},
    maxlen=max(100, settings.manual_trade_intent_stream_maxlen),
    approximate=True,
  )
