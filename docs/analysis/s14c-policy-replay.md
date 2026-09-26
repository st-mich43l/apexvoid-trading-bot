# S14C: deterministic Go-vs-Python policy replay (XAU M5 supply / demand)

Goal: compare the two engines at the same observation boundary on the same
bytes, and report what does and does not agree. **This document and its report
approve nothing.** Verdict on the committed evidence: `unresolved_differences`.

## Inputs (real, immutable, no fabricated bars)

`contracts/analysis/replay/xau-production-capture-20260921.json`: 1500 M5,
600 M15 and 300 H1 closed XAU bars (2026-09-14 → 2026-09-21 14:30Z) read from
the live feed's Redis through `RedisOHLCSource`. Independent corroboration
recorded in the file: all 154 overlapping M5 bars are bit-identical to
`analysis-engine/testdata/raw_xau_m5_snapshot.jsonl`, and all 400 overlapping
bars to `algo-bot/tests/fixtures/xau_m5_2026_09_21_supply_reversal.json`. The
capturing host/command are not re-verifiable from the repository.

**Finding: the production feed delivers no H4.** `config/trading-bot.yml`
records that the live trendbar subscription supports only M1/M5/M15/M30/H1. Go's
higher-timeframe context in production is therefore **H1 only** and the adapter's
H4 fallback is unreachable live. The faithful replay dispatches exactly the
captured timeframes; `-derive-h4` (UTC-aligned, complete buckets) exists for
exploration only and is labelled derived wherever it is used.

## How it runs

Causality rules (both engines): a bar is delivered at its close only; at a shared
close instant the higher timeframe is delivered first; malformed capture data is
refused, never repaired.

```bash
# Go: same Engine.Dispatch path as live; envelopes are the producer's own adapter output
cd analysis-engine
go run ./cmd/replay -capture ../contracts/analysis/replay/xau-production-capture-20260921.json \
  -config ../config/apexvoid.yml -symbol XAU -timeframe M5 -envelopes-out /reports/go-envelopes.jsonl

# Python: the live "Supply Demand" technique detector (scanner DEFAULT_DETECTORS) on the
# frames visible at each M5 close, full stride (~10 min for 1500 bars)
cd algo-bot
python -m app.scripts.policy_replay python-observations \
  --capture ../contracts/analysis/replay/xau-production-capture-20260921.json --out /reports/python-observations.jsonl

# Compare (Go envelopes go through the production decoder and the live adapter)
python -m app.scripts.policy_replay report --capture ../contracts/analysis/replay/xau-production-capture-20260921.json \
  --go-envelopes /reports/go-envelopes.jsonl --python-observations /reports/python-observations.jsonl \
  --json /reports/policy-replay.json --md /reports/policy-replay.md
```

A fresh capture of the current market (operator step, read-only):
`python -m app.scripts.capture_bars --symbol XAU --m5 1500 --m15 600 --h1 300 --out /reports/capture.json`.

Committed outputs: `contracts/analysis/replay/go-*` (Go golden, guarded by
`go test ./test/replaycapture`, regenerate with `UPDATE_GOLDEN=1`),
`python-observations-*`, and `docs/analysis/reports/s14c-policy-replay-xau-20260921.{md,json}`.
Python observations were produced locally on Python 3.14 with `pandas-ta 0.4.71b0`
and a no-op `numba.njit` shim (numba is unavailable there); the report records the
interpreter and library versions.

## Results on the committed capture

| | Go | Python |
| --- | ---: | ---: |
| confirmed setups | 185 (173 adaptable; 12 refused `higher_timeframe_bias_unavailable`) | 177 |
| strict 1:1 match (±300 s, overlapping zone) | 22 | 22 |
| unmatched | 163 Go-only | 155 Python-only |
| many-to-many coverage (±900 s, overlapping zone) | 39 / 185 have a Python confirmation nearby | 53 / 177 have a Go confirmation nearby |

1. **Confirmation.** Same wording, materially different detections. Only 22
   setups match; 11 of those differ by exactly one bar (Python confirms 300 s
   earlier). Matched Python kinds are wick_rejection 9, engulfing 5, sweep_reclaim 5,
   strong_reclaim 2, rejection_choch 1: Go asserts only `rejection`, so only 9/22
   are the same family. Go's touch→confirmation gap is 0 bars in 169 cases and 1 bar
   in 16 (the same-bar / next-bar rule of #640).
2. **Confluence.** Go's evidence-count confluence is **4 for every adaptable case**
   → Tier A for all 173. Matched Python confluence is 2 (19) or 3 (3) → Tier B/A.
   Consequences measured here: the risk multiplier is 1.0 on both sides (reaction
   sizing ignores tier); every Go case clears the global minimum (2) and the
   selective-session floor, and no matched setup passes on Go that Python would
   reject *in this sample*, but the constant 4 means Go can never be filtered by
   the confluence gates that do discriminate Python setups. **No calibrated
   mapping is proposed: this window has too few matched pairs (22) to justify a
   replacement, and the brief forbids one without evidence.** Open gate.
3. **HTF bias.** H1 is the only Go timeframe (173 cases). Of 22 matched pairs the
   H1 direction agrees with Python's H1/M15 bias in 13 and **conflicts in 9**;
   the conflicts are preserved, not reconciled, and no alignment is manufactured.
4. **ATR and geometry.** Go's reported ATR equals the configured `simple` formula
   recomputed from the capture in 185/185 cases. Python's detectors use Wilder/RMA
   (a known, documented split in `config/analysis.yml`): relative to Go it ranges
   −24.5 % … +30.9 % (median +1.2 %); only 3/22 matched pairs agree within 1 %.
   Pip 0.1, tick 0.01, 2 digits. **0/185 Go prices are tick-aligned** (they are
   unrounded floats), and the adapter's whole-pip target conversion loses up to
   0.0499 in price (median 0.026), so the pips-based `StrategyMatch` field is not
   the executable price. R:R to Go's first target is 0.19 … 3.30 (median 0.54),
   stop 43.6 … 695 pips (median 112.7).
5. **Opposing structure.** Go supplies no such fact. The worker's final barrier /
   target-room checks still recompute Python zones and levels from M15 OHLC
   (`worker._htf_zones`, `_htf_levels`, `_opposing_barrier_reason`,
   `structural_target_room`). They are **retained**. On identical frames their
   verdict agrees for 20/22 matched pairs (the two differences are Python-side
   vetoes the Go-derived geometry would not trigger).

## Outstanding gates this raises (owner decisions, not code)

* Confirmation semantics: 22/185 strict, 21 % coverage. Decide, with the review
  material in the report, whether Go's rejection rule is the intended replacement
  or which Python patterns it must additionally accept.
* Confluence mapping: needs a documented calibration on a larger real window.
* HTF: 9 preserved conflicts; policy for a Go H1 that disagrees with Python.
* Precision: Go prices reach the contract unrounded; S14E covers presentation and
  the pip-conversion loss.
* Every gate in the report except `python_replay_is_full_stride` is `OPEN`.

## Limits

One capture, one instrument, ~5 days; not a performance or live-equivalence claim.
Python replay covers only the "Supply Demand" technique detector (one best result
per bar) and not scanner actionability gates or the market-map layer. The first
149 closes are skipped for warm-up (fewer bars than a live cycle's M5 window).
The invalidation comparison is against the *unbuffered* Python zone edge and is
informational (Python's executed stop is planned later).
