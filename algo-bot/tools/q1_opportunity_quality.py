"""Q1: are the published opportunities technically valid, and do they beat matched random entries?

Inputs (both written by ``analysis-engine/test/aequality``):
  records.jsonl  one unique opportunity per line (AEQ_DUMP=dir go test ./test/aequality -run TestReplayAllStrategies)
  random.jsonl   the matched random counterparts of each opportunity (AEQ_RANDOM_OUT=dir ... -run TestMatchedRandomBaseline)

    python tools/q1_opportunity_quality.py --records dir/records.jsonl --random dir/random.jsonl --testdata ../analysis-engine/testdata --out q1.json

Sections (all hypothetical, closed candles, constant assumed spreads; nothing here is broker P&L):
  validity     causality of the published confirmation, its age, whether the entry is still valid on the closed bar, what the target is
               made of, components that are constants
  fx_context   technical invalidation vs the executable stop vs ordinary volatility vs spread vs protected structure vs reachable reward
  versus_random  the opportunity's simulated R minus the mean R of random entries with the same risk, cost, ladder and horizon, by
               strategy and instrument, development (before 2026-10-07 UTC) and out of sample (after); cluster bootstrap by symbol-day
"""
import argparse
import bisect
import collections
import datetime as dt
import glob
import json
import os
import random

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
SPREAD = {"XAU": 0.25, "EURUSD": 0.00008, "GBPUSD": 0.0001, "USDJPY": 0.008, "GBPJPY": 0.02}
FX = ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY")
CUT = int(dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc).timestamp())
FILLED = {"target", "stop", "stop_be", "timeout"}
SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}


def load_jsonl(path):
  return [json.loads(line) for line in open(path) if line.strip()]


