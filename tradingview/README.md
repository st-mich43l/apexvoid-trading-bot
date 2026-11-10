# TradingView indicators

This directory contains chart-side analytical tools that accompany the ApexVoid
trading stack. They are reference and discretionary-analysis tools; they are not
loaded by the Go analysis engine and cannot place orders through the bot.

| Path | Purpose |
| --- | --- |
| `apexvoid_price_action.pine` | The indicator (Pine Script v6, v2.21). |

## ApexVoid Price Action

A market-structure indicator that draws structure, order blocks, imbalances,
supply/demand, liquidity pools, key levels and session levels. It has no table or
panel on the chart (removed in v2.19). Every structural event and every zone state change
needs a **closed** chart bar, so nothing repaints once a bar has closed. A zone,
pool or sweep is an analytical reference, not an entry signal. The indicator tracks no trades: since v2.18 there is no trade plan, no paper results and no entry, stop or target logic.

### Install

1. Open TradingView's Pine Editor, paste `apexvoid_price_action.pine`, save, and
   add it to a chart.
2. Suggested start: XAUUSD on M5 or M15, default settings (`Balanced — Recommended`
   profile). Tune per symbol afterwards.
3. Set the chart's right margin to at least `Annotation gutter` + 15 bars (default 27): Chart settings → Appearance (older versions: Canvas) → Margins → Right (bars). The gutter is where captions and the label column live; without the margin TradingView clips them. The menu names are from memory of the TradingView UI and were not checked in this repository.
4. TradingView is the only authoritative compiler. After any edit, compile there and
   check on a long history that object limits (250 boxes, 350 lines, 500 labels) are
   not hit.

**Upgrading from an earlier version.** TradingView keeps the old script's settings on a
chart that already holds the indicator, including its price-scale pin, so a source change
does not reach it. Up to v2.14 the script declared `scale = scale.none`; a chart that still
carries that pin draws zones, lines and labels on their own hidden scale and they drift off
the candles when the price axis is dragged or zoomed.

1. Remove the indicator from the chart and add it again. Alternatively, on the indicator
   open ⋯ → Pin to scale → Pin to right scale.
2. With auto-scale on, right-click the price axis → **Scale price chart only**. Auto-scale
   then fits the candles, so zones, pools and session boxes that lie outside the visible
   candles cannot squash them; drawings stay at their true prices.

### What is drawn

