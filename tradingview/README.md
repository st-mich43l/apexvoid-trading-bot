# TradingView indicators

This directory contains chart-side analytical tools that accompany the ApexVoid
trading stack. They are reference and discretionary-analysis tools; they are not
loaded by the Go analysis engine and cannot place orders through the bot.

| Path | Purpose |
| --- | --- |
| `apexvoid_price_action.pine` | The indicator (Pine Script v6, v2.27). |
| `apexvoid_price_geometry.pine` | Price Geometry v1.0: major trendlines and parallel price channels (Pine Script v6), a companion to Price Action. |
| `COLOR_SCHEMES.md` | Colour schemes of Price Action v2.27 (dark and light variants, contrast tables) and the matching TradingView candle and background settings. |
| `CHART_THEMES.md` | 14 ready-made TradingView chart colour packs (canvas, candles and matching indicator settings), each contrast-checked on its own background. |
| `tests/` | Offline reference models and fixtures: the Price Geometry rules and the Price Action v2.27 right-edge label layout (`python -m unittest discover -s tradingview/tests`). |

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

### Volume profile (v2.26: one profile)

One quiet histogram of where the **current period** traded, with its point of control (POC) and value area (VA). **The volume is tick volume**: on XAUUSD (OANDA, FXCM, cTrader) it counts price updates, a measure of activity, not contracts. Read it as "where price spent its time and activity", not as traded size. It is a display tool with **no measured edge**; nothing here trades it.

`Volume profile` = `Auto` (default) needs no setting; the period follows the chart timeframe:

| Chart timeframe | Profile period |
|---|---|
| 5m and below | engine session (Asia start, London start, New York start) |
| 10m to 30m | trading day |
| 1h to 2h | week |
| 3h to 4h | month |
| 1D and above | quarter |

`Manual` lets you pick the period (Session, Day, Week, Month) and the rows. `Off` (and the `Minimal` display profile) draws nothing.

**How it is built (and why it cannot hit `RE10110`).** On the last bar only, from the period's own **chart bars** (at most the last 480): each bar's tick volume is spread evenly over the price rows its high-low range spans. The range is split into 16 to 40 rows (`range / (0.25 x ATR)`). There is **no lower-timeframe request, no per-bar accumulation and no stored history**: the cost is one bounded loop (about 480 bars x 40 rows at most) whatever the chart length. The only per-bar work is remembering where the period started. The trade-off is resolution: a chart bar's volume is spread evenly over its range instead of following its intrabars, so the shape is an approximation of the intrabar profile (the v2.20 to v2.25 builds).

**How to read it.**
- **POC** (solid line to the right edge, caption `POC`): the row with the most activity (the higher row on a tie).
- **VA** (`VAH` / `VAL`, dotted lines; the darker part of the histogram): the rows around the POC that hold `Value area %` (70) of the volume, built the CBOT way: from the POC, compare the next two rows above with the next two below and add the larger pair, until the share is reached.

**Drawing.** The histogram is a filled **polyline** anchored at the period's first bar (its width is 30 % of the period length at the busiest row); the three lines run from the same bar to the right edge so the profile is readable even when the histogram is scrolled out of view. Captions go to the right label column as category `volume profile`.

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
- **One label column (the annotation rail, v2.27):** `PROT`, `DEV▲`, `BSL`, `EQ`, `Asia H`, `iBSL`, key levels, `POC` / `VAH` / `VAL` and zone captions that do not fit inside their box are drawn as one column of short rows; see *Right-edge labels* below. `Right-edge labels` = `Legacy (v2.26)` restores the v2.26 merged one-line captions.
- **No opaque boxes over candles:** BOS / CHoCH captions and event labels are outlined text. Session titles are off by default (`Show session titles`; the box colour already names the session); when on they appear only on the live session and the latest completed one of each kind, in the tier-3 size at the bottom of the box.
- **Zone captions:** text inside a box needs a box tall enough (zoom-aware, and from v2.27 also scaled by `Global text scale`) and a free slot;
  otherwise the caption joins the label column (zone category). A zone gets one caption, never both.

Not covered: Asian levels and session boxes and the current major/protected lines are not
limited by the window. If one of them squashes the candles, switch it off in its group.

