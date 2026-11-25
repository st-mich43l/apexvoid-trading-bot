"""Per-strategy execution funnel for the owner's ``/strategy_funnel`` DM command.

Read-only. Every setup the analysis engine published in the window is counted once, by the furthest point it reached:
published -> not tradable (adapter refused it, from ``analysis_shadow_decisions``) -> trade (a TradePlan snapshot exists)
-> closed (an ``algo_auto`` result row with ``net_r``). Setups that are none of these were refused by the bot's admission
rules, expired or were invalidated; those reasons live only in the Algo Bot file logs, so the command cannot split them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

MAX_DAYS = 60
DEFAULT_PERIOD = "week"
PERIODS = ("week", "month", "last week", "last month")

# Engine strategy keys (analysis_opportunities.strategy) -> short labels that fit a phone-width table.
STRATEGY_LABELS: dict[str, str] = {
  "key_level": "Key Level",
  "supply": "Supply",
  "demand": "Demand",
  "confluence_zone": "Confl Zone",
  "fvg": "FVG",
  "ifvg": "iFVG",
  "order_block": "Order Block",
  "trendline": "Trendline",
  "flip_zone": "Flip Zone",
  "break_retest": "Break&Ret",
  "range_scalp": "Range Scalp",
  "round_number": "Round Num",
  "crt": "CRT",
  "impulse_retrace": "Impulse Ret",
  "impulse_pullback": "Impulse PB",
  "fade_scalp": "Fade Scalp",
  "asia_range": "Asia Range",
  "shock_retest": "Shock Ret",
}

FUNNEL_SQL = """
WITH opp AS (
  SELECT opportunity_id, strategy FROM analysis_opportunities
  WHERE created_at >= $1 AND created_at < $3 AND ($2::text IS NULL OR symbol = $2)
),
plan AS (
  SELECT DISTINCT ON (opportunity_id) opportunity_id, plan_id FROM auto_trade_plan_snapshots
  WHERE opportunity_id IN (SELECT opportunity_id FROM opp)
  ORDER BY opportunity_id, admitted_at
),
not_tradable AS (
  SELECT DISTINCT d.opportunity_id FROM analysis_shadow_decisions d
  WHERE d.outcome <> 'match_written' AND d.opportunity_id IN (SELECT opportunity_id FROM opp)
    AND NOT EXISTS (
      SELECT 1 FROM analysis_shadow_decisions w
      WHERE w.opportunity_id = d.opportunity_id AND w.outcome = 'match_written'
    )
)
SELECT
  o.strategy,
  count(*) AS published,
  count(p.plan_id) AS trades,
  count(*) FILTER (WHERE p.plan_id IS NULL AND n.opportunity_id IS NOT NULL) AS not_tradable,
  count(r.net_r) AS closed,
  count(*) FILTER (WHERE r.net_r > 0) AS wins,
  coalesce(sum(r.net_r), 0)::float8 AS net_r
