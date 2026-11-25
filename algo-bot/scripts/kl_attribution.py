"""Read-only attribution of XAU Key Level (PR-KL14): what broke it on 9 Nov, and why v13 is near-silent.

Two modes, neither writes anything but files under --out:

  trades (Part A, production)   every closed XAU algo_auto Key Level trade since --since, one row per trade with its version, role, confirmation, sweep,
                                approach side, HTF alignment, session, day of week, structure timeframe, v13 rule, net R and excursions; tables A1-A8.
  label  (Parts B and C, offline) forward-labels the setups `analysis-engine/cmd/klstudy attribution` and `klstudy funnel` replayed, on the bars of the same
                                capture, and prints tables B1, B2 and C plus the pre-registered decision lines R1-R5.

Read-only: Postgres is only read from and the session is READ ONLY; Redis is only read from (ZRANGE). ``--selftest`` includes a guard that scans this file for
any write verb.

Run Part A inside the algo-bot container (it reads the bot's own runtime config for the Postgres and Redis URLs):

  docker compose cp algo-bot/scripts/kl_attribution.py algo-bot:/tmp/
  docker compose exec algo-bot python /tmp/kl_attribution.py trades --out /tmp/kl14 [--since 2026-10-22]

Run Parts B and C anywhere with the algo-bot dependencies (from algo-bot/):

  python scripts/kl_attribution.py label --out OUT --klstudy KLSTUDY_OUT --capture sep=xau_sep.json --capture dump=xau_dump.json ...

Version of a live trade: the opportunity envelope carries no Key Level version, so it is mapped by deploy time. A version's deploy is the first production config
fingerprint first seen at or after its merge to master (fingerprints change on every engine deploy); a trade belongs to the newest version deployed before its
opportunity was created. v8, v9 and v10 merged within 45 minutes on 9 Nov and share one deploy, so live trades carry v10.

Labelling (Parts B and C): both columns use the bars of the capture the setup was replayed on (M5: the captures of 14-21 Sep, 13 Nov and 17 Nov hold no M1).
  * live geometry: the XAU ladder of today (60/20/20, legs one price unit apart from the zone's shallow edge toward its far edge), the published stop, targets
    1R..4R of L1's risk closing 40/20/20/20, stop to L1 +/- 6 ticks after TP1 (xau_review_replay.simulate_ladder); fillable from the labeller's eligibility to
    the opportunity's expiry; 24 h after the first fill the rest is closed; spread 3 pips (no archived quotes).
  * labeller default: app.research.outcome_model (Assumptions() v1, one proximal limit, first published target), conservative bound for ambiguous bars.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import xau_review_replay as xr  # noqa: E402  (the shared ladder simulator, table helpers and read-only guard)

UTC = timezone.utc
PIP = 0.1
SPACING_PIPS = 10.0  # execution.xau_ladder.min_leg_spacing_pips

# key_level.version merges to master (UTC), from `git log --first-parent master -- config/analysis.yml`.
VERSION_MERGES: tuple[tuple[str, int], ...] = (
  ("v5", int(datetime(2026, 10, 24, 16, 5, 3, tzinfo=UTC).timestamp())),
  ("v6", int(datetime(2026, 10, 28, 7, 38, 2, tzinfo=UTC).timestamp())),
  ("v7", int(datetime(2026, 11, 5, 23, 51, 33, tzinfo=UTC).timestamp())),
  ("v8", int(datetime(2026, 11, 9, 7, 19, 30, tzinfo=UTC).timestamp())),
  ("v9", int(datetime(2026, 11, 9, 7, 38, 35, tzinfo=UTC).timestamp())),
  ("v10", int(datetime(2026, 11, 9, 8, 3, 37, tzinfo=UTC).timestamp())),
  ("v11", int(datetime(2026, 11, 18, 8, 29, 15, tzinfo=UTC).timestamp())),
  ("v12", int(datetime(2026, 11, 23, 10, 11, 46, tzinfo=UTC).timestamp())),
  ("v13", int(datetime(2026, 11, 25, 3, 40, 50, tzinfo=UTC).timestamp())),
)
VERSION_NUMBER = {name: int(name[1:]) for name, _ in VERSION_MERGES}

ROLE_RE = re.compile(r"_key_level_role_(support|resistance|ambiguous)$")
WIDENED_RE = re.compile(r"_key_level_opposing_zone_widened$")
MAJOR_CODES = {"kl_major_session": "S", "kl_major_calendar": "S", "kl_major_daily": "D", "kl_major_round": "R", "kl_major_swing": "W", "kl_major_flip": "F"}
MFE_BUCKETS = ((0.3, "<0.3"), (0.6, "0.3-0.6"), (1.0, "0.6-1"), (float("inf"), ">=1"))
OBS1_FROM = int(datetime(2026, 11, 18, tzinfo=UTC).timestamp())
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Pre-registered decision thresholds (PR-KL14 brief).
STEP_DROP_R = 0.15
MIN_SETS = 2


# =====================================================================================================================================================================
# Pure classification helpers


def deploy_times(fingerprints: list[tuple[str, int]]) -> dict[str, int | None]:
  """version -> deploy time: the first config fingerprint first seen at or after the version's merge (None when none was seen yet)."""
  firsts = sorted(ts for _, ts in fingerprints)
  out: dict[str, int | None] = {}
  for name, merged in VERSION_MERGES:
    later = [ts for ts in firsts if ts >= merged]
    out[name] = later[0] if later else None
  return out


