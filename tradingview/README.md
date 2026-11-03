# TradingView indicators

This directory contains chart-side analytical tools that accompany the ApexVoid
trading stack. They are reference and discretionary-analysis tools; they are not
loaded by the Go analysis engine and cannot place orders through the bot.

| Path | Purpose |
| --- | --- |
| `apexvoid_price_action.pine` | The indicator (Pine Script v6, v2.13). |
| `reference/` | Python models of the indicator's order-block, liquidity, supply/demand and trade-plan logic with rule checks on real XAUUSD closed bars (`python tradingview/reference/test_<name>.py`). They validate the algorithm only; they do not compile or draw the Pine file. |

## ApexVoid Price Action

A market-structure indicator that draws structure, order blocks, imbalances,
supply/demand, liquidity pools and session levels, then summarises them in a small
"Market Intelligence" panel. Every structural event and every zone state change
needs a **closed** chart bar, so nothing repaints once a bar has closed. A zone,
pool or sweep is an analytical reference, not an entry signal. Since v2.8 the
indicator can also draw a rule-based **trade plan** as long/short position boxes (last 5
trades) with paper-tracked results; it sends no orders and the rules have not shown an edge

### Install

1. Open TradingView's Pine Editor, paste `apexvoid_price_action.pine`, save, and
   add it to a chart.
2. Suggested start: XAUUSD on M5 or M15, default settings (`Balanced — Recommended`
   profile, `Compact` panel). Tune per symbol afterwards.
3. TradingView is the only authoritative compiler. After any edit, compile there and
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
| `DEV HIGH` / `DEV LOW` | dashed line plus a live leg | The unconfirmed extreme of the current leg. Never labelled HH/LL. |
| `PROTECTED` | dashed line | The external level whose break would flip bias. Since v2.13 it **trails**: each internal break in the bias direction moves it toward price (never away), and a close beyond it is the CHoCH. |
| `OB` / `iOB` | box with thin border, midpoint dotted line, right-aligned label | Swing / internal order block. Dashed border and `· T` when Tested. |
| FVG | blue (bullish) / orange (bearish) box | Three-candle fair value gap. |
| `Demand` / `Supply` | green / pink box | Zone at a confirmed, displaced external swing. |
| `Demand · DBR` … `Supply · DBD` | same colours | Base-and-impulse zone; the suffix names the pattern (below). |
| `POI · Supply`, `POI · Bear OB + FVG`, … | thicker border | A zone that passed a named qualification path (below); the caption names the zone kind (`Supply`, `Demand`, `Bull/Bear OB`, `Bull/Bear FVG`, or a combination such as `Supply + OB`). |
| `PREMIUM` / `DISCOUNT` / `EQ` | a slim rose / green gauge at the right edge and a faint dashed 50 % line | Upper half (supply side) and lower half (demand side) of the **dealing range**, and its equilibrium. Since v2.13 the dealing range is the **protected leg** (bear: the protected high down to the lowest low since it; bull: mirror), so price is always inside it; without a bias it is the confirmed swing range extended to the live extreme of the leg. Clipped to the candle-scale window. |
| `BSL` / `SSL` | cool-grey dotted line, right-edge label | Buy-side liquidity above price / sell-side liquidity below (confirmed pools). |
| `iBSL` / `iSSL` | cool-grey dashed line, small label | The nearest internal swing high above / low below price that nothing has traded through since and that is not already a pool. A marker only: no state, no alerts. Switch off with `Show nearest unswept internal swing high / low`. |
| `EQH · BSL` / `EQL · SSL` | narrow grey band plus line | Equal highs / lows formed by two or more independent tests. |
| `PDH · BSL` / `PDL · SSL` | as above | Optional previous-day high / low. |
| `… · swept` / `… · taken` | faded line that stops at the event bar | A pool that was grabbed / consumed. |
| Short vertical bar on a wick | 2 px, rose on an upper wick, teal on a lower wick | Liquidity grab: the rejection wick after a sweep. |
| `Asia H · live` / `Asia L · live` | solid purple lines that grow with the session and update every bar; labels in the right-edge column | The Asian range as it forms. Since v2.13 Asia is the engine's session: 07:00 Tokyo (22:00 UTC) until London open, so the range is the one `asia_range` trades. When the session ends it becomes the completed range below. |
| `Asia` / `London` / `New York` boxes | faint fill with a clearer dashed outline and title per session, so the session reads at a glance without tinting the technical zones (purple / blue / amber), titled with the session name | The candle range of each session: from its first bar to its last bar, spanning the session high and low. The box grows while the session runs and freezes when it ends; the last 3 per session are kept. London and New York overlap by design. |
| `Asia H` / `Asia L` | purple dashed | Last completed Asian-session range (`Prev Asia H/L` while a session is live). |

