"""v2.17 key levels: one code path, two definitions (Engine key_level v6 = LegacyFrame.SwingLevels, Selective v7 preview), a display that never hides a level for being
"broken", and a selectivity readout. See keylevel_model.py (the v2.17 section) for the model and the PR for the decisions.

Parity (blocking):
  * Engine (v6) equals GOLDEN["strategy_levels"] of fixtures/keylevel-golden-scalpwindow-m5.json (11 levels, price / kind / touches / band).
  * Selective (v7) equals fixtures/keylevel-golden-v7-scalpwindow-m5.json, produced by this model with the v7 parameters (`python test_keylevel_v217.py --write-golden`).
    Engine PR-K2 has to reproduce that file.
  * The Pine algorithm (strides, simplified join) equals the model on windows of every committed capture (M5 / M15 / H1) and on random bars, in both modes.

Run: python tradingview/reference/test_keylevel_v217.py     (also prints the per-timeframe report)
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import keylevel_model as kl
import pa_model as m
import report_v213 as rep
import pipeline_model as pl

HERE = os.path.dirname(__file__)
GOLDEN_V6 = json.load(open(os.path.join(HERE, "fixtures", "keylevel-golden-scalpwindow-m5.json")))
V7_PATH = os.path.join(HERE, "fixtures", "keylevel-golden-v7-scalpwindow-m5.json")
SCALP = [p for n, p in rep.FIXTURES if n == "scalpwindow-1103"][0]
T, O, H, L, C = rep.bars_from(SCALP, "M5")
WINDOW = GOLDEN_V6["window"]


def v7_golden_document():
  r = kl.definition_levels(H, L, C, WINDOW, kl.V7)
  return dict(capture=GOLDEN_V6["capture"], timeframe=GOLDEN_V6["timeframe"], window=WINDOW, last_bar_time=GOLDEN_V6["last_bar_time"], last_close=C[-1],
              definition=dict(name="Selective (v7 preview)", cluster_atr=kl.V7["cluster_atr"], round_step=kl.V7["step"], merge=kl.V7["merge"], minimum_touches=2,
                              maximum_cluster_span_multiple=2.0, fractal_n=2, zigzag_atr_mult=1.0),
              median_atr_of_window=r["median_atr"], tolerance=r["tol"], coverage=r["coverage"],
              swings=[dict(bar=i, kind=k, price=p) for i, p, k in r["swings"]],
              levels=[dict(price=v["price"], kind=v["kind"], touches=v["touches"], band=v["band"], newest_member_bar=v["newest"] + r["offset"], origin=v["origin"]) for v in r["levels"]])


def test_engine_v6_equals_the_strategy_levels_of_the_golden():
  r = kl.definition_levels(H, L, C, WINDOW, kl.V6)
  want = GOLDEN_V6["strategy_levels"]
  assert len(r["levels"]) == len(want) == 11
  for a, b in zip(r["levels"], want):
    assert abs(a["price"] - b["price"]) < 1e-9 and a["kind"] == b["kind"] and a["touches"] == b["touches"] and abs(a["band"] - b["band"]) < 1e-9, (a, b)
  assert abs(r["median_atr"] - GOLDEN_V6["median_atr_of_window"]) < 1e-9
  assert [(i, p, k) for i, p, k in r["swings"]] == [(s["bar"], s["price"], s["kind"]) for s in GOLDEN_V6["strategy_swings"]]


def test_selective_v7_equals_its_committed_golden():
  want = json.load(open(V7_PATH))
  got = v7_golden_document()
  assert json.loads(json.dumps(got)) == want, "regenerate with --write-golden only if the v7 definition was changed on purpose (engine PR-K2 reproduces this file)"
  assert want["definition"]["cluster_atr"] == 0.25 and want["definition"]["round_step"] == 10.0


def test_v7_differs_from_v6_only_in_the_four_parameters():
  a, b = kl.definition_levels(H, L, C, WINDOW, kl.V6), kl.definition_levels(H, L, C, WINDOW, kl.V7)
  assert a["swings"] == b["swings"], "the ATR series, fractals and zigzag are shared"
  assert abs(b["tol"] - a["tol"] / 2) < 1e-12
  assert len(b["levels"]) < len(a["levels"]) and b["coverage"] < a["coverage"] / 1.5, "the selective definition halves coverage"
  for v in b["levels"]:
    assert v["kind"] == "reaction" or abs(v["price"] / 10 - round(v["price"] / 10)) < 1e-9, "a round level sits on the $10 grid, exactly (no averaging)"


def test_the_v7_merge_keeps_the_stronger_members_exact_price():
  tol = 1.0
  base = dict(band=tol, newest=0)
  # the strength order: touches, then reaction before round, then the newest member
  assert kl.strength(dict(base, touches=3, kind="round", newest=5)) > kl.strength(dict(base, touches=2, kind="reaction", newest=50))
  assert kl.strength(dict(base, touches=2, kind="reaction", newest=1)) > kl.strength(dict(base, touches=2, kind="round", newest=50))
  assert kl.strength(dict(base, touches=2, kind="reaction", newest=9)) > kl.strength(dict(base, touches=2, kind="reaction", newest=3))
  lv = lambda price, touches, kind, newest: dict(price=price, touches=touches, kind=kind, band=tol, newest=newest)
  a, b, c = lv(100.0, 3, "reaction", 10), lv(100.4, 3, "reaction", 20), lv(100.7, 2, "round", 30)
  avg = kl.merge_levels([a, b, c], tol, "average")
  assert len(avg) == 1 and abs(avg[0]["price"] - (((100.0 + 100.4) / 2) + 100.7) / 2) < 1e-12, "v6 averages, and the average drifts"
  best = kl.merge_levels([a, b, c], tol, "strongest")
  assert [(v["price"], v["touches"], v["kind"]) for v in best] == [(100.4, 3, "reaction")], "equal touches, both reaction: the newer member wins, the exact price is kept"
  assert kl.merge_levels([lv(100.0, 2, "round", 5), lv(100.5, 2, "reaction", 1)], tol, "strongest")[0]["price"] == 100.5, "reaction before round at equal touches"
  assert kl.merge_levels([lv(100.0, 4, "round", 5), lv(100.5, 2, "reaction", 99)], tol, "strongest")[0]["price"] == 100.0, "more touches first"
  assert len(kl.merge_levels([lv(100.0, 2, "reaction", 1), lv(101.5, 2, "reaction", 1)], tol, "strongest")) == 2, "outside tol both stay"


def test_the_window_atr_series_is_the_engines():
  series = kl.atr_series(H[-WINDOW:], L[-WINDOW:], C[-WINDOW:])
  assert abs(series[0] - (H[-WINDOW] - L[-WINDOW])) < 1e-12, "the first bar of the window has no previous close"
  assert kl.atr_scalar([3.0, 1.0, float("nan"), 2.0]) == 2.0


def captures():
  out = []
  for name, path in rep.FIXTURES:
    for tf, window in (("M5", 150), ("M15", 250), ("H1", 400)):
      try:
        bars = rep.bars_from(path, tf)
      except KeyError:
        continue
      out.append((name, tf, window, bars[2], bars[3], bars[4]))
  return out


def _same(h, l, c, window, step, mode, v7):
  a, _ = kl.pine_definition_levels(h, l, c, window, step, v7)
  b = kl.definition_levels(h, l, c, window, mode, step)["levels"]
  assert len(a) == len(b), (len(a), len(b), window, step, v7)
  for x, y in zip(a, b):
    assert abs(x[0] - y["price"]) < 1e-9 and x[1] == y["touches"] and ("round" if x[2] else "reaction") == y["kind"] and x[3] == y["newest"], (x, y)
  return len(b)


def test_the_pine_algorithm_equals_the_model_on_200_random_windows_of_the_captures():
  rng, caps, nonempty = random.Random(17), captures(), 0
  assert {tf for _, tf, *_ in caps} == {"M5", "M15", "H1"}
  for trial in range(200):
    name, tf, window, h, l, c = rng.choice(caps)
    end = rng.randint(window + 5, len(h))
    for mode, v7 in ((kl.V6, False), (kl.V7, True)):
      nonempty += 1 if _same(h[:end], l[:end], c[:end], window, mode["step"], mode, v7) else 0
  assert nonempty > 150, nonempty


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


def test_the_pine_algorithm_equals_the_model_on_random_bars():
  rng, nonempty = random.Random(23), 0
  for trial in range(200):
    h, l, c = random_bars(rng, rng.randint(30, 320))
    mode, v7 = rng.choice([(kl.V6, False), (kl.V7, True)])
    nonempty += 1 if _same(h, l, c, rng.choice([30, 80, 150, 250]), rng.choice([0.0, mode["step"]]), mode, v7) else 0
  assert nonempty > 60, nonempty


def golden_view(mode, per_side=3):
  r = kl.definition_levels(H, L, C, WINDOW, mode)
  return r, kl.display217(r, C[-WINDOW:], per_side, mode)


def test_with_no_other_objects_exactly_min_3_per_side_lines_and_none_is_hidden_for_being_broken():
  for mode in (kl.V6, kl.V7):
    r, chosen = golden_view(mode)
    last = C[-1]
    above = sum(1 for x in r["levels"] if x["price"] > last)
    below = sum(1 for x in r["levels"] if x["price"] <= last)
    assert len(chosen) == min(3, above) + min(3, below) and len(chosen) > 0, (mode["name"], above, below, len(chosen))
    assert [x["price"] for x in chosen] == sorted((x["price"] for x in chosen), key=lambda p: abs(p - last))
    assert len(kl.display217(r, C[-WINDOW:], 99, mode)) == len(r["levels"]), "every level can be shown"
    broken = sum(1 for x in r["levels"] if kl.broken(x["price"], last, C[-WINDOW:], r["tol"]))
    assert broken >= 7 and len(chosen) >= 2, "most levels are broken by the engine Role (v2.16 hid them); the display must not"
  assert C[-1] < min(x["price"] for x in golden_view(kl.V6)[0]["levels"]), "on the golden the last close is under all 11 levels: three lines, all resistance"


def test_3917_is_in_play_in_v6():
  r, chosen = golden_view(kl.V6, per_side=11)
  assert [round(x["price"], 2) for x in chosen if x["in_play"]] == [3917.18]
  tip = kl.tooltip([x for x in chosen if x["in_play"]][0], r["tol"], kl.V6, WINDOW, r["coverage"])
  assert "in play: price is still working this level" in tip


def test_flipped_levels_are_drawn_faint_and_v7_does_not_call_them_tradable():
  mode = kl.V7
  tol = 1.0
  base = dict(price=100.0, kind="reaction", touches=3, band=tol, newest=5, origin="support")
  res = dict(levels=[base], tol=tol)
  below = kl.display217(res, [102.0, 102.0, 102.0], 2, mode)[0]
  assert not below["flipped"] and below["side"] == "support" and below["transparency"] == 74
  above = kl.display217(res, [98.0, 98.0, 98.0], 2, mode)[0]
  assert above["flipped"] and above["side"] == "resistance" and above["transparency"] == 88 and not above["in_play"]
  tip = kl.tooltip(above, tol, mode, 150, 0.3)
  assert "formed as support → flipped" in tip and "Break & Retest territory; key_level v7 skips it" in tip
  v6 = kl.display217(res, [98.0, 98.0, 98.0], 2, kl.V6)[0]
  assert v6["flipped"] and "Break & Retest" not in kl.tooltip(v6, tol, kl.V6, 150, 0.3)
  play = kl.display217(res, [99.5, 99.8, 99.5], 2, kl.V6)[0]
  assert play["in_play"] and play["transparency"] == 45, "in v6 the engine trades a level in play, even a flipped one"
  mixed = kl.display217(dict(levels=[dict(base, origin="mixed")], tol=tol), [98.0] * 3, 2, mode)[0]
  assert not mixed["flipped"]


def test_major_round_levels_are_fainter_in_both_modes():
  tol = 1.0
  lv = dict(price=3950.0, kind="round", touches=4, band=tol, newest=5, origin="support")
  for mode in (kl.V6, kl.V7):
    d = kl.display217(dict(levels=[lv], tol=tol), [3960.0] * 3, 2, mode)[0]
    assert d["major"] and d["transparency"] == 60
  assert not kl.is_major(3945.0, 5.0, True) and not kl.is_major(3950.0, 5.0, False)


def test_a_key_level_caption_starts_with_the_price_of_its_line():
  tol = 1.0
  base = dict(price=3949.12, kind="reaction", touches=3, band=tol, newest=5, origin="support")
  rnd = dict(base, price=3950.0, kind="round", touches=6)
  moved = dict(base, price=3949.12, kind="round", touches=6)
  res = dict(levels=[base, rnd, moved], tol=tol)
  d = kl.display217(res, [3960.0] * 3, 9, kl.V6)
  texts = {(x["kind"], x["price"]): kl.key_text(x) for x in d}
  assert texts[("reaction", 3949.12)] == "3949.12 ×3", texts
  assert texts[("round", 3950.0)] == "3950 ×6", "a round level on its step price shows the step price"
  assert texts[("round", 3949.12)] == "3949.12 ×6", "v6 averaging moved the price: the caption shows the line's price, not the step"
  assert "round 3950, engine price merged" in kl.tooltip([x for x in d if x["price"] == 3949.12 and x["kind"] == "round"][0], tol, kl.V6, 150, 0.3)
  playing = kl.display217(dict(levels=[base], tol=tol), [3949.0, 3949.0, 3949.0], 2, kl.V6)[0]
  assert playing["in_play"] and kl.key_text(playing) == "3949.12 ×3 ●"


def _reg(y, text, cat, tier=1, prio=60):
  return dict(y=y, text=text, cat=cat, tier=tier, prio=prio, tip=text)


def drawn_labels(chosen, others, gap):
  return kl.emit_labels(kl.register_keys(chosen) + others, gap)


def test_every_drawn_key_level_shows_its_price_in_exactly_one_visible_label_text():
  for mode in (kl.V6, kl.V7):
    r, chosen = golden_view(mode, per_side=11)
    gap = 0.8
    for others in ([], [_reg(chosen[0]["price"] + 0.2, "BSL", 1), _reg(chosen[2]["price"] - 0.1, "PROT", 0, prio=80), _reg(chosen[4]["price"], "POI · Bull OB", 3, prio=62)]):
      labels = drawn_labels(chosen, others, gap)
      for lv in chosen:
        price = ("%g" % lv["caption_price"]) if lv["kind"] == "round" and abs(lv["price"] - lv["caption_price"]) < 0.005 else "%.2f" % lv["price"]
        owners = [x for x in labels if price + " ×%d" % lv["touches"] in x["text"]]
        assert len(owners) == 1, (mode["name"], price, [x["text"] for x in labels])
        assert "KL" in owners[0]["text"] and "+" not in owners[0]["text"].split("KL")[1], "a key level is never folded into a +n"
        assert owners[0]["text"].count(price + " ×") == 1


def test_two_key_levels_within_one_gap_make_one_label_listing_both_prices_highest_first():
  r, chosen = golden_view(kl.V6, per_side=11)
  two = [x for x in chosen if round(x["price"], 2) in (3949.12, 3951.42)]
  assert len(two) == 2
  labels = kl.emit_labels(kl.register_keys(two), 3.0)
  assert len(labels) == 1 and labels[0]["text"] == "KL 3951.42 ×3 · 3949.12 ×6", labels[0]["text"]
  apart = kl.emit_labels(kl.register_keys(two), 1.0)
  assert [x["text"] for x in apart] == ["KL 3951.42 ×3", "KL 3949.12 ×6"], "labels farther apart than the gap stay separate"


def test_a_key_level_next_to_a_bsl_label_is_appended_visibly_not_folded():
  r, chosen = golden_view(kl.V6, per_side=11)
  kl3949 = [x for x in chosen if round(x["price"], 2) == 3949.12]
  labels = drawn_labels(kl3949, [_reg(3949.5, "BSL", 1)], 1.0)
  assert len(labels) == 1 and labels[0]["text"] == "BSL · KL 3949.12 ×6", labels[0]["text"]
  assert labels[0]["host"]["text"] == "BSL" and labels[0]["host"]["cat"] == 1, "the host keeps its slot, position and colour"
  assert " ·KL" not in labels[0]["text"] and "+1" not in labels[0]["text"]
  # several members of the host's category still join, other categories still fold to +n, the key level is never counted in that +n
  busy = drawn_labels(kl3949, [_reg(3949.5, "BSL", 1), _reg(3949.4, "EQ 3949.40", 1, prio=35), _reg(3949.3, "PROT", 0, prio=80), _reg(3949.2, "Asia H", 2, prio=40)], 1.0)
  assert len(busy) == 1 and busy[0]["text"] == "PROT +3 · KL 3949.12 ×6", busy[0]["text"]
  # a key level in play keeps the host rule too (tier 2 does not make it the host)
  playing = dict(kl3949[0], in_play=True)
  assert drawn_labels([playing], [_reg(3949.5, "BSL", 1)], 1.0)[0]["text"] == "BSL · KL 3949.12 ×6 ●"


def test_without_another_member_the_key_level_is_the_whole_label_and_in_play_makes_it_tier_2():
  r, chosen = golden_view(kl.V6, per_side=11)
  play = [x for x in chosen if x["in_play"]][0]
  label = kl.emit_labels(kl.register_keys([play]), 1.0)[0]
  assert label["text"] == "KL 3917.18 ×3 ●" and label["host"]["tier"] == 2
  assert [round(x["price"], 2) for x in chosen if x["in_play"]] == [3917.18]


def test_the_panel_has_no_kl_row_and_coverage_lives_in_the_chart_tooltip():
  code = open(PINE, encoding="utf-8").read()
  assert 'f_key(17, "KL")' not in code and "keyInfo" not in code and "rowTxt" not in code
  assert 'string covTxt = "coverage "' in code and "+ covTxt)" in code, "the selectivity readout is kept, in the tooltip of every drawn level"


def test_coverage_is_the_union_of_the_bands_over_the_window_range_and_it_is_worded():
  assert abs(kl.coverage([dict(price=100.0), dict(price=100.5)], 1.0, 90.0, 110.0) - 0.125) < 1e-12, "overlapping bands count once"
  assert kl.coverage([dict(price=50.0)], 1.0, 90.0, 110.0) == 0.0
  assert kl.coverage_text(0.52) == "coverage 52 % of the recent range: low selectivity"
  assert kl.coverage_text(0.25) == "coverage 25 % of the recent range: selective"
  assert kl.coverage_text(0.36) == "coverage 36 % of the recent range"
  a, b = kl.definition_levels(H, L, C, WINDOW, kl.V6)["coverage"], kl.definition_levels(H, L, C, WINDOW, kl.V7)["coverage"]
  assert a > 0.40 > b > 0.25 and abs(a - 0.685) < 0.01 and abs(b - 0.363) < 0.01, (a, b)


def test_the_tooltip_has_the_documented_shape():
  r, chosen = golden_view(kl.V6, per_side=11)
  rnd = [x for x in chosen if x["kind"] == "round"][0]
  tip = kl.tooltip(rnd, r["tol"], kl.V6, WINDOW, r["coverage"])
  assert tip.startswith("Key level %.2f ± 1.95 · round · " % rnd["price"]) and "definition Engine (key_level v6) (SwingLevels, window 150)" in tip
  assert tip.endswith("coverage 69 % of the recent range: low selectivity"), tip


PINE = os.path.join(HERE, "..", "apexvoid_price_action.pine")


def _pine_code():
  import re
  src = open(PINE, encoding="utf-8").read()
  return "\n".join(l.split("//")[0] for l in src.splitlines()), src, re


def test_the_pine_source_carries_the_v217_contract():
  code, src, re = _pine_code()
  assert 'options = ["Engine (key_level v6)", "Selective (v7 preview)"]' in code and 'input.int(3, "Key levels per side"' in code
  start = code.index("f_keyLevels() =>")
  body = code[start:code.index("\nif barstate.islast", start)]
  assert len(body) > 3000 and "ta.pivothigh(" not in body and "ta.pivotlow(" not in body
  assert "Show broken" not in src and "keyRing" not in code and "showBrokenKeys" not in code
  assert "keyV7 ? 0.25 : 0.5" in code and "isGold ? (keyV7 ? 10.0 : 5.0) : 0.0" in code, "the four differing parameters mirror the model's V6 / V7"
  assert (kl.V6["cluster_atr"], kl.V6["step"], kl.V7["cluster_atr"], kl.V7["step"]) == (0.5, 5.0, 0.25, 10.0)
  assert "inPlay ? 45 : flipped ? 88 : major ? 60 : touches >= 4 ? 66 : touches == 3 ? 74 : 82" in code, "line transparency: in play, flipped, major round, then by touches"
  for text in ("in play: price is still working this level", "Break & Retest territory; key_level v7 skips it", "formed as ", " → flipped", "engine price merged", "low selectivity", ": selective", "of the recent range"):
    assert text in src, text
  assert "bool inPlay = run < 2 and not (keyV7 and flipped)" in code
  assert 'str.tostring(array.get(fin, j * 4 + 1), "0") + " touches' in code and "KL×" not in code and '"KL "' in code and '"R "' not in body
  # lines always draw: the only thing that yields to a stronger label is the caption; no zone-edge swallow rule
  assert "carried" not in code and "zoneTops" not in body and "zoneBottoms" not in body, "no rule folds a key level or hides it near another object"
  emit = code[code.index("f_emitCluster("):code.index("f_keyAdd(")]
  assert "topCat != 4" in emit and '"KL " + keyText' in emit and "== 4 ? 1000000.0" in emit, "key levels are appended to the host, never the host, never +n"
  assert ' ×" + str.tostring(touches, "0")' in body
  assert "dashboardDensity" not in code and "Standard\", \"Dashboard" not in src


def measurement():
  """The PR's report: per timeframe of the 3 Nov capture and per definition, the levels found, coverage, lines drawn, key-level labels shown (clusters), prices appended to
  another label, flipped, in play. The other labels are the protected level, EQ, every active pool edge and every qualified POI zone midpoint of the model at the last bar
  (an upper bound: the chart draws only the visible ones)."""
  rows = []
  for tf, secs, window in (("M5", 300, 150), ("M15", 900, 250), ("H1", 3600, 400)):
    bars = rep.bars_from(SCALP, tf)
    h, l, c = bars[2], bars[3], bars[4]
    sim = pl.Pipeline(bars, m.v213(secs)).run()
    s = sim.s
    hi, lo, mid = s.dealing_range()
    prot = s.protected_high if s.bias == -1 else s.protected_low if s.bias == 1 else m.NAN
    others = []
    if not m.isna(prot):
      others.append(_reg(prot, "PROT", 0, prio=80))
    if not m.isna(mid):
      others.append(_reg(mid, "EQ", 1, prio=35))
    others += [_reg(x.top if x.side == 1 else x.bottom, "BSL", 1) for x in sim.lm.pools if x.state == 0]
    others += [_reg((z.top + z.bottom) / 2, "POI", 3, prio=62) for z in sim.zones if sim.poi_path(z, not z.touched) != "Candidate"]
    gap = max(0.3 * s.atr[-1], (max(h[-300:]) - min(l[-300:])) / 36.0)
    for mode in (kl.V6, kl.V7):
      r = kl.definition_levels(h, l, c, window, mode)
      every, chosen = kl.levels217(r, c[-window:], 3, mode)
      labels = drawn_labels(chosen, others, gap)
      shown = [x for x in labels if any(mm["cat"] == 4 for mm in x["members"])]
      appended = [x for x in shown if x["host"]["cat"] != 4]
      rows.append(dict(tf=tf, definition=mode["name"], levels=len(r["levels"]), coverage_pct=round(r["coverage"] * 100), lines=len(chosen), key_labels=len(shown), prices_appended_to_other_labels=sum(sum(1 for mm in x["members"] if mm["cat"] == 4) for x in appended),
                       flipped=sum(1 for x in chosen if x["flipped"]), in_play=sum(1 for x in chosen if x["in_play"]), panel_row=kl.panel_row(chosen)))
  return rows


if __name__ == "__main__":
  if "--write-golden" in sys.argv:
    json.dump(v7_golden_document(), open(V7_PATH, "w"), indent=1)
    open(V7_PATH, "a").write("\n")
    print("wrote", V7_PATH)
  for name, fn in list(globals().items()):
    if name.startswith("test_"):
      fn()
      print("ok", name)
  print()
  for row in measurement():
    print(json.dumps(row))
