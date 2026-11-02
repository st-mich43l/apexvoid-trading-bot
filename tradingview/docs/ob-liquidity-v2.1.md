# ApexVoid Price Action v2.1 — order blocks and BSL/SSL liquidity pools

Scope: `tradingview/apexvoid_price_action.pine` only. Structure, FVG, supply/demand, the
Asian levels, the HTF context and the existing alerts keep their v2.0 behaviour except where
this document says otherwise.

## 0. What was and was not verified

| Item | Status |
| --- | --- |
| Algorithm behaviour on real XAUUSD closed bars (M5, M15, H1, H4) | **Verified** with the Python reference model in `tradingview/reference/` (`test_pa_model.py`, 10 checks pass). |
| Pine syntax | Parsed without errors by a third-party Pine parser (v5 grammar, `//@version=6` swapped for `5`). This is a syntax check only. |
| Pine v6 compile in TradingView | **Not done.** No TradingView access exists in this environment. |
| Chart rendering, label placement, object limits, alerts in TradingView | **Not done.** |
| Before/after screenshots | **Not possible here.** `docs/img/*.png` are renders of the *reference model's selections* (see §8), not TradingView screenshots. |
| Comparison with LuxAlgo at comparable settings | **Not done** (needs both indicators on a live chart). |

The acceptance criterion "Pine compiles and chart behaviour was visually verified" is therefore
**open**. §9 lists the exact checks to run in TradingView.

The reference model mirrors the Pine decision logic bar by bar. It does not prove the Pine
code is a faithful port: any compile error, Pine runtime quirk or drawing-API mistake is
invisible to it.

## 1. Order-block root cause (v2.0)

Evidence: v2.0 logic replayed on real closed bars. `live` = production Redis capture of
2026-11-02 02:33 UTC (`reference/fixtures/xau-closed-bars-20261102.json`); `repo` = the
2026-10-29 capture already in `analysis-engine/testdata/`. H4 is resampled from H1.

| Capture | TF | v2.0 blocks created | v2.0 active / visible at the last bar |
| --- | --- | --- | --- |
| live | M5 | 40 | **3 / 0** |
| live | M15 | 31 | **1 / 0** |
| live | H1 | 15 | 3 / 2 |
| live | H4 | 6 | **2 / 0** |
| repo | M5 / M15 / H1 / H4 | 40 / 14 / 5 / 1 | 2/2, 3/1, 1/1, **1/0** |

So the symptom reproduces: valid blocks exist, none is drawn. The detector was not the main
defect on this data. Causes, in order of observed impact:

1. **Distance gate (display).** A block further than 10 ATR from price is never drawn.
   After a large impulse the relevant blocks sit 12–27 ATR away (live M5: 12.5, 20.2 and
   26.5 ATR; live M15: 11.7 ATR). They stayed active and invisible.
2. **Cross-kind redundancy (display).** The selection loop hid any zone overlapping an
   already-chosen same-side zone by 60 %, whatever its kind. A fresh, qualified H4 bearish
   OB (live, 7.3 ATR away) was hidden because a bearish FVG that is a "POI" *only because it
   overlaps that OB* (composite confluence) was picked first and overlapped it by 90 %. On M15
   a qualified bullish OB was hidden by a Demand zone (repo capture).
3. **Shared budget (display).** OBs competed with FVG and supply/demand for 8 slots, ranked
   behind every POI.
4. **External-only detection.** Blocks were only built on an external BOS/CHoCH. Internal
   breaks outnumber external ones about 3:1 (live M5: 144 vs 45) and never produced a block.
5. **Opaque state.** The panel showed no per-block reason, so "OB 0" could not be told apart
   from "3 active, 3 far away".
6. **Latent, not observed:** retained-zone eviction was FIFO across all kinds, so FVG volume
   could evict blocks at `maxZones`. On the live capture the store peaked at 33 (M5), 21 (M15) and
   28 (H1) of 80 zones, so this did not cause the symptom; v2.1 evicts FVG first anyway.

Detector findings (v2.0): on a break it took the nearest opposite-colour candle whose high is
at least 0.5 ATR below the break close, with no impulse, base or birth-validity test. That
accepted nearly every last opposite candle (M5, both captures: 40 blocks from 45 external breaks);
on the repo capture 32 of those 40 were later invalidated and 6 aged out, with a median life of
about 32 bars (about 2.7 hours).

