"""Deterministic journal review and performance-stat formatting."""

from collections import defaultdict
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.core.symbols import pip_for

_SPARKS = "▁▂▃▄▅▆▇█"


def _number(value: float | int | None) -> str:
  if value is None:
    return "-"
  return f"{value:.2f}".rstrip("0").rstrip(".")


def _signed(value: float | int, suffix: str = "") -> str:
  return f"{value:+g}{suffix}"


def _signed_p(value: float | int) -> str:
  rounded = round(value)
  if rounded > 0:
    return f"+{rounded}p"
  if rounded < 0:
    return f"−{abs(rounded)}p"
  return "0p"


def _stars(value: int | None) -> str:
  return "⭐" * value if value else "-"


def _tp_icon(index: int) -> str:
  if index == 0:
    return "🥉"
  if index == 1:
    return "🥈"
  if index == 2:
    return "🥇"
  return "🎯"


def _round_result(signal: dict) -> str:
  net = signal.get("result_pips")
  if net is None:
    return signal.get("status", "open")
  if net > 0:
    return f"{net} pips win"
  if net < 0:
    return f"{abs(net)} pips loss"
  return "breakeven"


def _round_lines(signal: dict, index: int) -> list[str]:
  entry_end = signal.get("entry_end")
  if entry_end is None:
    entry_end = signal["entry"]
  midpoint = (signal["entry"] + entry_end) / 2
  # Risk is measured against the stop as originally placed; a stop moved to BE
  # or trailed must not shrink (or zero out) the denominator for realized R.
  original_sl = signal.get("original_sl")
  if original_sl is None:
    original_sl = signal["sl"]
  risk_price = abs(midpoint - original_sl)
  risk_pips = round(risk_price / pip_for(signal.get("symbol", "XAU")))
  tps = signal.get("tps") or []
  planned = abs(tps[-1] - midpoint) / risk_price if tps and risk_price else None
  net = signal.get("result_pips")
  realized = net / risk_pips if net is not None and risk_pips else None
  seq = signal.get("daily_seq") or signal["id"]
  tp_text = " ".join(
    f"{_tp_icon(i)}{_number(tp)}"
    for i, tp in enumerate(tps)
  )
  legs = signal.get("legs") or []
  if legs:
    leg_text = " · ".join(
      f"{round(float(leg['frac']) * 100)}% @ {_signed(int(leg['pips']))}"
      for leg in legs
    )
  else:
    leg_text = "—"
  net_text = _signed(net, "p") if net is not None else signal.get("status", "open")
  setup = escape(signal.get("setup_type") or "—")
  planned_text = f"~{planned:.1f}" if planned is not None else "—"
  realized_text = f"~{realized:.1f}R" if realized is not None else "—"
  return [
    (
      f"Round {index} · #{seq}   {signal['action']} "
      f"{_number(signal['entry'])}–{_number(entry_end)}"
    ),
    f"  🛡 {_number(signal['sl'])} · TP {tp_text or '—'}",
    f"  Legs: {leg_text} → net {net_text}",
    (
      f"  Setup: {setup} · {_stars(signal.get('confluence'))} · "
      f"R:R plan {planned_text} · realized {realized_text}"
    ),
  ]


def _map_block(signal: dict) -> str:
  entry_end = signal.get("entry_end")
  if entry_end is None:
    entry_end = signal["entry"]
  seq = signal.get("daily_seq") or signal["id"]
  action_icon = "🟢" if signal["action"] == "BUY" else "🔴"
  tps = signal.get("tps") or []
  lines = [
    (
      f"{action_icon} #{seq} {signal['action']}  "
      f"{_number(signal['entry'])}–{_number(entry_end)}"
    ),
    f"├ 🛡 SL  {_number(signal['sl'])}",
  ]
  for i, tp in enumerate(tps):
    branch = "└" if i == len(tps) - 1 else "├"
    lines.append(
      f"{branch} {_tp_icon(i)} TP{i + 1} {_number(tp)}"
    )
  lines.extend([
    f"Result: {_round_result(signal)}",
    "🤖 st_mich43l · auto-map",
  ])
  return f"<pre>{escape(chr(10).join(lines))}</pre>"