def version_at(created_at: int, deploys: dict[str, int | None], before_first: str = "<=v4") -> str:
  """The newest version deployed at or before ``created_at`` (ties: the higher version, so v8/v9/v10 deployed together read v10)."""
  live = [(ts, VERSION_NUMBER[name], name) for name, ts in deploys.items() if ts is not None and ts <= created_at]
  if not live:
    return before_first
  return max(live)[2]


def version_group(version: str) -> str:
  if version.startswith("<="):
    return "<=v7"
  n = int(version[1:])
  return "<=v7" if n <= 7 else (">=v9" if n >= 9 else "v8")


def approach_side(direction: str, band_low: float, band_high: float, prior_close: float | None) -> str:
  """with: a BUY that came down from above the band, a SELL that came up from below; against: the opposite; inside: the prior close was in the band."""
  if prior_close is None:
    return "unknown"
  if band_low <= prior_close <= band_high:
    return "inside"
  above = prior_close > band_high
  if direction.upper() == "BUY":
    return "with" if above else "against"
  return "against" if above else "with"


def session_of(ts: int) -> str:
  """Asia from 07:00 Tokyo, London from 08:00 London, NY from 09:00 New York, late from 17:00 New York (the FX rollover) to the next Asia open."""
  # In northern winter New York's 17:00 is Tokyo's 07:00: the late window is empty and Asia wins the tie (it sorts after late at the same instant).
  opens: list[tuple[int, int, str]] = []
  base = datetime.fromtimestamp(ts, UTC)
  for days in (-2, -1, 0, 1):
    d = (base + timedelta(days=days)).date()
    for order, (name, zone, hour) in enumerate((("late", "America/New_York", 17), ("Asia", "Asia/Tokyo", 7), ("London", "Europe/London", 8), ("NY", "America/New_York", 9))):
      local = datetime(d.year, d.month, d.day, hour, tzinfo=ZoneInfo(zone))
      opens.append((int(local.timestamp()), order, name))
  opens.sort()
  current = "late"
  for at, _, name in opens:
    if at <= ts:
      current = name
  return current


def dow(ts: int, hours: int = 0) -> str:
  return DOW[datetime.fromtimestamp(ts + hours * 3600, UTC).weekday()]


def evidence_codes(payload: dict | None) -> list[str]:
  if not payload:
    return []
  return [str(e.get("code")) for e in payload.get("evidence") or [] if isinstance(e, dict)]


def role_of(codes: list[str]) -> str:
  for code in codes:
    m = ROLE_RE.search(code)
    if m:
      return m.group(1)
  return "none"


def confirmation_of(payload: dict | None, codes: list[str]) -> str:
  tc = (payload or {}).get("technical_context") or {}
  conf = tc.get("confirmation") or {}
  pattern = conf.get("pattern") or conf.get("type") or conf.get("reaction_type")
  if pattern:
    return str(pattern)
  return "touch_only" if "candle_confirmation_optional" in codes else "none"


def major_rule_of(codes: list[str]) -> str:
  rules = sorted({MAJOR_CODES[c] for c in codes if c in MAJOR_CODES})
  return "+".join(rules) if rules else "-"


def mfe_bucket(mfe: float | None) -> str:
  if mfe is None:
    return "unknown"
  for limit, name in MFE_BUCKETS:
    if mfe < limit:
      return name
  return MFE_BUCKETS[-1][1]


def stats(rows: list[dict], value_key: str = "net_r", stop_key: str = "full_stop") -> dict:
  vals = [r[value_key] for r in rows if r.get(value_key) is not None]
  n = len(vals)
  return {
    "n": n, "net": sum(vals), "exp": (sum(vals) / n) if n else None, "win": (sum(1 for v in vals if v > 0) / n) if n else None,
    "stop": (sum(1 for r in rows if r.get(value_key) is not None and r.get(stop_key)) / n) if n else None,
  }


