"""ManualTradeIntent — the versioned contract for owner-armed manual signals.

Built from a ``manual_signals`` row when a DM signal carries the ``/ algo``
suffix (``execution_mode == "algo"``). Publishing this contract to Redis is
the entire scope of this PR: nothing in this codebase consumes
``runtime_config.manual_algo.streams.intents`` yet. A future ``ctrader-engine``
change (a separate, later PR) will watch live price against the intent's
absolute entry/SL/TP — the owner's exact entered stop, not a re-derived
structure stop like the existing box-scalp/trend auto-trade strategies — and
open/manage the position for real. Until that consumer exists, publishing an
intent has no broker-execution side effect whatsoever.
"""

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.config import runtime_config
from app.persistence import redis_state


@dataclass(frozen=True)
class ManualTradeIntent:
  intent_id: str            # f"manual:{manual_signal_id}:{revision}"
  manual_signal_id: int
  revision: int
  direction: str             # "BUY" | "SELL"
  symbol: str                # canonical instrument id (EURUSD, XAU, …)
  entry_low: float
  entry_high: float
  sl: float
  tps: tuple[float, ...]
  created_at: int             # unix ts
  expires_at: int | None
  setup_type: str | None
  confluence: int | None
  execution_mode: str         # "algo" (this contract only exists for algo-mode signals)
  # Owner opt-in via `/1r`: force a single full-volume entry regardless of
  # the instrument's configured entry_mode (see
  # manual_execution._intent_to_candidate_payload).
  single_entry_override: bool = False


def _end_of_trade_day(trade_date: str | None) -> int:
  """Unix ts for the end of ``trade_date`` in the owner's trade-day tz.

  Matches ``store._current_trade_date()``'s day boundary exactly (same tz,
  same local-midnight rollover) so a manual algo order expires at the same
  moment its ``daily_seq`` would roll over to the next trading day, rather
  than on some unrelated UTC-midnight or wall-clock boundary.
  """
  tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  day = date.fromisoformat(trade_date) if trade_date else datetime.now(tz).date()
  end_of_day = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
  return int(end_of_day.timestamp())


def build_intent(
  signal: dict, *, revision: int = 0, created_at: int | None = None,
) -> ManualTradeIntent:
  """Build a ManualTradeIntent from a ``manual_signals`` row dict.

  ``signal`` is the same shape ``store.get_manual_signal`` (and the row
  ``store.store_manual_signal`` inserts) produce: ``tps`` already decoded to
  a list of numbers by ``store._decode_signal`` — not a raw JSON string — so
  no extra JSON parsing happens here.

  ``created_at`` defaults to the signal's own ``ts`` (correct for the
  initial revision=0 arm, built moments after the signal itself is
  created — see ``fallback.py::_arm_algo_intent``). A re-arm
  (``trade_ops._rearm_algo_after_modify``, after /trade_modify) MUST pass
  a fresh timestamp instead: AutoTradeEngine.cs rejects any candidate as
  "stale" once ``now - CreatedAt`` exceeds ``CandidateMaxAgeSeconds``
  (default 420s), and this field flows straight through unchanged into
  the candidate payload (``manual_execution.py::_intent_to_candidate_
  payload``'s ``created_at``) - reusing the original signal.ts on a
  revision bumped well after that window means the re-armed order is
  rejected before ever reaching the broker, with no fill and no owner-
  visible error beyond the bare "stale candidate" reject reason. Owner-
  reported 2026-09: a modified BUY order got exactly this - "stale
  candidate and no broker fill."
  """
  return ManualTradeIntent(
    intent_id=f"manual:{signal['id']}:{revision}",
    manual_signal_id=signal["id"],
    revision=revision,
    direction=signal["action"],
    symbol=str(signal.get("symbol") or "XAU").upper(),
    entry_low=float(signal["entry"]),
    entry_high=float(signal["entry_end"]),
    sl=float(signal["sl"]),
    tps=tuple(float(v) for v in signal["tps"]),
    created_at=int(signal["ts"]) if created_at is None else int(created_at),
    expires_at=_end_of_trade_day(signal.get("trade_date")),
    setup_type=signal.get("setup_type"),
    confluence=signal.get("confluence"),
    execution_mode="algo",
    single_entry_override=bool(signal.get("personal_trade", False)),
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
    runtime_config.manual_algo.streams.intents,
    {"payload": json.dumps(payload, separators=(",", ":"))},
    maxlen=max(100, runtime_config.manual_algo.streams.manual_trade_intent_stream_maxlen),
    approximate=True,
  )
