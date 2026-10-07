"""Scalping risk controls — fail closed, no martingale."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any


@dataclass
class ScalpRiskState:
  daily_trades: int = 0
  session_trades: int = 0
  consecutive_losses: int = 0
  last_loss_ts: int | None = None
  open_positions: int = 0
  daily_r: float = 0.0
  session_r: float = 0.0
  day_key: str = ""
  session_key: str = ""
  measured: dict[str, Any] = field(default_factory=dict)
  # One id per scalping group — clip fills must not each increment concurrent.
  open_group_ids: list[str] = field(default_factory=list)

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ScalpRiskState:
    data = json.loads(raw)
    groups = [
      str(item).strip()
      for item in (data.get("open_group_ids") or [])
      if str(item).strip()
    ]
    return cls(
      daily_trades=int(data.get("daily_trades") or 0),
      session_trades=int(data.get("session_trades") or 0),
      consecutive_losses=int(data.get("consecutive_losses") or 0),
      last_loss_ts=(
        None if data.get("last_loss_ts") is None else int(data["last_loss_ts"])
      ),
      open_positions=int(data.get("open_positions") or 0),
      daily_r=float(data.get("daily_r") or 0.0),
      session_r=float(data.get("session_r") or 0.0),
      day_key=str(data.get("day_key") or ""),
      session_key=str(data.get("session_key") or ""),
      measured=dict(data.get("measured") or {}),
      open_group_ids=groups,
    )


def risk_key(symbol: str) -> str:
  return f"scalp:risk:{symbol.upper()}"


def _trading_day_key(now: int, cfg: Any) -> str:
  sessions = getattr(getattr(cfg, "market_data", None), "sessions", None)
  rollover = int(getattr(sessions, "daily_rollover_utc_hour", 21) or 21)
  shifted = datetime.fromtimestamp(int(now), tz=timezone.utc) - timedelta(
    hours=rollover
  )
  return shifted.date().isoformat()


def apply_daily_reset(
  state: ScalpRiskState,
  cfg: Any,
  *,
  now: int,
  session: str,
) -> ScalpRiskState:
  """Clear daily/session R and trade counters at trading-day/session edges.

  ``day_key``/``session_key`` were persisted but never compared against
  anything, so a losing streak that tripped scalp_daily_loss_limit stayed
  tripped indefinitely once daily_r crossed the threshold -- confirmed live,
  daily_r sat at -3.75R from a loss recorded 2026-08-12, still blocking
  every scalp entry over 24h later with no rollover in between.
  """
  day_key = _trading_day_key(now, cfg)
  if state.day_key != day_key:
    state.day_key = day_key
    state.daily_trades = 0
    state.daily_r = 0.0
  session_key = f"{day_key}:{session}"
  if state.session_key != session_key:
    state.session_key = session_key
    state.session_trades = 0
    state.session_r = 0.0
  return state


def _normalize_group_id(group_id: str | None) -> str | None:
  text = str(group_id or "").strip()
  return text or None


@dataclass(frozen=True)
class RecordScalpOutcomeResult:
  """Result of a risk-ledger update.

  ``accrued_r`` is None when the close was skipped because ``stop_pips`` was
  missing — callers must treat that as a data-quality bug, not invent R.
  Prefer the realized fill-to-invalidation distance as ``stop_pips`` (the R
  unit); fall back to planned ``expected_stop_pips`` only when fill is absent.
  Attribute access forwards to ``state`` so existing call sites keep working.
  """

  state: ScalpRiskState
  accrued_r: float | None = None
  skipped_no_stop: bool = False

  def __getattr__(self, name: str) -> Any:
    return getattr(self.state, name)


def record_scalp_outcome(
  state: ScalpRiskState,
  *,
  result_pips: float,
  stop_pips: float | None,
  now: int,
  opened: bool = False,
  closed: bool = False,
  group_id: str | None = None,
  r_multiple: float | None = None,
) -> RecordScalpOutcomeResult:
  """Update scalping risk counters from a fill or close. No martingale.

  When ``closed`` and neither a positive ``stop_pips`` nor an explicit
  ``r_multiple`` is provided, open-position bookkeeping still runs but R is
  **not** accrued (the old 20.0 pip fallback is gone).
  """
  if isinstance(state, RecordScalpOutcomeResult):
    state = state.state
  gid = _normalize_group_id(group_id)
  if opened:
    if gid is not None and gid in state.open_group_ids:
      return RecordScalpOutcomeResult(state=state, accrued_r=None)
    if gid is not None:
      state.open_group_ids.append(gid)
    state.open_positions = (
      len(state.open_group_ids)
      if state.open_group_ids
      else max(0, int(state.open_positions) + 1)
    )
    state.daily_trades = int(state.daily_trades) + 1
    state.session_trades = int(state.session_trades) + 1
  if not closed:
    return RecordScalpOutcomeResult(state=state, accrued_r=None)
  if gid is not None and gid in state.open_group_ids:
    state.open_group_ids = [item for item in state.open_group_ids if item != gid]
    state.open_positions = len(state.open_group_ids)
  else:
    state.open_positions = max(0, int(state.open_positions) - 1)

  if r_multiple is not None:
    accrued = float(r_multiple)
  else:
    try:
      risk_unit = float(stop_pips) if stop_pips is not None else 0.0
    except (TypeError, ValueError):
      risk_unit = 0.0
    if risk_unit <= 0:
      # Do not invent a denominator. Position book is already closed above.
      return RecordScalpOutcomeResult(
        state=state, accrued_r=None, skipped_no_stop=True,
      )
    accrued = float(result_pips) / risk_unit

  state.daily_r = float(state.daily_r) + float(accrued)
  state.session_r = float(state.session_r) + float(accrued)
  if float(accrued) < 0:
    state.consecutive_losses = int(state.consecutive_losses) + 1
    state.last_loss_ts = int(now)
  else:
    state.consecutive_losses = 0
    state.last_loss_ts = None
  return RecordScalpOutcomeResult(state=state, accrued_r=float(accrued))


def unwrap_risk_state(result: ScalpRiskState | RecordScalpOutcomeResult) -> ScalpRiskState:
  if isinstance(result, RecordScalpOutcomeResult):
    return result.state
  return result


async def load_risk(client: Any, symbol: str) -> ScalpRiskState:
  raw = await client.get(risk_key(symbol))
  if raw is None:
    return ScalpRiskState()
  return ScalpRiskState.from_json(raw)


async def save_risk(client: Any, symbol: str, state: ScalpRiskState) -> None:
  await client.set(risk_key(symbol), state.to_json())
