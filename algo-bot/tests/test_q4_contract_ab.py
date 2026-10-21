"""tools/q4_contract_ab.py: two replays of one strategy lined up by market context."""

import json

import pytest

from tools import q4_contract_ab as ab

pytestmark = pytest.mark.no_database

OCT = 1_790_800_000
SEP = 1_789_500_000


def _record(i, eval_time, low=4300.0, high=4304.0, direction="BUY", r=1.0):
  return {"id": f"o{i}", "strategy": "key_level", "symbol": "XAU", "direction": direction, "eval_time": eval_time, "entry_low": low, "entry_high": high, "atr": 3.0,
          "sim": {"ladder": {"r": r}, "ladder_cost": {"result": "target", "r": r, "mfe": 1.0, "mae": 0.5}}}


def _write(directory, records):
  directory.mkdir()
  (directory / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
  (directory / "random.jsonl").write_text("".join(json.dumps({"id": r["id"], "mean_r": 0.0}) + "\n" for r in records))


def test_opportunities_are_classified_by_market_context_not_by_id():
  a = [_record(1, OCT), _record(2, OCT + 300), _record(3, OCT + 600, low=4310.0, high=4314.0)]
  b = [_record(11, OCT), _record(13, OCT + 600, low=4320.0, high=4324.0), _record(14, OCT + 900)]
  kinds = {key: kind for key, (_, _, kind) in ab.classify(a, b).items()}
  assert kinds[("XAU", OCT, "BUY")] == "both", "another id, the same bar, direction and band"
  assert kinds[("XAU", OCT + 300, "BUY")] == "only_a"
  assert kinds[("XAU", OCT + 600, "BUY")] == "band_differs", "another level won the bar"
  assert kinds[("XAU", OCT + 900, "BUY")] == "only_b"


def test_periods_split_at_the_end_of_september_and_the_outcome_follows_the_class(tmp_path):
  a = [_record(1, SEP, r=-1.0), _record(2, OCT, r=1.0)]
  b = [_record(1, SEP, r=-1.0), _record(2, OCT, r=1.0), _record(3, OCT + 300, r=2.0)]
  ab_dir_a, ab_dir_b = tmp_path / "a", tmp_path / "b"
  _write(ab_dir_a, a)
  _write(ab_dir_b, b)
  out = ab.compare(str(ab_dir_a), str(ab_dir_b), "key_level")
  assert out["counts"] == {"both": 2, "only_b": 1}
  assert out["classes"]["XAU|september|both"]["a"]["mean_r"] == -1.0
  only_b = out["classes"]["XAU|october|only_b"]
  assert only_b["a"] == {"n": 0} and only_b["b"]["mean_r"] == 2.0
