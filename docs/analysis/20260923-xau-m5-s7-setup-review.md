# XAU M5 S7 Setup Review — 2026-09-23

## Scope and method

This is a replay/research result, not a performance claim and not a live
trading decision. The canonical replay command fed the 300 closed bars in
`analysis-engine/testdata/raw_xau_m5_snapshot.jsonl` through the same
`Engine.Dispatch` path used by the Analysis Engine. The fixture covers
2026-09-21 01:45 UTC through 2026-09-22 03:40 UTC.

For every candidate first observed by the opportunity lifecycle, replay keeps
that exact `AnalysisSnapshot`. `-setup-png-dir` then renders a candle window
around it using only those frozen structure, liquidity, zone, and candidate
facts. Later candles are included only to help a reviewer see what followed;
they do not alter the setup geometry.

```bash
cd analysis-engine
go run ./cmd/replay \
  -bars testdata/raw_xau_m5_snapshot.jsonl \
  -config ../config/apexvoid.yml -symbol XAU -timeframe M5 \
  -setup-png-dir /tmp/xau-s7-review -strategy key_level \
  -max-setups 1 -before-bars 80 -after-bars 30
```

The filename includes symbol, strategy, setup timestamp, and an opportunity-ID
prefix so several setups from the same strategy and second cannot overwrite
one another.

## Observed output

Final-state replay reported 73 swings, 1 structure break, 81 liquidity pools,
an internal bearish/SELL bias, 149 first-observed candidates, and 147 live
candidates at the last bar. First-observed candidates by strategy were:

| Strategy | Setups |
|---|---:|
| `key_level` | 94 |
| `demand` | 26 |
| `supply` | 24 |
| `order_block` | 5 |
| `fvg` | 0 |
| `flip_zone` | 0 |
| `session_level` | 0 |

The four strategies with output each produced a focused setup PNG in the
review run. `fvg`, `flip_zone`, and `session_level` did not have a qualifying
setup in this narrow XAU M5 window; that is absence of sample evidence, not a
claim that those implementations are invalid.

## Human-review findings

- The renderer now keeps candles readable under overlapping supply/demand and
  liquidity bands by alpha-compositing analytical areas. Candidate entry is a
  blue band; invalidation is red; targets are green; protected levels are
  indigo; BOS/CHoCH retain their existing blue/magenta event lines.
- A setup image is an audit artifact of what the engine knew when it created a
  candidate. It deliberately does not infer a new zone, move a stop, or decide
  whether a candidate should remain valid.
- The candidate count is high for a 300-bar review. It is useful evidence that
  the replay and rendering paths are exercising real strategy output, but it
  is not a quality or profitability measure. The next research pass needs a
  broader, human-labelled sample and outcome evaluation before any cutover or
  threshold-calibration decision.

## Reproduction and limits

Use `-strategy <id>` or `-opportunity-id <stable-prefix>` to select a setup,
`-before-bars`/`-after-bars` to change the review window, and `-verbose` only
when the full final live-candidate list is needed. The renderer is stdlib-only
and has no text glyph layer; the command output and deterministic filename
provide the strategy and identity, while color is documented above.

No live feed, broker order, Kafka consumer, P&L calculation, or strategy
validity rule is changed by this phase.