| Element | Look | Meaning |
| --- | --- | --- |
| BOS / CHoCH | solid (BOS), dashed (CHoCH) line with a centred caption, teal bullish / rose bearish | A **close** beyond a confirmed swing. CHoCH is a break against the current external bias. The line ends on the candle that first closed beyond the swing, the caption sits next to the break, and one candle that closes beyond several swings is drawn as one BOS (the most recent swing). A swing is confirmed late (pivot lag plus a displaced leg), so the BOS alert and the bias change can arrive a few bars after that candle; v2.6 draws the line on the real break candle (before v2.6 it ended on the later confirmation bar, up to 38 bars late on XAUUSD M15). |
| `MAJOR HIGH` / `MAJOR LOW` | tag plus a line extending from the swing | The latest confirmed external swing. Retired (dotted, faded) once broken. |
| `DEV▲` / `DEV▼` | dashed line plus a live leg | The unconfirmed extreme of the current leg. Never labelled HH/LL. |
| `PROT` | dashed line | The external level whose break would flip bias. Since v2.13 it **trails**: each internal break in the bias direction moves it toward price (never away), and a close beyond it is the CHoCH. |
| `OB` / `iOB` | box with thin border, midpoint dotted line, right-aligned label | Swing / internal order block. Dashed border and `· T` when Tested. |
| FVG | blue (bullish) / orange (bearish) box | Three-candle fair value gap. |
| `Demand` / `Supply` | green / pink box | Zone at a confirmed, displaced external swing. |
| `Demand · DBR` … `Supply · DBD` | same colours | Base-and-impulse zone; the suffix names the pattern (below). |
| `◆ OB+FVG`, `◆ D+OB`, `◆ S+FVG`, `◆ D·RBR` … | thicker border | A zone that passed a named qualification path (below). `◆` plus the border mark a POI; the colour carries the side, so the caption is only the kind: `OB`, `iOB`, `FVG`, `D` (demand), `S` (supply), base patterns `D·RBR` / `S·RBD`, composites joined with `+` (supply/demand, then OB, then FVG). `·T` = tested. Zones of one side that overlap or sit closer than a label height share **one** caption on the best member (`◆ OB+FVG ×3` = three boxes); hover for every member. |
| `EQ` and the premium / discount gauge | a slim rose / green gauge at the very right edge, just beyond the zone boxes (no text) and a faint dashed 50 % line | Upper half (supply side) and lower half (demand side) of the **dealing range**, and its equilibrium. Since v2.13 the dealing range is the **protected leg** (bear: the protected high down to the lowest low since it; bull: mirror), so price is always inside it; without a bias it is the confirmed swing range extended to the live extreme of the leg. Clipped to the candle-scale window. |
| `BSL` / `SSL` | cool-grey dotted line, right-edge label | Buy-side liquidity above price / sell-side liquidity below (confirmed pools). |
| `iBSL` / `iSSL` | cool-grey dashed line, small label | The nearest internal swing high above / low below price that nothing has traded through since and that is not already a pool. A marker only: no state, no alerts. Switch off with `Show nearest unswept internal swing high / low`. |
| `EQH · BSL` / `EQL · SSL` | narrow grey band plus line | Equal highs / lows formed by two or more independent tests. |
| `PDH · BSL` / `PDL · SSL` | as above | Optional previous-day high / low. |
| `… · swept` / `… · taken` | faded line that stops at the event bar | A pool that was grabbed / consumed. |
| Short vertical bar on a wick | 2 px, rose on an upper wick, teal on a lower wick | Liquidity grab: the rejection wick after a sweep. |
| `Asia H` / `Asia L` (live) | solid purple lines that grow with the session and update every bar; labels in the right-edge column | The Asian range as it forms. Since v2.13 Asia is the engine's session: 07:00 Tokyo (22:00 UTC) until London open, so the range is the one `asia_range` trades. When the session ends it becomes the completed range below. |
| `KL 3949.12 ×3` (key levels) | faint dotted line (dashed for a round level), neutral grey, fainter with fewer touches | What the engine's `key_level` strategy reads as a level (see Key levels below). Behind everything else; every drawn level shows its price in a muted right-column label that is never folded away. |
| `Asia` / `London` / `New York` boxes | faint fill with a clearer dashed outline and title per session, so the session reads at a glance without tinting the technical zones (purple / blue / amber), titled with the session name | The candle range of each session: from its first bar to its last bar, spanning the session high and low. The box grows while the session runs and freezes when it ends; the last 3 per session are kept. London and New York overlap by design. |
| `Asia H` / `Asia L` | purple dashed | Last completed Asian-session range (`pAsia H/L` while a session is live). |

Colours are inputs. Liquidity uses a neutral grey on purpose so it cannot be
confused with BOS/CHoCH (teal/rose) or Asian levels (purple).

### Key levels (v2.17)

One code path, two definitions (`Key level definition`). Both read what the engine's `key_level` strategy reads, `LegacyFrame.SwingLevels` (the `profit_week` contract): `legacyread.Stage` -> `techniquezone.FindSwings` -> `techniquezone.KeyLevels`
(`analysis.legacy_read`: window M5 150 / M15 250 / otherwise 400, `fractal_n` 2, `zigzag_atr_mult` 1.0). They differ only in four parameters:

| | `Engine (key_level v6)` (default) | `Selective (v7 preview)` |
| --- | --- | --- |
| Cluster tolerance | 0.5 x the median window ATR | 0.25 x the median window ATR |
| Round step (XAU; other symbols off) | 5.0 | 10.0 |
| Dedupe of neighbours within the tolerance | average the prices, touches = max | keep the **stronger member's exact price** (touches, then reaction before round, then the newest member) |
| Origin | display only | the level's kind for flipped / in play: a flipped level is not "in play" |

Engine v6 is what the bot trades today. Selective v7 is a preview of the definition engine PR-K2 is heading to; switch the default to v7 when PR-K2 merges.