def table(rows: list[dict], key, since: int, until: int, *, label: str, time_key: str = "closed_at") -> list[str]:
  """n, net R, expectancy, win rate, full-stop rate per key, for the window and each half (split at the time midpoint)."""
  mid = (since + until) / 2.0
  keys = sorted({key(r) for r in rows}, key=str)
  out = xr.md_table(
    [label, "n", "net R", "exp R", "win", "full stop", "H1 n", "H1 exp", "H2 n", "H2 exp"], [],
  )
  for k in keys:
    mine = [r for r in rows if key(r) == k]
    a = stats(mine)
    h1 = stats([r for r in mine if r[time_key] < mid])
    h2 = stats([r for r in mine if r[time_key] >= mid])
    out.append("| " + " | ".join(str(c) for c in (
      k, a["n"], xr._fmt(a["net"], 2), xr._fmt(a["exp"]), xr._fmt(a["win"], 2), xr._fmt(a["stop"], 2), h1["n"], xr._fmt(h1["exp"]), h2["n"], xr._fmt(h2["exp"]),
    )) + " |")
  return out


# =====================================================================================================================================================================
# Part A: production trades


COHORT_SQL = """
SELECT r.group_id, r.direction, r.net_r, r.exit_path, r.closed_at, r.mfe_r, r.mae_r, r.booked_tp_count,
       s.opportunity_id, s.opportunity_created_at, o.created_at AS opp_created_at, o.creation_envelope
FROM auto_trade_results r
JOIN auto_trade_plan_snapshots s ON s.plan_id = r.group_id
LEFT JOIN analysis_opportunities o ON o.opportunity_id = s.opportunity_id
WHERE r.trade_stream = 'algo_auto' AND r.symbol = 'XAU' AND s.strategy = 'Key Level'
  AND r.net_r IS NOT NULL AND r.closed_at >= $1 AND r.closed_at < $2
ORDER BY r.closed_at
"""

FINGERPRINT_SQL = """
SELECT creation_envelope->>'config_fingerprint' AS fp, min(created_at) AS first_seen
FROM analysis_opportunities WHERE created_at >= $1 GROUP BY 1
"""


def prior_close(bars_by_tf: dict[str, xr.Bars], touch_ts: int) -> tuple[float | None, str]:
  """Close of the last bar that closed at or before ``touch_ts`` (the touch bar's open), from the finest frame that reaches back that far."""
  for tf in ("M5", "M15", "H1"):
    bars = bars_by_tf.get(tf)
    if not bars or not bars.t:
      continue
    step = {"M5": 300, "M15": 900, "H1": 3600}[tf]
    if bars.t[0] + step > touch_ts:
      continue
    i = bars.first_at_or_after(touch_ts - step + 1) - 1
    if 0 <= i < len(bars.t) and bars.t[i] + step <= touch_ts:
      return bars.c[i], tf
  return None, "none"


def build_trade(row: dict, deploys: dict[str, int | None], bars_by_tf: dict[str, xr.Bars]) -> dict:
  env = xr._json(row.get("creation_envelope"))
  payload = (env or {}).get("payload") if isinstance(env, dict) else None
  codes = evidence_codes(payload)
  created = int(row.get("opportunity_created_at") or row.get("opp_created_at") or (payload or {}).get("created_at") or 0)
  tc = (payload or {}).get("technical_context") or {}
  conf = tc.get("confirmation") or {}
  structure = (payload or {}).get("structure") or {}
  direction = str((payload or {}).get("direction") or row.get("direction") or "").upper()
  touch = int(conf.get("touch_bar_time") or (payload or {}).get("formed_at") or created or 0)
  # The level band: the published structure (v11+), else the published entry band, which contains the level (older envelopes carry structure: null).
  band = structure if structure.get("low") is not None and structure.get("high") is not None else ((payload or {}).get("entry") or {})
  close, close_tf = (None, "none")
  if band.get("low") is not None and band.get("high") is not None and touch:
    close, close_tf = prior_close(bars_by_tf, touch)
  approach = approach_side(direction, float(band.get("low") or 0.0), float(band.get("high") or 0.0), close) if band else "unknown"
  factors = (tc.get("confluence") or {}).get("factors") or {}
  components = ((payload or {}).get("quality") or {}).get("components") or {}
  version = version_at(created, deploys) if created else "unknown"
  return {
    "group_id": row["group_id"], "closed_at": int(row["closed_at"]), "created_at": created, "version": version, "version_group": version_group(version) if created else "unknown",
    "direction": direction, "role": role_of(codes), "widened": any(WIDENED_RE.search(c) for c in codes), "swept": "key_level_reaction_swept" in codes,
    "confirmation": confirmation_of(payload, codes), "approach": approach, "approach_tf": close_tf,
    "htf_aligned": factors.get("htf_aligned") if isinstance(factors.get("htf_aligned"), bool) else None,
    "session": session_of(created) if created else "unknown", "dow_utc": dow(created) if created else "?", "dow_ict": dow(created, 7) if created else "?",
    "structure_tf": (payload or {}).get("structure_timeframe") or structure.get("timeframe") or "-",
    "kl_major_rule": major_rule_of(codes), "kl_strength_score": components.get("kl_strength_score"),
    "net_r": float(row["net_r"]), "mfe_r": None if row.get("mfe_r") is None else float(row["mfe_r"]), "mae_r": None if row.get("mae_r") is None else float(row["mae_r"]),
    "exit_path": row.get("exit_path"), "full_stop": str(row.get("exit_path") or "") == "full_stop", "envelope": payload is not None,
  }


