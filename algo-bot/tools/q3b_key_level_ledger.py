"""Q3b: matched Key Level decision ledger, Python (profitable-week reference a1c77584) against the current Go Analysis Engine, on identical candles.

Nothing here simulates a market: it joins three things the repository already produces.

  * the Python decisions: ``testdata/key-level-oracle.json`` (and the same generator run over the later captures), one entry per closed M5 bar at which
    ``key_level_reaction`` of commit a1c77584 fired;
  * the Go engine under the reference behaviour (``AEQ_PARAMS='{"key_level":{"frozen_session_context":true}}'``), which the parity test proves
    reproduces every Python decision bar by bar, replayed by ``test/aequality`` (records + matched random controls);
  * the Go engine as it runs now (same harness, no override).

Every Python thesis is matched to a Go record by market context (symbol, closed bar, direction), never by generated opportunity id. The earliest stage at which the
two engines diverge is attributed: detection / confirmation / geometry (the decisions are bit-identical there, so they are checked, not assumed) and acceptance
(the confluence floor, which is the only place the current engine reads a different number). The outcome of each thesis is the harness's candle-causal ladder
simulation with costs, identical for both engines; it is hypothetical. ``python q3b_key_level_ledger.py --help``.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402
import q3_quality_validation as q3  # noqa: E402

M5 = 300
# Harness capture name for each golden capture key, and the order in which the harness lets a capture own an opportunity.
HARNESS = {
  "XAU": "XAU Sep", "XAU_LATER": "XAU M1 Oct 2-6", "XAU_1013": "XAU Oct M5", "XAU_1014": "XAU Oct 14 (M1)",
  "EURUSD": "EURUSD early", "EURUSD_1013": "EURUSD", "EURUSD_1014": "EURUSD Oct 14",
  "GBPUSD": "GBPUSD early", "GBPUSD_1013": "GBPUSD", "GBPUSD_1014": "GBPUSD Oct 14",
  "USDJPY": "USDJPY early", "USDJPY_1013": "USDJPY", "USDJPY_1014": "USDJPY Oct 14",
  "GBPJPY": "GBPJPY early", "GBPJPY_1013": "GBPJPY", "GBPJPY_1014": "GBPJPY Oct 14",
}
ORDER = list(HARNESS)
SEPTEMBER_END = int(__import__("datetime").datetime(2026, 9, 28, tzinfo=__import__("datetime").timezone.utc).timestamp())
TOL = {"XAU": 0.011, "EURUSD": 1.1e-5, "GBPUSD": 1.1e-5, "USDJPY": 0.0011, "GBPJPY": 0.0011}


def period_of(t):
  return "september" if t < SEPTEMBER_END else "october"


def load_golden(paths):
  """{capture key: {symbol, bars, decisions}} from one or more oracle files (the first file to define a key wins)."""
  out = {}
  for path in paths:
    for key, entry in json.load(open(path))["symbols"].items():
      out.setdefault(key, {"capture": entry["capture"], "bars": entry["bars"], "decisions": entry["decisions"], "file": os.path.basename(path)})
  return out


def python_theses(golden):
  """One row per (symbol, closed bar) at which the Python fired, from the first capture in harness order that evaluated the bar.
  Bars evaluated by several captures are cross-checked: a Python decision that depends on the capture window is a finding."""
  rows, seen, disagreements, owner = {}, {}, [], {}
  for key in sorted(golden, key=lambda k: ORDER.index(k) if k in ORDER else 99):
    entry = golden[key]
    symbol = "XAU" if key.startswith("XAU") else key.split("_")[0]
    for bar in entry["bars"]:
      decision = entry["decisions"].get(str(bar))
      ident = (symbol, bar)
      if ident in seen:
        prior = seen[ident]
        same = (prior is None and decision is None) or (prior is not None and decision is not None and prior["direction"] == decision["direction"]
                                                       and abs(prior["entry_low"] - decision["entry_low"]) < TOL[symbol] and abs(prior["entry_high"] - decision["entry_high"]) < TOL[symbol])
        if not same:
          disagreements.append({"symbol": symbol, "bar": bar, "first_capture": rows.get(ident, {}).get("capture"), "later_capture": key})
        continue
      seen[ident] = decision
      owner[ident] = key
      if decision is not None:
        rows[ident] = {"symbol": symbol, "bar": bar, "capture_key": key, "capture": HARNESS.get(key), "python": decision}
  evaluated = dict(owner)
  return rows, evaluated, disagreements


def index_records(records):
  """(symbol, eval_time, direction) -> record, for the Key Level records of one replay."""
  out = {}
  for r in records:
    if r["strategy"] == "key_level":
      out.setdefault((r["symbol"], r["eval_time"], r["direction"]), r)
  return out


def close(a, b, symbol):
  return abs(a - b) < TOL[symbol]


def band_matches(record, python_row):
  p = python_row["python"]
  return close(record["entry_low"], p["entry_low"], python_row["symbol"]) and close(record["entry_high"], p["entry_high"], python_row["symbol"])


def band_drifts_with_atr(record, python_row, fraction=0.5):
  """A published identity keeps the band of the bar it was first seen on while the detector's band follows the ATR bar by bar; a later Python decision
  belongs to that standing thesis when both band edges are within half an ATR of the record's."""
  p, atr = python_row["python"], record.get("atr") or 0.0
  return abs(record["entry_low"] - p["entry_low"]) <= fraction * atr and abs(record["entry_high"] - p["entry_high"]) <= fraction * atr