def format_review(cluster: list[dict]) -> str:
  """Render a cluster-aware journal review from stored facts only."""
  root = cluster[0]
  root_seq = root.get("daily_seq") or root["id"]
  entry_end = root.get("entry_end")
  if entry_end is None:
    entry_end = root["entry"]
  lines = [
    (
      f"📋 <b>Review — {root['action']} zone "
      f"{_number(root['entry'])}–{_number(entry_end)} "
      f"(cluster root #{root_seq})</b>"
    ),
    "",
  ]
  for index, signal in enumerate(cluster, 1):
    lines.extend(_round_lines(signal, index))
    lines.append("")

  results = [
    signal["result_pips"]
    for signal in cluster
    if signal.get("result_pips") is not None
  ]
  wins = sum(value > 0 for value in results)
  losses = sum(value < 0 for value in results)
  lines.extend([
    (
      f"<b>Cluster:</b> {len(cluster)} rounds · {wins}W / {losses}L · "
      f"net {_signed(sum(results), 'p')}"
    ),
    "",
    "— <b>Technical analysis</b> —",
  ])
  notes = [signal["note"].strip() for signal in cluster if signal.get("note")]
  note = escape(notes[-1]) if notes else ""
  grades = [
    signal["confluence"]
    for signal in cluster
    if signal.get("confluence") is not None
  ]
  lines.extend([
    f"Key level / AOI:      {note}",
    "Structure (BOS/CHoCH/flip):  ",
    "Liquidity / OB / FVG / fib:   ",
    f"Confluence grade:     {_stars(grades[-1] if grades else None)}",
    f"Lesson / takeaway:    {note}",
    "",
  ])
  for signal in cluster:
    lines.append(_map_block(signal))
  return "\n".join(lines)