- **When:** on the last bar, from the last `Key level window` CLOSED bars (the forming bar is left out in real time), so levels do not flicker; only the last bar recomputes and nothing is stored per bar.
- **ATR:** the window's own series: the mean of the last (up to) 14 true ranges, the first bar's true range being high - low; the tolerance is a fraction of the **median** of that series.
- **Swings:** fractal candidates (2 bars each side; left strictly lower, right not higher) through the **zigzag filter** (same kind replaces when more extreme; the opposite kind is kept when the move is at least the ATR at its bar).
- **Levels:** swings sorted by price join a cluster while within the tolerance of the lowest member (equal to "within tolerance of every member", implying the 2 x tolerance span cap); clusters of 2+ are reaction levels (price = mean). Round levels: every multiple of `Round step` with 2+ swings within max(tolerance, 0.25 x the ATR at the swing).
- **Never hidden for being "broken":** the engine `Role` marks nearly every level price is not touching as broken (a decision-time test), so it is not a display filter. Above price = resistance, below = support. The swings within the tolerance say where the level formed; a level whose origin is opposite to its side is **flipped**: still drawn, at transparency 88, tooltip `flipped (formed as support)` (v7 adds `Break & Retest territory; key_level v7 skips it`).
- **In play:** the last 2 closes are not both beyond the band, so price is still working the level (v7: a flipped level is skipped by the strategy, so it is never in play). Line transparency 45, caption `KL 3917.18 ×3 ●` (Pine cannot switch a caption to bold at run time, so the marker is the ● only).
- **Sparse by default:** inside the candle-scale window, `Key levels per side` (default 3, up to 10) nearest levels each side, in play counted for its side. Research on the live levels found that nearness to *a* level carries no information, so more lines only add clutter.
- **Look:** width-1 dotted line (dashed for a round level), `Key level colour`; transparency 82 / 74 / 66 for 2 / 3 / 4+ touches; major round levels (XAU $50 / $100 multiples) 60 in both modes; from the earliest member swing (capped by `Maximum drawn length`) to the end of the gutter. Fainter than pools and zones.
- **Lines always draw, and every drawn level shows its price:** there is no "near a zone edge: not drawn" rule and no `+n` for a key level. The right-column caption always starts with `KL` and the line's own price: reaction `KL 3949.12 ×3`; round on its step price `KL 3950 ×6`; round where v6 averaging moved the price `KL 3949.12 ×6` (tooltip: `round 3950, engine price merged`); in play adds ` ●`.
- **Sharing a slot:** several key levels within one label height become one label that lists every price, highest first (`KL 3954.47 ×3 · 3951.42 ×3 · 3949.12 ×6`). A key level within one label height of another category (`PROT`, `BSL` / `SSL` / `EQH` / `EQL`, `EQ`, `MAJOR`, `DEV`, a zone caption, Asia) never replaces that label: the other label keeps its slot, position and colour, and the key-level text is appended visibly (`BSL · KL 3949.12 ×6`) with its tooltip line. Pine labels have one text colour, so the appended text takes the host's colour; the `KL` prefix identifies it. Other categories keep the earlier rule (the lower tier shows as `+n`).
- **Selectivity readout (tooltip only, nothing on the chart):** the share of the window's [low, high] inside any level band, over all levels: `coverage 69 % of the recent range: low selectivity` above 40 %, `selective` at or below 25 %. On the 3 Nov M5 capture: v6 69 %, v7 36 %.
- **Not shown:** the engine's `current` contract (`frame.Levels`: wick re-count).

### Volume profile (v2.20, intrabar timeframes changed in v2.21)

A quiet histogram of where the period traded, with its point of control (POC) and value area (VA). **The volume is tick volume**: on XAUUSD (OANDA, FXCM, cTrader) it counts price updates, a measure of activity, not contracts. Read the profile as "where price spent its time and activity", not as traded size. It is a display tool with **no measured edge**; nothing here trades it.

`Volume profile` = `Auto` (default) needs no setting: everything follows the chart timeframe.

| Chart timeframe | Profile period | Intrabar timeframe | Profiles drawn |
|---|---|---|---|
| 5m and below | engine session: Asia start, London start, New York start, next Asia start (contiguous) | 1m (1m charts: the chart bars) | developing + last 2 |
| 10m to 15m | trading day (XAU rolls 17:00 New York) | 3m | developing + last 1 |
| 30m | trading day | 5m | developing + last 1 |
| 1h to 2h | week | 15m | developing + last 1 |
| 3h to 4h | month | 60m | developing |
| 1D and above | quarter | 60m | developing |

