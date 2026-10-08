# Break & Retest

`break_retest` is the independent M5 break-and-retest thesis of the frozen
detector. It runs on the engine's detector-contract frame (see
[detector-contract read](../architecture.md#analysis-engine)),
so direction, premium/discount, chop, retest and confluence are the frozen
detector's.

Each closed-bar evaluation requires at least five bars, a non-chop regime, a
direction (the local M5 structure bias; the H1/M15 bias when counter-trend is
off), an allowing premium/discount zone and the frozen rejection shape on the
current bar. It then tries, in order:

1. **Trendlines**, nearest to the current price first. BUY needs a broken
   resistance line, SELL a broken support line. The current bar must come after
   the break, touch the line within `trendline_tolerance_atr` x ATR and close on
   the trade's side of it. The retest band is the line value plus/minus that
   tolerance; displacement and structural agreement are always set.
2. **Key levels**, nearest first, on the valid side of price. The level must
   have `breakout_accept_bars` consecutive closes beyond it, followed by a bar
   that touches it and closes back on the broken side (a support retest for BUY,
   a resistance retest for SELL). Displacement is set only when the current bar
   is a strong-body break of the latest opposite swing.

The first path that survives the shared qualification (entry band within the
maximum entry distance, valid-side level, confluence at or above the floor)
returns one candidate. The candidate carries no shared reaction confirmation:
the retest and rejection are the thesis. It does not reuse the M5 box detector
or the M1 scalp breakout detector.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. The retest band is a level, not a zone
  (median 0.26 on XAU, every one of 6 in a week under 1.0). **FX** takes one precise
  entry. **XAU** lays the entry out like Manual Algo's zone ladder: the band is widened
  to 50 pips toward the stop (the same execution-band rule the FVG uses, 30 pips there),
  80% rests at the near edge and 20% at the midpoint of the band, and the stop follows
  the band outward but stays inside the 50-60 pip envelope (the extension is held so the
  stop is never beyond the 60 pip cap measured from the near edge). The width gate
  still judges Go's own band, and the card prints the two resting prices.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
