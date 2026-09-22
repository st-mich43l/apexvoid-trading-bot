# ADR-002: Analysis vs. orchestration boundary

## Status
Accepted (2026-09-22).

## Context
The clearest, most-violated boundary in the current codebase is "is this
code deciding what's true about the market, or deciding what to do about
it." `app/autotrade/structural_barriers.py`, `structural_target_room.py`,
`range_context.py`, `range_lifecycle.py`, `entry_activation.py`,
`execution_confirmation.py` were confirmed (by the existing Go audit,
§2.7) to be consumers of already-built analysis, not computation sources —
correctly on the orchestration side today. `app/analysis/structure.py`'s
non-canonical wrapper functions and `app/scalping/context.py`'s own swing
algorithm were confirmed to be independent recomputation living on the
wrong side.

## Decision
The test for every module: **"does the answer change if the account,
operator, or broker changes?"** If yes, it's `algo-bot` (or
`ctrader-engine` for broker mechanics). If no — the same FVG is the same
FVG whether the account is $500 or $50,000 — it's `analysis-engine`.

Concretely:
- Technical strategy *detection* (is there a valid breakout-retest thesis
  here) is `analysis-engine`.
- Trade *eligibility* (should ApexVoid act on this valid thesis, given
  current exposure/risk/duplicate-guard state) is `algo-bot`.
- An `AnalysisOpportunity` never contains a lot size, an account balance
  reference, or a risk multiplier. A `TradePlan` never contains "is this
  FVG still valid" — that question was already answered upstream.

## Consequences
- `structural_barriers.py`/`structural_target_room.py`-class modules stay
  in `algo-bot`'s target `risk/`/`auto_algo/` packages, not migrated to Go
  — confirmed correct placement, not a violation, despite reading zone
  geometry (they consume it, they don't compute it).
- `app/analysis/structure.py`'s wrapper functions and `app/scalping/`'s
  independent swing algorithm are confirmed violations (documented in
  `service-boundaries.md` as V3/V4/V6) — they compute analysis from a
  package that should only be orchestrating around it. Fixing them is
  migration backlog, not done by this ADR.
