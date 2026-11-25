"""Per-strategy execution funnel for the owner's ``/strategy_funnel`` DM command.

Read-only. Every setup the analysis engine published in the window is counted once, by the furthest point it reached:
published -> not tradable (adapter refused it, from ``analysis_shadow_decisions``) -> trade (a TradePlan snapshot exists)
-> closed (an ``algo_auto`` result row with ``net_r``). Setups that are none of these were refused by the bot's admission
rules, expired or were invalidated; those reasons live only in the Algo Bot file logs, so the command cannot split them.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import time

DEFAULT_DAYS = 7
MAX_DAYS = 60

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
  WHERE created_at >= $1 AND ($2::text IS NULL OR symbol = $2)
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


def parse_args(raw: str) -> tuple[int, str] | None:
  """Return ``(days, remainder)`` from ``"[N|Nd] ..."``; None when the day count is out of range."""
  parts = raw.split()
  days = DEFAULT_DAYS
  rest: list[str] = []
  for part in parts:
    token = part.lower().removesuffix("d")
    if token.isdigit():
      days = int(token)
      if not 1 <= days <= MAX_DAYS:
        return None
    else:
      rest.append(part)
  return days, " ".join(rest)


async def fetch_strategy_funnel(days: int, symbol: str | None, *, now: float | None = None) -> list[StrategyFunnelRow]:
  from app.persistence.store import _connect

  since = int((now if now is not None else time.time()) - days * 86400)
  async with _connect() as db:
    records = await db.fetch(FUNNEL_SQL, since, symbol.upper() if symbol else None)
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


def format_strategy_funnel(rows: list[StrategyFunnelRow], days: int, symbol: str | None) -> str:
  scope = escape(symbol) if symbol else "All symbols"
  head = f"📈 <b>Strategy funnel — {scope} · last {days} day{'s' if days != 1 else ''}</b>"
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
  published = sum(r.published for r in rows)
  trades = sum(r.trades for r in rows)
  closed = sum(r.closed for r in rows)
  wins = sum(r.wins for r in rows)
  net = sum(r.net_r for r in rows)
  not_tradable = sum(r.not_tradable for r in rows)
  no_trade = sum(r.no_trade for r in rows)
  rate = 100 * trades / published if published else 0.0
  win_rate = f"{100 * wins / closed:.0f}%" if closed else "-"
  lines.extend([
    f"Published <b>{published:,}</b> → trades <b>{trades:,}</b> ({rate:.1f}%) → closed <b>{closed:,}</b>",
    f"Net <b>{_signed(net)} R</b> · win rate {win_rate}",
    f"No trade: {no_trade:,} refused by rules or expired · {not_tradable:,} not tradable",
    "",
    "Net R is volume-weighted, auto algo only. Refusal reasons: /algo_funnel.",
  ])
  return "\n".join(lines)
