"""XAU killzone session gate for the technique pack.

Owner 2026-08-10 (prod dig): dead hours UTC 01/03/05/08–09 bled equity while
London late / NY / late NY printed the edge. Enforce windows aligned with
that dig + common XAU killzone playbooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


KILLZONE_LONDON = "london"
KILLZONE_NY = "london_ny"
KILLZONE_LATE_NY = "late_ny"
KILLZONE_NONE = "outside"


@dataclass(frozen=True)
class KillzoneDecision:
  allowed: bool
  reason_code: str
  killzone_name: str
  utc_hour: int
  measured: dict[str, Any]


def technique_enforce(cfg: Any | None) -> bool:
  section = getattr(getattr(cfg, "execution", None), "technique", None)
  if section is None:
    # Bare test stubs / partial configs without the technique node stay on
    # pre-pack behaviour. Full ExecutionConfig always carries the node with
    # enforce=True by default.
    return False
  return bool(getattr(section, "enforce", True))


def technique_require_sweep_body(cfg: Any | None) -> bool:
  """Sweep/body confirmation follows the instrument technique node."""
  if not technique_enforce(cfg):
    return False
  section = getattr(getattr(cfg, "execution", None), "technique", None)
  if section is None:
    return True
  return bool(getattr(section, "require_sweep_body", True))


def _session_hours(cfg: Any | None) -> tuple[int, int, int, int]:
  sessions = getattr(getattr(cfg, "market_data", None), "sessions", None)
  london = int(getattr(sessions, "london_start", 7) or 7)
  ny = int(getattr(sessions, "ny_start", 13) or 13)
  asia = int(getattr(sessions, "asia_start", 22) or 22)
  rollover = int(getattr(sessions, "daily_rollover_utc_hour", 21) or 21)
  return london, ny, asia, rollover


def _include_late_ny(cfg: Any | None) -> bool:
  section = getattr(getattr(cfg, "execution", None), "technique", None)
  if section is None:
    return True
  return bool(getattr(section, "include_late_ny", True))


def _london_hours(cfg: Any | None) -> int:
  section = getattr(getattr(cfg, "execution", None), "technique", None)
  try:
    return max(1, int(getattr(section, "london_window_hours", 3) or 3))
  except (TypeError, ValueError):
    return 3


def _ny_hours(cfg: Any | None) -> int:
  section = getattr(getattr(cfg, "execution", None), "technique", None)
  try:
    return max(1, int(getattr(section, "ny_window_hours", 3) or 3))
  except (TypeError, ValueError):
    return 3


def classify_killzone(
  ts: int | float | None = None,
  *,
  hour: int | None = None,
  cfg: Any | None = None,
) -> KillzoneDecision:
  """Classify whether ``ts``/``hour`` (UTC) is inside an allowed killzone."""
  if hour is None:
    if ts is None:
      hour = datetime.now(timezone.utc).hour
    else:
      hour = datetime.fromtimestamp(int(ts), tz=timezone.utc).hour
  hour = int(hour) % 24
  london, ny, _asia, rollover = _session_hours(cfg)
  london_span = _london_hours(cfg)
  ny_span = _ny_hours(cfg)

  rollover_hours = {
    (rollover - 1) % 24,
    rollover % 24,
    (rollover + 1) % 24,
  }
  measured = {
    "utc_hour": hour,
    "london_start": london,
    "ny_start": ny,
    "rollover_utc_hour": rollover,
    "london_window_hours": london_span,
    "ny_window_hours": ny_span,
    "include_late_ny": _include_late_ny(cfg),
  }
  # Late NY (22–23) is an intentional killzone that overlaps rollover±1 —
  # prefer the killzone when the flag is on (prod dig +248/+143 hours).
  if _include_late_ny(cfg) and hour in {22, 23}:
    return KillzoneDecision(
      allowed=True,
      reason_code="killzone_late_ny",
      killzone_name=KILLZONE_LATE_NY,
      utc_hour=hour,
      measured=measured,
    )
  if hour in rollover_hours:
    return KillzoneDecision(
      allowed=False,
      reason_code="outside_killzone",
      killzone_name=KILLZONE_NONE,
      utc_hour=hour,
      measured={**measured, "block": "rollover"},
    )

  london_end = (london + london_span) % 24
  if london_end > london:
    in_london = london <= hour < london_end
  else:
    in_london = hour >= london or hour < london_end
  if in_london:
    return KillzoneDecision(
      allowed=True,
      reason_code="killzone_london",
      killzone_name=KILLZONE_LONDON,
      utc_hour=hour,
      measured=measured,
    )

  ny_end = (ny + ny_span) % 24
  if ny_end > ny:
    in_ny = ny <= hour < ny_end
  else:
    in_ny = hour >= ny or hour < ny_end
  if in_ny:
    return KillzoneDecision(
      allowed=True,
      reason_code="killzone_london_ny",
      killzone_name=KILLZONE_NY,
      utc_hour=hour,
      measured=measured,
    )

  return KillzoneDecision(
    allowed=False,
    reason_code="outside_killzone",
    killzone_name=KILLZONE_NONE,
    utc_hour=hour,
    measured={**measured, "block": "outside_windows"},
  )


def is_killzone_utc(
  hour: int,
  cfg: Any | None = None,
) -> bool:
  return classify_killzone(hour=hour, cfg=cfg).allowed


def evaluate_killzone_gate(
  *,
  ts: int | float | None = None,
  hour: int | None = None,
  cfg: Any | None = None,
  require: bool = True,
) -> KillzoneDecision:
  """Hard gate when technique.enforce and ``require`` are true."""
  decision = classify_killzone(ts=ts, hour=hour, cfg=cfg)
  if not require or not technique_enforce(cfg):
    return KillzoneDecision(
      allowed=True,
      reason_code=(
        decision.reason_code
        if decision.allowed
        else "outside_killzone_not_enforced"
      ),
      killzone_name=decision.killzone_name,
      utc_hour=decision.utc_hour,
      measured={
        **decision.measured,
        "require": require,
        "technique_enforce": technique_enforce(cfg),
        "would_block": not decision.allowed,
      },
    )
  return decision


def parse_reaction_publish_windows(cfg: Any | None = None) -> tuple[tuple[int, int], ...]:
  """Parse ``7-11,13-16`` into exclusive-end hour ranges."""
  section = getattr(getattr(cfg, "execution", None), "technique", None)
  raw = str(
    getattr(section, "reaction_publish_windows", "7-11,13-16") or "7-11,13-16"
  )
  windows: list[tuple[int, int]] = []
  for part in raw.split(","):
    token = part.strip()
    if not token or "-" not in token:
      continue
    left, right = token.split("-", 1)
    try:
      start = int(left) % 24
      end = int(right)
    except (TypeError, ValueError):
      continue
    if end <= start:
      continue
    windows.append((start, end % 24 if end >= 24 else end))
  return tuple(windows) if windows else ((7, 11), (13, 16))


def hour_in_reaction_publish_windows(hour: int, cfg: Any | None = None) -> bool:
  hour = int(hour) % 24
  for start, end in parse_reaction_publish_windows(cfg):
    if start <= hour < end:
      return True
  return False


def evaluate_reaction_publish_window(
  *,
  ts: int | float | None = None,
  hour: int | None = None,
  cfg: Any | None = None,
  require: bool = True,
) -> KillzoneDecision:
  """Non-scalp reaction hours (not the HFS killzone / late-NY clock)."""
  if hour is None:
    if ts is None:
      hour = datetime.now(timezone.utc).hour
    else:
      hour = datetime.fromtimestamp(int(ts), tz=timezone.utc).hour
  hour = int(hour) % 24
  windows = parse_reaction_publish_windows(cfg)
  inside = hour_in_reaction_publish_windows(hour, cfg)
  measured = {
    "utc_hour": hour,
    "reaction_publish_windows": list(windows),
    "require": require,
    "technique_enforce": technique_enforce(cfg),
  }
  if not require or not technique_enforce(cfg):
    return KillzoneDecision(
      allowed=True,
      reason_code=(
        "reaction_publish_window"
        if inside
        else "outside_reaction_publish_window_not_enforced"
      ),
      killzone_name=KILLZONE_NONE,
      utc_hour=hour,
      measured={**measured, "would_block": not inside},
    )
  if inside:
    return KillzoneDecision(
      allowed=True,
      reason_code="reaction_publish_window",
      killzone_name=KILLZONE_NONE,
      utc_hour=hour,
      measured=measured,
    )
  return KillzoneDecision(
    allowed=False,
    reason_code="outside_reaction_publish_window",
    killzone_name=KILLZONE_NONE,
    utc_hour=hour,
    measured=measured,
  )


# Confirmation patterns accepted as sweep / body / displacement for reaction.
SWEEP_BODY_TRIGGERS = frozenset({
  "sweep_reclaim",
  "strong_reclaim",
  "body_close",
  "strong_close",
  "engulfing",
  "rejection_choch",
  # Owner 2026-08-14 algo_manual: Key SELL #51 was a failure wick after tap.
  "wick_rejection",
})


def confirmation_is_sweep_body(pattern: str | None) -> bool:
  return str(pattern or "").strip().lower() in SWEEP_BODY_TRIGGERS
