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

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ScalpRiskState:
    data = json.loads(raw)
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
    )


def risk_key(symbol: str) -> str:
  return f"scalp:risk:{symbol.upper()}"


def risk_fraction(cfg: Any) -> float:
  """Constant fraction — never scales with losses or inactivity."""
  hfs = getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None)
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
  hfs = getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None)
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
    getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None),
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


def record_scalp_outcome(
  state: ScalpRiskState,
  *,
  result_pips: float,
  stop_pips: float | None,
  now: int,
  opened: bool = False,
  closed: bool = False,
) -> ScalpRiskState:
  """Update HFS risk counters from a fill or close. No martingale."""
  if opened:
    state.open_positions = max(0, int(state.open_positions) + 1)
    state.daily_trades = int(state.daily_trades) + 1
    state.session_trades = int(state.session_trades) + 1
  if not closed:
    return state
  state.open_positions = max(0, int(state.open_positions) - 1)
  risk_unit = float(stop_pips) if stop_pips and float(stop_pips) > 0 else 20.0
  r_multiple = float(result_pips) / risk_unit
  state.daily_r = float(state.daily_r) + r_multiple
  state.session_r = float(state.session_r) + r_multiple
  if float(result_pips) < 0:
    state.consecutive_losses = int(state.consecutive_losses) + 1
    state.last_loss_ts = int(now)
  else:
    state.consecutive_losses = 0
    state.last_loss_ts = None
  return state


async def load_risk(client: Any, symbol: str) -> ScalpRiskState:
  raw = await client.get(risk_key(symbol))
  if raw is None:
    return ScalpRiskState()
  return ScalpRiskState.from_json(raw)


async def save_risk(client: Any, symbol: str, state: ScalpRiskState) -> None:
  await client.set(risk_key(symbol), state.to_json())