Colours are inputs. Liquidity uses a neutral grey on purpose so it cannot be
confused with BOS/CHoCH (teal/rose) or Asian levels (purple).

### How it works

**Structure.** Internal swings use short pivots, external swings long ones; both
scale with the chart timeframe (internal 3 / 4 / 5 / 6 / 8 bars and external
12 / 16 / 20 / 24 / 30 bars for charts up to 5m / 15m / 1h / 4h / above; `Manual`
overrides them). **External swing publication (v2.13):** the candidate is the running
extreme since the last published swing (a zigzag extreme, not a pivot); it is published
on the first closed bar where `externalLength` bars have passed without a further
extreme **and** the counter-leg has reached `Minimum external leg displacement` plus the
confirmation threshold (1.65 ATR of the candidate bar by default). v2.12 waited for the
next opposite pivot to confirm, a median of 27 / 34 / 54 bars after the swing on M5 / M15 /
H1; v2.13 publishes at `externalLength` bars (12 / 16 / 20) when the leg is already
displaced (`External swing publication`: `Next opposite pivot (v2.12)` restores the old
rule). Small wiggles still never become major swings. Bias comes from external breaks
only; internal breaks never change it. All distances are ATR(14)-normalised.

**Protected level and CHoCH (v2.13).** With `Protected level` = `Trailing (internal BOS)`
(default) the protected high (bear bias) is the pullback high the latest internal bearish
break came from, and it only ratchets toward price; a newly published swing replaces it
only when it is nearer. A **confirmed close beyond the protected level is the CHoCH**, even
when the last confirmed swing is further away: it flips the bias, draws the line from the
protected bar to the first close beyond it, builds the order block on that event, fires the
CHoCH alert and sets the new protected level to the leg's extreme since the old one. A
break of the last confirmed swing in an agreeing bias stays a BOS. `Last confirmed swing
(v2.12)` reproduces the old assignment. A trailing level flips the bias more often than a
confirmed swing does: on the committed XAU captures CHoCH counts roughly double.

**Protected level, v2.14.** Two changes in the trailing mode (the v2.12 option is untouched). (1) *The higher low that made a higher high is promoted.* When a major high is
confirmed in a bull bias, the protected low becomes the most recent **internal** pivot low between the protected bar and that high, if it is higher (bear mirrors with the
lower high). (2) *A protected-level CHoCH takes the last internal pivot, not the start of the move.* The new protected low is the most recent internal pivot low between the
broken protected high and the break; only without one does it fall back to the lowest low since the protected high (the v2.13 rule, which could reach two days back and
left a flip level about 3.7 ATR from price on M30). The script keeps the last 8 internal pivot lows and highs for this; a pivot is used only after its right bars have closed.
A protected level that moves toward price in the bias direction (an internal BOS, or the promotion) also puts the **developing leg** on the bias side: DEV, the directional
leg, the phase and the dealing range now describe the same leg (v2.13 could print "Counter-trend rebound" with DEV HIGH sitting on the protected high).