## 2. Per-candidate diagnostic

The Debug view (`Debug: OB candidate log and rejection reasons`, §3) prints the same fields
live. Sample from the reference model, live XAUUSD M15, last candidates before 2026-11-02:

```
10-29 01:15 BULL swing External BOS    REJECT: base is not an opposite candle (0.55 ATR from leg extreme)
10-29 01:15 BULL int   Internal BOS    src 10-29 00:45 4036.21-4040.09 ACCEPT
10-29 05:30 BEAR int   Internal CHoCH  REJECT: weak displacement (0.33 ATR < 1.00)
10-29 07:45 BEAR swing External CHoCH  src 10-29 01:15 4052.90-4068.91 ACCEPT
10-29 16:15 BEAR swing External BOS    src 10-29 11:00 4030.33-4039.38 ACCEPT
10-30 00:45 BEAR int   Internal CHoCH  src 10-29 20:00 4021.43-4032.57 ACCEPT  (replaced by the next line)
10-30 01:00 BEAR swing External BOS    src 10-29 20:00 4021.43-4032.57 ACCEPT
10-30 17:00 BEAR int   Internal BOS    REJECT: base is not an opposite candle (0.52 ATR from leg extreme)
```

State at the last bar: `Bear OB External CHoCH 4052.90-4068.91 Fresh visible`,
`Bear OB External BOS 4030.33-4039.38 Tested hidden: overlaps a higher-ranked OB`,
`Bear iOB 3988.55-3999.27 Fresh hidden: per-side budget full`.
Every hidden block carries one reason: `far`, `overlaps a higher-ranked OB` or
`per-side budget full`.

## 3. v2.1 order blocks: construction and lifecycle

**Sources.** External BOS/CHoCH (class *swing*) and, if enabled, internal BOS/CHoCH (class
*internal*, labelled `iOB`). Detection runs on every closed break and does not depend on POI
qualification or on what is drawn.

**Origin candle.** Window = bars strictly between the broken pivot and the break, capped at
`Origin search window` (40). Among opposite-colour candles in the window (bearish for a
bullish block) take the one at the leg extreme (lowest low / highest high; the nearest wins
ties). Not every last opposite candle: a candidate must pass all of

| Gate | Default | Reject reason shown |
| --- | --- | --- |
| Base within `Base tolerance` of the leg extreme | 0.50 ATR | `base is not an opposite candle (x ATR from leg extreme)` |
| Origin range ≤ `Maximum origin candle range` | 2.5 ATR | `origin too wide` |
| Net displacement from the origin to the break close ≥ `Minimum displacement` | 1.00 ATR | `weak displacement` |
| An impulse candle in the leg with body ≥ `Minimum impulse candle body` | 0.60 ATR | `no impulse candle` |
| Not already invalidated or mitigated between origin and detection | — | `already invalidated/mitigated before detection` |

Zone = candle high/low (or body when `OB includes candle wicks` is off). Everything is
normalised by ATR at the break bar. No volume is used and no institutional order volume is
claimed.

**Overlaps.** A new block overlapping a same-side block by ≥ 50 % of the smaller height
replaces it (newest wins, class keeps the higher of the two). An internal block is dropped as
`covered by an existing swing OB`.

**Lifecycle** (closed bars only):

| State | Condition |
| --- | --- |
| Fresh | price has not entered the block |
| Tested | a wick entered the block and the mitigation rule is not met (a partial touch is **not** an invalidation) |
| Mitigated | `OB mitigation method`: **Close** (default) = a close beyond the far edge; **Wick** = a wick reaches the far edge; **Midpoint** = a wick reaches 50 % |
| Invalidated | a close beyond the far edge, whatever the method (takes precedence) |

Mitigated and invalidated blocks are removed and counted (panel tooltip, Debug log). Blocks
also retire at `Zone maximum age`.

**Display.** Separate budget `Visible bullish / bearish OBs` (2 + 2) and `Maximum visible OB
distance` (25 ATR), independent of the FVG/supply-demand budget. Rank: swing before internal,
fresh before tested, bias-aligned, nearer, newer. A hidden block is never deleted; it is
redrawn as soon as it ranks in. Style: teal bullish / muted rose bearish, 1 px border, subtle
fill, dashed border and lighter fill when Tested, compact right-aligned `OB` / `iOB` (`· T`
when tested), optional midpoint line. Capacity eviction removes FVG first, then supply/demand,
then OBs.