TRADE_COLUMNS = (
  "group_id", "closed_at", "created_at", "version", "version_group", "direction", "role", "widened", "swept", "confirmation", "approach", "approach_tf",
  "htf_aligned", "session", "dow_utc", "dow_ict", "structure_tf", "kl_major_rule", "kl_strength_score", "net_r", "mfe_r", "mae_r", "exit_path",
)


def part_a_report(trades: list[dict], since: int, until: int, deploys: dict[str, int | None], coverage: list[str]) -> list[str]:
  lines = ["## Part A: closed XAU algo_auto Key Level trades", ""]
  lines += coverage + [""]
  lines += ["Deploy time per version (first config fingerprint seen at or after the merge):", ""]
  lines += xr.md_table(["version", "merged (UTC)", "deployed (UTC)"], [[n, xr.iso(m), xr.iso(deploys.get(n))] for n, m in VERSION_MERGES]) + [""]
  sections = [
    ("A1 by version", lambda r: r["version"], "version"),
    ("A2 role x version group", lambda r: f"{r['version_group']} {r['role']}", "group role"),
    ("A3 confirmation pattern x version group", lambda r: f"{r['version_group']} {r['confirmation']}", "group pattern"),
    ("A4 swept vs not", lambda r: "swept" if r["swept"] else "not swept", "sweep"),
    ("A5 by session at creation", lambda r: r["session"], "session"),
    ("A6 HTF aligned vs not", lambda r: {True: "aligned", False: "not aligned", None: "unknown"}[r["htf_aligned"]], "htf"),
    ("A7a day of week (UTC)", lambda r: r["dow_utc"], "dow UTC"),
    ("A7b day of week (UTC+7)", lambda r: r["dow_ict"], "dow UTC+7"),
    ("A-extra approach side x version group", lambda r: f"{r['version_group']} {r['approach']}", "group approach"),
    ("A-extra structure timeframe", lambda r: r["structure_tf"], "structure tf"),
  ]
  for title, key, label in sections:
    lines += [f"### {title}", ""] + table(trades, key, since, until, label=label) + [""]
  late_stops = [r for r in trades if r["full_stop"] and r["closed_at"] >= OBS1_FROM]
  lines += [f"### A8 MFE of full stops closed since 18 Nov (OBS1; {len(late_stops)} stops)", ""]
  lines += xr.md_table(["MFE bucket", "n"], [[b, sum(1 for r in late_stops if mfe_bucket(r["mfe_r"]) == b)] for _, b in MFE_BUCKETS + ((0, "unknown"),)]) + [""]
  return lines


async def run_trades(args) -> None:
  import asyncpg
  import redis.asyncio as aioredis
  from app.core.config import runtime_config

  os.makedirs(args.out, exist_ok=True)
  since = int(datetime.fromisoformat(args.since).replace(tzinfo=UTC).timestamp())
  until = int(datetime.now(UTC).timestamp()) if args.until is None else int(datetime.fromisoformat(args.until).replace(tzinfo=UTC).timestamp())
  r = aioredis.Redis.from_url(args.redis_url or runtime_config.runtime.redis.url, decode_responses=True)
  pg = await asyncpg.connect(args.pg_dsn or runtime_config.runtime.postgres.url)
  await pg.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
  rows = [dict(x) for x in await pg.fetch(COHORT_SQL, since, until)]
  fps = [(str(x["fp"]), int(x["first_seen"])) for x in await pg.fetch(FINGERPRINT_SQL, since - 14 * 86400)]
  deploys = deploy_times(fps)
  bars_by_tf = {tf: await xr.load_redis_bars(r, tf) for tf in ("M5", "M15", "H1")}
  trades = [build_trade(row, deploys, bars_by_tf) for row in rows]
  coverage = [
    f"- Window {xr.iso(since)} -> {xr.iso(until)} UTC; {len(trades)} closed trades with net R; opportunity envelope found for {sum(t['envelope'] for t in trades)}.",
    f"- Approach side read from Redis bars ({', '.join(f'{tf} {b.span()}' for tf, b in bars_by_tf.items())}); by frame: "
    + ", ".join(f"{tf} {sum(1 for t in trades if t['approach_tf'] == tf)}" for tf in ("M5", "M15", "H1", "none")) + ".",
    f"- MFE known on {sum(1 for t in trades if t['mfe_r'] is not None)} trades (OBS1 tracks from 18 Nov).",
  ]
  lines = part_a_report(trades, since, until, deploys, coverage)
  with open(os.path.join(args.out, "part_a.md"), "w") as f:
    f.write("\n".join(lines) + "\n")
  xr.write_csv(os.path.join(args.out, "part_a_trades.csv"), list(TRADE_COLUMNS), [[t[c] for c in TRADE_COLUMNS] for t in trades])
  print("\n".join(lines))
  await pg.close()
  await r.aclose()