def outcome(record, randoms):
  """Candle-causal ladder with costs, the matched random control and the excursions; None when unfilled / not executable / censored."""
  sim = ((record or {}).get("sim") or {}).get("ladder_cost")
  rd = randoms.get((record or {}).get("id"))
  if not sim:
    return None
  out = {"result": sim["result"], "r": sim.get("r"), "mfe": sim.get("mfe"), "mae": sim.get("mae"), "random_r": rd["mean_r"] if rd else None}
  out["difference_r"] = (sim["r"] - rd["mean_r"]) if rd and sim["result"] in q1.FILLED else None
  return out


def divergence(python_row, frozen, current):
  """The earliest stage at which the current engine departs from the Python decision."""
  if frozen is None:
    return "detection", "the Go engine under the reference behaviour did not publish the Python decision"
  p = python_row["python"]
  if frozen["direction"] != p["direction"]:
    return "confirmation", "direction"
  if current is None:
    stars = frozen.get("stars")
    return "acceptance", f"not published by the current engine: reference confluence {p['confluence']} stars, current measured stars {stars}"
  if current["stars"] != frozen["stars"]:
    return "quality", f"published, stars {frozen['stars']} -> {current['stars']}"
  return "none", "identical decision, geometry and stars"


def build(golden_paths, frozen_records, frozen_random, current_records, current_random):
  """One ledger row per Go opportunity of the reference behaviour (a thesis: one published identity, first seen at one closed bar), joined to the Python decision at
  that bar. Python decisions that no reference opportunity explains, and current opportunities that no reference opportunity explains, are returned separately."""
  golden = load_golden(golden_paths)
  python, evaluated, disagreements = python_theses(golden)
  by_bar = {(sym, bar + M5): row for (sym, bar), row in python.items()}
  current_by_id = {r["id"]: r for r in current_records if r["strategy"] == "key_level"}
  current_idx = index_records(current_records)
  ledger, explained = [], set()
  # A bar belongs to the first capture that evaluated it; only that capture's Go replay is comparable with that capture's Python decisions
  # (another capture has another warm-up and may give the same thesis another identity).
  reference = sorted((r for r in frozen_records if r["strategy"] == "key_level" and HARNESS.get(evaluated.get((r["symbol"], r["eval_time"] - M5))) == r["capture"]),
                     key=lambda r: (r["symbol"], r["eval_time"]))
  for frozen in reference:
    row = by_bar.get((frozen["symbol"], frozen["eval_time"]))
    stage, why = None, None
    if row is None or row["python"]["direction"] != frozen["direction"]:
      stage, why = "detection", "published by the Go engine under the reference behaviour; the Python was silent (or the other way) on this bar"
      python_decision = {"decision": "silent" if row is None else "other_direction"}
    elif not band_matches(frozen, row):
      stage, why = "geometry", "same market context and direction, different entry band"
      python_decision = {"decision": "published", "stars": row["python"]["confluence"], "entry_low": row["python"]["entry_low"], "entry_high": row["python"]["entry_high"]}
    else:
      explained.add((frozen["symbol"], frozen["eval_time"]))
      p = row["python"]
      python_decision = {"decision": "published", "stars": p["confluence"], "entry_low": p["entry_low"], "entry_high": p["entry_high"]}
    current = current_by_id.get(frozen["id"]) or current_idx.get((frozen["symbol"], frozen["eval_time"], frozen["direction"]))
    if stage is None:
      stage, why = divergence(row, frozen, current)
    p = (row or {}).get("python") or {}
    ledger.append({
      "market_time": frozen["eval_time"] - M5, "evaluated_bar_close": frozen["eval_time"], "period": period_of(frozen["eval_time"] - M5), "symbol": frozen["symbol"],
      "group": q3.group_of(frozen["symbol"]), "direction": frozen["direction"], "capture": frozen["capture"],
      "thesis": {"level": p.get("key_level"), "kind": p.get("structural_kind"), "role": p.get("role"), "source_touches": p.get("source_touches"), "confirmation": p.get("confirmation_type"),
                 "touch_bar": p.get("touch_bar"), "confirmation_bar": p.get("confirmation_bar"), "structural_id": frozen.get("structural_id")},
      "python": python_decision,
      "go_reference": {"decision": "published", "stars": frozen["stars"], "id": frozen["id"]},
      "go_current": {"decision": "published", "stars": current["stars"], "id": current["id"]} if current else {"decision": "not_published"},
      "earliest_divergent_stage": stage, "divergence": why,
      "geometry": {k: frozen.get(k) for k in ("entry_low", "entry_high", "invalidation", "targets", "target_labels", "tech_risk_pips", "tech_reward_pips", "tech_rr",
                                               "exec_risk_pips", "executable", "room_cover", "atr", "entry_width_pips")},
      "factors": frozen.get("factors"), "evidence": frozen.get("evidence"),
      "outcome": outcome(frozen, frozen_random),
    })
  # Python decisions with no reference opportunity at their bar: legitimate when a standing thesis (same direction and band, within a day) already covers them.
  standing = collections.defaultdict(list)
  for r in reference:
    standing[(r["symbol"], r["direction"])].append(r)
  python_only = []
  for (sym, closes), row in sorted(by_bar.items()):
    if (sym, closes) in explained:
      continue
    covered = any(r["eval_time"] <= closes and closes - r["eval_time"] <= 86400 and band_drifts_with_atr(r, row) for r in standing[(sym, row["python"]["direction"])])
    if not covered:
      python_only.append({"symbol": sym, "market_time": row["bar"], "python": row["python"]})
  current_ids = {x["go_current"].get("id") for x in ledger}
  current_only = [r for r in current_idx.values() if HARNESS.get(evaluated.get((r["symbol"], r["eval_time"] - M5))) == r["capture"] and r["id"] not in current_ids]
  return ledger, python_only, current_only, disagreements, len(evaluated)


