"""MFE / MAE of every ``algo_auto`` trade, measured on closed M1 bars.

Why: without the excursion of a trade, "wrong from the start" cannot be told apart from "right but stopped before 1R", and that decides between
analysis fixes and exit/stop fixes. Until this module only the scalp lane tracked an excursion, and that one never moved (see
``app.scalping.outcomes.excursion_from_opportunity``: it starts at the entry and nothing advanced it).

How it works, one state per ``group_id`` in Redis (``autotrade:excursion:<group_id>``, TTL 7 days, indexed by ``autotrade:open_excursions:<symbol>``):

* **start**: the group's FIRST fill event (``opened`` / ``add`` / ``order_filled``) creates the state with ``SET NX``; later fills never move
  ``reference_price`` (the first fill's price) or ``original_stop_price`` (the stop on that first fill, never a moved stop).
* **advance**: every closed M1 bar of the symbol widens ``max_high`` / ``min_low`` of each open state. A bar counts only when its OPEN time is at or
  after ``first_fill_at``: the bar that CONTAINS the fill is excluded, because the order of the high and the low inside a minute is unknown (a
  low that came before the fill is not an adverse excursion of this trade). A trade that closes inside its fill minute therefore has ``bars = 0`` and
  its MFE/MAE are stored as NULL, never as a fabricated 0. Bars are the feed's BID OHLC, as everywhere else in the bot; no spread is added.
* **finalize**: ``app.persistence.store`` writes the figures next to the result when a close event arrives (each later close event of the same group
  overwrites them with the fuller values: extremes only grow), and the state is deleted when the whole group is finished.

Resolution is one minute: an excursion inside a bar is not seen, so MFE/MAE are lower bounds of the true extremes, and a stop and a target touched in
the same minute cannot be ordered. History before the deploy is NULL and is never backfilled.

Nothing here may break trading: every public coroutine that runs on the trading path catches its own failures, logs them and counts
``excursion_update_failed``.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)

METHOD = "m1_bar_v1"
STATE_TTL_SECONDS = 7 * 24 * 3600  # longer than the longest strategy expiry (24 h) plus any holding time
BAR_SECONDS = 60
CATCH_UP_BARS = 15  # closed bars read per M1 event; the state remembers the last bar it applied, so a missed event is caught up
FILL_EVENTS = frozenset({"opened", "add", "order_filled"})

METRIC_STARTED = "excursion_started"
METRIC_FINALIZED = "excursion_finalized"
METRIC_MISSING = "excursion_missing_at_close"
METRIC_FAILED = "excursion_update_failed"


def state_key(group_id: str) -> str:
  return f"autotrade:excursion:{group_id}"


def open_key(symbol: str) -> str:
  return f"autotrade:open_excursions:{symbol.upper()}"


def _missing_key(group_id: str) -> str:
  return f"autotrade:excursion_missing:{group_id}"


def _text(value: object) -> str:
  return value.decode() if isinstance(value, bytes) else str(value)


@dataclass
class ExcursionState:
  group_id: str
  symbol: str
  direction: str  # BUY | SELL
  first_fill_at: int  # unix seconds of the group's first fill
  reference_price: float  # the first fill's price
  original_stop_price: float | None  # the stop on the first fill; None when the event carried none (R is then NULL)
  max_high: float
  min_low: float
  mfe_at: int | None = None  # open time of the bar that set the favourable extreme
  mae_at: int | None = None  # open time of the bar that set the adverse extreme
  bars: int = 0
  last_bar_t: int = 0  # open time of the last bar applied; a bar is never applied twice

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: object) -> "ExcursionState":
    return cls(**json.loads(_text(raw)))


async def _count(client: Any, name: str, symbol: str = "XAU") -> None:
  """Bump a counter; a failure to count is never an error."""
  try:
    from app.autotrade.lifecycle import increment_metric

    await increment_metric(client, name, symbol=symbol)
  except Exception:
    log.debug("excursion metric %s not recorded", name, exc_info=True)


def _positive(value: object) -> float | None:
  try:
    number = float(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return None
  return number if number > 0 and math.isfinite(number) else None


def new_state(event: dict, *, pip_size: float) -> ExcursionState | None:
  """The state a first-fill event opens, or None when the event lacks a usable fill price or direction."""
  group_id = str(event.get("group_id") or event.get("position_id") or "").strip()
  direction = str(event.get("direction") or "").strip().upper()
  price = _positive(event.get("price") or event.get("fill_price") or event.get("entry_price"))
  if not group_id or direction not in {"BUY", "SELL"} or price is None:
    return None
  stop = _positive(event.get("stop_loss"))
  if stop is None:
    stop_pips = _positive(event.get("stop_pips"))
    if stop_pips is not None and pip_size > 0:
      stop = price - stop_pips * pip_size if direction == "BUY" else price + stop_pips * pip_size
  try:
    filled_at = int(event.get("timestamp") or time.time())
  except (TypeError, ValueError):
    filled_at = int(time.time())
  return ExcursionState(
    group_id=group_id, symbol=str(event.get("symbol") or "XAU").upper(), direction=direction, first_fill_at=filled_at,
    reference_price=price, original_stop_price=stop, max_high=price, min_low=price,
  )


def apply_bars(state: ExcursionState, bars: Iterable[tuple[int, float, float]]) -> bool:
  """Fold closed M1 bars ``(open_time, high, low)`` into ``state``; True when anything changed.

  A bar counts when its open time is at or after ``first_fill_at`` (the bar containing the fill is excluded) and after the last bar already applied.
  """
  changed = False
  buy = state.direction == "BUY"
  for t, high, low in sorted(bars, key=lambda bar: bar[0]):
    if t < state.first_fill_at or t <= state.last_bar_t:
      continue
    state.bars += 1
    state.last_bar_t = int(t)
    if high > state.max_high:
      state.max_high = float(high)
      if buy:
        state.mfe_at = int(t)
      else:
        state.mae_at = int(t)
    if low < state.min_low:
      state.min_low = float(low)
      if buy:
        state.mae_at = int(t)
      else:
        state.mfe_at = int(t)
    changed = True
  return changed


def bars_from_frame(frame: Any) -> list[tuple[int, float, float]]:
  """Closed bars of a ``RedisOHLCSource`` frame (UTC ``time`` index = bar open time) as ``(open_time, high, low)``."""
  if frame is None or len(frame) == 0:
    return []
  return [(int(ts.timestamp()), float(row["high"]), float(row["low"])) for ts, row in frame.iterrows()]


def compute(state: ExcursionState, pip_size: float) -> dict[str, Any]:
  """The columns written next to the result. MFE/MAE are NULL when no bar followed the fill (nothing was observed)."""
  columns: dict[str, Any] = {
    "mfe_pips": None, "mae_pips": None, "mfe_r": None, "mae_r": None, "mfe_at": None,
    "first_fill_at": state.first_fill_at, "excursion_bars": state.bars, "excursion_method": METHOD,
  }
  if state.bars <= 0 or not (pip_size > 0):
    return columns
  if state.direction == "BUY":
    favourable, adverse = state.max_high - state.reference_price, state.reference_price - state.min_low
  else:
    favourable, adverse = state.reference_price - state.min_low, state.max_high - state.reference_price
  mfe, mae = max(0.0, favourable) / pip_size, max(0.0, adverse) / pip_size
  columns.update(mfe_pips=mfe, mae_pips=mae, mfe_at=state.mfe_at)
  if state.original_stop_price is not None:
    risk = abs(state.reference_price - state.original_stop_price) / pip_size
    if risk > 0:
      columns.update(mfe_r=mfe / risk, mae_r=mae / risk)
  return columns


def is_final_close(event: dict) -> bool:
  """True when the event ends the whole group: ``group_result``, or a close that reports no volume left."""
  event_type = str(event.get("type") or "")
  if event_type == "group_result":
    return True
  if event_type in {"position_closed", "manual_closed"}:
    remaining = event.get("remaining_volume")
    try:
      return remaining is not None and int(remaining) <= 0
    except (TypeError, ValueError):
      return False
  return False


async def load(client: Any, group_id: str) -> ExcursionState | None:
  raw = await client.get(state_key(group_id))
  if raw is None:
    return None
  try:
    return ExcursionState.from_json(raw)
  except (TypeError, ValueError, KeyError):
    log.warning("unreadable excursion state group_id=%s", group_id)
    return None


async def start(client: Any, event: dict, *, pip_size: float) -> bool:
  """Open the group's state on its first fill. Safe: never raises. True only when this call created the state."""
  try:
    state = new_state(event, pip_size=pip_size)
    if state is None:
      return False
    created = await client.set(state_key(state.group_id), state.to_json(), nx=True, ex=STATE_TTL_SECONDS)
    if not created:
      return False  # a later fill: the reference is the FIRST one
    await client.sadd(open_key(state.symbol), state.group_id)
    await client.expire(open_key(state.symbol), STATE_TTL_SECONDS)
    await _count(client, METRIC_STARTED, state.symbol)
    return True
  except Exception:
    log.exception("excursion start failed group_id=%s", event.get("group_id"))
    await _count(client, METRIC_FAILED, str(event.get("symbol") or "XAU"))
    return False


