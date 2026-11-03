"""v2.16 key levels: the Python port against the Go keylevel package, and the Pine algorithm (simplified clustering, stride arrays) against the port.

The expected list comes from the committed golden tradingview/reference/fixtures/keylevel-golden-scalpwindow-m5.json, which analysis-engine/test/keylevelgolden prints
from keylevel.Cluster / keylevel.Role on the same bars (and which that Go test keeps honest).

Run: python tradingview/reference/test_keylevel_v216.py     (also prints the measurement the PR reports)
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import keylevel_model as kl
import pa_model as m
import report_v213 as rep

HERE = os.path.dirname(__file__)
GOLDEN = json.load(open(os.path.join(HERE, "fixtures", "keylevel-golden-scalpwindow-m5.json")))
SCALP = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
T, O, H, L, C = rep.bars_from(SCALP, "M5")


def model_levels(atr=None):
  atr = GOLDEN["atr"] if atr is None else atr
  swings = kl.window_swings(H, L, GOLDEN["window"])
  return swings, kl.cluster([p for _, p, _ in swings], atr, GOLDEN["cluster_atr"], GOLDEN["round_step"], GOLDEN["minimum_touches"], GOLDEN["maximum_cluster_span_multiple"])


def test_the_model_atr_equals_the_engine_atr_of_the_golden():
  # the engine's ATR is the plain 14-bar mean of the true range, so the layer uses ta.sma(ta.tr, 14); Wilder's ta.atr would be about 3 % smaller here
  assert abs(kl.simple_atr(H, L, C) - GOLDEN["atr"]) < 1e-9
  wilder = [x for x in m.rma_atr(H, L, C) if not m.isna(x)][-1]
  assert 0.02 < 1 - wilder / GOLDEN["atr"] < 0.05, wilder
  assert T[-1] == GOLDEN["last_bar_time"]


def test_raw_fractals_of_the_window_equal_the_go_swings():
  swings, _ = model_levels()
  got = [(i, T[i], k, p) for i, p, k in swings]
  want = [(s["bar"], s["time"], s["kind"], s["price"]) for s in GOLDEN["swings"]]
  assert got == want and len(got) > 30


def test_the_fractals_are_exactly_what_ta_pivothigh_and_pivotlow_return():
  # the Pine ring is filled from ta.pivothigh / ta.pivotlow (2, 2); pa_model.pivot_* mirror them, the Go helper's rule is left strict / right not higher
  for i in range(len(H)):
    ph, pl = m.pivot_high(H, i, 2), m.pivot_low(L, i, 2)
    c = i - 2
    assert (not m.isna(ph)) == (c >= 2 and any(b == c and k == "high" for b, _, k in kl.window_swings(H, L, len(H)))), (i, "high")
    assert (not m.isna(pl)) == (c >= 2 and any(b == c and k == "low" for b, _, k in kl.window_swings(H, L, len(H)))), (i, "low")
    if i > 400:
      break


def test_levels_and_touches_equal_what_keylevel_cluster_returns():
  _, levels = model_levels()
  want = GOLDEN["levels"]
  assert len(levels) == len(want) == 11, (len(levels), len(want))
  for a, b in zip(levels, want):
    assert abs(a["price"] - b["price"]) < 1e-9 and a["kind"] == b["kind"] and a["touches"] == b["touches"] and abs(a["band"] - b["band"]) < 1e-9, (a, b)


def test_roles_equal_what_keylevel_role_returns():
  _, levels = model_levels()
  window_closes = C[-GOLDEN["window"]:]
  roles = []
  for a, b in zip(levels, GOLDEN["levels"]):
    band = max(a["band"], GOLDEN["atr"] * GOLDEN["cluster_atr"])
    got = kl.role(a["kind"], a["price"] - band, a["price"] + band, window_closes, GOLDEN["breakout_accept_bars"])
    assert got == b["role"], (a, got, b["role"])
    roles.append(got)
  assert "ambiguous" in roles and "broken_support" in roles


def test_round_levels_appear_only_with_two_swings():
  atr = 4.0
  assert kl.round_levels([3921.0], atr, 5.0, 2.0, 2) == []
  one_near = kl.round_levels([3919.0, 3931.0], atr, 5.0, 2.0, 2)
  assert one_near == [], "a swing 1 $ from 3920 and none near 3930 is not a round level"
  two = kl.round_levels([3919.5, 3920.8, 3931.0], atr, 5.0, 2.0, 2)
  assert [x["price"] for x in two] == [3920.0] and two[0]["touches"] == 2
  assert kl.round_levels([3920.0, 3920.2], atr, 0.0, 2.0, 2) == [], "step 0 = off"
  assert all(x["touches"] >= 2 for x in model_levels()[1] if x["kind"] == "round")


def test_the_role_turns_broken_after_exactly_two_accepted_closes():
  lo, hi = 99.0, 101.0
  assert kl.role("reaction", lo, hi, [100.0, 98.0], 2) == "ambiguous", "one close beyond the band"
  assert kl.role("reaction", lo, hi, [100.0, 98.0, 98.5], 2) == "broken_support", "two consecutive closes below"
  assert kl.role("reaction", lo, hi, [98.0, 100.0, 98.0], 2) == "ambiguous", "an inside close resets the count"
  assert kl.role("reaction", lo, hi, [100.0, 102.0, 103.0], 2) == "broken_resistance"
  assert kl.role("reaction", lo, hi, [102.0, 103.0, 100.0], 2) == "ambiguous", "the count runs from the LAST bar"
  # the Pine one-directional run agrees with the engine role everywhere on the capture's levels
  closes = C[-GOLDEN["window"]:]
  tol = GOLDEN["atr"] * 0.5
  for lv in model_levels()[1]:
    engine = kl.role(lv["kind"], lv["price"] - tol, lv["price"] + tol, closes, 2).startswith("broken")
    assert kl.broken(lv["price"], closes[-1], closes, tol) == engine


def test_the_pine_algorithm_equals_the_port_on_the_capture():
  pairs = [(i, p) for i, p, _ in kl.window_swings(H, L, GOLDEN["window"])]
  pine = kl.pine_levels(pairs, GOLDEN["atr"], GOLDEN["round_step"], len(H) - GOLDEN["window"] + 2)
  _, levels = model_levels()
  assert len(pine) == len(levels)
  for (p, t, b, k), lv in zip(pine, levels):
    assert abs(p - lv["price"]) < 1e-9 and t == lv["touches"] and ("round" if k else "reaction") == lv["kind"], ((p, t, k), lv)


def test_the_pine_algorithm_equals_the_port_on_random_swing_sets():
  rng = random.Random(7)
  for trial in range(400):
    atr = rng.uniform(0.5, 6.0)
    n = rng.randint(0, 40)
    base = rng.choice([3900.0, 101.0, 1.08])
    spread = atr * rng.uniform(2, 30)
    pairs = [(i + 3, base + rng.uniform(0, spread)) for i in range(n)]
    step = rng.choice([0.0, 5.0])
    port = kl.cluster([p for _, p in pairs], atr, 0.5, step, 2, 2.0)
    pine = kl.pine_levels(pairs, atr, step, 0)
    assert len(port) == len(pine), (trial, len(port), len(pine))
    for lv, (p, t, b, k) in zip(port, pine):
      assert abs(lv["price"] - p) < 1e-9 and lv["touches"] == t and lv["kind"] == ("round" if k else "reaction"), (trial, lv, (p, t, k))


def test_default_window_follows_the_engine_detection_windows():
  assert [kl.default_window(s) for s in (60, 300, 900, 1800, 3600, 14400)] == [150, 150, 250, 400, 400, 400]


def test_display_rules_nearest_first_per_side_and_yield_to_stronger_objects():
  closes = [100.0] * 10
  tol = 1.0
  levels = [(100 + d, 3, 0, 0) for d in (-9, -5, -3, -1.5, 1.5, 3, 5, 9)]
  # (every level further than the band from the closes is "broken" for the engine, so the ordering rules are checked with broken levels shown)
  drawn, folded = kl.select(levels, 100.0, closes, tol, 2, [], show_broken=True)
  assert [lv[0] for lv in drawn] == [98.5, 101.5, 97.0, 103.0] and not folded
  drawn, folded = kl.select(levels, 100.0, closes, tol, 2, [101.2], show_broken=True)
  assert [lv[0] for lv in folded] == [101.5] and 101.5 not in [lv[0] for lv in drawn] and len(drawn) == 3
  assert len(kl.select(levels, 100.0, closes, tol, 1, [], show_broken=True)[0]) == 2
  assert len(kl.select(levels, 100.0, closes, tol, 5, [], scale_ok=lambda p: abs(p - 100) < 4, show_broken=True)[0]) == 4, "candle-scale window"
  # a level above price with the last two closes below its band is broken and hidden unless shown
  broken_closes = [100.0, 95.0, 95.0]
  assert kl.select([(110.0, 3, 0, 0)], 95.0, broken_closes, tol, 3, [])[0] == []
  assert kl.select([(110.0, 3, 0, 0)], 95.0, broken_closes, tol, 3, [], show_broken=True)[0] == [(110.0, 3, 0, 0)]
  # one close inside the band keeps it
  assert kl.select([(95.5, 3, 0, 0)], 95.0, [100.0, 95.0, 95.2], tol, 3, [])[0] == [(95.5, 3, 0, 0)]


def measurement():
  """What the PR reports: the key levels of the capture, how they differ from what the live strategy reads, and the drawn / folded split at the last bar."""
  _, levels = model_levels()
  strat = GOLDEN["strategy_levels"]
  tol = GOLDEN["atr"] * 0.5
  near = sum(1 for a in levels if any(abs(a["price"] - b["price"]) <= tol and a["kind"] == b["kind"] for b in strat))
  closes = C[-GOLDEN["window"]:]
  brokens = sum(1 for lv in levels if kl.broken(lv["price"], closes[-1], closes, tol))
  pine = [(lv["price"], lv["touches"], 0, 1 if lv["kind"] == "round" else 0) for lv in levels]
  drawn, folded = kl.select(pine, closes[-1], closes, tol, 3, [])
  return dict(levels=len(levels), strategy_levels=len(strat), same_within_tol_and_kind=near, broken=brokens, drawn_no_objects=len(drawn), folded_no_objects=len(folded),
              reaction=sum(1 for lv in levels if lv["kind"] == "reaction"), round=sum(1 for lv in levels if lv["kind"] == "round"))


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)
  print(json.dumps(measurement()))
