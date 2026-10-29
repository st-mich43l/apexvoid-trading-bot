"""Q5.5: does the historical replay measure what the production bot really trades?

Offline and read-only. It joins closed production groups to replayed opportunities that are *exactly* the same technical opportunity (same
symbol, strategy, direction and creation time, and the same entry band, invalidation and first target to half a pip), then compares the two on
the same footing:

  * the entry: where the broker filled against the replay's assumption (a limit at the zone's proximal edge);
  * the stop: the executed stop distance against the replay's invalidation widened to the floor;
  * the result: measured group R against the replay's hypothetical R;
  * a re-simulation of the same bars with the production fill and stop substituted one at a time (``simulate`` is a port of
    ``analysis-engine/test/aequality`` ``simulate`` and reproduces it exactly, see the tests), to separate the entry from the exits.

Measured results only (``fills_v1``, ``fill_record_risk``), and only groups closed once full stops were booked with their realized pips
(``FIRST_RELIABLE_CLOSE``): earlier, a losing group was stored as an assumed -1 and excluded, which biases a measured-only mean upward.
Reconstructed and assumed outcomes are reported separately and never merged. Fills and prices are never taken from candles.

    python q55_replay_parity.py --groups groups.jsonl --records records.jsonl --captures testdata --decisions d.jsonl --random r.jsonl --out q55.json
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from app.autotrade import result_accounting as accounting  # noqa: E402
import q1_opportunity_quality as q1  # noqa: E402
import q54_executable_room as q54  # noqa: E402

PIP = q1.PIP
PURGED = q54.PURGED
MEASURED = (accounting.FILLS_V1, accounting.FILL_RECORD_RISK)
# Assumed constant spreads of the replay (price units): the captured bars are bid-only.
SPREAD = {"XAU": 0.25, "EURUSD": 0.00008, "GBPUSD": 0.0001, "USDJPY": 0.008, "GBPJPY": 0.02}
FILLED = ("stop", "stop_be", "target", "timeout")
# Fix 9a4b9749 (2026-10-14) made a full stop carry its realized pips: groups closed from this day on are measured whatever their outcome.
FIRST_RELIABLE_CLOSE = "2026-10-15"
REPS = 2000
SEED = 55


# ---- the replay's simulator (a port; tests pin it to the Go results) ---------------------------------------------------------

def load_bars(paths):
  """Merge the M5 bars of every capture of a symbol (the captures overlap): {symbol: (bars, {time: index})}."""
  merged = collections.defaultdict(dict)
  for path in paths:
    capture = json.load(open(path))
    for row in capture["timeframes"].get("M5", []):
      merged[capture["symbol"]][row[0]] = row
  out = {}
  for symbol, rows in merged.items():
    times = sorted(rows)
    out[symbol] = ([rows[t] for t in times], {t: i for i, t in enumerate(times)})
  return out


def ladder_for(symbol, strategy):
  if symbol == "XAU":
    return [1, 2, 3, 4], [.4, .2, .2, .2]
  return [1, 2], [.5, .5]


def simulate(rec, bars, index, cost, *, fill_idx=None, fill_price=None, risk=None, target_first=False):
  """Returns (result, R). With no override this is the replay: a limit at the proximal edge filled on a later bar, the stop at Go's invalidation
  widened to the floor. ``fill_idx``/``fill_price``/``risk`` substitute the production entry and stop (risk in price units)."""
  sell = rec["direction"] == "SELL"
  pip = PIP[rec["symbol"]]
  mult, ratio = ladder_for(rec["symbol"], rec["strategy"])
  if fill_idx is None:
    e = index.get(rec["eval_time"] - 300)
    if e is None:
      return "censored", 0.0
    px = rec["entry_low"] if sell else rec["entry_high"]
    limit = rec["eval_time"] + 12 * 3600
    if rec.get("expires_at") and rec["expires_at"] < limit:
      limit = rec["expires_at"]
    fill_idx = -1
    j = e + 1
    while j < len(bars) and bars[j][0] < limit:
      b = bars[j]
      if not sell and b[3] + cost <= px:
        fill_idx, fill_price = j, min(b[1] + cost, px)
        break
      if sell and b[2] >= px:
        fill_idx, fill_price = j, max(b[1], px)
        break
      j += 1
    if fill_idx < 0:
      return "unfilled", 0.0
  fill = fill_price
  if risk is None:
    risk = (rec["invalidation"] - fill) if sell else (fill - rec["invalidation"])
    if not risk > 0:
      return "unfilled", 0.0
    risk = max(risk, rec["floor_pips"] * pip)
    if rec["cap_pips"] and risk > rec["cap_pips"] * pip + 1e-12:
      return "not_executable", 0.0
  direction = -1.0 if sell else 1.0
  stop = fill + risk if sell else fill - risk

  def stop_hit(b, level):
    return (b[2] + cost >= level) if sell else (b[3] <= level)

  def reached(b, price):
    return (b[3] + cost <= price) if sell else (b[2] >= price)

  if stop_hit(bars[fill_idx], stop):
    return "stop", -1.0
  end = min(fill_idx + 24 * 3600 // 300, len(bars) - 1)
  remaining, total, current, nxt, breakeven = 1.0, 0.0, stop, 0, False
  for k in range(fill_idx + 1, end + 1):
    b = bars[k]
    if target_first:
      while nxt < len(mult) and reached(b, fill + direction * mult[nxt] * risk):
        total += ratio[nxt] * mult[nxt]
        remaining -= ratio[nxt]
        if nxt == 0 and len(mult) > 1:
          current, breakeven = fill, True
        nxt += 1
      if nxt == len(mult):
        return "target", total
    if stop_hit(b, current):
      total += remaining * (direction * (current - fill) / risk)
      return ("stop_be" if breakeven else "stop"), total
    while nxt < len(mult) and reached(b, fill + direction * mult[nxt] * risk):
      total += ratio[nxt] * mult[nxt]
      remaining -= ratio[nxt]
      if nxt == 0 and len(mult) > 1:
        current, breakeven = fill, True
      nxt += 1
    if nxt == len(mult):
      return "target", total
  if end == len(bars) - 1 and bars[end][0] < bars[fill_idx][0] + 24 * 3600 - 300:
    return "censored", 0.0
  return "timeout", total + remaining * direction * (bars[end][4] - fill) / risk


# ---- matching ------------------------------------------------------------------------------------------------------------------

def exact_match(envelope, records_by_key, pip):
  """The replay record that is the same technical opportunity as the production envelope, or None and why."""
  key = (envelope["symbol"], envelope["strategy"], envelope["direction"], envelope["created_at"])
  candidates = records_by_key.get(key, [])
  if not candidates:
    return None, "no_replay_opportunity_with_the_same_key"
  target = ((envelope.get("targets") or [{}])[0].get("price") or {}).get("price")
  half = pip * .5
  for r in candidates:
    if (abs(r["entry_low"] - envelope["entry"]["low"]) < half and abs(r["entry_high"] - envelope["entry"]["high"]) < half
        and abs(r["invalidation"] - envelope["invalidation"]["price"]) < half and target is not None and r["targets"]
        and abs(r["targets"][0] - target) < half):
      return r, None
  return None, "replay_opportunity_differs_in_geometry"


def build_pairs(groups, records):
  by_key = collections.defaultdict(list)
  for r in records:
    by_key[(r["symbol"], r["strategy"], r["direction"], r["created_at"])].append(r)
  pairs, unmatched = [], collections.Counter()
  for g in groups:
    envelope = ((g.get("env") or {}).get("payload")) or {}
    fills = g.get("fills") or []
    volume = sum(f["vol"] for f in fills)
    if not envelope or volume <= 0:
      unmatched["no_envelope_or_fill"] += 1
      continue
    if envelope["strategy"] in PURGED:
      unmatched["purged_strategy"] += 1
      continue
    symbol = g["symbol"]
    record, why = exact_match(envelope, by_key, PIP[symbol])
    if record is None:
      unmatched[why] += 1
      continue
    estimate, method = accounting.estimate({
      "exit_path": g["exit_path"], "realized_pips": g["realized_pips"], "risk_pips": g["risk_pips"], "symbol": symbol,
      "booked_tp_count": g["booked_tp_count"], "result_pips": g["result_pips"], "stop_pips": g["stop_pips"], "closed_at": g["closed_at"],
      "net_r": g.get("net_r"), "result_method": g.get("result_method"), "weighted_risk_pips": g.get("weighted_risk_pips")})
    buy = g["direction"] == "BUY"
    proximal = envelope["entry"]["high"] if buy else envelope["entry"]["low"]
    fill = sum(f["entry"] * f["vol"] for f in fills) / volume
    pip = PIP[symbol]
    pairs.append({
      "group_id": g["group_id"], "symbol": symbol, "group": "XAU" if symbol == "XAU" else "FX", "strategy": envelope["strategy"],
      "closed_at": g["closed_at"], "first_fill_at": min(f["at"] for f in fills), "fill": fill, "proximal": proximal,
      "deeper_pips": ((proximal - fill) if buy else (fill - proximal)) / pip, "band_pips": (envelope["entry"]["high"] - envelope["entry"]["low"]) / pip,
      "production_stop_pips": g["stop_pips"], "replay_stop_pips": record["exec_risk_pips"], "technical_risk_pips": record["tech_risk_pips"],
      "floor_pips": record["floor_pips"], "production_r": estimate, "method": method, "exit_path": g["exit_path"],
      "replay_result": ((record.get("sim") or {}).get("ladder_cost") or {}).get("result"),
      "replay_r": ((record.get("sim") or {}).get("ladder_cost") or {}).get("r"), "same_id": envelope["id"] == record["id"], "record": record,
    })
  return pairs, unmatched


# ---- statistics ----------------------------------------------------------------------------------------------------------------

def cluster_mean_ci(pairs, key):
  clusters = collections.defaultdict(list)
  for p in pairs:
    clusters[(p["symbol"], dt.datetime.fromtimestamp(p["closed_at"], dt.timezone.utc).date().isoformat())].append(key(p))
  keys = list(clusters)
  if len(keys) < 3:
    return None
  rnd = random.Random(SEED)
  draws = []
  for _ in range(REPS):
    pool = [v for k in (rnd.choice(keys) for _ in keys) for v in clusters[k]]
    draws.append(sum(pool) / len(pool))
  draws.sort()
  return [round(draws[int(REPS * .025)], 3), round(draws[int(REPS * .975)], 3)]


def profit_factor(values):
  gains, losses = sum(v for v in values if v > 0), -sum(v for v in values if v < 0)
  return round(gains / losses, 2) if losses else None


def quantiles(values):
  xs = sorted(values)
  n = len(xs)
  return {"n": n, "mean": round(statistics.mean(xs), 2), "p10": round(xs[int(.1 * n)], 2), "p50": round(xs[n // 2], 2), "p90": round(xs[min(n - 1, int(.9 * n))], 2)}


def paired_block(pairs, minimum=8):
  """Measured R against replayed R for the pairs where the replay filled."""
  both = [p for p in pairs if p["replay_result"] in FILLED and p["production_r"] is not None]
  out = {"matched": len(pairs), "paired": len(both)}
  if len(both) < minimum:
    return out
  diff = [p["production_r"] - p["replay_r"] for p in both]
  out.update({
    "production_mean_r": round(statistics.mean(p["production_r"] for p in both), 3), "production_pf": profit_factor([p["production_r"] for p in both]),
    "replay_mean_r": round(statistics.mean(p["replay_r"] for p in both), 3), "replay_pf": profit_factor([p["replay_r"] for p in both]),
    "difference_mean": round(statistics.mean(diff), 3), "difference_ci": cluster_mean_ci(both, lambda p: p["production_r"] - p["replay_r"]),
    "difference_sd": round(statistics.pstdev(diff), 2), "within_quarter_r": round(sum(abs(d) < .25 for d in diff) / len(diff), 2),
    "win_loss_agreement": round(sum((p["production_r"] > 0) == (p["replay_r"] > 0) for p in both) / len(both), 2),
    "exit_paths": dict(collections.Counter(f"{p['exit_path']}/{p['replay_result']}" for p in both).most_common(8)),
  })
  return out


def entry_and_stop(pairs):
  out = {"matched": len(pairs)}
  if pairs:
    out["fill_deeper_than_proximal_pips"] = quantiles([p["deeper_pips"] for p in pairs])
    out["share_filled_worse_than_proximal"] = round(sum(p["deeper_pips"] < -0.5 for p in pairs) / len(pairs), 3)
    out["fill_depth_percent_of_band"] = quantiles([100 * p["deeper_pips"] / p["band_pips"] for p in pairs if p["band_pips"] > 0])
    out["production_over_replay_stop"] = quantiles([p["production_stop_pips"] / p["replay_stop_pips"] for p in pairs])
    out["floor_binds_in_replay"] = round(sum(p["technical_risk_pips"] < p["floor_pips"] for p in pairs) / len(pairs), 3)
  return out


def resimulate(pairs, bars):
  """Same bars, production entry and stop substituted one at a time. Pairs the bars do not cover are skipped, not guessed."""
  rows = []
  for p in pairs:
    if p["symbol"] not in bars or p["method"] not in MEASURED:
      continue
    series, index = bars[p["symbol"]]
    first = index.get(p["first_fill_at"] - p["first_fill_at"] % 300)
    if first is None:
      continue
    record, pip, cost = p["record"], PIP[p["symbol"]], SPREAD[p["symbol"]]
    results = {
      "replay": simulate(record, series, index, cost),
      "production_fill_replay_stop": simulate(record, series, index, cost, fill_idx=first, fill_price=p["fill"]),
      "production_fill_and_stop": simulate(record, series, index, cost, fill_idx=first, fill_price=p["fill"], risk=p["production_stop_pips"] * pip),
      "production_fill_and_stop_target_first": simulate(record, series, index, cost, fill_idx=first, fill_price=p["fill"],
                                                         risk=p["production_stop_pips"] * pip, target_first=True),
    }
    if all(r[0] in FILLED for r in results.values()):
      rows.append({"production": p["production_r"], **{k: v[1] for k, v in results.items()}})
  out = {"n": len(rows)}
  if rows:
    for name in rows[0]:
      out[name] = round(statistics.mean(r[name] for r in rows), 3)
    d = [r["production_fill_and_stop"] - r["production"] for r in rows]
    out["resimulation_minus_production"] = round(statistics.mean(d), 3)
    out["resimulation_minus_production_se"] = round(statistics.pstdev(d) / len(d) ** .5, 3)
  return out


def parity_report(pairs, unmatched, bars=None):
  cutoff = dt.datetime.strptime(FIRST_RELIABLE_CLOSE, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
  primary = [p for p in pairs if p["closed_at"] >= cutoff and p["method"] in MEASURED]
  report = {"matched_exact": len(pairs), "same_opportunity_id": sum(p["same_id"] for p in pairs), "unmatched": dict(unmatched),
            "methods": dict(collections.Counter(p["method"] for p in pairs)), "first_reliable_close": FIRST_RELIABLE_CLOSE,
            "primary": {"definition": "measured methods, closed on or after first_reliable_close", "n": len(primary)}, "by_group": {}}
  for group in ("FX", "XAU"):
    s = [p for p in primary if p["group"] == group]
    block = {"entry_and_stop_all_matched": entry_and_stop([p for p in pairs if p["group"] == group]), "paired_primary": paired_block(s),
             "by_instrument": {sym: paired_block([p for p in s if p["symbol"] == sym]) for sym in sorted({p["symbol"] for p in s})},
             "by_strategy": {st: paired_block([p for p in s if p["strategy"] == st]) for st in sorted({p["strategy"] for p in s})}}
    if group == "FX":
      block["by_strategy"]["supply+demand"] = paired_block([p for p in s if p["strategy"] in ("supply", "demand")])
    if bars:
      block["resimulation"] = resimulate(s, bars)
    # other methods, never merged with the primary figures
    block["other_methods"] = {
      "all_dates_measured_only_BIASED_UPWARD": paired_block([p for p in pairs if p["group"] == group and p["method"] in MEASURED]),
      "legacy_loss_unbooked_ASSUMED_MINUS_ONE": paired_block([p for p in pairs if p["group"] == group and p["method"] == accounting.LEGACY_LOSS_UNBOOKED]),
      "reconstructed_ladder": paired_block([p for p in pairs if p["group"] == group and p["method"] == accounting.RECONSTRUCTED_LADDER_V1])}
    report["by_group"][group] = block
  return report


# ---- why the FX replay is negative ---------------------------------------------------------------------------------------------

def session(t):
  hour = dt.datetime.fromtimestamp(t, dt.timezone.utc).hour
  return "asia 00-07" if hour < 7 else "london 07-12" if hour < 12 else "ny 12-17" if hour < 17 else "late 17-24"


def decomposition(observations):
  """Where the mean hypothetical R of the admitted replay observations comes from. Each stratum is read against the matched random entry of the
  same technical risk, so the stop, the ladder and the cost model are common to both. Exploratory: nothing here selects a rule."""
  def block(items):
    if not items:
      return {"n": 0}
    return {"n": len(items), "mean_r": round(statistics.mean(x["r"] for x in items), 3), "random_r": round(statistics.mean(x["rand"] for x in items), 3),
            "above_random": round(statistics.mean(x["diff"] for x in items), 3), "above_random_ci": q54.cluster_bootstrap(_by_cluster(items, "diff"))}

  def _by_cluster(items, key):
    out = collections.defaultdict(list)
    for x in items:
      out[x["cluster"]].append(x[key])
    return out

  out = {}
  for group in ("FX", "XAU"):
    items = [o for o in observations if o["group"] == group]
    no_cost = [o for o in items if o.get("r_nocost") is not None]
    out[group] = {
      "all": block(items),
      "cost_model_effect": {"n": len(no_cost), "mean": round(statistics.mean(o["r"] - o["r_nocost"] for o in no_cost), 3) if no_cost else None,
                            "ci": q54.cluster_bootstrap(_by_cluster([{**o, "d": o["r"] - o["r_nocost"]} for o in no_cost], "d")) if no_cost else None},
      "by_instrument": {s: block([o for o in items if o["symbol"] == s]) for s in sorted({o["symbol"] for o in items})},
      "by_strategy": {s: block([o for o in items if o["strategy"] == s]) for s in sorted({o["strategy"] for o in items})},
      "by_session_utc": {s: block([o for o in items if session(o["t"]) == s]) for s in ("asia 00-07", "london 07-12", "ny 12-17", "late 17-24")},
      "by_htf_alignment": {name: block([o for o in items if o["facts"].get("htf_aligned") == value]) for name, value in (("aligned", 1.0), ("not_aligned", 0.0))},
      "by_stop_widening": {name: block([o for o in items if q54.bucket(o["widening"], q54.WIDENING_BUCKETS) == name]) for _, _, name in q54.WIDENING_BUCKETS},
    }
  return out


def funnel(lifecycle_rows):
  """Why a published Go opportunity did not become a plan, from the lifecycle records (Redis, about 6 days: a snapshot of the retained window)."""
  by_id = collections.defaultdict(set)
  for row in lifecycle_rows:
    if row.get("state") in ("rejected", "invalidated", "expired", "candidate_published"):
      by_id[row.get("correlation_id")].add((row["state"], row.get("reason_code")))
  final = collections.Counter()
  for states in by_id.values():
    if any(s == "candidate_published" for s, _ in states):
      final["published"] += 1
      continue
    reasons = sorted({c for s, c in states if s == "rejected"}) or sorted({c for _, c in states})
    final[reasons[0] if reasons else "none"] += 1
  return {"opportunities": len(by_id), "final_outcome": [[reason, count] for reason, count in final.most_common()]}


def jsonl(path):
  return [json.loads(line) for line in open(path) if line.startswith("{")]


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--groups", required=True)
  ap.add_argument("--records", required=True)
  ap.add_argument("--decisions", required=True)
  ap.add_argument("--random", required=True)
  ap.add_argument("--captures", required=True, help="directory with replay-*production-capture-*.json")
  ap.add_argument("--lifecycle", help="lifecycle records harvested from Redis, one JSON object per line")
  ap.add_argument("--out", required=True)
  args = ap.parse_args()
  records = jsonl(args.records)
  pairs, unmatched = build_pairs(jsonl(args.groups), records)
  bars = load_bars(sorted(glob.glob(os.path.join(args.captures, "replay-*production-capture-*.json"))))
  randoms = {r["id"]: r for r in jsonl(args.random)}
  _, observations = q54.replay_report(records, jsonl(args.decisions), randoms)
  out = {"parity": parity_report(pairs, unmatched, bars), "fx_and_xau_replay_decomposition": decomposition(observations)}
  if args.lifecycle:
    out["production_funnel"] = funnel(jsonl(args.lifecycle))
  json.dump(out, open(args.out, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
  main()
