"""Scalping risk controls — fail closed, no martingale."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from app.scalping.models import ScalpDecision


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
  # One id per HFS group — clip fills must not each increment concurrent.
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


def risk_fraction(cfg: Any) -> float:
  """Constant fraction — never scales with losses or inactivity."""
  hfs = getattr(getattr(cfg, "strategies", None), "scalping", None)
  risk = getattr(hfs, "risk", None)
  try:
    return float(getattr(risk, "risk_fraction_per_trade", 0.10) or 0.10)
  except (TypeError, ValueError):
    return 0.10


def evaluate_risk(
  state: ScalpRiskState,
  cfg: Any,
  *,
  session: str,
  now: int,
) -> ScalpDecision:
  hfs = getattr(getattr(cfg, "strategies", None), "scalping", None)
  risk = getattr(hfs, "risk", None)
  measured = {
    "daily_trades": state.daily_trades,
    "session_trades": state.session_trades,
    "consecutive_losses": state.consecutive_losses,
    "open_positions": state.open_positions,
    "daily_r": state.daily_r,
    "session_r": state.session_r,
    "risk_fraction": risk_fraction(cfg),
  }
  max_open = int(getattr(risk, "maximum_concurrent_positions", 1) or 1)
  if state.open_positions >= max_open:
    return ScalpDecision(False, True, "scalp_max_concurrent_positions", 0.0, measured)

  max_daily = int(getattr(risk, "maximum_daily_trades", 30) or 30)
  if state.daily_trades >= max_daily:
    return ScalpDecision(False, True, "scalp_daily_trade_cap", 0.0, measured)

  max_session = int(getattr(risk, "maximum_session_trades", 12) or 12)
  if state.session_trades >= max_session:
    return ScalpDecision(False, True, "scalp_session_trade_cap", 0.0, measured)

  max_losses = int(getattr(risk, "maximum_consecutive_losses", 3) or 3)
  cooldown = int(getattr(risk, "cooldown_after_loss_minutes", 5) or 5) * 60
  if state.consecutive_losses >= max_losses:
    # Live 2026-08-12: old logic only blocked *during* cooldown, then allowed
    # trading again with streak still ≥ max. Serve the full cooldown, then
    # require the streak to be cleared (see apply_loss_streak_cooldown_reset).
    cooled = (
      state.last_loss_ts is None
      or int(now) - int(state.last_loss_ts) >= cooldown
    )
    if not cooled:
      return ScalpDecision(False, True, "scalp_loss_streak_cooldown", 0.0, measured)
    return ScalpDecision(False, True, "scalp_loss_streak_active", 0.0, measured)

  daily_limit = float(getattr(risk, "daily_loss_limit_r", 3.0) or 3.0)
  if state.daily_r <= -abs(daily_limit):
    return ScalpDecision(False, True, "scalp_daily_loss_limit", 0.0, measured)

  session_limit = float(getattr(risk, "session_loss_limit_r", 2.0) or 2.0)
  if state.session_r <= -abs(session_limit):
    return ScalpDecision(False, True, "scalp_session_loss_limit", 0.0, measured)

  if session == "rollover":
    return ScalpDecision(False, True, "scalp_session_rollover_block", 0.0, measured)

  return ScalpDecision(True, False, "scalp_risk_allowed", 1.0, measured)


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


def live_exposure_ids(exposures: list[Any]) -> set[str]:
  """Stable ids from open V6 positions / V8 plan runtimes."""
  ids: set[str] = set()
  for item in exposures or []:
    for raw in (
      getattr(item, "group_id", None),
      getattr(item, "plan_id", None),
    ):
      token = _normalize_group_id(None if raw is None else str(raw))
      if token is not None:
        ids.add(token)
        if token.startswith("v8:"):
          ids.add(token[3:])
        else:
          ids.add(f"v8:{token}")
  return ids


def reconcile_open_positions(
  state: ScalpRiskState,
  live_ids: set[str] | None,
) -> ScalpRiskState:
  """Drop ghost HFS concurrent when the broker/plan book no longer has them.

  Live 2026-08-14: five-clip ``order_filled`` events each incremented
  ``open_positions`` while one ``position_closed`` decremented once, then
  ``scalp_max_concurrent_positions`` blocked real Impulse discoveries with
  an empty ``auto_trade:positions`` set.
  """
  live = set(live_ids or ())
  if state.open_group_ids:
    state.open_group_ids = [
      gid for gid in state.open_group_ids if gid in live
    ]
    state.open_positions = len(state.open_group_ids)
    return state
  if not live:
    state.open_positions = 0
  elif int(state.open_positions) > len(live):
    state.open_positions = len(live)
  return state


def apply_loss_streak_cooldown_reset(
  state: ScalpRiskState,
  cfg: Any,
  *,
  now: int,
) -> ScalpRiskState:
  """After the cooldown window, clear the streak so trading can resume.

  Wins also clear the streak via ``record_scalp_outcome``. This path covers
  the case where the bot sat out the cooldown without a win.
  """
  risk = getattr(
    getattr(getattr(cfg, "strategies", None), "scalping", None),
    "risk",
    None,
  )
  max_losses = int(getattr(risk, "maximum_consecutive_losses", 3) or 3)
  cooldown = int(getattr(risk, "cooldown_after_loss_minutes", 5) or 5) * 60
  if state.consecutive_losses < max_losses:
    return state
  if state.last_loss_ts is None:
    return state
  if int(now) - int(state.last_loss_ts) < cooldown:
    return state
  state.consecutive_losses = 0
  state.last_loss_ts = None
  return state


@dataclass(frozen=True)
class RecordScalpOutcomeResult:
  """Result of a risk-ledger update.

  ``accrued_r`` is None when the close was skipped because ``stop_pips`` was
  missing — callers must treat that as a data-quality bug, not invent R.
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
  """Update HFS risk counters from a fill or close. No martingale.

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