# =====================================================================================================================================================================
# Parts B and C: forward labels of replayed setups


def load_capture_bars(path: str) -> tuple[xr.Bars, dict[str, xr.Bars]]:
  """(the labelling series: M5, every frame for the approach side)."""
  capture = json.load(open(path))
  frames = {}
  for tf, rows in capture["timeframes"].items():
    frames[tf] = xr.make_bars(tf, f"capture:{tf}", [(int(row[0]), row[1], row[2], row[3], row[4]) for row in rows])
  return frames["M5"], frames


def label_setup(creation: dict, bars: xr.Bars, frames: dict[str, xr.Bars]) -> dict:
  from app.research import outcome_model

  payload = creation["payload"]
  direction = str(payload["direction"]).upper()
  buy = direction == "BUY"
  low, high = float(payload["entry"]["low"]), float(payload["entry"]["high"])
  stop = float(payload["invalidation"]["price"])
  spec, _ = outcome_model.spec_from_rows(creation, None, pip=PIP)
  a = outcome_model.Assumptions()
  start = outcome_model.eligible_from(spec, a) if spec else int(payload["created_at"]) + 300
  expires = int(payload.get("expires_at") or start + 86400)
  proximal, far = (high, low) if buy else (low, high)
  try:
    legs = xr._ladder(direction, proximal, far, SPACING_PIPS, False)
  except Exception:  # noqa: BLE001 - a zone that holds no ladder is reported, not raised
    legs = []
  live = xr.simulate_ladder(
    bars, buy=buy, legs=legs, stop=stop, start_ts=start, fill_end_ts=expires,
    risk_unit_pips=abs(legs[0][0] - stop) / PIP if legs else 0.0,
  )
  default = None
  if spec is not None:
    lb = [outcome_model.Bar(t, o, h, l, c) for t, o, h, l, c in zip(bars.t, bars.o, bars.h, bars.l, bars.c)]
    label = outcome_model.label_opportunity(spec, lb, lambda minute: None, a, coverage_end=bars.t[-1] if bars.t else None)
    default = {"outcome": label.outcome, "r": xr.label_net_r(label.outcome, label.r_low, label.r_high, label.r_mark)}
  structure = payload.get("structure") or {}  # replay envelopes: the published structure, else the entry band below
  conf = (payload.get("technical_context") or {}).get("confirmation") or {}
  touch = int(conf.get("touch_bar_time") or payload.get("formed_at") or payload["created_at"])
  close, _ = prior_close(frames, touch)
  codes = evidence_codes(payload)
  final = live["r"] is not None and not live["missed"] and "bars_end" not in live["flags"]
  return {
    "created_at": int(payload["created_at"]), "direction": direction, "role": role_of(codes),
    "approach": approach_side(direction, float(structure.get("low") or low), float(structure.get("high") or high), close),
    "live_r": live["r"] if final else None, "live_outcome": live["outcome"], "missed": live["missed"],
    "full_stop": live["outcome"] == "stop", "default_outcome": default["outcome"] if default else None, "default_r": default["r"] if default else None,
  }


def label_file(path: str, bars: xr.Bars, frames: dict[str, xr.Bars]) -> dict[str, dict]:
  out = {}
  if not os.path.exists(path):
    return out
  for line in open(path):
    if line.strip():
      record = json.loads(line)
      out[record["key"]] = label_setup(record["creation"], bars, frames)
  return out


def summary(labels: list[dict], days: float) -> dict:
  s = stats(labels, value_key="live_r")
  d = [x["default_r"] for x in labels if x.get("default_r") is not None]
  return {
    "setups": len(labels), "per_day": len(labels) / days if days > 0 else None, "n": s["n"], "exp": s["exp"], "win": s["win"], "stop": s["stop"],
    "missed": sum(1 for x in labels if x["missed"]), "default_n": len(d), "default_exp": (sum(d) / len(d)) if d else None,
  }


