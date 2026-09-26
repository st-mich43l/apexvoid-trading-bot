"""S14C: deterministic Go-vs-Python policy-input replay (comparator, capture, CLI).

The comparator's *logic* is exercised with observations derived from a real
replayed Go case with controlled deltas (clearly test inputs, never evidence).
The evidence itself is the committed real capture and the Go envelopes replayed
from it (``contracts/analysis/replay``), decoded by the production decoder and
the live adapter.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from app.autotrade import policy_replay as pr
from app.scripts import policy_replay as cli

pytestmark = pytest.mark.no_database

REPLAY_DIR = Path(__file__).resolve().parents[2] / "contracts" / "analysis" / "replay"
CAPTURE = REPLAY_DIR / "xau-production-capture-20260921.json"
GO_GOLDEN = REPLAY_DIR / "go-supply-demand-confirmed-envelopes-xau-20260921.jsonl"
GO_META = json.loads((REPLAY_DIR / "go-replay-xau-20260921.meta.json").read_text())


@pytest.fixture(scope="module")
def capture():
  return pr.load_capture(CAPTURE)


@pytest.fixture(scope="module")
def go():
  return pr.load_go_cases(GO_GOLDEN)


def adaptable(cases):
  return [c for c in cases if c.match is not None]


def mirror(case: pr.GoCase, **overrides) -> pr.PythonObservation:
  """A Python observation that agrees with ``case`` on every input, then overridden."""
  go = pr._go_view(case)
  base = pr.PythonObservation(
    id="py_test", scope=case.scope, direction=go["direction"], detected_at_close=go["confirmation_at"] + 300,
    confirmation_bar_ts=go["confirmation_at"], touch_bar_ts=go["touch_at"], confirmation_type="wick_rejection",
    confluence=len(go["evidence"]), htf_bias=case.match.htf_bias if case.match else "up", atr=go["atr"],
    entry_low=go["entry_low"], entry_high=go["entry_high"], structural_low=go["entry_low"], structural_high=go["entry_high"],
    structural_id="zone-x", bias_relationship="with_bias", reasons=("r1",),
  )
  return replace(base, **overrides)


def run(python, cases, capture, **kw):
  kw.setdefault("opposing", False)
  kw.setdefault("models", False)
  return pr.compare(python, cases, capture, **kw)


# ---- capture ----------------------------------------------------------------------------

def test_committed_capture_is_the_corroborated_real_capture(capture):
  assert capture.symbol == "XAU"
  assert {tf: len(df) for tf, df in capture.frames.items()} == {"M5": 1500, "M15": 600, "H1": 300}
  assert not capture.h4_derived                                   # the production feed delivers no H4
  explored = pr.load_capture(CAPTURE, derive_h4_from_h1=True)
  assert explored.h4_derived and len(explored.frames["H4"]) > 40
  corroboration = capture.provenance["independent_corroboration"]
  assert len(corroboration) == 2 and all("identical" in text for text in corroboration.values())


@pytest.mark.parametrize("mutate,message", [
  (lambda d: d.update(version=2), "version 1"),
  (lambda d: d["timeframes"]["M5"].reverse(), "strictly increasing"),
  (lambda d: d["timeframes"]["M5"].__setitem__(0, [301, 1, 2, 1, 2, 1]), "aligned"),
  (lambda d: d["timeframes"]["M5"].__setitem__(0, [300, 2, 2, 1, 3, 1]), "OHLC-consistent"),
  (lambda d: d.update(columns=["t", "o"]), "columns"),
  (lambda d: d["timeframes"].update(M7=[]), "unknown timeframe"),
])
def test_malformed_capture_is_refused_not_repaired(tmp_path, mutate, message):
  data = json.loads(CAPTURE.read_text())
  mutate(data)
  path = tmp_path / "capture.json"
  path.write_text(json.dumps(data))
  with pytest.raises(pr.ReplayError, match=message):
    pr.load_capture(path)


def test_derive_h4_uses_complete_utc_aligned_buckets_only():
  hr = 3600
  rows = [
    [0, 10, 12, 9, 11, 10], [hr, 11, 15, 10, 14, 10], [2 * hr, 14, 14, 8, 9, 10], [3 * hr, 9, 11, 9, 10, 10],
    [4 * hr, 10, 11, 10, 11, 10], [5 * hr, 11, 12, 11, 12, 10], [7 * hr, 12, 13, 12, 13, 10],
    [8 * hr, 20, 21, 19, 20, 10], [9 * hr, 20, 25, 20, 24, 10], [10 * hr, 24, 24, 22, 23, 10], [11 * hr, 23, 23, 21, 22, 10],
    [12 * hr, 22, 22, 21, 21, 10],
  ]
  h4 = pr.derive_h4(pr._frame("H1", rows))
  assert [int(t.timestamp()) for t in h4.index] == [0, 8 * hr]
  assert list(h4.iloc[0]) == [10, 15, 8, 10, 40] and list(h4.iloc[1]) == [20, 25, 19, 22, 40]


def test_frames_at_never_exposes_a_bar_that_has_not_closed(capture):
  m5 = capture.frames["M5"]
  for index in (300, 700, 1200):
    close_at = int(m5.index[index].timestamp()) + 300
    for tf, df in pr.frames_at(capture, close_at).items():
      closes = df.index + pd.Timedelta(minutes=pr.TF_MINUTES[tf])
      assert (closes <= pd.Timestamp(close_at, unit="s", tz="UTC")).all(), tf
    assert pr.frames_at(capture, close_at)["M5"].index[-1] == m5.index[index]
  # One second before the M5 bar closes it is still forming: not visible.
  close_at = int(m5.index[700].timestamp()) + 300
  assert pr.frames_at(capture, close_at - 1)["M5"].index[-1] == m5.index[699]


# ---- Go side: production decoder + live adapter -----------------------------------------------

def test_go_replay_envelopes_go_through_the_production_decoder_and_adapter(go):
  cases, skipped = go
  assert len(cases) == GO_META["golden_supply_demand_confirmed_count"] and not skipped
  rejections = Counter(c.rejection for c in cases if c.rejection)
  assert set(rejections) <= {"higher_timeframe_bias_unavailable"}          # the only expected, honest refusal
  for case in adaptable(cases):
    assert case.match.match_id == f"go_{case.id}" and case.match.tier in {"A", "B", "C"}
  assert {c.scope for c in cases} == {"supply", "demand"}
  assert [c.confirmation_at for c in cases] == sorted(c.confirmation_at for c in cases)


def test_meta_and_capture_describe_the_same_replay(capture):
  assert GO_META["capture"] == CAPTURE.name and GO_META["h4_derived_from_h1_utc_aligned"] is capture.h4_derived
  assert GO_META["events_dispatched"] == sum(len(df) for df in capture.frames.values())
  assert GO_META["timeframes_dispatched"] == len(capture.frames)


# ---- comparator logic ---------------------------------------------------------------------------

def test_a_perfect_mirror_matches_and_every_gate_passes(go, capture):
  case = adaptable(go[0])[0]
  report = run([mirror(case)], [case], capture)
  assert report["matching"] == {"matched": 1, "python_only": 0, "go_only": 0}
  assert all(v["agree"] == v["total"] for v in report["agreement"].values() if v["total"])
  assert report["verdict"] == "pass" and all(report["gates"].values())


def test_verdict_is_never_pass_while_anything_is_open(go, capture):
  case = adaptable(go[0])[0]
  assert run([mirror(case)], [case], capture, stride=5)["verdict"] == "unresolved_differences"    # sampled replay can't pass
  assert run([], [case], capture)["matching"]["go_only"] == 1
  assert run([mirror(case), mirror(case, id="py_extra", confirmation_bar_ts=case.confirmation_at + 9_000)], [case], capture)["matching"]["python_only"] == 1


@pytest.mark.parametrize("override", [
  {"confirmation_bar_ts": 10_000_000},                     # different time
  {"entry_low": 1.0, "entry_high": 2.0},                   # no zone overlap
  {"scope": "demand"},                                     # other scope
])
def test_only_the_same_setup_matches(go, capture, override):
  case = next(c for c in adaptable(go[0]) if c.scope == "supply")
  report = run([mirror(case, **override)], [case], capture)
  assert report["matching"] == {"matched": 0, "python_only": 1, "go_only": 1}


def test_tolerance_edge_and_one_to_one_matching(go, capture):
  case = adaptable(go[0])[0]
  inside = mirror(case, confirmation_bar_ts=case.confirmation_at + 300)
  outside = mirror(case, confirmation_bar_ts=case.confirmation_at + 301)
  assert run([inside], [case], capture)["matching"]["matched"] == 1
  assert run([outside], [case], capture)["matching"]["matched"] == 0
  twin = replace(case, event=case.event)                    # two Go cases, one Python observation
  report = run([mirror(case)], [case, twin], capture)
  assert report["matching"] == {"matched": 1, "python_only": 0, "go_only": 1}


def test_field_disagreements_are_measured_not_hidden(go, capture):
  case = adaptable(go[0])[0]
  obs = mirror(case, confirmation_type="engulfing", confluence=2, atr=case.match.atr * 1.05 if hasattr(case.match, "atr") else pr._go_view(case)["atr"] * 1.05,
               entry_low=pr._go_view(case)["entry_low"] + 0.5, entry_high=pr._go_view(case)["entry_high"] + 0.5, htf_bias="down" if case.match.htf_bias == "up" else "up")
  report = run([obs], [case], capture)
  agreement = report["agreement"]
  assert agreement["confirmation_kind_same_family"]["agree"] == 0
  assert agreement["atr_within_1pct"]["agree"] == 0
  assert agreement["tier_agrees"]["agree"] == 0                      # Python 2 -> B, Go 4 -> A
  assert agreement["htf_bias_agree"]["agree"] == 0 and report["htf_relations"] == {"conflict": 1}
  assert report["verdict"] == "unresolved_differences" and not report["gates"]["all_matched_fields_in_tolerance"]
  pair = report["pairs"][0]["fields"]
  assert pair["confluence"]["python_tier"] == "B" and pair["confluence"]["go_tier"] == "A"
  assert pair["entry_zone"]["low_delta"] == pytest.approx(-0.5)


def test_htf_relations_distinguish_unavailable_from_conflict(go, capture):
  cases = go[0]
  ok = adaptable(cases)[0]
  assert run([mirror(ok, htf_bias="unknown")], [ok], capture)["htf_relations"] == {"python_unavailable": 1}
  refused = next(c for c in cases if c.rejection)
  report = run([mirror(refused, htf_bias="up")], [refused], capture)
  assert report["htf_relations"] == {"go_unavailable": 1} and report["go_adaptation"]["rejections"] == {"higher_timeframe_bias_unavailable": 1}
  assert not report["gates"]["every_go_case_adaptable"] and not report["gates"]["no_htf_unavailability_or_conflict"]


def test_go_only_notes_whether_the_python_window_could_have_seen_it(go, capture):
  case = adaptable(go[0])[0]
  inside = run([], [case], capture, python_window=(case.confirmation_at, case.confirmation_at + 600))["go_only"][0]
  outside = run([], [case], capture, python_window=(case.confirmation_at + 10_000, case.confirmation_at + 20_000))["go_only"][0]
  assert inside["python_window_covers_it"] is True and outside["python_window_covers_it"] is False


def test_the_report_is_deterministic_and_states_its_limits(go, capture):
  case = adaptable(go[0])[0]
  a = json.dumps(run([mirror(case)], [case], capture), sort_keys=True)
  b = json.dumps(run([mirror(case)], [case], capture), sort_keys=True)
  assert a == b
  report = json.loads(a)
  assert any("not a performance or live-equivalence claim" in item for item in report["limits"])
  assert any("approves nothing" in item for item in report["limits"])
  assert report["inputs"]["capture"]["sha256"] == capture.sha256


def test_markdown_report_reads_for_a_human(go, capture):
  case = adaptable(go[0])[0]
  text = pr.render_markdown(run([mirror(case, entry_low=1.0, entry_high=2.0)], [case], capture))
  for needle in ("# Go vs Python policy replay", "Verdict:", "Field agreement", "Go-only (1", "Python-only (1", "## Limits", "approves nothing"):
    assert needle in text, needle


# ---- opposing structure: the worker's own check on identical frames --------------------------------

def test_opposing_structure_uses_the_workers_check_on_frames_visible_at_the_bar(go, capture):
  case = adaptable(go[0])[0]
  view = pr._go_view(case)
  a = pr._opposing(capture, view["direction"], (view["entry_low"] + view["entry_high"]) / 2, view["atr"], view["entry_low"], view["entry_high"], view["confirmation_at"] + 300)
  b = pr._opposing(capture, view["direction"], (view["entry_low"] + view["entry_high"]) / 2, view["atr"], view["entry_low"], view["entry_high"], view["confirmation_at"] + 300)
  assert a == b and isinstance(a["vetoed"], bool) and (a["reason"] is None) == (not a["vetoed"])


# ---- CLI (report step) ------------------------------------------------------------------------------

def test_cli_report_writes_machine_readable_and_human_reports(tmp_path, go, capture, capsys):
  case = adaptable(go[0])[0]
  obs = tmp_path / "python.jsonl"
  pr.write_python_observations(obs, [mirror(case)])
  meta = tmp_path / "meta.json"
  meta.write_text(json.dumps({"stride": 1, "window_close_at": [case.confirmation_at, case.confirmation_at + 600]}))
  out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
  code = cli.main(["report", "--capture", str(CAPTURE), "--go-envelopes", str(GO_GOLDEN), "--python-observations", str(obs),
                   "--python-meta", str(meta), "--json", str(out_json), "--md", str(out_md), "--no-opposing"])
  assert code == 0
  report = json.loads(out_json.read_text())
  assert report["kind"] == "go_vs_python_policy_replay" and report["matching"]["matched"] == 1
  assert report["inputs"]["python"]["window_close_at"] == [case.confirmation_at, case.confirmation_at + 600]
  assert report["inputs"]["go"]["file_sha256"] and "Verdict" in out_md.read_text()
  assert json.loads(capsys.readouterr().out)["verdict"] == "unresolved_differences"     # the Go-only remainder is open


def test_cli_refuses_a_bad_capture_without_writing_anything(tmp_path, capsys):
  bad = tmp_path / "bad.json"
  bad.write_text(json.dumps({"version": 9}))
  assert cli.main(["report", "--capture", str(bad), "--go-envelopes", str(GO_GOLDEN), "--python-observations", str(bad), "--json", str(tmp_path / "j"), "--md", str(tmp_path / "m")]) == 2
  assert not (tmp_path / "j").exists()


# ---- real-bar capture tool (read-only) --------------------------------------------------------

async def _seed_bars(client, rows, tf="M5"):
  for t, o, h, l, c, v in rows:
    await client.zadd(f"bars:XAU:{tf}", {json.dumps({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}): t})


@pytest.mark.asyncio
async def test_capture_tool_reads_redis_writes_nothing_and_roundtrips_into_the_replay(tmp_path):
  from datetime import datetime, timezone

  from app.persistence import redis_state
  from app.scripts import capture_bars

  client = redis_state.get_client()
  rows = [[1_789_900_200 + 300 * i, 4300 + i, 4301 + i, 4299 + i, 4300.5 + i, 100 + i] for i in range(12)]
  await _seed_bars(client, rows)
  before = {k: await client.zrange(k, 0, -1, withscores=True) async for k in client.scan_iter("*")}

  document = await capture_bars.capture("XAU", {"M5": 10, "M15": 0}, client, now=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc))

  assert {k: await client.zrange(k, 0, -1, withscores=True) async for k in client.scan_iter("*")} == before   # read-only
  assert document["provenance"]["captured_at_utc"] == "2026-09-26T12:00:00Z" and document["provenance"]["derived"] == "none"
  assert list(document["timeframes"]) == ["M5"] and len(document["timeframes"]["M5"]) == 10
  assert document["timeframes"]["M5"] == rows[-10:]                          # exactly the newest ten, untouched
  path = tmp_path / "capture.json"
  path.write_text(json.dumps(document))
  loaded = pr.load_capture(path)
  assert loaded.symbol == "XAU" and len(loaded.frames["M5"]) == 10


def test_capture_tool_refuses_to_leave_a_file_the_replay_would_reject(tmp_path, monkeypatch, capsys):
  from app.scripts import capture_bars

  async def fake_capture(symbol, counts, client=None, *, now=None):
    return {"version": 1, "symbol": symbol, "provenance": {}, "columns": ["t", "open", "high", "low", "close", "volume"],
            "timeframes": {"M5": [[301, 1, 2, 1, 2, 1]]}}          # not aligned to the timeframe

  monkeypatch.setattr(capture_bars, "capture", fake_capture)
  out = tmp_path / "bad.json"
  assert capture_bars.main(["--out", str(out)]) == 2
  assert not out.exists() and "aligned" in capsys.readouterr().err


# ---- ATR / geometry / policy model on the real Go cases ----------------------------------------------

def test_go_atr_is_the_configured_simple_formula_and_differs_from_pythons_wilder(go, capture):
  models = [pr.go_model(c, capture) for c in go[0]]
  assert all(m["atr"]["reported_matches_simple"] for m in models)            # config: analysis.indicators.atr.algorithm = simple
  relative = [m["atr"]["wilder_vs_reported_relative"] for m in models if m["atr"]["wilder_vs_reported_relative"] is not None]
  assert relative and any(abs(r) > 0.05 for r in relative)                   # the two formulas genuinely diverge on real XAU M5


def test_geometry_conversion_is_measured_on_real_prices(go, capture):
  models = [pr.go_model(c, capture) for c in go[0]]
  pip = models[0]["geometry"]["pip"]
  assert pip == 0.1 and models[0]["geometry"]["tick"] == 0.01 and models[0]["geometry"]["digits"] == 2
  for m in models:
    geometry = m["geometry"]
    assert geometry["stop_pips"] > 0 and geometry["rr_first_target"] is not None
    assert geometry["max_target_rounding_error_price"] <= pip / 2 + 1e-9        # whole-pip conversion loses at most half a pip
  # Go emits unrounded floats: the contract must not be tick-rounded silently on its way to the plan.
  assert not all(m["geometry"]["prices_tick_aligned"] for m in models)


def test_policy_model_shows_what_evidence_count_confluence_does_to_tier_and_eligibility(go, capture):
  cases = go[0]
  adaptable_models = [pr.go_model(c, capture)["policy"] for c in cases if c.match is not None]
  assert {m["tier"] for m in adaptable_models} == {"A"}                       # evidence count 4 -> Tier A for everything
  assert {m["risk_multiplier"] for m in adaptable_models} == {1.0}            # reaction sizing ignores tier
  assert all(m["passes_global_min"] and m["passes_session_min"] for m in adaptable_models)
  rejected = [pr.go_model(c, capture)["policy"] for c in cases if c.match is None]
  assert all(m["adapter_rejection"] == "higher_timeframe_bias_unavailable" and "tier" not in m for m in rejected)


def test_findings_answer_the_five_questions_from_the_numbers(go, capture):
  case = adaptable(go[0])[0]
  report = pr.compare([mirror(case)], go[0], capture, opposing=False)
  findings = report["findings"]
  assert set(findings) == {"confirmation", "confluence", "htf", "atr", "geometry"}
  assert findings["confirmation"]["extra_in_go"] == len(go[0]) - 1 and findings["confirmation"]["missed_by_go"] == 0
  assert findings["htf"]["timeframe_used"].get("H1", 0) + findings["htf"]["timeframe_used"].get("none", 0) == len(go[0])
  assert findings["atr"]["go_reported_equals_configured_simple"] == {"agree": len(go[0]), "total": len(go[0]), "rate": 1.0}
  assert len(report["go_cases"]) == len(go[0]) and sum(1 for g in report["go_cases"] if g["matched"]) == 1
  assert "Findings on the five S14C questions" in pr.render_markdown(report)


# ---- committed evidence stays reproducible -------------------------------------------------------------

PY_OBS = REPLAY_DIR / "python-observations-xau-20260921.jsonl"
COMMITTED_REPORT = REPLAY_DIR.parents[2] / "docs" / "analysis" / "reports" / "s14c-policy-replay-xau-20260921.json"


def test_committed_python_observations_are_causal_and_bound_to_the_capture(capture):
  observations = pr.read_python_observations(PY_OBS)
  meta = json.loads((REPLAY_DIR / "python-observations-xau-20260921.jsonl.meta.json").read_text())
  assert meta["capture_sha256"] == capture.sha256 and meta["stride"] == 1 and len(observations) == meta["observations"]
  assert meta["detectors"] == ["supply_demand_technique_reaction"]
  for obs in observations:
    assert obs.confirmation_bar_ts is not None and obs.confirmation_bar_ts + 300 <= obs.detected_at_close, obs.id   # no look-ahead
    assert obs.detected_at_close <= meta["window_close_at"][1] and obs.scope in ("supply", "demand")


def test_committed_report_is_what_the_committed_inputs_produce(go, capture):
  committed = json.loads(COMMITTED_REPORT.read_text())
  observations = pr.read_python_observations(PY_OBS)
  fresh = pr.compare(observations, go[0], capture, skipped=go[1], python_source=committed["inputs"]["python"]["source"],
                     go_file_sha256=committed["inputs"]["go"]["file_sha256"], python_window=tuple(committed["inputs"]["python"]["window_close_at"]))
  assert committed["inputs"]["capture"]["sha256"] == capture.sha256
  for key in ("matching", "coverage", "verdict", "gates", "go_adaptation", "htf_relations"):
    assert fresh[key] == committed[key], key
  assert fresh["agreement"] == committed["agreement"]
  assert committed["verdict"] == "unresolved_differences"


def test_python_replay_reproduces_the_committed_observations_in_a_window(capture):
  """Re-run the live technique detector on a few real bars and expect exactly the
  committed observations detected there (heavy: ~0.3 s per bar)."""
  committed = pr.read_python_observations(PY_OBS)
  target = committed[len(committed) // 2]
  first, last = target.detected_at_close - 6 * 300, target.detected_at_close + 2 * 300
  replay = pr.replay_python(capture, first_close_at=first, last_close_at=last)
  again = pr.replay_python(capture, first_close_at=first, last_close_at=last)
  assert replay == again                                                # deterministic
  assert replay.evaluated == 9 and replay.warmup_skipped == 0
  expected = [o for o in committed if first <= o.detected_at_close <= last]
  assert [o.id for o in replay.observations] == [o.id for o in expected]
  assert target.id in {o.id for o in replay.observations}


def test_python_replay_skips_closes_before_a_live_sized_window(capture):
  m5 = capture.frames["M5"]
  early = int(m5.index[10].timestamp()) + 300
  replay = pr.replay_python(capture, first_close_at=early, last_close_at=early + 600)
  assert replay.evaluated == 0 and replay.warmup_skipped == 3 and replay.observations == []