**Order blocks (v2.1).** Built when an internal or external BOS/CHoCH closes (v2.13: searched from the
bar that really broke, not from the later publication bar). The
origin is the opposite-colour candle at the extreme of the leg between the broken
pivot and the break, and it must pass gates for base position, range, net
displacement and an impulse candle. Fresh → Tested (price entered) → Mitigated
(your chosen rule) / Invalidated (close beyond the far edge). A partial touch never
invalidates. Blocks have their own display budget (default 2 bullish + 2 bearish)
and are never deleted just because they are hidden. No volume is used.

**Base-and-impulse supply / demand (v2.2).** Independent of swing pivots. A base is 1–5
small candles; a zone is built once price leaves it within 3 bars with a real impulse
candle and enough net distance (1.5 ATR and 2× the base height) without closing back
through it. Patterns: `DBR` / `RBD` reversals, `RBR` / `DBD` continuations (filterable).
Invalidated by a close beyond the far edge; a touch only marks it Tested. A fresh base
zone on the bias side qualifies as a `Base-impulse zone` POI.

**FVG.** A three-candle gap. `FVG definition` = `Engine (fvg v9)` (default, `docs/strategies/fvg.md`):
the gap and the middle candle's body are at least 0.5 ATR, the body at least half of its
own range, and the middle candle closed in the gap's direction. `Loose (v2.12 keys)` uses
`Minimum gap size` and `Middle-candle body` plus, new in v2.13, the body-direction test (v2.12
could print a bullish gap from a bearish middle candle). Invalidation follows `Wick`, `Close` or `Midpoint`.

**Supply / demand.** Created at a confirmed external swing that was followed by a
departure of at least `Supply/demand departure`; width is capped. Invalidated by a
close beyond the far edge. **Freshness (v2.13):** before a swing zone is drawn the bars
after its departure (`externalLength + 1` bars after the swing up to now) are scanned: a
close beyond the far edge means it is not drawn; a touch means it is drawn already
`Tested` (first touch and measured depth), never Fresh, never actionable and never a
trade trigger. On the captures, a third to a half of the zones v2.12 drew as Fresh had been
retested or closed through before they were drawn.