def sparkline(values: list[int]) -> str:
  """Scale cumulative values into one dependency-free block per trade."""
  if not values:
    return "—"
  low, high = min(values), max(values)
  if low == high:
    return _SPARKS[len(_SPARKS) // 2] * len(values)
  return "".join(
    _SPARKS[round((value - low) * (len(_SPARKS) - 1) / (high - low))]
    for value in values
  )


def _session_name(
  ts: int | None,
  timezone_name: str,
  asia_start: int,
  london_start: int,
  ny_start: int,
) -> str:
  if ts is None:
    return "Legacy"
  hour = datetime.fromtimestamp(ts, ZoneInfo(timezone_name)).hour
  if london_start <= hour < ny_start:
    return "London"
  if ny_start <= hour < asia_start:
    return "NY"
  return "Asia"


def _group_stats(label: str, rows: list[dict]) -> dict:
  values = [row["value"] for row in rows]
  wins = sum(value > 0 for value in values)
  losses = sum(value < 0 for value in values)
  return {
    "label": label,
    "rows": rows,
    "trades": len(values),
    "wins": wins,
    "losses": losses,
    "win_rate": wins / len(values) * 100 if values else 0,
    "net": sum(values),
  }


def _group_line(group: dict) -> str:
  return (
    f"{escape(group['label'])}: {group['trades']} · "
    f"{group['wins']}W/{group['losses']}L · "
    f"{_signed(group['net'], 'p')} · {group['win_rate']:.0f}%"
  )


def _stats_title(period: str) -> str:
  words = period.strip().split()
  if not words:
    return "STATS"
  if words[0].upper() == "XAU":
    words[0] = "XAU/USD"
  return " ".join(words).upper()


def _metric_line(
  icon: str,
  label: str,
  value: str,
  suffix: str = "",
) -> str:
  line = f"{icon} {label:<11} {value:>7}"
  if suffix:
    line = f"{line}  {suffix}"
  return line


def _setup_label(value: str | None) -> str:
  words = (value or "untagged").replace("_", " ").replace("-", " ").split()
  return " ".join(
    word.upper() if word.lower() in {"ob", "fvg", "ny"} else word.title()
    for word in words
  )


def _short_label(value: str, width: int = 19) -> str:
  if len(value) <= width:
    return value
  if width <= 1:
    return value[:width]
  return value[:width - 1] + "…"


def _stats_group_lines(groups: list[dict], *, setup: bool) -> list[str]:
  if not groups:
    return ["└─ —"]
  lines = []
  session_icons = {
    "Asia": "🌏",
    "London": "🌍",
    "NY": "🌎",
    "Legacy": "🕐",
  }
  for index, group in enumerate(groups):
    branch = "└─" if index == len(groups) - 1 else "├─"
    if setup:
      label = _short_label(_setup_label(group["label"]))
      lines.append(
        f"{branch} {label:<19} {_signed_p(group['net']):>7} · "
        f"{group['wins']}W/{group['losses']}L · {group['win_rate']:.0f}%"
      )
    else:
      icon = session_icons.get(group["label"], "🕐")
      lines.append(
        f"{branch} {icon} {group['label']:<8} "
        f"{_signed_p(group['net']):>7} · "
        f"{group['wins']}W/{group['losses']}L · {group['win_rate']:.0f}%"
      )
  return lines


def _cluster_lines(groups: list[dict]) -> list[str]:
  if not groups:
    return ["└─ —"]
  lines = []
  for index, group in enumerate(groups):
    branch = "└─" if index == len(groups) - 1 else "├─"
    lines.append(
      f"{branch} {_short_label(group['label'], 20):<20} "
      f"{_signed_p(group['net']):>7} · "
      f"{group['rounds']}r · {group['wins']}W/{group['losses']}L"
    )
  return lines


_STREAM_ORDER = ("algo_auto", "algo_manual", "manual")
_STREAM_LABELS = {
  "algo_auto": "Algo auto",
  "algo_manual": "Algo manual",
  "manual": "Manual signal",
  "all_unique": "All unique",
}


def _performance_stats(rows: list[dict]) -> dict:
  values = [float(row["value"]) for row in rows]
  wins = sum(value > 0 for value in values)
  r_values = [
    float(row["r_multiple"])
    for row in rows
    if row.get("r_multiple") is not None
  ]
  weighted_stops = [
    (float(row["stop_pips"]), int(row.get("fill_count") or 1))
    for row in rows
    if row.get("stop_pips") is not None
  ]
  stop_weight = sum(weight for _, weight in weighted_stops)
  return {
    "trades": len(rows),
    "fill_count": sum(int(row.get("fill_count") or 1) for row in rows),
    "wins": wins,
    "losses": sum(value < 0 for value in values),
    "win_rate": wins / len(rows) * 100 if rows else 0,
    "mean_r": sum(r_values) / len(r_values) if r_values else 0,
    "total_pips": sum(values),
    "mean_stop_pips": (
      sum(stop * weight for stop, weight in weighted_stops) / stop_weight
      if stop_weight else 0
    ),
  }


def _unique_trade_rows(rows: list[dict]) -> list[dict]:
  """Collapse cross-view duplicates while preserving the broker facts."""
  priority = {"manual": 0, "algo_auto": 1, "algo_manual": 2}
  selected: dict[str, dict] = {}
  for index, row in enumerate(rows):
    key = str(row.get("trade_key") or f"row:{index}")
    current = selected.get(key)
    if current is None:
      selected[key] = row
      continue
    if priority.get(row.get("stream"), 0) <= priority.get(current.get("stream"), 0):
      continue
    selected[key] = {
      **current,
      **{key: value for key, value in row.items() if value is not None},
    }
  return sorted(selected.values(), key=lambda row: row.get("ts") or 0)


def build_stats(
  records: list[dict],
  signals: list[dict],
  timezone_name: str,
  asia_start: int,
  london_start: int,
  ny_start: int,
) -> dict:
  """Build the canonical trade-stat aggregation used by every renderer."""
  stream_rows = [
    {
      **record,
      "stream": record.get("stream") or "manual",
      "fill_count": int(record.get("fill_count") or 1),
      "value": record["pips"] if record["sign"] == "+" else -record["pips"],
    }
    for record in records
  ]
  rows = _unique_trade_rows(stream_rows)
  by_stream = {
    stream: _performance_stats([
      row for row in stream_rows if row["stream"] == stream
    ])
    for stream in _STREAM_ORDER
  }
  by_stream["all_unique"] = _performance_stats(rows)
  all_values = [row["value"] for row in rows]
  wins = [value for value in all_values if value > 0]
  losses = [value for value in all_values if value < 0]
  total = len(all_values)
  rate = len(wins) / total * 100 if total else 0
  net = sum(all_values)
  average_win = sum(wins) / len(wins) if wins else 0
  average_loss = sum(losses) / len(losses) if losses else 0
  expectancy = net / total if total else 0

  by_setup: dict[str, list[dict]] = defaultdict(list)
  by_session: dict[str, list[dict]] = defaultdict(list)
  for row in rows:
    by_setup[row.get("setup_type") or "untagged"].append(row)
    by_session[
      _session_name(
        row.get("signal_ts"),
        timezone_name,
        asia_start,
        london_start,
        ny_start,
      )
    ].append(row)

  setup_groups = sorted(
    (
      _group_stats(label, group)
      for label, group in by_setup.items()
    ),
    key=lambda group: group["net"],
    reverse=True,
  )
  session_groups = []
  for name in ("Asia", "London", "NY", "Legacy"):
    if name in by_session:
      session_groups.append(_group_stats(name, by_session[name]))

  signal_by_id = {signal["id"]: signal for signal in signals}
  cluster_sizes: dict[int, int] = defaultdict(int)
  for signal in signals:
    cluster_sizes[signal.get("parent_id") or signal["id"]] += 1
  by_cluster: dict[int, list[dict]] = defaultdict(list)
  for row in rows:
    signal = signal_by_id.get(row.get("signal_id"))
    if signal:
      root_id = signal.get("parent_id") or signal["id"]
      if cluster_sizes[root_id] >= 2:
        by_cluster[root_id].append(row)

  cluster_groups = []
  for root_id, group in sorted(by_cluster.items()):
    root = signal_by_id[root_id]
    entry_end = root.get("entry_end")
    if entry_end is None:
      entry_end = root["entry"]
    cluster_values = [row["value"] for row in group]
    wins_count = sum(value > 0 for value in cluster_values)
    losses_count = sum(value < 0 for value in cluster_values)
    cluster_groups.append({
      "root_id": root_id,
      "label": (
        f"zone {_number(root['entry'])}–{_number(entry_end)} "
        f"{root['action']}"
      ),
      "rounds": cluster_sizes[root_id],
      "wins": wins_count,
      "losses": losses_count,
      "net": sum(cluster_values),
    })

  cumulative = []
  running = 0
  for value in all_values:
    running += value
    cumulative.append(running)
  return {
    "rows": rows,
    "trades": total,
    "wins": len(wins),
    "losses": len(losses),
    "win_rate": rate,
    "net": net,
    "average_win": average_win,
    "average_loss": average_loss,
    "expectancy": expectancy,
    "best": max(rows, key=lambda row: row["value"]) if rows else None,
    "worst": min(rows, key=lambda row: row["value"]) if rows else None,
    "by_setup": setup_groups,
    "by_session": session_groups,
    "by_cluster": cluster_groups,
    "by_stream": by_stream,
    "cumulative": cumulative,
  }


def _stream_lines(by_stream: dict[str, dict]) -> list[str]:
  lines = []
  shown = [
    stream for stream in (*_STREAM_ORDER, "all_unique")
    if by_stream.get(stream, {}).get("fill_count")
  ]
  for index, stream in enumerate(shown):
    item = by_stream[stream]
    branch = "└─" if index == len(shown) - 1 else "├─"
    lines.extend([
      f"{branch} {_STREAM_LABELS[stream]:<13} "
      f"{item['fill_count']} fills · {item['win_rate']:.0f}% WR",
      f"   {_signed_p(item['total_pips'])} · {item['mean_r']:+.2f}R "
      f"· stop {item['mean_stop_pips']:.0f}p",
    ])
  return lines or ["└─ —"]


def format_stats(stats: dict, period: str) -> str:
  """Render the interactive stats view from canonical aggregated facts."""
  if not stats["trades"]:
    text = "\n".join([
      f"📊 STATS — {_stats_title(period)}",
      "━━━━━━━━━━━━━━━━━━━━━━",
      "🧘 No closed trades",
      "capital preserved · no stats to report",
      "━━━━━━━━━━━━━━━━━━━━━━",
      "🤖 Apex Void · stats",
    ])
    return f"<pre>{escape(text)}</pre>"

  best = stats["best"]
  worst = stats["worst"]
  best_seq = best.get("daily_seq") or best.get("signal_id") or "?"
  worst_seq = worst.get("daily_seq") or worst.get("signal_id") or "?"
  net_icon = "🟢" if stats["net"] >= 0 else "🔴"
  lines = [
    f"📊 STATS — {_stats_title(period)}",
    "━━━━━━━━━━━━━━━━━━━━━━",
    _metric_line("💰", "Net", _signed_p(stats["net"]), net_icon),
    _metric_line(
      "🎯",
      "Winrate",
      f"{stats['win_rate']:.0f}%",
      f"({stats['wins']}W / {stats['losses']}L)",
    ),
    _metric_line("📦", "Trades", str(stats["trades"])),
    _metric_line("🟢", "Avg win", _signed_p(stats["average_win"])),
    _metric_line("🔴", "Avg loss", _signed_p(stats["average_loss"])),
    _metric_line("⚖", "Expectancy", _signed_p(stats["expectancy"]), "/ trade"),
    _metric_line(
      "🏆",
      "Best",
      _signed_p(best["value"]),
      f"· #{best_seq} {_setup_label(best.get('setup_type'))}",
    ),
    _metric_line(
      "🩸",
      "Worst",
      _signed_p(worst["value"]),
      f"· #{worst_seq} {_setup_label(worst.get('setup_type'))}",
    ),
    "",
    "🧬 By stream",
    *_stream_lines(stats["by_stream"]),
    "",
    "📐 By setup",
    *_stats_group_lines(stats["by_setup"], setup=True),
    "",
    "🕐 By session",
    *_stats_group_lines(stats["by_session"], setup=False),
  ]

  if stats["by_cluster"]:
    lines.extend([
      "",
      "🔁 By re-entry",
      *_cluster_lines(stats["by_cluster"]),
    ])

  lines.extend([
    "",
    "📈 Equity",
    f"{sparkline(stats['cumulative'])}  {_signed_p(stats['net'])}",
    "━━━━━━━━━━━━━━━━━━━━━━",
    "🤖 Apex Void · stats",
  ])
  return f"<pre>{escape(chr(10).join(lines))}</pre>"
