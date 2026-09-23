# Manual algo chart capture window (PR-K)

**Historical record — the `manual_algo_charts` feature this describes was
removed** (Analysis Engine V2 strategy rebuild, Phase S1; see
`docs/analysis-engine-v2-migration.md` and
`apexvoid-bot-prompts/rebuild-strategies.md` §58–§68). Kept for the
methodological lesson below (undersized lookback windows silently
distorting measured structure density, which drove a since-withdrawn
config tune) — not as usage documentation for a feature that no longer
exists.

`manual_algo_charts` existed to replay strategy math on owner `/algo` outcomes.
Until PR-K the snapshotter used a private `_LOOKBACK` table that was a fraction
of the live scanner window from `market_data.lookbacks` /
`window_for_timeframe()`:

| TF | live (XAU) | stored `_LOOKBACK` | ratio |
|---|---|---|---|
| H1 | 400 (~16 d) | 36 (36 h) | 11.1× |
| M15 | 250 (~2.6 d) | 48 (12 h) | 5.2× |
| M5 | 150 (12.5 h) | 72 (6 h) | 2.1× |
| M1 | 150 (2.5 h) | 90 (1.5 h) | 1.7× |

H1 (36) and M15 (48) were also **below** the schema floor (`ge=50` /
`max(50, …)`).

Measured structure density on comparable synthetic series:

```
TF/window   bars  swings  breaks  legs  SDzones  OBs  keylvls
H1/snap       36       4       1     6        6    1        1
H1/live      400      76      30    46       46   13       24
M15/snap      48       9       4     6        6    0        3
M15/live     250      55      24    36       35    7       14
M5/snap       72      12       4     8        7    1        3
M5/live      150      34      13    23       22    6        9
```

That undersized window drove the PR-J anomalies: sticky HTF bias (few H1
swings → momentum dominates), dealing-range miss rates, and technique detectors
with nothing to fire on. The Key Level `min_sell_zone_score: 10` tune was
withdrawn because it was fitted on that view.

## What changed

- Snapshots resolve lookbacks through `window_for_timeframe(..., root=instrument_runtime_view(symbol))`.
- Rows record `bars_requested` / `bars_stored` / `bars_after_event` /
  `capture_version` (`1` = pre-PR-K, `2` = live window).
- `load_manual_algo_charts(..., causal_only=True)` is the shared causal trim.
- `backfill_manual_algo_charts` can widen version-1 rows while Redis still holds
  history (H1/M15 often recoverable; M5/M1 usually not for older trades).
- Formula replay defaults to excluding inadequate captures and writes
  `scorecard.v3.json` — do not pool with v1/v2 artefacts.

## Recovery expectation

At `BARS_WINDOW_MAX = 1500`, many existing trades can be upgraded on H1/M15.
The ~115 pre-PR-K trades **cannot** be fully recovered on M5/M1 once Redis has
trimmed those windows. New trades accumulate at version 2; tune only after that.
