"""v2.16.1 key levels: the definition the live key_level strategy reads (LegacyFrame.SwingLevels), and a display that never hides a level for being "broken".

Parity (blocking): the zigzag swings and the levels equal what analysis-engine/test/keylevelgolden prints from techniquezone.FindSwings / KeyLevels on the committed
3 Nov XAU M5 capture (GOLDEN["strategy_swings"], GOLDEN["strategy_levels"]); the Pine algorithm equals the model on the capture and on 200 random bar sets. The v2.16
keylevel.Cluster port stays tested in test_keylevel_v216.py as a second, labelled reference. It is not what is drawn.

Run: python tradingview/reference/test_keylevel_v2161.py     (also prints the report table)
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import keylevel_model as kl
import pa_model as m
import report_v213 as rep
import trade_model as tm

HERE = os.path.dirname(__file__)
GOLDEN = json.load(open(os.path.join(HERE, "fixtures", "keylevel-golden-scalpwindow-m5.json")))
SCALP = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
T, O, H, L, C = rep.bars_from(SCALP, "M5")
STEP = GOLDEN["round_step"]


def test_the_zigzag_swings_equal_the_go_swings():
  r = kl.swing_levels(H, L, C, GOLDEN["window"], STEP)
  got = [(i, p, k) for i, p, k in r["swings"]]
  want = [(s["bar"], s["price"], s["kind"]) for s in GOLDEN["strategy_swings"]]
  assert got == want and len(got) == 36
  assert len(GOLDEN["swings"]) > len(want), "the zigzag filter drops same-kind fractals"


def test_the_levels_equal_what_the_strategy_reads():
  r = kl.swing_levels(H, L, C, GOLDEN["window"], STEP)
  want = GOLDEN["strategy_levels"]
  assert len(r["levels"]) == len(want) == 11
  for a, b in zip(r["levels"], want):
    assert abs(a["price"] - b["price"]) < 1e-9 and a["kind"] == b["kind"] and a["touches"] == b["touches"] and abs(a["band"] - b["band"]) < 1e-9, (a, b)
  assert abs(r["median_atr"] - GOLDEN["median_atr_of_window"]) < 1e-9


def test_the_window_atr_series_is_the_engines():
  series = kl.atr_series(H[-150:], L[-150:], C[-150:])
  assert abs(series[0] - (H[-150] - L[-150])) < 1e-12, "the first bar of the window has no previous close"
  assert abs(series[5] - sum(max(H[-150 + k] - L[-150 + k], abs(H[-150 + k] - C[-151 + k]), abs(L[-150 + k] - C[-151 + k])) if k else H[-150] - L[-150] for k in range(6)) / 6) < 1e-12
  assert abs(kl.atr_scalar([3.0, 1.0, float("nan"), 2.0]) - 2.0) < 1e-12 and kl.atr_scalar([float("nan")]) == 1.0 and kl.atr_scalar([0.0, 0.0]) == 1.0


def test_the_zigzag_rules():
  atr = [1.0] * 10
  cands = [(2, 10.0, "high"), (3, 12.0, "high"), (4, 9.0, "low"), (5, 8.5, "low"), (6, 9.2, "high"), (7, 12.5, "high")]
  assert kl.zigzag(cands, atr) == [(3, 12.0, "high"), (5, 8.5, "low"), (7, 12.5, "high")], "a same-kind candidate replaces the last only when more extreme; 9.2 is under 1 ATR from 8.5? no: 0.7 < 1"
  assert kl.zigzag([(2, 10.0, "high"), (3, 10.5, "low")], atr) == [(2, 10.0, "high")], "a move under 1 ATR is dropped"


def random_bars(rng, n):
  price, h, l, c = rng.choice([100.0, 3900.0]), [], [], []
  scale = price * 0.002
  for _ in range(n):
    op = price
    price = max(1.0, price + rng.gauss(0, scale))
    h.append(max(op, price) + abs(rng.gauss(0, scale * 0.5)) + 1e-6)
    l.append(min(op, price) - abs(rng.gauss(0, scale * 0.5)) - 1e-6)
    c.append(price)
  return h, l, c


def test_the_pine_algorithm_equals_the_model_on_the_capture_and_on_random_bars():
  fin, sw = kl.pine_swing_levels(H, L, C, GOLDEN["window"], STEP)
  r = kl.swing_levels(H, L, C, GOLDEN["window"], STEP)
  assert len(fin) == len(r["levels"]) == 11
  for (p, t, k), lv in zip(fin, r["levels"]):
    assert abs(p - lv["price"]) < 1e-9 and t == lv["touches"] and ("round" if k else "reaction") == lv["kind"]
  for h, l, c, w in ((H, L, C, 150), (*rep.bars_from(SCALP, "M15")[2:5], 250), (*rep.bars_from(SCALP, "H1")[2:5], 400)):
    a, _ = kl.pine_swing_levels(h, l, c, w, 5.0)
    b = kl.swing_levels(h, l, c, w, 5.0)["levels"]
    assert [(round(x[0], 9), x[1]) for x in a] == [(round(y["price"], 9), y["touches"]) for y in b], w
  rng = random.Random(11)
  nonempty = 0
  for trial in range(200):
    h, l, c = random_bars(rng, rng.randint(30, 320))
    window, step = rng.choice([30, 80, 150, 250]), rng.choice([0.0, 5.0])
    a, _ = kl.pine_swing_levels(h, l, c, window, step)
    b = kl.swing_levels(h, l, c, window, step)["levels"]
    assert len(a) == len(b), (trial, len(a), len(b))
    for x, y in zip(a, b):
      assert abs(x[0] - y["price"]) < 1e-9 and x[1] == y["touches"] and ("round" if x[2] else "reaction") == y["kind"], (trial, x, y)
    nonempty += 1 if b else 0
  assert nonempty > 60, nonempty


def golden_view(per_side=3, labels=()):
  r = kl.swing_levels(H, L, C, GOLDEN["window"], STEP)
  chosen = kl.display(r["levels"], r["swings"], C[-150:], r["tol"], per_side)
  return r, chosen, kl.captions(chosen, labels, 0.5)


def test_with_no_other_objects_min_3_levels_per_side_are_drawn_and_none_is_hidden_for_being_broken():
  r, chosen, _ = golden_view()
  last = C[-1]
  above = sum(1 for x in r["levels"] if x["price"] > last)
  below = sum(1 for x in r["levels"] if x["price"] <= last)
  assert len(chosen) == min(3, above) + min(3, below) == 3, (above, below, len(chosen))
  assert [x["price"] for x in chosen] == sorted((x["price"] for x in chosen), key=lambda p: abs(p - last))
  # v2.16 hid every "broken" level: the engine Role marks 10 of the 11, the display must not
  broken = sum(1 for x in r["levels"] if kl.broken(x["price"], last, C[-150:], r["tol"]))
  assert broken >= 9 and len(chosen) > 1, (broken, len(chosen))
  assert len(kl.display(r["levels"], r["swings"], C[-150:], r["tol"], 11)) == len(r["levels"]), "every level can be shown"


def test_3917_is_in_play_and_the_others_are_not():
  r, chosen, _ = golden_view(per_side=11)
  play = [x for x in chosen if x["in_play"]]
  assert [round(x["price"], 2) for x in play] == [3917.18], play
  assert abs(play[0]["price"] - 3917.183) < 1e-3 and kl.role("reaction", play[0]["price"] - r_tol(), play[0]["price"] + r_tol(), C[-150:], 2) == "ambiguous"


def r_tol():
  return GOLDEN["strategy_levels"][0]["band"]


def test_side_origin_and_flipped():
  swings = [(0, 100.0, "low"), (5, 100.2, "low"), (9, 100.1, "low"), (12, 99.9, "high")]
  assert kl.origin(100.0, swings, 0.5) == (1, 3, 0)
  lv = [dict(price=100.0, kind="reaction", touches=4, band=0.5)]
  up = kl.display(lv, swings, [98.0, 98.0, 98.0], 0.5, 3)[0]
  assert up["side"] == "resistance" and up["formed"] == "support" and up["flipped"], "formed as support, now above price"
  down = kl.display(lv, swings, [102.0, 102.0, 102.0], 0.5, 3)[0]
  assert down["side"] == "support" and not down["flipped"]
  both = kl.display(lv, [(0, 100.0, "low"), (3, 100.1, "high")], [98.0] * 3, 0.5, 3)[0]
  assert both["formed"] == "both" and not both["flipped"]
  inside = kl.display(lv, swings, [101.0, 99.9, 100.2], 0.5, 3)[0]
  assert inside["in_play"], "the last 2 closes are not both beyond the band"
  assert not kl.display(lv, swings, [101.0, 98.0, 98.0], 0.5, 3)[0]["in_play"]


def test_only_the_caption_yields_to_a_stronger_label():
  r, chosen, _ = golden_view()
  first = chosen[0]["price"]
  shown, folded = kl.captions(chosen, [first + 0.3], 0.5)
  assert len(chosen) == 3 and len(shown) + len(folded) == 3, "every selected level keeps its line"
  assert [lv["price"] for _, lv in folded] == [first] and first not in [lv["price"] for _, lv in shown]
  assert all("R " not in text for text, _ in shown + folded), "never `R ...`"
  text = dict((lv["price"], t) for t, lv in kl.captions(chosen, [], 0.5)[0])
  assert text[first].endswith("●") and text[first].startswith("KL×3") and all(not t.endswith("●") for p, t in text.items() if p != first)
  rnd = kl.captions([dict(price=3940.17, kind="round", touches=6, side="resistance", formed="both", flipped=False, in_play=False, first=0)], [], 0.5)[0][0][0]
  assert rnd == "KL 3940", rnd


def measurement():
  """The PR's report table: per timeframe of the 3 Nov capture, the levels found, lines drawn, captions shown, captions folded into a stronger label, in play.
  The stronger labels are the protected level, EQ, every active pool edge and every qualified POI zone midpoint of the model at the last bar (an upper bound: the chart
  draws only the visible ones)."""
  rows = []
  for tf, secs, window in (("M5", 300, 150), ("M15", 900, 250), ("H1", 3600, 400)):
    bars = rep.bars_from(SCALP, tf)
    h, l, c = bars[2], bars[3], bars[4]
    r = kl.swing_levels(h, l, c, window, 5.0)
    closes = c[-window:]
    chosen = kl.display(r["levels"], r["swings"], closes, r["tol"], 3)
    sim = tm.TradePlanSim(bars, m.v213(secs), tm.v213_trade()).run()
    s = sim.s
    hi, lo, mid = s.dealing_range()
    prot = s.protected_high if s.bias == -1 else s.protected_low if s.bias == 1 else m.NAN
    labels = [x for x in (prot, mid) if not m.isna(x)]
    labels += [x.top if x.side == 1 else x.bottom for x in sim.lm.pools if x.state == 0]
    labels += [(z.top + z.bottom) / 2 for z in sim.zones if sim.poi_path(z, not z.touched) != "Candidate"]
    atr = s.atr[-1]
    gap = max(0.3 * atr, (max(h[-300:]) - min(l[-300:])) / 36.0)
    shown, folded = kl.captions(chosen, labels, gap)
    rows.append(dict(tf=tf, window=window, levels=len(r["levels"]), lines=len(chosen), captions=len(shown), folded=len(folded), in_play=sum(1 for x in chosen if x["in_play"]),
                     flipped=sum(1 for x in chosen if x["flipped"]), broken_by_engine_role=sum(1 for x in r["levels"] if kl.broken(x["price"], c[-1], closes, r["tol"]))))
  return rows


if __name__ == "__main__":
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)
  print()
  for row in measurement():
    print(json.dumps(row))
