# Manual formula replay — Key Level `min_sell_zone_score: 10` withdrawal

**Historical record — the `manual_formula_replay` harness and its
`manual_algo_charts` data source were removed** (Analysis Engine V2
strategy rebuild, Phase S1). Kept because the config decision below is
still live (the leaf stays at its withdrawn-to default) and the
statistical reasoning is a real methodological lesson.

PR #449 enabled `strategies.reaction.key_level.min_sell_zone_score: 10.0` on
XAU from the first `manual_formula_replay` scorecard. That override is
withdrawn (PR-J). The config leaf remains at default `0.0` (gate off); the
mechanism is fine, the threshold was not.

## Why it was wrong

1. **Direction confound.** In the sampled trades HTF bias was mostly `up`, so
   `htf_aligned` agreed with `direction == "BUY"` on ~75% of rows. "Aligned
   helps BUY / counter helps SELL" was largely a restatement of side
   performance during an up-labelled window, not an HTF finding. Pooled BUY
   aligned vs counter was Fisher exact **p = 1.000**.

2. **Unstable zone-score axis on SELL.** Key Level SELL showed winners mean
   zone score 9.97 vs losers 8.94 (~1pt on a 0–24.5 scale, n=17/9). In four of
   eight scorecard SELL cells the *losers* scored higher (Supply Demand, Flip,
   Order Block, FVG).

3. **Threshold discarded winning trades.** Sampled Key Level SELL `z` values
   included 5.0, 3.0, 9.0, 5.5, 9.5 — four of five were winners and all were
   below 10.0. The deployed floor would have blocked them.

## Follow-up

Harness repairs (scan window, match semantics, clamped entry position,
expectancy / Wilson / Fisher / confound guards) land in the same PR. Do **not**
re-tune from the new scorecard until those guards are active and reviewed.