### Colour schemes (v2.27)

`06 Visuals & Performance` → `Colour scheme`: **ApexVoid** (default; on a dark chart exactly the v2.26 colours), **Classic**,
**Colorblind safe** (blue / orange, Okabe-Ito), **Ocean**, **Muted**, or **Custom** (every per-category colour input as set).
`Chart theme` (Auto / Dark / Light; Auto reads the chart background) picks each scheme's dark or light variant. Every scheme colour is
at least 4.5:1 against its chart background (`#131722` dark, white light), and caption text, label backgrounds and text outlines
follow the theme, so the indicator reads on both black and white charts. A per-category colour changed in the inputs always
overrides the scheme for that category (both themes); inputs left at their defaults follow the scheme, so charts with customised
colours keep them. Palettes, contrast tables and the matching candle /
background settings for TradingView are in [`COLOR_SCHEMES.md`](COLOR_SCHEMES.md). Rendering only: nothing analytical depends on a colour.

`Candle colours` (Off / **Scheme** default / Neutral / Structure bias) recolours the chart's candles with `barcolor()`: the scheme's
quieter up / down candle colours, graphite / slate, or the external bias (with-bias candles in colour, counter-bias candles grey).
`Up candle colour` / `Down candle colour` override the scheme's candle colours once changed. `Off` leaves the chart's own candle
settings. Display only; details in [`COLOR_SCHEMES.md`](COLOR_SCHEMES.md).

### Right-edge labels (v2.27)

`06 Visuals & Performance` → `Right-edge labels`: **Smart** (default), **Compact** or **Legacy (v2.26)**. One shared layout; only the
row content differs. Nothing here changes a price, a zone, a state or an alert: the rail only decides where existing annotations are
written.

- **Rows, not sentences.** Each annotation is one row: name, badge, then the price for levels that show one, in a monospace column:
  `KL ×3    3548.04`, `POC      3549.67`, `EQ       3551.45`, `PROT`, `FVG · T`, `◆ D+OB ×2`. Touch counts (`×n`), in play (`●`),
  tested (`· T`), POI (`◆`), `pAsia` and `DEV▲` / `DEV▼` keep their meaning. Key levels show their own price (the round price for
  a round level, as before).
- **Groups only when genuinely close, and only within one colour family.** Key levels, POC / VAH / VAL, pools, iBSL / iSSL and EQ are
  the neutral family (EQ stays EQ, a dealing-range level, not a volume statistic); every coloured annotation (PROT, DEV, zones, Asia)
  groups only with the same colour, so a bearish `PROT` stays bearish next to a key level. A member joins a group when it is less than
  one row below the previous member **and** at most two rows below the group's top member, with at most four rows: a chain of near
  neighbours no longer grows into one tall cluster (v2.26 merged any chain whose neighbours were close). Rows are in descending price
  order. Grouping is about space on the chart, not confluence.
- **Collision handling with real heights.** Each group is centred on its members' prices and moved as little as possible so that no
  two groups overlap, counting every row of a multi-row group; price order is always kept. Decision levels (tier 1) move least
  (weights 100 / 10 / 1 by tier). When a group sits more than half a row from its price, a thin connector runs from the price at the
  gutter to the label (at most 8). Labels never leave the column (`gutter + 4` bars right of the last candle), so they cannot cover a
  candle or the premium / discount gauge.
- **Budget.** At most 20 groups (Compact 14). Over it, and when a context group would be pushed more than 3 rows from its price, the
  lowest-priority tier-3 groups without a price (Asia, secondary pools, iBSL / iSSL, …) are hidden first and listed with their prices
  in the tooltip of the nearest shown label (`Also near this price`). A key level, POC / VAH / VAL, EQ or any tier-1 annotation is never
  hidden.
- **Colour and size.** Coloured groups use their category colour (tier 1 full, tier 2 at 10 %, tier 3 at 25 % transparency); neutral
  groups use neutral text (`#CBD5E1`, tier-3-only groups `#94A3B8`). Matte background `#161923` at 18 % transparency (v2.26: `#090D18`
  at 60 %), left-aligned monospace text. Sizes follow `Global text scale` and the tier as before; Compact mode uses one step smaller.
