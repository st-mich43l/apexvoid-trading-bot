# Premium and discount in the zone score

Supply earns points for sitting in the premium half of a dealing range, demand for sitting in the discount half. The sign was always right; the **range** under it was not. This page records the old reading, why it leaned against the trend, and the reading `supply` v9 and `demand` v7 use. Premium/discount is a **score input only, never a gate**: a counter-bias fade at premium or discount is a valid trade, and quality filtering is not a hard veto.

## The old reading

The shared zone score (`techniquezone.ScoreZones`) took the equilibrium of the frame's dealing range, `fib.Resolve`, chosen **around the current price**: the first opposite-kind swing pair that contains price, and, if none does, the last opposing pair even though price is outside it (`lastOpposingPair`). In a trend price breaks out of every recent swing pair, so the fallback is common: the position is then clamped to 1 in a rally or 0 in a sell-off. In a rally every zone at price therefore read "premium": a with-trend demand retest never earned the +2 while a counter-trend supply zone did; the mirror happened in a sell-off. +2 is a quarter of the two-star line (8), so the scoring leaned towards fading trends.

## The reading now

Each strategy builds the range on the **structure frame of the zone** (M15 or H1), as of the decision bar (`dealing.go`):

- `swingHigh` / `swingLow`: the most recent confirmed swing high and low of that frame (`structure_pivot_bars` bars on each side, a zigzag filter of 1 ATR).
- `rangeHigh` = the highest of `swingHigh` and every high after it, over the frame's closed bars and then the M5 bars since the last of them closed; `rangeLow` = the lowest of `swingLow` and every low after it, the same way.
- The range therefore always contains price: a breakout extends it instead of falling back to a range price has left. In a rally it is [last swing low, running high]; in a sell-off [running low, last swing high]; in a range, the last swing pair.
- A supply zone earns `pd_position_score` (2) when `zone.Low >= equilibrium` (evidence `m5_supply_zone_premium`); a demand zone when `zone.High <= equilibrium` (`m5_demand_zone_discount`); a zone straddling the equilibrium earns 0. Without both swings there is no range and no points.
- The quality measure `pd_position` is the zone's proximal edge in the range, 0 (low) to 1 (high).

Both strategies implement this separately (the mirror is written out); `test/pdsymmetry` proves that a market and its price mirror give mirrored answers.

## Measured

See the pull request that introduced it for the tables (side x trend state x points under the old and the new reading, stars, the XAU SELL/BUY mix). They are publish counts, not P&L.
