# RN-1 outcome scripts (read-only, historical, not P&L)

`RN_DIR` is a directory holding `cap-xau.json` (a `tools.capture_bars` capture), `study.jsonl` (`go run ./cmd/rnstudy`) and, for the overlap and alignment
scripts, `env-prod.jsonl` (`go run ./cmd/replay -capture ... -envelopes-out`).

| script | answers |
|---|---|
| `sim.py` | the simulator: one resting limit at the proximal edge of the entry band, bid/ask candles with a $0.30 spread, stop at `max(structural, 50 pips)`, stop before target inside one candle; exits: the published target, and the XAU 1R/2R/3R/4R ladder (40/20/20/20, break-even after 1R) |
| `outcomes.py` | what is published now, before AE-CF1 (confirmation required), and at the 1-star floor, per setup kind |
| `controls.py` | the same detector on levels shifted off the round numbers |
| `align.py` | existing strategies' outcomes with and without round-number alignment, with placebo grids |
| `breakdown.py` | direction, session, volatility, M5 structure, handle, retest candle |
| `geometry.py` | risk, ATR, room, chase, barriers |
| `overlap.py` | same-direction overlap of entry zone, window, stop and target with the other strategies |

Every number these print is a sample of one 11-day regime; read the confidence intervals, not the means.