- **Compact** shows one row per group (its highest-priority member) plus every row that carries a price, and counts the rest as `+n`;
  the tooltip lists everything.
- **Row height is an estimate:** `max(Annotation vertical spacing × ATR, candle range / 36)` × 0.85 / 1.0 / 1.25 for Compact /
  Standard / Large text. Pine exposes no pixel geometry, so on a strongly zoomed chart rows can still touch or leave gaps; there is no
  pixel-perfect collision avoidance.
- **Right margin:** unchanged at `Annotation gutter` + 15 bars (27 by default). Rows are about 16 characters wide instead of the v2.26
  captions of up to ~45, so the column needs less room than before.
- **Performance and objects:** everything runs once, on the last bar: one price sort, grouping in one pass, a one-pass order-preserving
  solver (run at most twice), labels and connectors reused from pools (`label.set_*` / `line.set_*`) and surplus objects deleted. No
  request, no table, no per-bar work, no change to `max_*_count`. Rail objects: at most 20 labels (Compact 14; a chart whose unhideable
  levels exceed that shows them all) and 8 connector lines, against one label per cluster in v2.26. The offline reference model of the
  layout and its fixtures are `tests/annotation_rail_ref.py` and `tests/test_annotation_rail.py`.

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
distance` (10 ATR, FVG and supply/demand), POI
structure window, label and event limits, zone maximum age (600 bars), global text scale, annotation spacing, `Annotation gutter` (12), `Right-edge labels` (Smart / Compact / Legacy (v2.26)), `Colour scheme` (ApexVoid / Classic / Colorblind safe / Ocean / Muted / Custom), `Chart theme` (Auto / Dark / Light), `Candle colours` (Off / Scheme / Neutral / Structure bias), `Up candle colour`, `Down candle colour`; `Show session titles` is in 05 Sessions.


**07 Key Levels** — `Show key levels` (on), `Key level definition` (Engine v6 / Selective v7), `Key levels per side` (3), `Key level window (0 = auto)`, `Round step (0 = by definition: XAU 5 / 10)`, `Show key level band` (off), `Key level colour`.

**08 Volume Profile** — `Volume profile` (Auto / Manual / Off), `Value area %` (70), `Volume profile colour`; Manual only: `Manual: period`, `Manual: rows`.

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
  TradingView; nothing in this repository compiles or runs the Pine file. The offline models in `tests/` check the rules, not the Pine source.
- The indicator sends no orders and tracks no trades (the v2.8 paper trade plan was removed in v2.18).
- Defaults were tuned on a week or two of XAUUSD; retune per instrument.
- Bold text: Pine's `text_formatting` appears to need a constant value, so a tier cannot switch it on at run time; only `MAJOR HIGH/LOW` is bold. Tier 1 stands out by size and full colour instead.
- The candle-scale protection works on the last `Candle range reference` bars, not on the visible window. Asia lines and session boxes are not clipped.

### Version history

| Version | Change |
| --- | --- |
| 2.27 | **Smart annotation rail (rendering only; not compiled or run in TradingView).** The right-edge column is rebuilt as collection (structured fields per annotation), layout and rendering: short rows with the price in a monospace column instead of merged sentences such as `PROT · KL 3565.83 ×2 · 3558.42 ×3 · VAH 3560.65`; groups only within one colour family and with a bounded span (no chain merging); multi-row heights in an order-preserving, tier-weighted collision layout; connectors for displaced labels; a 20-label budget that hides only low-priority unpriced context and names it in a tooltip. New input `Right-edge labels` (Smart / Compact / Legacy (v2.26)). Zone-caption fit follows the text scale. Colour schemes (ApexVoid, Classic, Colorblind safe, Ocean, Muted, Custom) with dark and light variants chosen by `Chart theme` (Auto reads the chart background), theme-aware caption text, backgrounds and outlines; see *Colour schemes (v2.27)* and `COLOR_SCHEMES.md`. Structure, zones, liquidity, key levels, volume profile, premium / discount and alerts are unchanged. See *Right-edge labels (v2.27)*. |
| 2.26 | **One volume profile, built from chart bars on the last bar (not measured in TradingView).** The profile of v2.20 to v2.25 accumulated lower-timeframe intrabars on every bar (`request.security_lower_tf` over the whole history plus per-bar array work), which is where the volume profile's share of `RE10110` came from. v2.26 draws one profile of the current period, computed on the last bar from at most 480 chart bars: no lower-timeframe request, no per-bar accumulation, no stored history. Naked POCs, completed-profile history and the intrabar / profiles-drawn inputs are gone; period rules, rows, POC and value-area rules are unchanged. Approximation: a chart bar's volume is spread evenly over its range. |
| 2.25 | **The volume profile stays on in every mode, and now costs almost nothing outside the last 900 bars (not measured in TradingView).** v2.23's `Free plan (fast)` switched it off; that is reverted (fast mode keeps it). The profile only needs the developing period plus the completed periods that are drawn or kept as naked POCs, so it is accumulated on the last 900 bars only (9 days on 15m, 37 days on 1h, 150 days on 4h, 3 days on 5m); older bars do no volume-profile work at all. Periods, intrabar timeframes, rows, POC and value-area rules are unchanged. With v2.24's `calc_bars_count = 2000` the intrabar request covers at most 2,000 chart bars. |
| 2.24 | **`calc_bars_count = 2000`: the fourth `RE10110` fix, not measured in TradingView.** The error survived v2.21 to v2.23 (all of which only shortened the last bar), so the limit is probably being spent over the whole history, not on one bar: a free plan loads up to 10,000 bars and the script runs its structure, pool, zone and session logic on every one. The script now calculates only the last 2,000 bars (about 21 days on 15m, 7 on 5m, 1.4 on 1m; every window it uses is at most 800 bars, zones age out after 600), which cuts the total work 5x on a 10,000-bar chart. Objects are drawn at most 250 bars back, so nothing visible changes; the first ~800 calculated bars are the warm-up of the structure state. If it still fails, use More, Profiler in the Pine Editor and send the table. |
| 2.23 | **`Performance` input (06 Visuals & Performance), default `Free plan (fast)`: the third `RE10110` fix, not measured in TradingView.** v2.21 and v2.22 reduced individual blocks and the error persisted, so the last bar is now bounded by configuration rather than by guesses: in `Free plan (fast)` the nearest-internal-swing markers (iBSL / iSSL) are off (v2.23 also switched the volume profile off; v2.25 restored it), at most 30 zones and 12 liquidity pools are retained (the pairwise zone passes shrink about 7x) and the key level window is at most 150 bars. `Full` restores every setting as configured and needs a plan whose limit is longer than 0.5 s. Everything else (structure, BOS / CHoCH, pools, zones, alerts) is unchanged in both modes. |
| 2.22 | **Second runtime fix for `RE10110` (0.5 s on the free plans), not measured in TradingView (it showed on AV PA v2.21, XAUUSD M15, so the v2.21 volume-profile change was not the cause or not the only one).** Work removed from the last bar and from event bars, with identical output: (1) the "analytical POI", "actionable POI" and POI-barrier passes (three loops over all retained zones, two of them nested) fed only variables that nothing has read since the panel was removed in v2.19, so they are deleted, with the unused `Actionable POI distance` input; (2) the POI qualification walked every zone against every other zone twice (once inline, once in `f_poiPath`): the overlap relation is symmetric, so it is now walked once per pair (about a quarter of the iterations); (3) `f_pullbackOk` re-walked up to 750 bars of history for every equal-high / equal-low candidate, up to 80 candidates per pivot in a range: one running high / low table per pivot is now shared by all of its candidates. Pools, zones, POIs, captions and alerts are unchanged. If the error persists, set `Display profile` to `Minimal` to confirm it is a feature, then switch features off one at a time and report the first that clears it (or use TradingView's Profiler: Pine Editor, More, Profiler). |
| 2.21 | **Runtime fix for `RE10110` (the script takes too long to execute, 0.5 s on the free plans), not measured in TradingView.** The volume profile is recomputed over every intrabar of the period on the last bar; on a 15m chart that was up to 1,380 one-minute intrabars (a trading day), and when a profile outgrew 3,000 intrabars it was thinned with 1,500 `array.remove` calls. v2.21 uses coarser automatic intrabar timeframes (15m chart: 3m, 30m: 5m, 1h to 2h: 15m, 3h to 4h: 60m, so a day is at most 460 intrabars), caps a profile at 800 intrabars (when it passes that, neighbours are merged pairwise and the tail popped, no `array.remove`), and nothing else changes: same periods, rows, POC and value-area rules. If the error persists, set `Volume profile` to `Off` and use TradingView's Profiler (Pine Editor, More, Profiler) to see which block is slow. |
| 2.20 | Volume profile inside the main indicator (tick volume): POC, value area, naked POCs, polylines, `request.security_lower_tf` of the plain high / low / volume, period and intrabar timeframe follow the chart timeframe (`Auto`). Right-column captions get a category `volume` (listed together, appended next to other labels). New group `08 Volume Profile`, `max_polylines_count` 20. Display only; no measured edge. |
| 2.19 | The Market Intelligence panel (the table) and the state that only fed it are removed. |
| 2.18 | The trade plan (inputs, paper tracker, `Trades` row, four alerts) is removed. |

## ApexVoid Price Geometry (v1.0)

A companion to Price Action: it draws only **major trendlines** (ascending support, descending resistance) and **parallel price channels**, through
actual confirmed swing reactions. It adds no structure, zones, liquidity, key levels, volume, oscillators, signals, entries, stops, targets or scores.
Horizontal levels are Price Action's. A line or channel is a geometric reference, not guaranteed support or resistance; the number of touches is
evidence of geometry, not a probability.

**Status: written without a TradingView compiler.** The Pine source has not been compiled, run or replayed in TradingView in this repository; compile
it there first. Its rules are mirrored by an offline reference model (`tests/price_geometry_ref.py`) with fixtures (`tests/test_price_geometry.py`);
those fixtures check the mathematics of the rules, not the Pine source.

### Install

Pine Editor → paste `apexvoid_price_geometry.pine` → Save → Add to chart. It is independent of Price Action; both can be on the chart together
(both draw on the price scale). Start with XAUUSD M5 or M15 and the defaults.

### Swings

- A **raw pivot** n bars back is a high strictly above each of the n bars before it and at least as high as each of the n bars after it (lows mirrored); it
  is known only when the n right-hand bars have closed. n is the pivot length: `Auto` = 6 up to M15, 7 on M30, 8 on H1 and H4, 10 above (Price Action's
  external swings use 12/16/20; these are major reactions for line anchors, finer than external structure, coarser than internal); `Manual` sets it.
- **Significant swings** alternate high / low: a pivot on the same side as the last swing replaces it only when more extreme; a pivot on the other side is
  kept only when price moved `Minimum swing displacement` × ATR(14) at the pivot bar from the last swing. At most `Maximum retained swings` are kept.
- Raw pivots (the last 64 per side) are the reactions that can be independent touches; significant swings are the only line and channel anchors.

### Trendline mathematics

Anchors (B1, P1) and (B2, P2), both significant swings of the same side (lows for support, highs for resistance), wick prices:
`m = (P2 − P1) / (B2 − B1)`, `L(t) = P1 + m·(t − B1)` (arithmetic; it reproduces both anchors exactly). ATR is only a tolerance and a significance
unit, always the ATR **of the bar being judged**.

A candidate (searched only when a swing of the side changes or the active line ends: the 3 most recent swings as B2, the 6 before each as B1, at most
18 pairs) is published only if, scanning every closed bar from B1 + 1 to now:

1. B2 − B1 ≥ `Minimum anchor separation`, B1 within `Maximum candidate lookback`;
2. the slope points the right way (support rises, resistance falls) by at least 0.5 ATR between the anchors (else effectively horizontal);
3. no `Break confirmation closes` consecutive closes beyond L(t) by more than `Break tolerance` × ATR(t) (a candidate already broken before
   publication is rejected);
4. no wick beyond L(t) by more than 0.5 ATR(t) (smaller wick penetrations are tolerated);
5. between the anchors price moved at least `Minimum swing displacement` ATR away from the line (on the side it holds);
6. the line is within 20 ATR of the last close.

**Selection** among valid candidates, lexicographic (no weighted score): more independent touches (3 or more count alike); larger displacement between
the anchors in whole ATRs (capped at 4); longer span B2 − B1; the line nearer the close; the later B2, then the later B1.

**Independent touches.** The two anchors are touches 1 and 2. Another touch is a raw pivot of the side whose wick lies within `Touch tolerance` ×
ATR of L(t), at least `Minimum anchor separation` bars after the previous touch (and before B2 by as much, for a touch between the anchors), after price
moved at least `Minimum departure between touches` ATR away from the line since the previous touch. Bars resting on the line are one pivot, so one touch;
an anchor is never counted twice. Touches before publication count toward the style; touches after publication are counted when their pivot is confirmed
(optional markers, off by default, mark only those).

### Lifecycle

`Candidate → Active → Broken / Retired`. An active line keeps its anchors and slope and is extended `10` bars past the last bar; it is never refitted.

- **Broken**: `Break confirmation closes` (1 by default, or 2 consecutive) closed bars beyond L(t) by more than `Break tolerance` × ATR(t). A wick alone
  never breaks a line. A break is a geometric event, not a BOS, CHoCH or reversal.
- **Retired**: untouched for `Retire a line untouched for` bars, farther than 20 ATR from the close, or replaced.
- **Replacement**: an active line is replaced only by a candidate with more touches (counted up to 3) whose B2 is later; a new pivot alone never moves it.
  After a break or retirement the best candidate of the side is published at once if one is valid.
- Broken lines are deleted, or, with `Show broken geometry`, kept faded and dotted, ending at the break bar (at most `Maximum retained historical drawings`).

### Channels

On the active line with the later second anchor (support on a tie): the opposing significant swing after B1 (a swing high above a support, a swing low
below a resistance) with the largest offset `d = Q − L(O)` (the later swing on a tie). The opposing boundary is `C(t) = L(t) + d`, the midpoint
`M(t) = L(t) + d/2`: same slope, constant width, through the opposing reaction exactly. It is built once, from swings confirmed by then (when the line is
published, when an opposing swing is confirmed, or when the previous channel ended), and only if `Minimum channel width` ≤ |d| / ATR ≤ 15 and no close
went beyond C(t) by more than the break tolerance since B1 (within the lookback). It is never widened: a later confirmed close beyond the opposing boundary
is a **breach** (the boundary turns dotted); the channel ends with its primary line. One channel at a time; the primary line is reused, never drawn twice.

### Repainting and publication

Swings, publication, touches, breaks, retirement, channels and breaches are processed on closed bars only and read no later bar. A line is drawn from its
first anchor, but it exists only from its **publication bar** (the bar on which the second anchor's pivot was confirmed and the candidate passed): the
anchor-to-anchor segment is the geometric construction, not a line that was known at the first anchor. The forming bar only moves the forward projection.
With the same history and settings the result is the same after a refresh; a different amount of loaded history can change the first swings.

### Performance

No `request.*` calls; chart OHLC only. Ordinary bars do constant work (two pivot tests, two line updates). Candidate searches (at most 18 pairs, each a
bounded scan with early exit) run only when a swing changes or a line ends. Objects: normally 2-3 lines, captions and markers bounded
(`max_lines_count = 40`, `max_labels_count = 40`), `max_bars_back = 1000`. Not measured in TradingView, so no claim about `RE10110`.

### Defaults (engineering starting points, not optimised)

Auto pivots; swing displacement 1.5 ATR; 24 swings; anchor separation 10 bars; touch tolerance 0.20 ATR; departure 1.0 ATR; break tolerance 0.15 ATR,
1 close; retire after 600 untouched bars; channel minimum width 2.0 ATR; lookback 250 bars; support, resistance and channels on; midpoint, fill, markers,
captions and broken geometry off.

### Limits

- **Arithmetic geometry only.** On a logarithmic chart TradingView draws the line straight in log space between its two drawn points, so between them it
  departs from L(t); the rules (touches, breaks) always use the arithmetic L(t).
- Sideways markets usually get no line, by design. Converging patterns, wedges and triangles are out of scope.
- Every threshold is a hypothesis chosen from geometry, not fitted to results; retune per instrument and timeframe.
- Not compiled or replayed in TradingView here; the fixtures check the reference model only.