`Manual` shows the period (Session, Day, Week, Month), the intrabar timeframe, the rows and how many completed profiles to draw. `Off` (and the `Minimal` display profile) draws nothing.

**How it is built.** Each closed chart bar contributes its intrabars (the plain high, low and volume of the lower timeframe, never a structure series). The period's range is split into 16 to 40 equal rows (`range / (0.25 x ATR)`, fixed when the period completes), and each intrabar's volume is spread over the rows its high-low range overlaps, in proportion to the overlap (a doji goes entirely into its row). Where TradingView has no intrabars (older history) the chart bar's own high, low and volume are used and the profile tooltip says `approx (chart bars)`; a profile is "intrabar" only if every bar had intrabars. A completed profile never changes; the developing one is recomputed on the last bar (and includes the forming bar on the realtime bar, tooltip `developing`).

**How to read it.**
- **POC** (solid line, `dPOC` developing / `POC` last completed): the row with the most activity (the higher row on a tie).
- **VA** (`VAH` / `VAL`, dotted lines; the darker part of the histogram): the rows around the POC that hold `Value area %` (70) of the volume, built the CBOT way: from the POC, compare the next two rows above with the next two below and add the larger pair, until the share is reached.
- **nPOC** (`Show naked POCs`): a completed POC that no later bar has traded through yet (a bar with `low <= POC <= high` ends it). Dashed line from the period's end to the right edge; at most 6 are kept, the oldest dropped.

**Drawing.** The histograms are filled **polylines** (not boxes, which the zones, pools and sessions already use); each profile stays inside its own period, anchored at the period's first bar, and its width does not change as the period grows. Captions go to the right label column as category `volume`: when several share a slot they are listed in one label (never `+n`), and next to another label they are appended visibly (`BSL · nPOC 3938.40`). Captions and nPOC lines follow the candle-scale protection: a naked POC outside the candle-scale window gets neither a caption nor a line (a hidden line would still stretch the price auto-scale), and it appears again when price comes back within the window.

### Display profiles

| Profile | Zones drawn | Pools drawn | Other |
| --- | --- | --- | --- |
| Minimal | up to 4 | none | 3 external structure events, 1 Asian range |
| Balanced — Recommended | up to 8 (+ OB budget) | up to 8 (nearest BSL above and SSL below first) | 6 external events |
| Detailed | at least 12 | at least 12 | internal structure available |
| Debug | up to 20, distance limits off | all retained | OB and pool distance limits off |

### Keeping candles readable

Distant zones and lines stretch the price scale and squash the candles, and heavy fills
hide them, so:

- **Candle-scale protection** (`Keep candles readable…`, default on): a zone, order block
  or liquidity pool is drawn only if it is within `Candle-scale window` (15 ATR) of the close
  **or** inside the price range the last `Candle range reference` (300) bars already span (plus
  one ATR). An object inside that range cannot stretch the price scale, so it is kept; one
  outside it is not drawn. Hidden objects keep their state. The Debug profile ignores the limit; turn the toggle off to
  ignore it everywhere. Each object type also keeps its own distance setting.
- **Maximum drawn length** (250 bars): the left edge of zone boxes, midlines, pool lines
  and EQ bands is cut to this many bars back, so old zones no longer paint across the whole
  chart.
- **Lighter zones:** default `Zone transparency` 92, fainter borders; POI keeps a stronger border.
- **Right gutter (v2.15):** every object that used to end 5 bars right of the last candle now ends `Annotation gutter` bars (default 12) right of it, and zone captions are right-aligned inside it, so no caption covers a live candle. The premium / discount gauge is 3 bars wide just beyond it, and the label column starts 4 bars beyond the gutter.
- **Three label tiers:** 1 decision (`PROT`, the nearest active `BSL` / `SSL`, `EQ`, `MAJOR HIGH/LOW`, POI captions), 2 structure (swing `BOS` / `CHoCH`, non-POI captions, `DEV`; swing `BOS` / `CHoCH` at full colour, other tier-2 text at 25 % transparency), 3 context (internal `BOS` / `CHoCH`, `iBSL` / `iSSL`, Asia, other pools, session titles; muted at 20 % transparency). `Global text scale` picks the sizes (v2.15.1, one step up from v2.15): Compact = small / small / tiny, Standard = normal / normal / small, Large = large / normal / normal. Zone captions inside boxes use the tier-1 size (POI) or tier-2 size (non-POI), so Standard prints them at `normal`. Only `MAJOR HIGH/LOW` is bold (see Limits).
- **One label column, one category per label:** `PROT`, `DEV▲`, `BSL`, `EQ`, `Asia H`, `iBSL` … are drawn as one column. Labels at nearly the same price merge only within a category (structure, liquidity, session, zone). When categories collide, the higher tier keeps the label and its colour; the others move into its tooltip and show as `+n`. Column labels are 60 % transparent.
- **No opaque boxes over candles:** BOS / CHoCH captions and event labels are outlined text. Session titles are off by default (`Show session titles`; the box colour already names the session); when on they appear only on the live session and the latest completed one of each kind, in the tier-3 size at the bottom of the box.
- **Zone captions:** text inside a box needs a box tall enough (zoom-aware) and a free slot;
  otherwise the caption joins the label column (zone category).

