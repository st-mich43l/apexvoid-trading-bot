// Package trendline owns causal Trendline V2 construction and its
// read-only live-interaction classifier — Phase S4's fourth and largest
// shared technical primitive (apexvoid-bot-prompts/rebuild-strategies.md
// §23-26).
//
// Ported from algo-bot/app/analysis/trendline_v2.py
// (build_causal_trendlines, evaluate_live_interaction) and the shared
// Trendline/TrendlineValidationTouch/TrendlineInteraction dataclasses in
// trendlines.py. V1 (trendlines.py::_trendlines_v1, an all-pairs
// brute-force fit) is confirmed dead/shadow-metrics-only in production
// (analysis.trendlines.version: v2 is the live setting; V1 only runs to
// emit comparison metrics) and is NOT ported — this package is V2 only.
//
// V2's central invariant, preserved exactly: a line's two anchors (A/B)
// are immutable once chosen. A later pivot (C) can VALIDATE the A/B
// projection (becoming a ValidationTouch) but can never be promoted into
// a new anchor that redraws the line around B — the loop only ever pairs
// CHRONOLOGICALLY ADJACENT confirmed pivots as A/B candidates
// (build.go's confirmedPoints/Build), never a later, non-adjacent
// re-pairing.
//
// Coordinates are candle INDEX (position within the candles slice a
// given call was made with), not wall-clock time — matching Python's
// own DataFrame integer-position coordinate exactly (slope is price per
// BAR, not price per second; a weekend gap between two candles does not
// distort the line, because bar count, not calendar time, is what's
// measured). A Trendline's Slope/Intercept are therefore only meaningful
// against the SAME candles window (same index-0 origin) it was built
// from — the same coupling Python's own value_at(line, df.index...) has
// to its DataFrame. This holds automatically here: Build recomputes
// every Trendline from scratch on every closed bar from the full stored
// candle window (the same "no incremental state" contract every other
// domain package's Update already follows), so a stale index-0 origin
// never actually occurs in practice.
//
// One simplification from _confirmed_points: Python defensively handles
// a Swing whose confirmed_index is None (an older cached/replay payload)
// by falling back to the pivot's own index. structure.Swing.ConfirmedAt
// in this codebase is always populated by structure.Update (a plain
// int64 field, not a pointer, with a single producer) — that legacy-
// payload case cannot occur here, so a swing whose ConfirmedAt cannot be
// located in the given candles window is simply skipped rather than
// carrying forward dead fallback logic for a case this Go port's own
// type system already rules out.
//
// One faithfully-ported ATR choice, not simplified like the sibling S4
// domains: build_causal_trendlines calls atr_scalar(atr) — the MEDIAN of
// the whole ATR series, not its last value. Unlike internal/keylevel's
// per-swing atr_at() lookup (real added plumbing complexity, documented
// there as a deliberate simplification), computing a median here is
// cheap and mechanically simple, and a long-lived structural object like
// a trendline benefits from the same stability against one volatile bar
// that Python's own choice already buys — so this package ports it
// exactly (see build.go's medianATR).
//
// Dependency rule: trendline depends on structure (Swing.Kind/Price/
// Time/ConfirmedAt for anchor/validation pivots) and MUST NOT import
// zone, liquidity, fib, keylevel, context, opportunity, strategy, or
// transport — it joins zone/liquidity/fib/keylevel at rank 3, the same
// promotion those packages already established for needing
// structure.Swing directly — see
// docs/architecture/dependency-rules.md's "zone promoted above
// structure" amendment, extended through Phase S4's own domains.
package trendline