def thin(records):
  """One opportunity per symbol, strategy, direction and UTC hour: a thesis repeats for hours."""
  seen, out = set(), []
  for r in sorted(records, key=lambda r: (r["eval_time"], r["id"])):
    key = (r["symbol"], r["strategy"], r["direction"], r["eval_time"] // 3600)
    if key in seen:
      continue
    seen.add(key)
    out.append(r)
  return out


def bar_seconds(r):
  return 60 if r.get("observed_tf") == "M1" else 300


def validity(records):
  by = collections.defaultdict(lambda: collections.Counter())
  ages = collections.defaultdict(list)
  consts = collections.defaultdict(lambda: collections.defaultdict(set))
  labels = collections.defaultdict(collections.Counter)
  evidence = collections.defaultdict(collections.Counter)
  examples = collections.defaultdict(list)
  n = collections.Counter()
  for r in records:
    s = r["strategy"]
    n[s] += 1
    c = by[s]
    sec = bar_seconds(r)
    bar_open = r["eval_time"] - sec
    buy = r["direction"] == "BUY"
    band_lo, band_hi, inv = r["entry_low"], r["entry_high"], r["invalidation"]
    price, atr = r.get("ref_price") or 0.0, r.get("atr") or 0.0
    # causality of the published confirmation
    if r.get("reaction_confirm"):
      c["has_reaction"] += 1
      if r["reaction_touch"] > r["reaction_confirm"]:
        c["touch_after_confirmation"] += 1
      if r["reaction_confirm"] > bar_open:
        c["confirmation_after_the_closed_bar"] += 1
        if len(examples["confirmation_after_the_closed_bar"]) < 3:
          examples["confirmation_after_the_closed_bar"].append({"id": r["id"], "strategy": s, "confirm": r["reaction_confirm"], "bar_open": bar_open})
      ages[s].append((bar_open - r["reaction_confirm"]) // sec)
    if r["formed_at"] > r["created_at"] + 1:
      c["formed_after_created"] += 1
    # is the entry still valid on the closed bar that published it?
    if price > 0 and atr > 0:
      if (buy and price <= inv) or (not buy and price >= inv):
        c["price_already_beyond_invalidation"] += 1
        if len(examples["price_already_beyond_invalidation"]) < 3:
          examples["price_already_beyond_invalidation"].append({"id": r["id"], "strategy": s, "symbol": r["symbol"], "direction": r["direction"], "price": price, "invalidation": inv})
      t1 = r["targets"][0] if r["targets"] else None
      if t1 is not None and ((buy and price >= t1) or (not buy and price <= t1)):
        c["price_already_at_first_target"] += 1
      away = (price - band_hi) / atr if buy else (band_lo - price) / atr   # >0: price is on the far side of the band, waiting for a retest
      if away > 2.0:
        c["price_over_2_atr_from_the_band"] += 1
      elif away > 0:
        c["price_waiting_for_a_retest"] += 1
      elif (buy and price >= band_lo) or (not buy and price <= band_hi):
        c["price_inside_the_band"] += 1
      else:
        c["price_through_the_band"] += 1
    if not r.get("executable", True):
      c["technical_stop_beyond_the_envelope_cap"] += 1
    if r["tech_reward_pips"] <= 0 or r["tech_risk_pips"] <= 0:
      c["non_positive_reward_or_risk"] += 1
    # what the target is made of
    if r["target_labels"]:
      labels[s][r["target_labels"][0]] += 1
    # the reward the strategy itself aims at, in ATR
    if atr > 0:
      c["target_atr_sum"] += r["tech_reward_pips"] * PIP[r["symbol"]] / atr
    # components and evidence that never vary
    for k, v in (r.get("components") or {}).items():
      consts[s][k].add(round(v, 6))
    for code in r.get("evidence") or []:
      evidence[s][code] += 1
  out = {}
  for s, c in by.items():
    total = n[s]
    a = sorted(ages[s])
    out[s] = {
      "opportunities": total,
      "counts": {k: v for k, v in c.items() if k != "target_atr_sum"},
      "mean_first_target_atr": round(c["target_atr_sum"] / total, 2) if total else None,
      "confirmation_age_bars": None if not a else {"median": a[len(a) // 2], "p90": a[int(len(a) * .9)], "max": a[-1]},
      "target_labels": dict(labels[s]),
      "constant_components": {k: sorted(v)[0] for k, v in consts[s].items() if len(v) == 1},
      "varying_components": sorted(k for k, v in consts[s].items() if len(v) > 1),
      "evidence_always_present": sorted(code for code, k in evidence[s].items() if k == total),
      "evidence_conditional": {code: k for code, k in sorted(evidence[s].items()) if k != total},
    }
  return out, dict(examples)


# --- FX context -----------------------------------------------------------------------------------------------------


def load_captures(testdata):
  """Closed M5/M15/H1 candles per symbol from every committed production capture."""
  series = {}
  for path in sorted(glob.glob(os.path.join(testdata, "replay-*-production-capture-*.json"))):
    raw = json.load(open(path))
    for tf in ("M5", "M15", "H1"):
      rows = series.setdefault(raw["symbol"], {}).setdefault(tf, {})
      for t, o, h, l, c, *_ in raw["timeframes"].get(tf, []):
        rows[int(t)] = (float(o), float(h), float(l), float(c))
  return {s: {tf: sorted(rows.items()) for tf, rows in d.items()} for s, d in series.items()}


def fractal_lows_highs(rows, upto, n=2):
  """Confirmed fractal pivots of closed candles opening before ``upto`` (a pivot needs n later bars)."""
  times = [t for t, _ in rows]
  end = bisect.bisect_left(times, upto)
  seg = rows[:end]
  lows, highs = [], []
  for i in range(n, len(seg) - n):
    lo = seg[i][1][2]
    hi = seg[i][1][1]
    if all(lo < seg[i - k][1][2] and lo < seg[i + k][1][2] for k in range(1, n + 1)):
      lows.append((seg[i][0], lo))
    if all(hi > seg[i - k][1][1] and hi > seg[i + k][1][1] for k in range(1, n + 1)):
      highs.append((seg[i][0], hi))
  return lows, highs


def median(xs):
  xs = sorted(xs)
  return xs[len(xs) // 2] if xs else None


def pct(xs, q):
  xs = sorted(xs)
  return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def fx_context(records, candles, barrier_rows):
  out = {}
  rows_by_symbol = collections.defaultdict(list)
  for r in records:
    if r["symbol"] in FX and r.get("floor_pips", 0) > 0:
      rows_by_symbol[r["symbol"]].append(r)
  samples = {"suitable": [], "unsuitable": []}
  for sym, rs in rows_by_symbol.items():
    pip = PIP[sym]
    spread_pips = SPREAD[sym] / pip
    acc = collections.defaultdict(list)
    cnt = collections.Counter()
    for r in rs:
      buy = r["direction"] == "BUY"
      proximal = r["entry_high"] if buy else r["entry_low"]
      tech, execr = r["tech_risk_pips"], r["exec_risk_pips"]
      atr_pips = (r.get("atr") or 0) / pip
      if atr_pips <= 0:
        continue
      cnt["n"] += 1
      acc["tech_risk_pips"].append(tech)
      acc["exec_risk_pips"].append(execr)
      acc["floor_pips"].append(r["floor_pips"])
      acc["atr_m5_pips"].append(atr_pips)
      acc["exec_risk_over_atr_m5"].append(execr / atr_pips)
      acc["tech_risk_over_atr_m5"].append(tech / atr_pips)
      acc["spread_over_exec_risk"].append(spread_pips / execr)
      acc["spread_over_tech_risk"].append(spread_pips / tech if tech > 0 else 9.99)
      acc["room_cover"].append(r["room_cover"])
      if tech < r["floor_pips"]:
        cnt["widened_to_the_floor"] += 1
      if r["room_cover"] < 1:
        cnt["technical_target_closer_than_1R_of_the_executable_stop"] += 1
      # reachable reward: the nearer of the strategy's own target and the nearest valid opposing barrier ahead
      b = barrier_rows.get(r["id"])
      if b:
        raw = b.get("raw_room_pips")
        ahead = b.get("barrier") is not None and raw is not None and raw > 0
        reach = min(r["tech_reward_pips"], raw) if ahead else r["tech_reward_pips"]
        acc["reachable_reward_over_exec_risk"].append(reach / execr)
        cnt["with_barrier_row"] += 1
        if ahead:
          cnt["a_valid_opposing_barrier_ahead"] += 1
          if raw < r["tech_reward_pips"]:
            cnt["opposing_barrier_closer_than_the_technical_target"] += 1
        if reach < execr:
          cnt["reachable_reward_below_1R_of_the_executable_stop"] += 1
      # protected structure: nearest confirmed fractal pivot beyond the entry on M5, M15 and H1 at publication
      cs = candles.get(sym, {})
      protect = {}
      for tf in ("M5", "M15", "H1"):
        rows = cs.get(tf)
        if not rows:
          continue
        lows, highs = fractal_lows_highs(rows[-4000:] if len(rows) > 4000 else rows, r["eval_time"])
        if buy:
          below = [p for _, p in lows if p < r["entry_low"]]
          dist = (proximal - max(below)) / pip if below else None
        else:
          above = [p for _, p in highs if p > r["entry_high"]]
          dist = (min(above) - proximal) / pip if above else None
        protect[tf] = dist
      for tf, dist in protect.items():
        if dist is None:
          continue
        cnt[f"{tf}_pivot_known"] += 1
        if execr >= dist:
          cnt[f"executable_stop_beyond_nearest_{tf}_pivot"] += 1
        acc[f"nearest_{tf}_pivot_distance_pips"].append(dist)
      # descriptive classes (not rules): a stop that is inside ordinary M5 noise, inside the nearest M15 pivot, or wider than the thesis needs
      inside_noise = execr < 1.0 * atr_pips * 0.5
      cls = []
      if protect.get("M15") is not None and execr < protect["M15"]:
        cls.append("stop_inside_the_nearest_M15_pivot")
      if spread_pips / execr > 0.2:
        cls.append("spread_over_20pct_of_the_stop")
      if r["room_cover"] < 0.5:
        cls.append("technical_target_below_half_of_1R")
      if inside_noise:
        cls.append("stop_under_half_an_M5_ATR")
      if not cls and protect.get("M15") is not None and execr >= protect["M15"] and r["room_cover"] >= 1:
        samples["suitable"].append({"id": r["id"], "symbol": sym, "strategy": r["strategy"], "direction": r["direction"], "eval_time": r["eval_time"],
                                    "tech_risk_pips": round(tech, 1), "exec_risk_pips": round(execr, 1), "atr_m5_pips": round(atr_pips, 1),
                                    "nearest_M15_pivot_pips": round(protect["M15"], 1), "tech_reward_pips": round(r["tech_reward_pips"], 1), "room_cover": round(r["room_cover"], 2)})
      elif cls:
        samples["unsuitable"].append({"id": r["id"], "symbol": sym, "strategy": r["strategy"], "direction": r["direction"], "eval_time": r["eval_time"], "why": cls,
                                      "tech_risk_pips": round(tech, 1), "exec_risk_pips": round(execr, 1), "atr_m5_pips": round(atr_pips, 1),
                                      "nearest_M15_pivot_pips": None if protect.get("M15") is None else round(protect["M15"], 1),
                                      "tech_reward_pips": round(r["tech_reward_pips"], 1), "room_cover": round(r["room_cover"], 2)})
        for c in cls:
          cnt["class:" + c] += 1
    n = cnt["n"]
    out[sym] = {
      "n": n,
      "median": {k: round(median(v), 2) for k, v in acc.items() if v},
      "p10_p90": {k: [round(pct(v, .1), 2), round(pct(v, .9), 2)] for k, v in acc.items() if v},
      "counts": dict(cnt),
      "shares": {k: round(v / n, 3) for k, v in cnt.items() if k != "n" and n},
    }
  rnd = random.Random(11)
  for k in samples:
    rnd.shuffle(samples[k])
    samples[k] = samples[k][:8]
  return out, samples


# --- versus matched random -----------------------------------------------------------------------------------------


def boot(values_by_cluster, rounds=400, seed=5):
  clusters = list(values_by_cluster.values())
  if len(clusters) < 3:
    return None
  rnd = random.Random(seed)
  stats = []
  for _ in range(rounds):
    pick = [rnd.choice(clusters) for _ in clusters]
    tot = sum(sum(c) for c in pick)
    cnt = sum(len(c) for c in pick)
    stats.append(tot / cnt if cnt else 0.0)
  stats.sort()
  return [round(stats[int(rounds * .025)], 3), round(stats[int(rounds * .975)], 3)]


def versus_random(records, randoms):
  rows = []
  for r in thin(records):
    rd = randoms.get(r["id"])
    sim = (r.get("sim") or {}).get("ladder_cost")
    if not rd or not sim or sim["result"] not in FILLED:
      continue
    rows.append({"strategy": r["strategy"], "symbol": r["symbol"], "t": r["eval_time"], "r": sim["r"], "rand": rd["mean_r"], "diff": sim["r"] - rd["mean_r"],
                 "cluster": (r["symbol"], r["eval_time"] // 86400)})

  def summarise(sub):
    n = len(sub)
    if n == 0:
      return {"n": 0}
    clusters = collections.defaultdict(list)
    for x in sub:
      clusters[x["cluster"]].append(x["diff"])
    return {"n": n, "mean_r": round(sum(x["r"] for x in sub) / n, 3), "mean_random_r": round(sum(x["rand"] for x in sub) / n, 3),
            "mean_difference_r": round(sum(x["diff"] for x in sub) / n, 3), "ci95": boot(clusters), "clusters": len(clusters)}

  out = {"pooled": {}, "by_strategy": {}, "by_symbol": {}, "by_strategy_symbol": {}}
  for window, pred in (("all", lambda x: True), ("development", lambda x: x["t"] < CUT), ("out_of_sample", lambda x: x["t"] >= CUT)):
    sel = [x for x in rows if pred(x)]
    out["pooled"][window] = summarise(sel)
    for key, label in (("strategy", "by_strategy"), ("symbol", "by_symbol")):
      groups = collections.defaultdict(list)
      for x in sel:
        groups[x[key]].append(x)
      for k, g in groups.items():
        out[label].setdefault(k, {})[window] = summarise(g)
    groups = collections.defaultdict(list)
    for x in sel:
      groups[f"{x['strategy']}|{x['symbol']}"].append(x)
    for k, g in groups.items():
      out["by_strategy_symbol"].setdefault(k, {})[window] = summarise(g)
  return out


def fx_room_split(records, randoms, barrier_rows):
  """Does the reachable reward (the nearer of the strategy's own target and the nearest valid opposing barrier) relative to the executable
  stop separate outcomes? Buckets fixed in advance: 1R is the first rung of the executed ladder, half of it the obvious lower cut."""
  rows = []
  for r in thin([x for x in records if x["symbol"] in FX]):
    sim = (r.get("sim") or {}).get("ladder_cost")
    rd = randoms.get(r["id"])
    b = barrier_rows.get(r["id"])
    if not sim or sim["result"] not in FILLED or not rd or not b or r["exec_risk_pips"] <= 0:
      continue
    raw = b.get("raw_room_pips")
    ahead = b.get("barrier") is not None and raw is not None and raw > 0
    reach = min(r["tech_reward_pips"], raw) if ahead else r["tech_reward_pips"]
    ratio = reach / r["exec_risk_pips"]
    bucket = "reachable < 0.5R" if ratio < 0.5 else "reachable 0.5R - 1R" if ratio < 1.0 else "reachable >= 1R"
    rows.append({"bucket": bucket, "t": r["eval_time"], "r": sim["r"], "rand": rd["mean_r"], "diff": sim["r"] - rd["mean_r"], "win1": 1 if sim["r"] >= 1 or sim["result"] == "target" else 0,
                 "cluster": (r["symbol"], r["eval_time"] // 86400)})
  out = {}
  for window, pred in (("development", lambda x: x["t"] < CUT), ("out_of_sample", lambda x: x["t"] >= CUT)):
    for bucket in ("reachable < 0.5R", "reachable 0.5R - 1R", "reachable >= 1R"):
      sub = [x for x in rows if pred(x) and x["bucket"] == bucket]
      if not sub:
        continue
      clusters = collections.defaultdict(list)
      for x in sub:
        clusters[x["cluster"]].append(x["diff"])
      n = len(sub)
      out.setdefault(bucket, {})[window] = {"n": n, "mean_r": round(sum(x["r"] for x in sub) / n, 3), "mean_random_r": round(sum(x["rand"] for x in sub) / n, 3),
                                           "mean_difference_r": round(sum(x["diff"] for x in sub) / n, 3), "ci95": boot(clusters)}
  return out


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--records", required=True)
  ap.add_argument("--random", required=True)
  ap.add_argument("--testdata", required=True)
  ap.add_argument("--barriers", default="", help="decide_dump.py output (rule x books) to join the nearest valid opposing barrier room")
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  records = load_jsonl(a.records)
  randoms = {r["id"]: r for r in load_jsonl(a.random)}
  barrier_rows = {}
  if a.barriers:
    for r in load_jsonl(a.barriers):
      barrier_rows.setdefault(r["id"], r)
  val, examples = validity(records)
  candles = load_captures(a.testdata)
  fx, samples = fx_context(thin([r for r in records if r["symbol"] in FX]), candles, barrier_rows)
  result = {"records": len(records), "thinned": len(thin(records)), "validity": val, "validity_examples": examples, "fx_context": fx, "fx_examples": samples,
            "versus_random": versus_random(records, randoms), "fx_reachable_room_split": fx_room_split(records, randoms, barrier_rows)}
  json.dump(result, open(a.out, "w"), indent=1)
  print(json.dumps({"records": result["records"], "thinned": result["thinned"], "pooled_vs_random": result["versus_random"]["pooled"]}, indent=1))


if __name__ == "__main__":
  main()
