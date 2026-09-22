package market

// Symbol is a canonical instrument identifier (e.g. "XAU", "EURUSD"),
// typed for the same reason Timeframe and Direction are (see timeframe.go,
// direction.go): callers wiring Symbol through context/opportunity/state
// should get a compile-time signal if they mix it up with an arbitrary
// string, the way config.Document.GeometryFor's own `symbol string`
// parameter (internal/config/v3_instruments.go) currently cannot.
//
// This file is named instrument.go rather than symbol.go because
// internal/market/symbol.go already exists and holds Geometry, not a
// Symbol type — a pre-existing naming mismatch against the proposed tree
// in docs/architecture/analysis-engine.md, left as-is by this task rather
// than renamed, to avoid touching an existing, tested, unrelated file for
// a purely cosmetic reason. A future cleanup may rename symbol.go to
// geometry.go and fold this file into the resulting symbol.go; not done
// here — no live behavior changes were introduced by this architecture
// task, including refactors of already-correct code.
type Symbol string

// String satisfies fmt.Stringer.
func (s Symbol) String() string { return string(s) }
