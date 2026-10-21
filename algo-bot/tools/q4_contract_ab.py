"""Q4: the same strategy under two structure contracts, on identical candles.

``AEQ_PARAMS`` makes ``test/aequality`` replay one strategy under a different parameter set. This tool lines two replays up by market context (symbol, closed bar,
direction), never by opportunity id, and reports which opportunities exist only under one contract, which under both, and the hypothetical outcome of each class
against its matched random control, September (development) and October (out of sample) separately, with symbol-day cluster intervals.

    python q4_contract_ab.py --strategy key_level --a <dir: records.jsonl random.jsonl> --b <dir> --a-name profit_week --b-name current --out ab.json

An opportunity whose bar and direction match but whose entry band differs (another level won the bar) is a class of its own, ``band_differs``.
"""
import argparse
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402
import q3_quality_validation as q3  # noqa: E402

SEPTEMBER_END = int(datetime.datetime(2026, 9, 28, tzinfo=datetime.timezone.utc).timestamp())
TOL = {"XAU": 0.011, "EURUSD": 1.1e-5, "GBPUSD": 1.1e-5, "USDJPY": 0.0011, "GBPJPY": 0.0011}


def load(directory, strategy):
  records = [r for r in q1.load_jsonl(os.path.join(directory, "records.jsonl")) if r["strategy"] == strategy]
  randoms = {r["id"]: r for r in q1.load_jsonl(os.path.join(directory, "random.jsonl"))}
  return records, randoms


def classify(a_records, b_records):
  """{(symbol, eval_time, direction): (a_record or None, b_record or None, class)}. A record keeps the band it was first published with."""
  a_idx = {(r["symbol"], r["eval_time"], r["direction"]): r for r in a_records}
  b_idx = {(r["symbol"], r["eval_time"], r["direction"]): r for r in b_records}
  out = {}
  for key in set(a_idx) | set(b_idx):
    a, b = a_idx.get(key), b_idx.get(key)
    if a is None:
      kind = "only_b"
    elif b is None:
      kind = "only_a"
    else:
      tol = TOL[key[0]]
      same = abs(a["entry_low"] - b["entry_low"]) < tol and abs(a["entry_high"] - b["entry_high"]) < tol
      kind = "both" if same else "band_differs"
    out[key] = (a, b, kind)
  return out


def period_of(eval_time):
  return "september" if eval_time - 300 < SEPTEMBER_END else "october"


def compare(a_dir, b_dir, strategy):
  a_records, a_random = load(a_dir, strategy)
  b_records, b_random = load(b_dir, strategy)
  classes = classify(a_records, b_records)
  out = {"strategy": strategy, "counts": collections.Counter(kind for _, _, kind in classes.values()), "classes": {}}
  out["counts"] = dict(out["counts"])
  for group in ("XAU", "FX"):
    for period in ("september", "october"):
      for kind in ("both", "only_a", "only_b", "band_differs"):
        a_rows, b_rows = [], []
        for (symbol, eval_time, _), (a, b, k) in classes.items():
          if k != kind or q3.group_of(symbol) != group or period_of(eval_time) != period:
            continue
          if a is not None:
            a_rows += q3.observations([a], a_random, thin=False)
          if b is not None:
            b_rows += q3.observations([b], b_random, thin=False)
        out["classes"][f"{group}|{period}|{kind}"] = {"a": q3.summary(a_rows), "b": q3.summary(b_rows)}
  return out


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--strategy", required=True)
  ap.add_argument("--a", required=True)
  ap.add_argument("--b", required=True)
  ap.add_argument("--a-name", default="a")
  ap.add_argument("--b-name", default="b")
  ap.add_argument("--out", required=True)
  args = ap.parse_args()
  result = compare(args.a, args.b, args.strategy)
  result["a_name"], result["b_name"] = args.a_name, args.b_name
  json.dump(result, open(args.out, "w"), indent=1)
  print(json.dumps(result["counts"]))
  for key, value in sorted(result["classes"].items()):
    for side in ("a", "b"):
      s = value[side]
      if s.get("n"):
        print(f"{key:28s}{args.a_name if side == 'a' else args.b_name:12s} n={s['n']:4d} meanR={s['mean_r']:+.3f} diff={s['mean_difference_r']:+.3f} ci={s['ci95']} win={s['win_rate']}")


if __name__ == "__main__":
  main()
