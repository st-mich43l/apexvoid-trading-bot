package structure

// Book is the structure-domain slice of a symbol's canonical state —
// internal/state.SymbolState holds one of these per symbol. Fields are
// placeholders (this package has no swing/break implementation yet, see
// doc.go); Book exists so SymbolState has a real, typed field to wire
// today, proving the state -> structure dependency edge in
// docs/architecture/dependency-rules.md rather than describing it only in
// prose.
type Book struct {
	// Swings, Breaks, Hierarchy, ProtectedLevels: added once
	// internal/structure has real types to hold (Stage 2 of
	// docs/go-analysis-migration-audit.md).
}