Not covered: Asian levels and session boxes and the current major/protected lines are not
limited by the window. If one of them squashes the candles, switch it off in its group.

### Settings

Inputs are grouped as in the indicator's settings dialog.

**01 Market Structure** — `External swing publication` (on displacement / next opposite pivot), `Protected level` (trailing / last confirmed swing), `Swing sensitivity` (Auto/Manual), manual pivot bars,
`Minimum external leg displacement` (1.50 ATR), `External confirmation threshold`
(0.15), `BOS/CHOCH close buffer`, extended
major high/low, internal structure, draw BOS/CHoCH, external event history (6).

**02 Liquidity** — show pools, nearest unswept internal swing marker (`iBSL` / `iSSL`), highlight grabs, `EQH / EQL tolerance` (0.15 ATR),
`Minimum bars between independent tests` (8), `Minimum pullback between tests`
(0.5 ATR), `Sweep penetration` (0.01 ATR), `Sweep return rule` / window,
`Pool break buffer` (0.10 ATR), `Closes beyond a pool to consume it` (2),
`Consumed pool policy` and fade window (40), retained (24) / visible (6) pools,
`Maximum visible pool distance` (30 ATR), pool maximum age, previous-day pools
(off), `Minimum rejection wick for grab highlight` (0.20 ATR), grab history (10).

**03 Order Blocks & Zones** — (v2.2) `Show base-and-impulse supply / demand`, pattern filter, base candle limits, departure gates, proximal edge, retained base zones; show OBs, internal OBs, `Origin search window` (40),
`POI: OB-to-break proximity` (12), `Minimum displacement from OB` (1.0 ATR),
`Minimum impulse candle body` (0.6), `Maximum origin candle range` (2.5),
`Base tolerance vs leg extreme` (0.5), wicks, `OB mitigation method`
(Close/Wick/Midpoint), visible bullish / bearish OBs (2 / 2), `Maximum visible OB
distance` (25 ATR), midpoint line, supply/demand switch, departure (0.8) and width (1.0).

**04 Fair Value Gaps** — show FVG, `FVG definition` (Engine / Loose), minimum gap (0.10 ATR) and middle-candle body
(0.5 ATR) for Loose, invalidation method.

**05 Sessions** — Asian show/hours/timezone/retained ranges, `Draw a range box for each session`, London and New York hours and timezones (defined in local time, so daylight saving is handled), retained boxes per session (3), box fill transparency (93; higher is fainter).

**06 Visuals & Performance** — display profile, `Show premium / discount range`, `Keep candles readable` toggle, `Candle-scale window` (15 ATR), `Candle range reference` (300 bars), `Maximum drawn length` (250 bars), colours, zone transparency (92),
retained zone state (80), visible qualified zones (8), `Maximum visible zone
distance` (10 ATR, FVG and supply/demand), actionable POI distance (3 ATR), POI
structure window, label and event limits, zone maximum age (600 bars), global text scale, annotation spacing, `Annotation gutter` (12); `Show session titles` is in 05 Sessions.


**07 Key Levels** — `Show key levels` (on), `Key level definition` (Engine v6 / Selective v7), `Key levels per side` (3), `Key level window (0 = auto)`, `Round step (0 = by definition: XAU 5 / 10)`, `Show key level band` (off), `Key level colour`.

**08 Volume Profile** — `Volume profile` (Auto / Manual / Off), `Value area %` (70), `Show naked POCs` (on), `Volume profile colour`; Manual only: `Manual: period`, `Manual: intrabar timeframe (minutes)`, `Manual: rows`, `Manual: profiles drawn`.

