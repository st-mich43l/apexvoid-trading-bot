"""Q3b: the same signals under two execution configurations of the same replay harness.

``AEQ_CONFIG`` lets ``test/aequality`` replay the real captures under a copy of the configuration in which one historical setting is changed (for example the FX stop
envelope of the profitable-week commit). The signals are identical bar by bar; only the executable geometry and the simulated outcome move. This tool lines the two
record sets up by opportunity id and reports, per symbol, the envelope, the number of theses, how many could not be executed, the mean R and the difference to the matched
random control (which is simulated under the same configuration, so a wider stop cannot flatter the signal).

    python q3b_execution_counterfactual.py --strategy key_level --a <dir with records.jsonl, random.jsonl> --b <dir> --out result.json
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import q1_opportunity_quality as q1  # noqa: E402
import q3_quality_validation as q3  # noqa: E402


def load(directory, strategy):
  records = [r for r in q1.load_jsonl(os.path.join(directory, "records.jsonl")) if r["strategy"] == strategy]
  randoms = {r["id"]: r for r in q1.load_jsonl(os.path.join(directory, "random.jsonl"))}
  return records, randoms


def result_of(record):
  return ((record.get("sim") or {}).get("ladder_cost") or {}).get("result")


def per_symbol(records, randoms):
  out = {}
  for symbol in sorted({r["symbol"] for r in records}):
    sub = [r for r in records if r["symbol"] == symbol]
    rows = q3.observations(sub, randoms, thin=False)
    out[symbol] = {"envelope_floor_cap_pips": sorted({(r["floor_pips"], r["cap_pips"]) for r in sub}), "theses": len(sub),
                   "not_executable": sum(result_of(r) == "not_executable" for r in sub), "outcome": q3.summary(rows)}
  return out


def compare(dir_a, dir_b, strategy):
  a_records, a_random = load(dir_a, strategy)
  b_records, b_random = load(dir_b, strategy)
  same_theses = {r["id"] for r in a_records} == {r["id"] for r in b_records}
  return {"strategy": strategy, "identical_signals": same_theses, "a": per_symbol(a_records, a_random), "b": per_symbol(b_records, b_random)}


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--strategy", default="key_level")
  ap.add_argument("--a", required=True, help="replay directory under the configuration in force")
  ap.add_argument("--b", required=True, help="replay directory under the counterfactual configuration")
  ap.add_argument("--out", required=True)
  args = ap.parse_args()
  result = compare(args.a, args.b, args.strategy)
  json.dump(result, open(args.out, "w"), indent=1)
  for symbol in sorted(result["a"]):
    for label in ("a", "b"):
      entry = result[label][symbol]
      outcome = entry["outcome"]
      print(symbol, label, entry["envelope_floor_cap_pips"], entry["theses"], entry["not_executable"], outcome.get("mean_r"), outcome.get("mean_difference_r"))


if __name__ == "__main__":
  main()
