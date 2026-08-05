"""Scalping risk controls — fail closed, no martingale."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    if state.last_loss_ts is not None and int(now) - int(state.last_loss_ts) < cooldown:
      return ScalpDecision(False, True, "scalp_loss_streak_cooldown", 0.0, measured)

  daily_limit = float(getattr(risk, "daily_loss_limit_r", 3.0) or 3.0)
  if state.daily_r <= -abs(daily_limit):
    return ScalpDecision(False, True, "scalp_daily_loss_limit", 0.0, measured)

  session_limit = float(getattr(risk, "session_loss_limit_r", 2.0) or 2.0)
  if state.session_r <= -abs(session_limit):
    return ScalpDecision(False, True, "scalp_session_loss_limit", 0.0, measured)

  if session == "rollover":
    return ScalpDecision(False, True, "scalp_session_rollover_block", 0.0, measured)

  return ScalpDecision(True, False, "scalp_risk_allowed", 1.0, measured)


async def load_risk(client: Any, symbol: str) -> ScalpRiskState:
  raw = await client.get(risk_key(symbol))
  if raw is None:
    return ScalpRiskState()
  return ScalpRiskState.from_json(raw)


async def save_risk(client: Any, symbol: str, state: ScalpRiskState) -> None:
  await client.set(risk_key(symbol), state.to_json())