**POI.** A zone is a *qualified* POI only through a named path: `Composite`
(two different zone kinds overlap, and since v2.13 their origins differ by more than 3 bars: an
OB and the FVG its own impulse printed are one event), `Structure-origin OB` (a swing OB on the bias
side near the last break), `Displaced swing zone` (supply/demand on the bias side)
or `Swept imbalance` (an FVG on the bias side whose impulse followed a sweep: a pool or external sweep in
the 6 bars up to the gap's first candle; v2.12 also accepted a sweep after the gap). Proximity
and display rank never qualify a zone. A POI is *actionable* only if it is fresh,
on the correct side of price, within `Actionable POI distance` and has at least
1 ATR of room before the nearest opposing barrier.

**Liquidity pools (v2.1).** A pool is a level, not a pivot. It exists for a
confirmed external swing, for an equal high/low band (two independent tests within
`EQH / EQL tolerance`, spaced apart with a pullback between them) or, optionally,
the previous-day high/low. Lone internal pivots are never drawn.

| Event | Rule (defaults) |
| --- | --- |
| Sweep / grab | Wick beyond the edge by 0.01 ATR, then a close back inside within 2 bars (or the same bar). Pool becomes *Swept*. |
| Consumed | 2 consecutive closes beyond the edge by 0.10 ATR, or a structural BOS/CHoCH on the swing the pool rests on. |
| Retired | Faded for 40 bars after either event (policy is an input), or older than 1500 bars. |

A pool leaves the active state once, so each pool alerts once. A grab never
changes bias and never creates an entry.

**Sessions.** Defaults follow the engine's session calendar (`analysis.sessions.calendar`): Asia 07:00-17:00
Tokyo ended early at London open (a bar inside London is never Asia, so the end follows London daylight saving: 07:00 UTC on
23 Oct 2026, 08:00 UTC on 27 Oct), London 08:00-16:30 London, New York 09:00-17:00 New York. The Asian range is
drawn on the chart only, live while the session runs and then as the completed range (configurable); the panel does not show it. The live
and completed Asian high/low are also key levels for the trade-plan take-profit. On charts above 5m the session is evaluated on a 5-minute request.

**Multi-timeframe structure (not in this script).** v2.14 first shipped a panel row that requested the chart's own `bias` / `activeProtected` on two other frames
(`request.security` of a global series). TradingView rejected it: **104,204 compiled tokens against the 100,256 limit (CE10117)** at 33,514 source tokens. A
`request.security` of a global series compiles that series' whole dependency chain once per call (the structure state machine here), about 3,000 compiled tokens per
frame, and this script has no room for even one. The row, its inputs and the `chart against M15+H1` tag are therefore removed; the old optional HTF context stays removed
(it used a different algorithm and could contradict the HTF chart). The aligned multi-frame view is specified and verified in the model (`reference/mtf.py`,
`reference/test_mtf_v214.py`, `reference/report_v214.py`) for a small companion indicator that carries only the structure algorithm.

### Display profiles

| Profile | Zones drawn | Pools drawn | Other |
| --- | --- | --- | --- |
| Minimal | up to 4 | none | 3 external structure events, 1 Asian range |
| Balanced — Recommended | up to 8 (+ OB budget) | up to 8 (nearest BSL above and SSL below first) | 6 external events |
| Detailed | at least 12 | at least 12 | internal structure available |
| Debug | up to 20, distance limits off | all retained | `xEH` / `xEL` raw-pivot labels, OB and pool distance limits off |

### Keeping candles readable

Distant zones and lines stretch the price scale and squash the candles, and heavy fills
hide them, so:

- **Candle-scale protection** (`Keep candles readable…`, default on): a zone, order block
  or liquidity pool is drawn only if it is within `Candle-scale window` (15 ATR) of the close
  **or** inside the price range the last `Candle range reference` (300) bars already span (plus
  one ATR). An object inside that range cannot stretch the price scale, so it is kept; one
  outside it is not drawn. The panel reports the reason, and
  hidden objects keep their state. The Debug profile ignores the limit; turn the toggle off to
  ignore it everywhere. Each object type also keeps its own distance setting.
- **Maximum drawn length** (250 bars): the left edge of zone boxes, midlines, pool lines
  and EQ bands is cut to this many bars back, so old zones no longer paint across the whole
  chart.
- **Lighter zones:** default `Zone transparency` 92, fainter borders; POI keeps a stronger border.
- **One label column:** `PROTECTED`, `DEV HIGH/LOW`, `BSL/SSL`, `EQ`, `Asia H/L`, `iBSL/iSSL` and `PREMIUM/DISCOUNT` are drawn as one column at the right edge. Labels at nearly the same price merge into one (`PROTECTED · BSL · PREMIUM`); hover for every member and its price.
- **Zone captions:** text inside a box needs a box tall enough (zoom-aware) and a free slot;
  otherwise the caption joins the label column.

Not covered: Asian levels and session boxes and the current major/protected lines are not
limited by the window. If one of them squashes the candles, switch it off in its group.

### Market Intelligence panel

Default `Compact` shows **major information only**, one line each; hover a cell for the
detail. `Standard` and `Expanded` show the full panel described further down.

| Compact row | Shows |
| --- | --- |
| Bias / Phase | External bias; market phase |
| Range | `Premium 68 % · EQ 3951.50` or `Discount …`: where the close sits in the dealing range (tooltip shows its low and high) |
| Range H / L | The dealing range high / low (same range as the premium/discount gauge; tooltip: the confirmed swings with times and the developing extreme) |
| Protected | The external level whose break flips the bias |
| Last break | Last external BOS/CHoCH with its age (tooltip: last internal break) |
| Bull POI / Bear POI | Nearest qualified POI, distance in ATR, `A` actionable / `Q` qualified / `D` distant or tested |
| BSL / SSL | Nearest active pools (`*` = active but not drawn) |
| Zones | `OB v/a · S/D v/a · FVG v/a` (visible / active) |
| Trades | Open plan (`LONG open · 1.8R → Swing high`) or paper stats (`11 · 27% · −1.6R`); tooltip has profit factor, drawdown and the last 8 trades |

Full panel (`Standard` / `Expanded`):

| Section | Rows |
| --- | --- |
| Market state | External bias, market phase (`Counter-trend rebound/pullback` when the live leg runs against the bias), directional leg |
| Structure | Confirmed major high/low (tooltip: when each was confirmed), developing extreme, protected level, last external BOS/CHoCH with its age, **last internal break** with its age, invalidated level (Expanded) |
| Price action | Nearest bullish / bearish POI (`A` actionable, `Q` qualified, `D` distant or tested), POI status, barrier / liquidity count |
| Blocks & liquidity | **Supply / Demand** `Swing n · Base visible/active`; **Order Blocks** `Bull visible/active · Bear visible/active · hid n` (or `none active · <last reason>`), **Nearest BSL / SSL** (a trailing `*` means that pool is active but not drawn, e.g. beyond the candle-scale window; `LQ n/m` in the barrier row is drawn / active pools) |

The Order Blocks tooltip splits hidden blocks into far / overlapping / budget and
shows lifetime counters (accepted, rejected, replaced, covered, mitigated,
invalidated, aged, evicted).

### Settings

Inputs are grouped as in the indicator's settings dialog.

**01 Market Structure** — `External swing publication` (on displacement / next opposite pivot), `Protected level` (trailing / last confirmed swing), `Swing sensitivity` (Auto/Manual), manual pivot bars,
`Minimum external leg displacement` (1.50 ATR), `External confirmation threshold`
(0.15), `BOS/CHOCH close buffer`, historical major classifications, extended
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
structure window, label and event limits, zone maximum age (600 bars), panel
switch / density / position, global text scale, annotation spacing.


**08 Trade Plan** — show, entry zones, trigger candle, trigger window, wick share, maximum chase, premium/discount filter, stop buffer, minimum/maximum stop, target selection (next key level), minimum reward : risk (1.5), take-profit buffer, maximum target, timeout, cost, `Maximum stop (pips, 0 = off)` (70) and `Pip size` (0 = auto: 0.1 for XAU, 10 x mintick otherwise), trades drawn (5), box colours.

**Trade-plan room (v2.13).** The target search also treats every unswept internal swing ahead as an obstacle (the `iBSL` / `iSSL` test): the nearest level still decides, so an
obstacle that pays less than the minimum reward : risk refuses the trade instead of being skipped. A stop over `Maximum stop (pips)` refuses and is never tightened (structure sets the stop;
the ATR limits stay). The `Trades` tooltip lists the trigger bars that
opened nothing, each counted once by the first failing check: not a POI, wrong premium/discount side, entry too far from the zone, stop outside the ATR limits, stop over the pip cap, no target room, or no zone touched.

### Alerts

All fire on bar close. Create them from the indicator's alert dialog.

| Alert | Fires when |
| --- | --- |
| `ApexVoid Bullish / Bearish BOS` | External close-confirmed break in the bias direction |
| `ApexVoid Bullish / Bearish CHOCH` | External close-confirmed break against the bias (v2.13: a close beyond the trailing protected level) |
| `ApexVoid Buy-Side / Sell-Side Sweep` | A liquidity pool is swept (once per pool) |
| `ApexVoid External Buy-Side / Sell-Side Sweep` | The current external swing is swept |
| `ApexVoid Buy-Side / Sell-Side Liquidity Consumed` | A pool is accepted through or broken by structure (once per pool) |
| `ApexVoid Trade Plan Long / Short` | A trade-plan setup opens (price-action trigger at a qualified POI) |
| `ApexVoid Trade Plan Target Hit / Stop Hit` | The paper trade reaches its target / stop |
| `ApexVoid Demand / Supply Base Zone` | A new base-and-impulse zone is accepted |
| `ApexVoid Bullish / Bearish FVG` | A new FVG forms |
| `ApexVoid Bullish / Bearish OB` | A new qualified order block is accepted |
| `ApexVoid Asian Range Complete` | A new Asian range completes |

### Tuning notes

- **Too few order blocks:** raise `Visible bullish / bearish OBs`, raise `Maximum
  visible OB distance`, lower `Minimum displacement from OB`, or lower `Minimum
  impulse candle body`. The panel tooltip counts accepted and rejected candidates.
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
  TradingView; the reference tests cover the decision logic, not the drawing code.
- `reference/test_pine_builtins.py` fails if the indicator calls a Pine built-in that has not
  been confirmed to exist, or if the script body outside functions grows past TradingView's
  size limit (CE10295).
- The trade plan sends no orders. On about two weeks of XAUUSD its paper results showed no edge;
  treat the in-chart tracker as a way to collect evidence on your own history.
- Defaults were tuned on a week or two of XAUUSD; retune per instrument.
- The candle-scale protection works on the last `Candle range reference` bars, not on the visible window. Asia lines and session boxes are not clipped.

### Version history

| Version | Change |
| --- | --- |
| 2.14 | Multi-timeframe coherence: the higher low that made a higher high is the protected low; a protected-level CHoCH takes the last internal pivot instead of the start of the move; the developing leg (DEV, phase, range) follows the protected level; the optional HTF context and its inputs are removed (a first version of an `MTF` panel row exceeded TradingView's token limit and was taken out, see Multi-timeframe structure); the trades row says `Plan off: 70-pip cap < min stop on this frame (use M5/M15)` when the cap is below the smallest possible stop (H1 on XAU) |
| 2.14.1 | Pullback leg kept after a confirmed swing; scale.none removal reaches existing charts (upgrade note). A confirmed higher high (lower low) still promotes the higher low (lower high), but only an internal BOS in the bias direction moves the developing leg to the bias side, so the pullback the swing was published after stays visible (14 / 17 / 4 / 17 of 98 / 77 / 26 / 55 swings on M5 / M15 / M30 / H1 had hidden it in v2.14). The planned RR preview box (`plan 2.6R`) and its `Show a planned RR box` input are removed; trade boxes still appear for real triggers. `scale = scale.none` is gone (zones follow the candles' scale); existing charts keep the old pin until the indicator is re-added or re-pinned, see Install → Upgrading. |
| 2.13 | External swings published on displacement (lag 12 / 16 / 20 bars instead of 27 / 34 / 54); trailing protected level and CHoCH on it; dealing range = protected leg; swing zones scanned for retests before they are drawn; order blocks searched from the real break bar; engine FVG definition; independent Composite and sweep-before-impulse Swept imbalance; engine session calendar (Asia 07:00 Tokyo to London open, New York 09:00); trade plan: internal swings as obstacles, pip cap, honest preview, refusal histogram. Removed to fit TradingView's 100,256-token limit: the temporary OB debug table (`Debug: OB candidate log and rejection reasons`) and the v2.12 swing-publication option |
| 2.12 | Range box for each Asia, London and New York session (grows live, freezes at the end) |
| 2.11 | Asian H/L drawn live on the chart while the session runs; removed from the panel |
| 2.9.1 | Zone captions join the right-edge label column; consumed pools carry no label |
| 2.9 | Planned RR box for the nearest qualified POI; trade boxes show only R:R |
| 2.8 | Trade plan: price-action trigger at a qualified POI, next-key-level take-profit, last 5 trades drawn, paper stats |
| 2.10 | Premium/discount and EQ use the dealing range (confirmed swing to the live extreme of the leg), not a stale confirmed swing; trade plan uses the same EQ |
| 2.7 | One right-edge label column; one BOS per candle; slim premium/discount gauge |
| 2.6 | BOS/CHoCH lines end on the candle that closed beyond the swing |
| 2.5 | BSL/SSL kept when inside the candle range; nearest unswept internal swing markers |
| 2.4 | Compact "major only" panel; premium/discount range |
| 2.3 | Candle-scale protection; lighter zones |
| 2.2 | Base-and-impulse supply/demand (DBR, RBR, RBD, DBD) |
| 2.1 | Order blocks (swing + internal, lifecycle) and level-first BSL/SSL pools |
