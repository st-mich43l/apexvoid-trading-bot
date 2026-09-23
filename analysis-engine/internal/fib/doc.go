// Package fib owns Fibonacci retracement/extension ladders and the
// premium/discount dealing range they're built from — Phase S4's second
// shared technical primitive (apexvoid-bot-prompts/rebuild-strategies.md
// §23-26).
//
// Ported from algo-bot/app/analysis/fibonacci.py and dealing_range.py,
// kept as one package since Python keeps them as directly-coupled
// siblings (fibonacci.py::fib_from_swings calls
// dealing_range.py::swing_range_pair directly, and dealing_range.py
// itself calls back into fibonacci.py::fib_zone_label — the two modules
// already have a real, intentional two-way dependency in production;
// splitting them into separate Go packages would just recreate an
// import cycle as two files instead of one).
//
// Update's ladder and dealing range are always built from the SAME
// bracketing swing pair (swingRangePair, called once) — the same
// invariant fib_from_swings/dealing_range give for free by both calling
// swing_range_pair themselves; this package computes it once and reuses
// it for both outputs rather than searching twice.
//
// Dependency rule: fib depends on structure (Swing.Kind/Price for the
// bracketing search) and MUST NOT import zone, liquidity, keylevel,
// trendline, context, opportunity, strategy, or transport — it joins
// zone/liquidity at rank 3 (mutually independent siblings), the same
// promotion those two packages already established for needing
// structure.Swing directly — see
// docs/architecture/dependency-rules.md's "zone promoted above
// structure" amendment, extended to fib/keylevel/trendline as Phase S4
// lands each one.
package fib
