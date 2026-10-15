# AE analysis ground-truth check: structure and zones against independent labels (2026-10-15)

Tool: `algo-bot/tools/ae_ground_truth.py` (labels and scoring), `analysis-engine/test/groundtruth/dump_test.go` (engine state dump).
Results: [ae-ground-truth-results.json](ae-ground-truth-results.json). Hand-marked set: [ground-truth/hand-marked.json](ground-truth/hand-marked.json).
Tests of the labels themselves: `algo-bot/tests/test_ae_ground_truth_tool.py`.

**Verdict.** The analysis layer does what it says. Every swing is a real local extreme at the exact candle price, nearly every
significant swing is found, every close-confirmed break follows a real close beyond one of the engine's own swings, every fair
value gap zone is a real three-candle gap, no live zone sits behind a departure the price never came back from, and supply/demand
zones are followed by a reaction twice as often as an arbitrary candle. The findings are in the *breadth* of what is published, not
in its correctness: the micro swing layer and the tiny-gap FVG zones are noise by design, and supply/demand recall by origin time
is weakest on H1 (67%). This says the engine implements and applies its definitions faithfully. It says nothing about whether
those definitions make money; the calibration (#787) says the scores built on them do not predict outcomes.

## 1. What the labels are, and what they are not

Two kinds, scored separately.

* **Definition-based labels** (`Oracle`): an independent, deliberately plain Python implementation of textbook definitions over the raw
  closed candles of the committed captures. No engine code, no engine parameter, own Wilder ATR(14). Swing: strict +-5-bar extreme
  that reverses >= 1.5 ATR within 20 bars (tiers 1.5, 3.0). Fair value gap: three candles with a gap of >= 0.2 ATR, invalid after a
  close through the far edge. Impulse origin: the last opposite-colour candle before a run of <= 6 same-colour candles that travels
  >= 2 ATR. The label code has its own tests on constructed candles (`test_ae_ground_truth_tool.py`).
* **Hand-marked**: 3 windows (XAU M15, EURUSD M15, USDJPY H1; 80 candles each, rendered with no engine output), 20 swings and 9 zones
  marked by eye. **The marker is the same author as the scoring tool (Claude), one pass, so it is not an independent human
  check.** The file format is fixed so a person's marks can be added or can replace these.

The engine state is dumped at 192 checkpoints (every 12 hours of M5 after a 600-bar warm-up) of the five newest production
captures. Checkpoints of one capture overlap in what they contain, so the counts below are not independent observations.

## 2. Results

| Check | Result |
|---|---|
| Swings: engine swing is a strict +-2-bar extreme at the candle's exact price | 100% in every layer and timeframe (M5/M15/H1; 32,000+ swings) |
| Swings: oracle swings reversing >= 1.5 ATR found by the engine (+-1 bar, 0.05 ATR) | 99.8% / 99.8% / 100% (M5/M15/H1); >= 3 ATR: 99.8% / 100% / 100% |
| Swings: share of engine swings that reverse >= 3 ATR within 20 bars | major 100%; intermediate 74-80%; internal 52-57%; **micro 32-48%** |
| Breaks: close-confirmed breaks with no earlier close beyond an engine swing of that layer | 0 of 133; the stamp trails the first close by 0-1 bar (median), 2 bars (p90); 90-95% within 3 bars |
| FVG: engine zones (any state) that are real three-candle gaps | 100% of 4,595 H1 / 9,994 M15 |
| FVG: live oracle gaps >= 0.5 ATR (last 150 bars) present in the engine | 100% (106 H1, 147 M15) |
| FVG: live zones that price has since left by more than 1 ATR, unreclaimed | 0% (0.0-0.4% beyond 0.5 ATR; 3-4% beyond the edge itself, which the engine's 0.5 ATR tolerance forgives by design) |
| FVG: live zones by size | 29% (H1) / 39% (M15) are under 0.2 ATR; the FVG strategy filters at 0.5 ATR, the zone layer publishes all |
| Supply/demand: zone followed by >= 1.5 ATR within 8 bars | 58.7% (H1) / 62.4% (M15), against **29% / 28%** for an arbitrary candle |
| Supply/demand: oracle impulse origins with a live zone of that side overlapping | 99.6% / 98.6% |
| Supply/demand: ... and an origin time within 3 bars | **67.2% (H1)** / 84.6% (M15) |
| Hand-marked swings found (+-1 bar, same kind) | 18 of 20 (90%) |
| Hand-marked zones found (overlap >= 30% of the smaller width, live) | 8 of 9 (89%) |
| Engine intermediate/major swings in the marked windows that the marker also marked | 7 of 8 (a lower bound: only unambiguous swings were marked) |

## 3. What is a finding, and what is only a definition

* **Micro swings are mostly noise by the "reverses 3 ATR" standard** (a third to a half of them), but 91% reverse >= 1 ATR. Anything that
  trades off a micro swing alone should not be described as structure.
* **Tiny FVG zones are published and live** (a third of live zones). Correct by the 3-candle definition and filtered later; a
  consumer that does not apply the 0.5 ATR floor would trade noise.
* **Supply/demand has a real reaction lift** (2.1x the baseline) but the lift is moderate: 4 in 10 zones show no 1.5 ATR reaction in
  8 bars. A zone is a hypothesis, not a barrier.
* **Supply/demand recall by origin time on H1 is 67%**: the origin's zone exists in 99.6% of cases but is often anchored on a
  different candle of the base, so a strategy that keys on the origin candle can differ from a textbook base. The disagreeing
  examples are in the results file.
* **The bias** (the higher-timeframe direction used in arbitration) is not labelled here: there is no definition-based ground truth for
  it that is not a choice of definition. It was shown not to carry an outcome edge in [arbitration-impact-report.md](arbitration-impact-report.md).
* Not scored: order blocks, breakers, iFVG, flip zones (rare in the checkpoints), liquidity pools, trendlines, key levels,
  session ranges. No precision/recall for break *recall*: the engine keeps only the latest break per layer.

## 4. Limits

The labels share the engine's premise that a swing is a local extreme and a zone is a base before an impulse, so this tests
implementation fidelity and the reaction premise, not the premise's profitability. Two weeks; every checkpoint of a capture sees
the same bars. The hand-marked sample is 3 windows by the tool's author. A definition-based label can be wrong in a way an
agreeing engine hides; the tests on constructed candles and the visible disagreements (examples in the results) are the
guard, a person's review of both is the real one.

## 5. Reproduce

```
cd analysis-engine && GT_DUMP_DIR=/tmp/gt go test ./test/groundtruth -run TestDumpState
cd ../algo-bot && python -m tools.ae_ground_truth /tmp/gt \
    --hand-marked ../docs/strategies/ground-truth/hand-marked.json --out ../docs/strategies/ae-ground-truth-results.json
# to mark more by eye (no engine output is drawn):
python -m tools.ae_ground_truth --render replay-xau-production-capture-20261014.json M15 1791547200 80 /tmp/xau.png   # needs matplotlib
```
Deterministic: two runs give byte-identical results.
