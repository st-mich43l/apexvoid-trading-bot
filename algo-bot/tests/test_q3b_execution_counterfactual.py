"""tools/q3b_execution_counterfactual.py: two replays of the same signals under two execution configurations."""

import json

import pytest

from tools import q3b_execution_counterfactual as cf

pytestmark = pytest.mark.no_database


def _write(directory, rows, randoms):
  directory.mkdir()
  (directory / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
  (directory / "random.jsonl").write_text("".join(json.dumps(r) + "\n" for r in randoms))


def _record(i, r, floor, cap, result="target", symbol="GBPUSD"):
  return {"id": f"o{i}", "strategy": "key_level", "symbol": symbol, "direction": "BUY", "eval_time": 1_790_000_000 + i * 86400, "floor_pips": floor, "cap_pips": cap, "atr": 0.001,
          "sim": {"ladder": {"r": r}, "ladder_cost": {"result": result, "r": r, "mfe": 1.0, "mae": 0.5}}}


def test_the_comparison_reports_the_envelope_and_the_difference_to_the_random_control_of_each_configuration(tmp_path):
  randoms_a = [{"id": f"o{i}", "mean_r": -0.1} for i in range(3)]
  randoms_b = [{"id": f"o{i}", "mean_r": -0.2} for i in range(3)]
  _write(tmp_path / "a", [_record(i, 1.0, 15, 25) for i in range(3)], randoms_a)
  _write(tmp_path / "b", [_record(0, 0.5, 12, 22), _record(1, 0.5, 12, 22), _record(2, 0.0, 12, 22, result="not_executable")], randoms_b)
  out = cf.compare(str(tmp_path / "a"), str(tmp_path / "b"), "key_level")
  assert out["identical_signals"] is True
  assert out["a"]["GBPUSD"]["envelope_floor_cap_pips"] == [(15, 25)] and out["b"]["GBPUSD"]["envelope_floor_cap_pips"] == [(12, 22)]
  assert out["b"]["GBPUSD"]["not_executable"] == 1
  assert out["a"]["GBPUSD"]["outcome"]["mean_difference_r"] == pytest.approx(1.1)
  assert out["b"]["GBPUSD"]["outcome"]["mean_difference_r"] == pytest.approx(0.7)


def test_a_replay_that_changed_the_signals_is_flagged(tmp_path):
  _write(tmp_path / "a", [_record(0, 1.0, 15, 25)], [{"id": "o0", "mean_r": 0.0}])
  _write(tmp_path / "b", [_record(1, 1.0, 15, 25)], [{"id": "o1", "mean_r": 0.0}])
  assert cf.compare(str(tmp_path / "a"), str(tmp_path / "b"), "key_level")["identical_signals"] is False
