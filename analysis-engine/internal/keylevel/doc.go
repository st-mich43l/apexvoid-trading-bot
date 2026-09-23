// Package keylevel owns price-clustered key levels (reaction clusters +
// round-number levels, deduped and optionally wick-touch re-enriched)
// and the pure closed-bar role classifier (support/resistance/broken) —
// Phase S4's third shared technical primitive
// (apexvoid-bot-prompts/rebuild-strategies.md §23-26).
//
// Ported from algo-bot/app/analysis/levels.py (Cluster) and
// key_level_role.py (Role). The two stay one Go package because Python
// keeps them logically paired (levels.py produces the Level a caller
// then classifies with key_level_role.py) even though the real
// production Role call sites (algo-bot/app/analysis/detectors.py,
// actionability.py) source `kind` from several different vocabularies —
// not only levels.py's own Level.kind. Role's kind parameter is
// therefore a plain string here too, exactly mirroring Python's own
// `kind: str | None`, not restricted to this package's Kind enum.
//
// One parameter Python's classify_key_level_role declares but never
// reads: level_price. Confirmed by reading the full function body — it
// has no effect on the returned role. Deliberately omitted from Role's
// Go signature rather than ported as dead surface area.
//
// One deliberate ATR simplification from Python: key_levels()'s
// clustering tolerance uses atr_scalar() (the MEDIAN of the whole ATR
// series) while _round_levels()'s per-swing touch check uses atr_at()
// (that swing's OWN bar's ATR value) — two different ATR reads in one
// function. This package uses ONE canonical scalar ATR (the same
// "current/last value" convention structure.Update/zone.Update/
// liquidity.Update/fib.Update already use) for both, documented here
// rather than silently diverging. See docs/analysis/
// shared-primitives-v2.md's Key Level section for the full reasoning.
//
// Dependency rule: keylevel depends on structure (Swing.Kind/Price for
// clustering) and MUST NOT import zone, liquidity, fib, trendline,
// context, opportunity, strategy, or transport — it joins zone/
// liquidity/fib at rank 3, the same promotion those packages already
// established for needing structure.Swing directly — see
// docs/architecture/dependency-rules.md's "zone promoted above
// structure" amendment, extended through Phase S4.
package keylevel