def run_label(args) -> None:
  os.makedirs(args.out, exist_ok=True)
  attribution = json.load(open(os.path.join(args.klstudy, "attribution.json")))
  funnel = {r["capture"]: r for r in json.load(open(os.path.join(args.klstudy, "funnel.json")))}
  captures = dict(item.split("=", 1) for item in args.capture)
  lines = ["## Part B: component replay on identical bars", ""]
  b1 = []
  b2 = []
  step_exp: dict[str, list[tuple[str, float | None]]] = {}
  listings = []
  c_rows = []
  c_cmp: dict[str, tuple[dict, dict]] = {}
  for cap in attribution["captures"]:
    name = cap["name"]
    bars, frames = load_capture_bars(captures[name])
    days = (bars.t[-1] - bars.t[0]) / 86400.0 if bars.t else 0.0
    lines.append(f"- {name}: " + ", ".join(f"{tf} {n} bars ({xr.iso(cap['first_open'][tf])} -> {xr.iso(cap['last_open'][tf])})" for tf, n in sorted(cap["bars"].items())))
    labelled = {b: label_file(os.path.join(args.klstudy, name, f"{b}.jsonl"), bars, frames) for b in cap["builds"]}
    summaries = {b: summary(list(v.values()), days) for b, v in labelled.items()}
    for b in cap["builds"]:
      s = summaries[b]
      v = list(labelled[b].values())
      by_app = {k: stats([x for x in v if x["approach"] == k], value_key="live_r") for k in ("with", "against", "inside")}
      by_role = {k: stats([x for x in v if x["role"] == k], value_key="live_r") for k in ("support", "resistance", "ambiguous", "none")}
      b1.append([
        name, b, s["setups"], xr._fmt(s["per_day"], 1), s["n"], xr._fmt(s["exp"]), xr._fmt(s["win"], 2), xr._fmt(s["stop"], 2), s["missed"], s["default_n"], xr._fmt(s["default_exp"]),
        " ".join(f"{k}:{st['n']}/{xr._fmt(st['exp'], 2)}" for k, st in by_app.items()),
        " ".join(f"{k}:{st['n']}/{xr._fmt(st['exp'], 2)}" for k, st in by_role.items() if st["n"]),
      ])
    for step in cap["steps"]:
      f, t = step["from"], step["to"]
      sf, st = summaries[f], summaries[t]
      delta = None if sf["exp"] is None or st["exp"] is None else st["exp"] - sf["exp"]
      step_exp.setdefault(f"{f}->{t}", []).append((name, delta))
      removed = [labelled[f][k] | {"key": k} for k in step["removed"]]
      added = [labelled[t][k] | {"key": k} for k in step["added"]]
      rs, as_ = stats(removed, value_key="live_r"), stats(added, value_key="live_r")
      b2.append([name, f"{f}->{t}", sf["setups"], st["setups"], xr._fmt(sf["exp"]), xr._fmt(st["exp"]), xr._fmt(delta), len(removed), rs["n"], xr._fmt(rs["exp"]), len(added), as_["n"], xr._fmt(as_["exp"])])
      for kind, items in (("removed", removed), ("added", added)):
        for x in items:
          listings.append([name, f"{f}->{t}", kind, x["key"], x["role"], x["approach"], x["live_outcome"], xr._fmt(x["live_r"], 2)])
    if name in funnel:
      fr = funnel[name]
      fl = {k: label_file(os.path.join(args.klstudy, name, f"{k}.jsonl"), bars, frames) for k in ("v12", "v13", "v12_minor_only", "v12_major_dropped", "v12_not_in_book")}
      sm = {k: stats(list(v.values()), value_key="live_r") for k, v in fl.items()}
      c_cmp[name] = (sm["v12_minor_only"], sm["v13"])
      c_rows.append([
        name, fr["evaluations"], " ".join(f"{k}:{v:.2f}" for k, v in sorted(fr["major_levels_in_book_per_evaluation"].items())),
        " ".join(f"{k}:{v}" for k, v in sorted(fr["refused"].items())), fr["reaction_without_sweep"], fr["stop_budget_refused"], fr["published"],
        fr["v12_setups"], " ".join(f"{k}:{v}" for k, v in sorted(fr["v12_classes"].items())), " ".join(f"{k}:{v}" for k, v in sorted(fr["minor_reasons"].items())) or "-",
        f"{sm['v12_minor_only']['n']}/{xr._fmt(sm['v12_minor_only']['exp'])}", f"{sm['v12_major_dropped']['n']}/{xr._fmt(sm['v12_major_dropped']['exp'])}",
        f"{sm['v12_not_in_book']['n']}/{xr._fmt(sm['v12_not_in_book']['exp'])}", f"{sm['v13']['n']}/{xr._fmt(sm['v13']['exp'])}",
      ])
  lines += ["", "### B1 per build and bar set (live geometry; approach and role as n/expectancy)", ""]
  lines += xr.md_table(["set", "build", "setups", "per day", "labelled n", "exp R", "win", "full stop", "missed", "default n", "default exp", "by approach", "by role"], b1)
  lines += ["", "### B2 step deltas (live-geometry expectancy per labelled setup)", ""]
  lines += xr.md_table(["set", "step", "setups from", "setups to", "exp from", "exp to", "delta", "removed", "removed n", "removed exp", "added", "added n", "added exp"], b2)
  lines += ["", "<details><summary>Setups each step removes or adds</summary>", ""]
  lines += xr.md_table(["set", "step", "kind", "key", "role", "approach", "outcome", "R"], listings) + ["", "</details>", ""]
  lines += ["## Part C: why v13 is near-silent", ""]
  lines += xr.md_table([
    "set", "M5 evals", "major levels in book per eval", "refused", "reaction w/o sweep", "stop budget", "published", "v12 setups", "v12 class in v13", "minor: rule failed",
    "minor-only n/exp", "major-dropped n/exp", "not-in-book n/exp", "v13 n/exp",
  ], c_rows) + [""]
  lines += ["## Pre-registered decision", ""] + decide(step_exp, c_cmp, funnel)
  with open(os.path.join(args.out, "parts_b_c.md"), "w") as f:
    f.write("\n".join(lines) + "\n")
  print("\n".join(lines))


