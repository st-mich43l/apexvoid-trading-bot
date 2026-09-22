package opportunity

// Book is the current set of live opportunities for one symbol — the
// opportunity-domain slice of internal/state.SymbolState.
type Book struct {
	Candidates []Candidate
}