def summarise(ledger):
  """Per period and instrument group: Python theses, published by the current engine, filled, and the outcome of the retained and the removed."""
  out = {}
  for period in ("september", "october"):
    for group in ("XAU", "FX"):
      sub = [x for x in ledger if x["period"] == period and x["group"] == group]
      if not sub:
        continue
      entry = {"python_theses": len(sub), "published_by_current_go": sum(x["go_current"]["decision"] == "published" for x in sub),
               "stages": dict(collections.Counter(x["earliest_divergent_stage"] for x in sub))}
      for name, pick in (("all", lambda x: True), ("retained", lambda x: x["go_current"]["decision"] == "published"), ("removed", lambda x: x["go_current"]["decision"] != "published")):
        rows = [row_of(x) for x in sub if pick(x) and x["outcome"] and x["outcome"]["result"] in q1.FILLED and x["outcome"]["random_r"] is not None]
        entry[name] = q3.summary(rows)
      out[f"{period}|{group}"] = entry
  return out


def comparisons(ledger):
  """The differences the report relies on, each with a symbol-day cluster interval (cluster bootstrap of the difference of mean outcome-minus-random)."""
  def rows(pick):
    return [row_of(x) for x in ledger if pick(x) and x["outcome"] and x["outcome"]["result"] in q1.FILLED and x["outcome"]["random_r"] is not None]

  def diff(high, low):
    if len(high) < q3.MIN_SIDE or len(low) < q3.MIN_SIDE:
      return {"n_high": len(high), "n_low": len(low), "difference": "insufficient"}
    est, _, ci = q3.boot_difference(high, low)
    return {"n_high": len(high), "n_low": len(low), "difference": round(est, 3), "ci95": [round(ci[0], 3), round(ci[1], 3)]}

  published = lambda x: x["go_current"]["decision"] == "published"
  out = {"reference_xau_september_minus_october": diff(rows(lambda x: x["group"] == "XAU" and x["period"] == "september"), rows(lambda x: x["group"] == "XAU" and x["period"] == "october"))}
  for group in ("XAU", "FX"):
    for period in ("september", "october", None):
      if group == "FX" and period == "september":
        continue
      sel = lambda x, g=group, p=period: x["group"] == g and (p is None or x["period"] == p)
      out[f"current_retained_minus_removed|{group}|{period or 'all'}"] = diff(rows(lambda x, s=sel: s(x) and published(x)), rows(lambda x, s=sel: s(x) and not published(x)))
      out[f"current_retained_minus_reference_all|{group}|{period or 'all'}"] = diff(rows(lambda x, s=sel: s(x) and published(x)), rows(sel))
  return out