### Alerts

All fire on bar close. Create them from the indicator's alert dialog.

| Alert | Fires when |
| --- | --- |
| `ApexVoid Bullish / Bearish BOS` | External close-confirmed break in the bias direction |
| `ApexVoid Bullish / Bearish CHOCH` | External close-confirmed break against the bias (v2.13: a close beyond the trailing protected level) |
| `ApexVoid Buy-Side / Sell-Side Sweep` | A liquidity pool is swept (once per pool) |
| `ApexVoid External Buy-Side / Sell-Side Sweep` | The current external swing is swept |
| `ApexVoid Buy-Side / Sell-Side Liquidity Consumed` | A pool is accepted through or broken by structure (once per pool) |
| `ApexVoid Demand / Supply Base Zone` | A new base-and-impulse zone is accepted |
| `ApexVoid Bullish / Bearish FVG` | A new FVG forms |
| `ApexVoid Bullish / Bearish OB` | A new qualified order block is accepted |
| `ApexVoid Asian Range Complete` | A new Asian range completes |

### Tuning notes

- **Too few order blocks:** raise `Visible bullish / bearish OBs`, raise `Maximum
  visible OB distance`, lower `Minimum displacement from OB`, or lower `Minimum
  impulse candle body`.
- **Too many / noisy order blocks:** switch off internal OBs, raise displacement, or
  use `Midpoint` or `Wick` mitigation so spent blocks leave sooner.
- **Block disappears after a wick through it:** that is the `Wick` / `Midpoint`
  mitigation rule; the default `Close` keeps it as Tested until a close beyond the
  far edge.
- **Too many liquidity lines:** lower `Visible liquidity pools` or switch to
  `Minimal`; remember lone minor pivots are never drawn.
- **Missing equal highs/lows:** raise `EQH / EQL tolerance` or lower `Minimum bars
  between independent tests` / `Minimum pullback between tests`.
- **Different symbols:** every threshold is ATR-based, but defaults were tuned on one
  week of XAUUSD; retune sensitivity and gates per instrument.

### Limits

- Compile errors, label placement, object limits and alert behaviour are only checked in
  TradingView; nothing in this repository compiles or tests the Pine file.
- The indicator sends no orders and tracks no trades (the v2.8 paper trade plan was removed in v2.18).
- Defaults were tuned on a week or two of XAUUSD; retune per instrument.
- Bold text: Pine's `text_formatting` appears to need a constant value, so a tier cannot switch it on at run time; only `MAJOR HIGH/LOW` is bold. Tier 1 stands out by size and full colour instead.
- The candle-scale protection works on the last `Candle range reference` bars, not on the visible window. Asia lines and session boxes are not clipped.

### Version history

| Version | Change |
| --- | --- |
| 2.21 | **Runtime fix for `RE10110` (the script takes too long to execute, 0.5 s on the free plans), not measured in TradingView.** The volume profile is recomputed over every intrabar of the period on the last bar; on a 15m chart that was up to 1,380 one-minute intrabars (a trading day), and when a profile outgrew 3,000 intrabars it was thinned with 1,500 `array.remove` calls. v2.21 uses coarser automatic intrabar timeframes (15m chart: 3m, 30m: 5m, 1h to 2h: 15m, 3h to 4h: 60m, so a day is at most 460 intrabars), caps a profile at 800 intrabars (when it passes that, neighbours are merged pairwise and the tail popped, no `array.remove`), and nothing else changes: same periods, rows, POC and value-area rules. If the error persists, set `Volume profile` to `Off` and use TradingView's Profiler (Pine Editor, More, Profiler) to see which block is slow. |
| 2.20 | Volume profile inside the main indicator (tick volume): POC, value area, naked POCs, polylines, `request.security_lower_tf` of the plain high / low / volume, period and intrabar timeframe follow the chart timeframe (`Auto`). Right-column captions get a category `volume` (listed together, appended next to other labels). New group `08 Volume Profile`, `max_polylines_count` 20. Display only; no measured edge. |
| 2.19 | The Market Intelligence panel (the table) and the state that only fed it are removed. |
| 2.18 | The trade plan (inputs, paper tracker, `Trades` row, four alerts) is removed. |