async def advance(client: Any, symbol: str, load_bars) -> int:
  """Apply the closed M1 bars to every open state of ``symbol``; returns the number of states that changed. Never raises.

  ``load_bars`` is an async callable returning ``[(open_time, high, low), ...]`` of recent CLOSED bars. It is called only when a state is open, so a
  symbol with nothing to track costs one SMEMBERS.
  """
  try:
    members = await client.smembers(open_key(symbol))
    group_ids = sorted(_text(member) for member in members or ())
    if not group_ids:
      return 0
    raws = await client.mget([state_key(group_id) for group_id in group_ids])
    live: list[tuple[str, ExcursionState]] = []
    gone: list[str] = []
    for group_id, raw in zip(group_ids, raws):
      if raw is None:
        gone.append(group_id)
        continue
      try:
        live.append((group_id, ExcursionState.from_json(raw)))
      except (TypeError, ValueError, KeyError):
        gone.append(group_id)
    if gone:
      await client.srem(open_key(symbol), *gone)
    if not live:
      return 0
    bars = await load_bars()
    if not bars:
      return 0
    pipe = client.pipeline()
    changed = 0
    for group_id, state in live:
      if apply_bars(state, bars):
        # xx + keepttl: an update never resurrects a state a close just deleted and never extends the 7-day life.
        pipe.set(state_key(group_id), state.to_json(), xx=True, keepttl=True)
        changed += 1
    if changed:
      await pipe.execute()
    return changed
  except Exception:
    log.exception("excursion advance failed symbol=%s", symbol)
    await _count(client, METRIC_FAILED, symbol)
    return 0


async def result_columns(client: Any, group_id: str, *, pip_size: float, symbol: str, already_recorded: bool) -> dict[str, Any] | None:
  """The excursion columns for a close event, or None when there is nothing to write. Never raises.

  ``already_recorded`` says the result row already carries an excursion (a re-delivered close after the state was deleted): then a missing state is not
  a defect and is not counted.
  """
  try:
    state = await load(client, group_id)
    if state is None:
      if not already_recorded and await client.set(_missing_key(group_id), "1", nx=True, ex=STATE_TTL_SECONDS):
        await _count(client, METRIC_MISSING, symbol)
      return None
    return compute(state, pip_size)
  except Exception:
    log.exception("excursion result columns failed group_id=%s", group_id)
    await _count(client, METRIC_FAILED, symbol)
    return None


async def discard(client: Any, group_id: str, symbol: str) -> None:
  """Delete a finished group's state. Never raises."""
  try:
    existed = await client.delete(state_key(group_id))
    await client.srem(open_key(symbol), group_id)
    if existed:
      await _count(client, METRIC_FINALIZED, symbol)
  except Exception:
    log.exception("excursion discard failed group_id=%s", group_id)
    await _count(client, METRIC_FAILED, symbol)