FROM opp o
LEFT JOIN plan p ON p.opportunity_id = o.opportunity_id
LEFT JOIN not_tradable n ON n.opportunity_id = o.opportunity_id
LEFT JOIN auto_trade_results r ON r.group_id = p.plan_id AND r.trade_stream = 'algo_auto'
GROUP BY o.strategy
"""


@dataclass(frozen=True)
class StrategyFunnelRow:
  strategy: str
  published: int
  trades: int
  not_tradable: int
  closed: int
  wins: int
  net_r: float

  @property
  def no_trade(self) -> int:
    """Refused by admission rules, expired or invalidated (not split: reasons are only in file logs)."""
    return max(0, self.published - self.trades - self.not_tradable)


@dataclass(frozen=True)
class Window:
  """Half-open ``[start, end)`` in epoch seconds, with a human label and the window just before it."""
  start: int
  end: int
  label: str
  previous: "Window | None" = None


def parse_args(raw: str) -> tuple[str | int, str] | None:
  """Return ``(period, remainder)``. Period is ``week``, ``month``, ``last week``, ``last month`` or a day count.

  ``this week`` / ``this month`` mean ``week`` / ``month``; ``N`` or ``Nd`` is a rolling window of N days. None when the
  day count is out of range.
  """
  words = raw.split()
  period: str | int = DEFAULT_PERIOD
  rest: list[str] = []
  i = 0
  while i < len(words):
    word = words[i].lower()
    nxt = words[i + 1].lower() if i + 1 < len(words) else ""
    if word in {"last", "this"} and nxt in {"week", "month"}:
      period = nxt if word == "this" else f"last {nxt}"
      i += 2
      continue
    if word in {"week", "month"}:
      period = word
    elif word.removesuffix("d").isdigit():
      days = int(word.removesuffix("d"))
      if not 1 <= days <= MAX_DAYS:
        return None
      period = days
    else:
      rest.append(words[i])
    i += 1
  return period, " ".join(rest)


def _day(ts: datetime) -> str:
  return f"{ts.day} {ts:%b}"


def _span(start: datetime, end: datetime) -> str:
  """Inclusive calendar span for a half-open window ending at local midnight or at ``now``."""
  last = end - timedelta(seconds=1)
  return _day(start) if start.date() == last.date() else f"{_day(start)} – {_day(last)}"


def period_window(period: str | int, now: datetime, *, with_previous: bool = True) -> Window:
  """Calendar weeks start Monday 00:00 and months on the 1st, in ``now``'s timezone (the bot's ``/trade_stats`` rule)."""
  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  if isinstance(period, int):
    start, end = now - timedelta(days=period), now
    label = f"last {period} day{'s' if period != 1 else ''}"
    prev = Window(int((start - timedelta(days=period)).timestamp()), int(start.timestamp()), f"previous {period} days")
    return Window(int(start.timestamp()), int(end.timestamp()), label, prev if with_previous else None)
  if period in {"week", "last week"}:
    monday = today - timedelta(days=today.weekday())
    if period == "week":
      start, end, name = monday, now, "this week"
    else:
      start, end, name = monday - timedelta(weeks=1), monday, "last week"
    prev_start = start - timedelta(weeks=1)
    prev = Window(int(prev_start.timestamp()), int(start.timestamp()), f"week of {_day(prev_start)}")
  elif period in {"month", "last month"}:
    first = today.replace(day=1)
    if period == "month":
      start, end, name = first, now, "this month"
    else:
      start = (first - timedelta(days=1)).replace(day=1)
      end, name = first, "last month"
    prev_start = (start - timedelta(days=1)).replace(day=1)
    prev = Window(int(prev_start.timestamp()), int(start.timestamp()), f"{prev_start:%B}")
  else:
    raise ValueError(f"unknown period {period!r}")
  label = f"{name} ({_span(start, end)})"
  return Window(int(start.timestamp()), int(end.timestamp()), label, prev if with_previous else None)


async def fetch_strategy_funnel(start: int, end: int, symbol: str | None) -> list[StrategyFunnelRow]:
  from app.persistence.store import _connect

  async with _connect() as db:
    records = await db.fetch(FUNNEL_SQL, start, symbol.upper() if symbol else None, end)
  return [
    StrategyFunnelRow(
      strategy=str(r["strategy"]),
      published=int(r["published"]),
      trades=int(r["trades"]),
      not_tradable=int(r["not_tradable"]),
      closed=int(r["closed"]),
      wins=int(r["wins"]),
      net_r=float(r["net_r"] or 0.0),
    )
    for r in records
  ]


def _signed(value: float) -> str:
  return f"{value:+.2f}"


def _label(strategy: str) -> str:
  return STRATEGY_LABELS.get(strategy, strategy.replace("_", " ").title())[:11]


def _totals(rows: list[StrategyFunnelRow]) -> dict[str, float]:
  t = {k: sum(getattr(r, k) for r in rows) for k in ("published", "trades", "closed", "wins", "net_r", "not_tradable", "no_trade")}
  t["rate"] = 100 * t["trades"] / t["published"] if t["published"] else 0.0
  return t


def format_strategy_funnel(
  rows: list[StrategyFunnelRow],
  window: Window,
  symbol: str | None,
  previous_rows: list[StrategyFunnelRow] | None = None,
) -> str:
  scope = escape(symbol) if symbol else "All symbols"
  head = f"📈 <b>Strategy funnel — {scope} · {escape(window.label)}</b>"
  if not rows:
    return f"{head}\n\nNo setups were published in this window."
  rows = sorted(rows, key=lambda r: (-r.published, r.strategy))
  lines = [
    head,
    "Setups by publish time · each counted once",
    "",
    "<pre>",
    f"{'Strategy':<11} {'Pub':>5} {'Trd':>4} {'Cls':>4} {'Win%':>4} {'NetR':>6}",
  ]
  for r in rows:
    win = f"{100 * r.wins / r.closed:.0f}" if r.closed else "-"
    net = _signed(r.net_r) if r.closed else "-"
    # Pad before escaping: "&" becomes "&amp;" but still shows as one character.
    lines.append(f"{escape(f'{_label(r.strategy):<11}')} {r.published:>5} {r.trades:>4} {r.closed:>4} {win:>4} {net:>6}")
  lines.append("</pre>")
  t = _totals(rows)
  win_rate = f"{100 * t['wins'] / t['closed']:.0f}%" if t["closed"] else "-"
  lines.extend([
    f"Published <b>{t['published']:,}</b> → trades <b>{t['trades']:,}</b> ({t['rate']:.1f}%) → closed <b>{t['closed']:,}</b>",
    f"Net <b>{_signed(t['net_r'])} R</b> · win rate {win_rate}",
    f"No trade: {t['no_trade']:,} refused by rules or expired · {t['not_tradable']:,} not tradable",
  ])
  if previous_rows is not None and window.previous is not None:
    p = _totals(previous_rows)
    if p["published"]:
      lines.append(
        f"{escape(window.previous.label[:1].upper() + window.previous.label[1:])}: published {p['published']:,} → "
        f"trades {p['trades']:,} ({p['rate']:.1f}%) · net {_signed(p['net_r'])} R"
      )
    else:
      lines.append(f"{escape(window.previous.label[:1].upper() + window.previous.label[1:])}: no setups")
  lines.extend([
    "",
    "Net R is volume-weighted, auto algo only; trades still open count once closed.",
  ])
  return "\n".join(lines)