def rule_fires(deltas: list[tuple[str, float | None]]) -> tuple[bool, list[str]]:
  hits = [name for name, d in deltas if d is not None and d <= -STEP_DROP_R]
  return len(hits) >= MIN_SETS, hits


def decide(step_exp: dict, c_cmp: dict, funnel: dict) -> list[str]:
  r1, r1_sets = rule_fires(step_exp.get("v8->v9", []))
  r2, r2_sets = rule_fires(step_exp.get("v9->v10", []))
  fmt = lambda key: ", ".join(f"{n} {xr._fmt(d)}" for n, d in step_exp.get(key, []))
  out = [f"- v8->v9 expectancy delta per set: {fmt('v8->v9')} (R1 fires when <= -{STEP_DROP_R} on >= {MIN_SETS} sets: {'yes ' + str(r1_sets) if r1 else 'no'})",
         f"- v9->v10 expectancy delta per set: {fmt('v9->v10')} (R2: {'yes ' + str(r2_sets) if r2 else 'no'})"]
  if r1 and r2:
    out.append("- **R3: PR-KL15: role_model: approach | structural and confirmation_model: v9 | v10 on Key Level, XAU = approach and v9, v13 selection kept**")
  elif r1:
    out.append("- **R1: PR-KL15: role_model: approach | structural on Key Level, XAU = approach (pre-v9 directions), v13 selection kept**")
  elif r2:
    out.append("- **R2: PR-KL15: confirmation_model: v9 | v10, XAU = v9**")
  else:
    out.append("- **R4: no component named; the 9 Nov drop is not reproduced on bars** (Part A's strongest split is reported above)")
  gate_totals: dict[str, int] = {}
  for fr in funnel.values():
    for cls, n in fr["v12_classes"].items():
      if cls != "kept":
        gate_totals[cls] = gate_totals.get(cls, 0) + n
  biggest = max(gate_totals.items(), key=lambda kv: kv[1])[0] if gate_totals else "-"
  out.append(f"- v12 setups v13 drops, by class over all sets: {gate_totals}; the largest is **{biggest}**")
  relax_sets = [n for n, (minor, v13) in c_cmp.items() if minor["exp"] is not None and minor["exp"] >= 0 and (v13["exp"] is None or minor["exp"] >= v13["exp"])]
  reasons: dict[str, int] = {}
  for fr in funnel.values():
    for k, v in fr["minor_reasons"].items():
      reasons[k] = reasons.get(k, 0) + v
  top = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else None
  if len(relax_sets) >= MIN_SETS and top:
    out.append(f"- **R5: PR-KL16: relax {top}** (minor-only setups >= v13's and >= 0 on {relax_sets})")
  else:
    out.append(f"- **R5: v13 sparsity is selection, keep** (minor-only setups met the bar on {relax_sets or 'no set'}; needs {MIN_SETS})")
  return out


# =====================================================================================================================================================================
# Self-test