**Dashboard.** `Order Blocks` row: `Bull v/a · Bear v/a · hid n`, or `none active · <last
reason>`. The tooltip gives hidden-by-far/overlap/budget counts and the lifetime counters.

## 4. BSL / SSL pool qualification

A pool is a level, not a pivot. Retained pools default to 24, drawn pools to 6 (Balanced).

| Source | Becomes a pool when | Degree |
| --- | --- | --- |
| Confirmed external swing high / low | always (it already passed the leg-displacement filter) | 2 |
| EQH / EQL | two independent tests within `EQH/EQL tolerance` (0.15 ATR) | 1 (internal only) or 3 (includes an external swing) |
| Previous-day high / low (optional, ≤ H4 charts) | completed day, no repaint | 3 |

Two tests are independent only if they are ≥ `Minimum bars between tests` (8) apart, price
pulled back ≥ `Minimum pullback` (0.5 ATR) between them, and neither level was exceeded in
between. An EQ band is the min–max of its tests (narrow band, ≤ 2 × tolerance) with a line at
the sweep edge (top for BSL, bottom for SSL). Lone internal pivots are held as candidates and
never drawn. On the live capture v2.1 created 105 / 83 / 53 / 13 pools (M5 / M15 / H1 / H4)
where v2.0 created one pool per internal pivot (369 / 311 / 252 / 48).

Visibility (Balanced): the nearest active BSL above and SSL below are always considered, the
rest rank by degree, then active state, then proximity, within `Maximum visible pool
distance` (30 ATR). Labels `BSL`, `SSL`, `EQH · BSL`, `EQL · SSL`, `PDH · BSL`, `PDL · SSL` at
the right edge; the exact price, tests, degree and age are in the tooltip. Pools use a neutral
cool-grey (`Liquidity pool color`) so they differ from BOS/CHoCH (teal/rose) and Asian H/L
(purple). Minimal hides pools; Detailed and Debug raise the budget. Hidden pools keep their
state.

## 5. Sweep versus break

| | Sweep (grab) | Consumed by acceptance | Consumed by structure | Structural BOS/CHoCH (unchanged) |
| --- | --- | --- | --- | --- |
| Test | high/low beyond the edge by `Sweep penetration` (0.01 ATR), then a close back inside | `Closes beyond a pool to consume it` (2) consecutive closes beyond the edge by `Pool break buffer` (0.10 ATR) | the BOS/CHoCH on the swing the pool rests on | one close beyond the swing + confirmation buffer (0.15 ATR) |
| Return rule | `Within N bars` (2) or `Same bar` | none | none | none |
| Result | state *Swept*, grab highlight, `BSL/SSL swept` alert | state *Consumed*, `Liquidity Consumed` alert | state *Consumed* | existing BOS/CHoCH alerts, bias change |
| Bias / entries | never | never | by the existing structure logic only | yes |

A pool leaves the active state exactly once, so it alerts once. Swept and consumed pools fade
for `Consumed pool fade window` (40 bars) and are then retired (policy: `Fade, then retire` /
`Retire immediately` / `Keep faded`); their lines stop at the event bar and never extend
indefinitely. A wick through a swing and a return is a sweep and never a break; a close-beyond
that is not followed by acceptance within the window is neither.

## 6. Grab visualisation

On a sweep the rejection wick of the extreme bar is overlaid with a 2 px line: the upper wick
after a BSL sweep (bearish rejection, rose), the lower wick after an SSL sweep (bullish
rejection, teal). It appears only if the wick is ≥ `Minimum rejection wick` (0.20 ATR), is
attributed to a known pool, and keeps the last 10. The v2.0 text badges (`BSL sweep ↓`,
`EXT … ×n`) are removed. A grab changes neither bias nor any entry. A supply/demand-context
attribution is not implemented (pool attribution only).

Existing behaviour kept: a fresh FVG on the side of the last sweep can still qualify as
"Swept imbalance" POI, as in v2.0.

## 7. Settings, alerts and compatibility