def row_of(x):
  o = x["outcome"]
  return {"r": o["r"], "r_nocost": o["r"], "rand": o["random_r"], "diff": o["difference_r"], "mfe": o["mfe"], "mae": o["mae"],
          "cluster": (x["symbol"], x["market_time"] // 86400), "stars": x["go_reference"]["stars"], "t": x["market_time"]}


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--golden", nargs="+", required=True, help="oracle files (key-level-oracle.json, then the generator output over the later captures)")
  ap.add_argument("--frozen", required=True, help="directory with records.jsonl and random.jsonl of the replay under the reference behaviour")
  ap.add_argument("--current", required=True, help="directory with records.jsonl and random.jsonl of the replay as the engine runs now")
  ap.add_argument("--ledger", required=True)
  ap.add_argument("--summary", required=True)
  a = ap.parse_args()
  frozen = q1.load_jsonl(os.path.join(a.frozen, "records.jsonl"))
  current = q1.load_jsonl(os.path.join(a.current, "records.jsonl"))
  fr = {r["id"]: r for r in q1.load_jsonl(os.path.join(a.frozen, "random.jsonl"))}
  cr = {r["id"]: r for r in q1.load_jsonl(os.path.join(a.current, "random.jsonl"))}
  ledger, python_only, current_only, disagreements, evaluated = build(a.golden, frozen, fr, current, cr)
  with open(a.ledger, "w") as handle:
    for row in ledger:
      handle.write(json.dumps(row, sort_keys=True) + "\n")
  summary = {"bars_evaluated": evaluated, "go_reference_opportunities": len(ledger), "python_decisions_unexplained": len(python_only),
             "python_unexplained_examples": python_only[:20], "current_go_opportunities_unexplained": len(current_only), "capture_window_disagreements": disagreements[:50],
             "capture_window_disagreement_count": len(disagreements), "by_period_group": summarise(ledger), "comparisons": comparisons(ledger)}
  json.dump(summary, open(a.summary, "w"), indent=1)
  print(json.dumps({k: v for k, v in summary.items() if k != "by_period_group"}, indent=1))


if __name__ == "__main__":
  main()
