# v2.7 — clean right-edge labels and valid BOS lines

## 0. Status

| Item | Status |
| --- | --- |
| Label merge rule, BOS de-duplication and break-candle logic | Checked with the Python reference model on 2000 real XAUUSD M15 bars (`reference/test_pa_model.py`, 16 checks pass). |
| Pine syntax | Parsed without errors by a third-party Pine parser (v5 grammar). Syntax only. |
| TradingView compile and look on a chart | **Not done here.** Re-paste the file and check the screenshots again. |
| Your second screenshot (two BOS lines) | **Not reproduced.** It has no timestamps and a cropped price axis (`23…`), so I could not replay it; the rules below are derived from the research and from XAUUSD M15 replays. |

## 1. Research

Sources read: the LuxAlgo concept page on break of structure (fetched) and search summaries
of educational and charting-library pages (not read in full, so only used for general
principles).

**Break of structure** ([LuxAlgo: break of structure](https://www.luxalgo.com/library/concept/break-of-structure/)):
- A BOS only counts against an established trend; it is a break of **the most recent confirmed
  swing in the trend's direction** ("the last swing high in an uptrend").
- Price must **close** beyond the swing; a wick-only poke "frequently turns out to be a
  liquidity sweep". Pick one rule and apply it consistently.
- A break with displacement carries more weight than a grinding push through the level.
- After the break the pullback sets the new protected swing.

**Label collisions** (search summaries of
[avoid-overlap](https://www.github.com/kevinschaul/avoid-overlap),
[AG Charts axis labels](https://www.ag-grid.com/charts/react/axes-labels.md) and
[SAP Analytics Cloud](https://userapps.support.sap.com/sap/support/knowledge/en/2983065)):
- Resolve collisions by **priority**: lower-priority labels give way.
- Use a **minimum spacing** threshold, then a graduated fallback (nudge, shorten, skip).
- Hiding a colliding label is acceptable when the value stays reachable on **hover**.

## 2. Design decisions

| Problem seen on the chart | Cause | v2.7 |
| --- | --- | --- |
| `PROTECTED`, `BSL` and `PREMIUM` on one spot; `DEV HIGH` over `iBSL`; `DISCOUNT` over `SSL` | Each block drew its own label with its own spacing rule, and the spacing (0.30 ATR) is smaller than a label at the chart's zoom | One **label column**: every right-edge label is registered, sorted by price, and labels closer than the gap are **merged into one** (`PROTECTED · BSL · PREMIUM`). Priority (PROTECTED 80, DEV 70, pools 60, Asia 50, iBSL/iSSL 40, EQ 35, premium/discount 15) picks the position and colour; the tooltip lists every member with its price. Nothing is hidden and nothing overlaps. |
| Gap too small for the zoom | Spacing was a fixed ATR fraction | Gap = max(annotation spacing × ATR, candle range of the last 300 bars ÷ 48), about one label height at a typical chart height, so it follows the zoom. It is a heuristic: Pine cannot read pixel sizes. |
| Premium/discount tinted the whole chart | Full-width fills over the whole range | A slim gauge (9 bars wide) at the right edge plus a faint dashed EQ line |
| BOS caption sat far from the break on long lines | Caption centred on the whole line | Caption centred on the last 40 bars of the line, next to the break |
| Two BOS lines ending on one candle | A candle that closes beyond two swings produced two events | One BOS per candle, kept on the most recent swing ("the last swing" rule) |

## 3. BOS rules as implemented

1. **Close beyond.** A bar must close beyond the swing by the confirmation buffer (0.15 ATR by
   default); wicks never break structure. A wick through and a close back inside is a sweep
   and is handled by the liquidity pools.
2. **Break of a confirmed external swing**, in the direction of the bias for a BOS, against it
   for a CHoCH.
3. **The line ends on the first closing candle** beyond the swing (v2.6), not on the later
   bar where the swing was confirmed. The bias change, alerts and order-block creation still
   happen on the confirmation bar because that is when the swing is known.
4. **One BOS per candle** (v2.7): several swings broken by one close collapse to the most
   recent swing. CHoCH lines are never merged.
5. Internal breaks never change the external bias.

Replay on XAUUSD M15 (2000 bars): 32 external breaks, 10 had been drawn late before v2.6, 1 shares a
break candle with another BOS, lines are 34 bars long at the median and 85 at most.

Not implemented: a displacement filter on the break candle (the research treats it as a
quality grade, not a validity rule) and "most recent internal swing first" ordering (the
external structure deliberately ignores minor swings; the `Last Internal Break` row shows
the internal view).

## 4. Known limits

- The gap heuristic assumes a typical chart height; on a very short pane merged labels may
  still touch. Hover shows every member.
- Zone captions drawn inside boxes (for example `POI · Supply + OB`) and box text are not part
  of the column; they can still sit under candles.
- Not compiled or viewed in TradingView.

## 5. v2.9.1 follow-up: zone captions and consumed-pool labels

After v2.7 a chart with many stacked zones still showed overlapping text, because captions drawn **inside**
zone boxes and the old consumed-pool labels were outside the label column. Changes:

- Text inside a box needs a box at least 1.4 label heights tall (one label height = about 1/48 of the candle
  range, so it follows the zoom) **and** a free slot: no other box text within one label height. A caption that
  does not qualify joins the right-edge column instead (`◆ Supply + OB` for a POI, the zone name otherwise), where
  nearby labels merge into one. A zone covered by a POI stays silent.
- Consumed pools no longer carry a label; the fading line ends on the event bar.
- The planned-box caption only appears when the box is tall enough.
- Reference check: `test_zone_captions_never_overlap_inside_boxes`.

Still not handled: BOS/CHoCH captions, `MAJOR HIGH/LOW` tags and grab marks are placed by their own rules and can
still touch candles or each other on a very zoomed-out chart.