def selftest() -> None:
  ts = lambda *a: int(datetime(*a, tzinfo=UTC).timestamp())
  # deploys: the first fingerprint at or after each merge; v8-v10 share one deploy and read v10
  fps = [("a", ts(2026, 10, 28, 8, 0)), ("b", ts(2026, 11, 6, 8, 10)), ("c", ts(2026, 11, 8, 22, 10)), ("d", ts(2026, 11, 9, 11, 55)), ("e", ts(2026, 11, 19, 7, 55))]
  d = deploy_times(fps)
  assert d["v6"] == ts(2026, 10, 28, 8, 0) and d["v7"] == ts(2026, 11, 6, 8, 10), d
  assert d["v8"] == d["v9"] == d["v10"] == ts(2026, 11, 9, 11, 55), d
  assert d["v12"] is None
  assert version_at(ts(2026, 11, 9, 10, 0), d) == "v7"
  assert version_at(ts(2026, 11, 9, 12, 0), d) == "v10"
  assert version_at(ts(2026, 11, 20, 0, 0), d) == "v11"
  assert version_at(ts(2026, 10, 27), d) == "<=v4"
  assert version_group("v7") == "<=v7" and version_group("v10") == ">=v9" and version_group("v8") == "v8"
  # approach: BUY from above and SELL from below are with the approach
  assert approach_side("BUY", 100, 101, 103) == "with" and approach_side("BUY", 100, 101, 98) == "against"
  assert approach_side("SELL", 100, 101, 98) == "with" and approach_side("SELL", 100, 101, 103) == "against"
  assert approach_side("SELL", 100, 101, 100.5) == "inside" and approach_side("BUY", 100, 101, None) == "unknown"
  # sessions (25 Nov 2026: London GMT 08:00 UTC, NY EST 14:00 UTC, rollover 22:00 UTC, Asia 22:00 UTC the day before)
  assert session_of(ts(2026, 11, 25, 3, 0)) == "Asia"
  assert session_of(ts(2026, 11, 25, 8, 30)) == "London"
  assert session_of(ts(2026, 11, 25, 15, 0)) == "NY"
  assert session_of(ts(2026, 11, 25, 22, 30)) == "Asia"  # winter: 07:30 Tokyo, the late window is empty
  assert session_of(ts(2026, 7, 15, 21, 30)) == "late"  # summer: 17:30 New York, before 07:00 Tokyo (22:00 UTC)
  # in northern summer London opens at 07:00 UTC
  assert session_of(ts(2026, 7, 15, 7, 30)) == "London"
  assert dow(ts(2026, 11, 25, 20, 0)) == "Wed" and dow(ts(2026, 11, 25, 20, 0), 7) == "Thu"
  assert role_of(["structure_tf_m5", "m5_key_level_role_support"]) == "support" and role_of([]) == "none"
  assert major_rule_of(["kl_major_flip", "kl_major_session", "x"]) == "F+S" and major_rule_of([]) == "-"
  assert mfe_bucket(0.29) == "<0.3" and mfe_bucket(0.6) == "0.6-1" and mfe_bucket(1.0) == ">=1" and mfe_bucket(None) == "unknown"
  # decision rules: R1 needs a drop of 0.15 R on two sets, in the same direction
  assert rule_fires([("a", -0.2), ("b", -0.15), ("c", 0.3)]) == (True, ["a", "b"])
  assert rule_fires([("a", -0.2), ("b", -0.1), ("c", None)])[0] is False
  lines = decide({"v8->v9": [("a", -0.3), ("b", -0.2)], "v9->v10": [("a", 0.1), ("b", -0.5)]}, {}, {})
  assert any("R1: PR-KL15: role_model" in line for line in lines), lines
  lines = decide({}, {"a": ({"exp": 0.1}, {"exp": -0.2}), "b": ({"exp": 0.05}, {"exp": None})}, {"a": {"v12_classes": {"minor": 3}, "minor_reasons": {"span": 3}}})
  assert any("R5: PR-KL16: relax span" in line for line in lines), lines
  # the live-geometry label on flat bars: a SELL that fills and then runs to 4R
  bars = xr.make_bars("M5", "t", [(i * 300, p, p + 0.05, p - 0.05, p) for i, p in enumerate([99.0, 100.0, 100.0, 96.0, 90.0, 85.0, 80.0])])
  legs = [(100.0, 0.6), (101.0, 0.2), (102.0, 0.2)]
  res = xr.simulate_ladder(bars, buy=False, legs=legs, stop=105.0, start_ts=300, fill_end_ts=10**9, risk_unit_pips=50.0)
  assert res["r"] is not None and res["r"] > 0 and not res["missed"], res
  # read-only guard over this file
  found = xr.scan_source(open(os.path.abspath(__file__)).read().replace("FORBIDDEN_TOKENS", ""))
  assert not found, f"write verbs in source: {found}"
  print("selftest ok")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("mode", nargs="?", choices=("trades", "label"))
  parser.add_argument("--selftest", action="store_true")
  parser.add_argument("--out")
  parser.add_argument("--since", default="2026-10-22")
  parser.add_argument("--until", default=None)
  parser.add_argument("--pg-dsn", default=None)
  parser.add_argument("--redis-url", default=None)
  parser.add_argument("--klstudy", help="label: the directory both klstudy modes wrote")
  parser.add_argument("--capture", action="append", default=[], help="label: name=path of each capture (the same names klstudy used)")
  args = parser.parse_args()
  if args.selftest:
    selftest()
    return
  if not args.mode or not args.out:
    parser.error("a mode (trades | label) and --out are required")
  if args.mode == "trades":
    asyncio.run(run_trades(args))
  else:
    run_label(args)


if __name__ == "__main__":
  main()
