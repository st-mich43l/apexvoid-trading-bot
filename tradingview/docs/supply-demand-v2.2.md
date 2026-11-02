# ApexVoid Price Action v2.2 — base-and-impulse supply / demand

v2.0 drew supply/demand only at a confirmed external swing (one zone per major swing, fixed
departure and width). v2.2 adds a second, independent source: **base-and-impulse zones**
(DBR, RBR, RBD, DBD) found from consolidation bases, whether or not a swing pivot exists
there. The swing zones, order blocks, FVG, liquidity pools and alerts are unchanged.

## 0. Verification status

| Item | Status |
| --- | --- |
| Detector logic on real XAUUSD closed bars (M5, M15, H1, H4) | Checked with the Python model `reference/sd_base_model.py` (`reference/test_sd_base_model.py`, 6 checks pass: synthetic DBR/RBR, filters, weak or closed-back departures, wide base, real-bar invariants, no duplicates). |
| Pine syntax | Parsed without errors by a third-party Pine parser (v5 grammar, `//@version=6` swapped for `5`). A syntax check only. |
| Pine v6 compile, drawing, label placement, object limits, alerts in TradingView | **Not done** (no TradingView access here). |
| Predictive value of the zones | **Not tested.** This change is detection, not an edge claim. |

## 1. Definition

A **base** is 1 to `Base maximum candles` (5) consecutive small candles: each with range
≤ `Base candle maximum range` (0.90 ATR) and body ≤ `Base candle maximum body` (0.45 ATR),
and the whole base no taller than `Max supply/demand width / base height` (1.0 ATR). Base
bars sit immediately before the departure window.

A **departure** is the 1 to `Departure window` (3) bars after the base. A zone is accepted
only if all of these hold:

| Gate | Default | Reject reason shown |
| --- | --- | --- |
| The first departure bar closes beyond the base (above its high for demand, below its low for supply) | — | (no base) |
| No departure bar closes back through the far edge | — | `departure closed back through the base` |
| Net departure (best close beyond the base edge) ≥ max(`Minimum departure from base`, `Minimum departure vs base height` × base height) | 1.5 ATR, 2.0× | `weak departure (x ATR < y)` |
| At least one departure candle with body ≥ `Minimum departure candle body` in the direction | 0.8 ATR | `no impulse candle` |
| Pattern passes `Base-and-impulse patterns` (All / Reversal only / Continuation only) | All | `continuation / reversal filtered` |
| Not a ≥ 85 % overlap duplicate of an active same-side zone | — | counted as duplicate |

All distances use ATR(14) at the departure bar. No volume is used.

**Pattern** is named from the move into the base (`Leg-in lookback`, 3 bars, close before the
base vs three bars earlier):

| Pattern | Zone | Meaning |
| --- | --- | --- |
| DBR drop-base-rally | demand | reversal |
| RBR rally-base-rally | demand | continuation |
| RBD rally-base-drop | supply | reversal |
| DBD drop-base-drop | supply | continuation |

**Bounds.** Distal edge = base low (demand) or base high (supply). Proximal edge = base wick
by default, or base bodies with `Base proximal edge = Body`. The box starts at the first base
bar.

**Timing.** A zone is built on the bar its departure qualifies, so it appears 1–3 bars after
the base (the departure window), never earlier. Nothing repaints after a bar closes.

## 2. Lifecycle, display and POI

Base zones are ordinary supply/demand zones (kind 3) with source class 1, so they share the
existing lifecycle and display: Fresh until price enters; Tested afterwards (dimmer);
invalidated and removed on a **close beyond the distal edge** (a partial touch is not an
invalidation); aged out at `Zone maximum age`. They compete for the normal zone budget and
distance limit, and a same-side overlap of 60 % or more with an already-shown zone is hidden.
At most `Retained base-and-impulse zones` (12) are kept; the oldest base zone goes first and
swing zones are never evicted by this cap.

Labels read `Demand · DBR`, `Supply · RBD`, and so on (`POI · …` when promoted). A new POI
path, **Base-impulse zone**, qualifies a *fresh* base zone on the side of the external bias.
The old `Displaced swing zone` path now applies to swing zones only.

The panel has a `Supply / Demand` row (`Swing n · Base visible/active`); its tooltip shows
accepted, duplicate, cap-retired and per-reason rejection counts and the last outcome. The
POI-status tooltip lists `S/D v/a (base v/a)`.

New alerts: `ApexVoid Demand Base Zone`, `ApexVoid Supply Base Zone` (new zone, bar close).

## 3. Results on real bars (reference model, default settings)

Repo capture (2026-10-29, `analysis-engine/testdata/obwindow-xau-20261029.json`; H4 resampled
from H1):

| TF | Bars | Zones | DBR / RBR / RBD / DBD | Invalidated | Active at last bar |
| --- | --- | --- | --- | --- | --- |
| M5 | 2000 | 53 | 14 / 16 / 8 / 15 | 46 | 7 |
| M15 | 1000 | 26 | 9 / 5 / 7 / 5 | 18 | 8 |
| H1 | 600 | 18 | 5 / 7 / 2 / 4 | 15 | 3 |
| H4 | 162 | 4 | 0 / 1 / 1 / 2 | 2 | 2 |

Most zones are eventually closed through (for example 46 of 53 on M5), which is expected for
small bases; this says nothing about whether the survivors react. The thresholds were chosen
on one week of XAUUSD (this capture plus a 2026-11-02 production capture): the first defaults
(1.2 ATR / 1.5×) produced about 13 M5 zones a day, which was judged too noisy. Other symbols
and regimes are untested. On M15 the model places zones on the bases before the late-October
drop (for example supply 3953.79–3959.54 and 3940.54–3948.03) and on the 3871.87–3877.43 low.

## 4. Settings

G3 `Order Blocks & Zones`: `Show base-and-impulse supply / demand`, `Base-and-impulse
patterns`, `Base maximum candles`, `Base candle maximum range / body`, `Minimum departure from
base`, `Minimum departure vs base height`, `Minimum departure candle body`, `Departure
window`, `Leg-in lookback for pattern type`, `Base proximal edge`, `Retained base-and-impulse
zones`. `Max supply/demand width` also bounds the base height. `Show swing supply / demand`
still controls only the swing zones.

## 5. Known limitations

- Not compiled or viewed in TradingView.
- Strict gates reject most candidate bases (about 450 weak-departure rejections on M5 over a
  week) while M5 still yields several zones a day; retune per symbol and timeframe.
- A gap departure (for example after a weekend) can create a zone whose base is the last bar
  before the gap.
- A departure bar's wick may dip back into the base without invalidating it.
- Zones appear only after the departure qualifies; a base that is still forming is not shown.
- Swing zones and base zones can overlap; the shared display rule hides the lower-ranked one.
- No volume, order-flow or institutional-activity claim is made.