New inputs: G2 pool, sweep, break, fade and display controls; G3 `Include internal-structure
order blocks`, search/displacement/impulse/range/base gates, `OB mitigation method`, per-side
visible counts, OB distance, midline, Debug log; G6 `Liquidity pool color`. Removed:
`Break candle minimum body (ATR)` (`obImpulseATR`) and `Sweep consolidation window`
(`sweepClusterBars`). `obLookback` is now only the POI "OB-to-break proximity". `Retained
liquidity levels` is now `Retained liquidity pools` (default 24). Saved settings for removed
inputs are ignored.

New alerts: `ApexVoid Buy-Side Liquidity Consumed`, `ApexVoid Sell-Side Liquidity Consumed`. All
v2.0 alerts keep their names; `ApexVoid Bullish/Bearish OB` now fire for a *qualified* block
(internal or swing) instead of any candidate, and BSL/SSL sweep alerts come from pools, once
per pool. Only swing-class blocks can be a "Structure-origin OB" POI.

## 8. Reference-model renders

`docs/img/xauusd-{m5,m15,h1,h4}-before-after.png`: left = v2.0 selection, right = v2.1
selection, same bars (live capture, last bar 2026-11-02). Regenerate with
`python tradingview/reference/render.py`. They draw the model's boxes and lines in matplotlib;
they say nothing about TradingView's own drawing.

What they show on live M5: v2.0 draws no order block and 9 BSL/SSL lines (one per recent
internal pivot, 10 retained). v2.1 selects four blocks (two bearish swing OBs, two bullish
internal OBs; the plot window shows the three nearest) and six pools: the nearest BSL
(3923.8) and SSL (3870.2) plus four higher external-swing BSLs, three of them above the plot.
No lone internal pivot appears.

## 9. Verification results and the TradingView checklist

Reference model, real bars (`python tradingview/reference/test_pa_model.py`):

- v2.0 reproduction: active blocks with zero visible on live M5 and M15.
- v2.1: whenever blocks are active at least one is visible; per-side budgets respected; every
  hidden block has a reason; no same-side block pair overlaps ≥ 50 %.
- Construction: every accepted bullish block starts from a bearish candle (bearish from a
  bullish one), top > bottom, displacement ≥ the configured ATR multiple; every rejection has
  a reason; blocks the Close rule has not closed through stay active as Tested.
- Liquidity: synthetic cases confirm sweep ≠ consumption (wick + return = swept; two closes
  beyond = consumed; one close beyond then a return = swept; a late return = nothing); a
  consumed pool never alerts again; each pool produces at most one terminal event on real
  bars; fewer pools than pivots; EQ bands within tolerance and with ≥ 2 tests.

Still to do **in TradingView** (XAUUSD M5, M15, H1, H4; Balanced profile, then Debug):

1. Compile with no errors or warnings that change behaviour (first suspects: the history
   operator on `time[...]` inside `f_obBuild`, `array.new_*` default arguments, `box.set_lefttop`).
2. Blocks listed in the panel tooltip are the ones on the chart; Debug table matches the
   boxes; hidden blocks reappear when price approaches.
3. Right-edge labels do not collide with `DEV HIGH`, `PROTECTED`, zone captions or Asia tags.
4. Grab highlights sit on the correct wick; no highlight without a pool.
5. Object counts stay within `max_boxes/lines/labels_count` on a 5k-bar history.
6. Alerts: one `Liquidity Consumed` and one sweep alert per pool.
7. Compare against LuxAlgo at comparable settings; expected, legitimate differences are the
   level-first pool filter (LuxAlgo draws more equal-level zones) and the displacement gates.

## 10. Known limitations

- Not compiled or viewed in TradingView (see §0). Parameter defaults were tuned only on one
  week of XAUUSD (live and repo captures); other symbols and regimes are untested.
- The model's Asia, HTF, previous-day and Pine drawing code are not covered. Previous-day pools
  start at the day's first bar rather than at the prior high's bar.
- Pools and OB origin search look back at most 750 / 40 bars; very old external swings that
  predate the 800-bar history cannot be re-derived after a restart.
- A multi-bar sweep highlights the extreme bar's full wick, not only the part beyond the pool.
- Late detection is inherent: swing pools qualify only after external-pivot confirmation and
  leg displacement; blocks appear on the bar their break closes.
- Distance and budget defaults can hide valid blocks by design; the panel now says so, but a
  block hidden as `far` is still unseen on the chart.
- LuxAlgo comparison and screenshots remain open.
