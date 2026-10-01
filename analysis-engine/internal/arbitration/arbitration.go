package arbitration

import (
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Status is one candidate's outcome from one Arbitrate call.
type Status string

const (
	StatusWinner       Status = "winner"
	StatusSuppressed   Status = "suppressed"
	StatusConflictHeld Status = "conflict_held"
	StatusUncontested  Status = "uncontested"
)

// ReasonCode is shared across every Decision in one thesis group — it
// describes the group's batch outcome (why its representative won, or why
// the symbol held), not a per-candidate property.
const (
	ReasonUncontested           = "uncontested"
	ReasonRankedSingleDirection = "ranked_single_direction"
	ReasonOppositeDirectionHeld = "opposite_direction_conflict"
	// ReasonNotInPlay marks a live candidate whose entry zone price is not
	// at (or near) right now. It takes no part in the direction decision: a
	// resting demand zone far below price does not conflict with a supply
	// zone far above it, and neither is executable until price arrives.
	ReasonNotInPlay = "not_in_play"
)

// Config is Arbitrate's tunable input.
type Config struct {
	// ConflictMarginQuality is the minimum Quality.Overall gap ([0, 1]
	// scale) the top-ranked thesis must hold over the strongest opposite-
	// direction thesis to be decisive. Below this margin, arbitration
	// holds rather than picking a side on a near-tie.
	ConflictMarginQuality float64
	// InPlayATR is how far (in the candidate's own ATR) price may sit from
	// its entry zone for the candidate to count as in play. Zero disables
	// the filter (every live candidate contends).
	InPlayATR float64
}

// Decision is one candidate's arbitration outcome as of one
// opportunity.Book.Live() snapshot for its symbol.
type Decision struct {
	CandidateID     string
	Status          Status
	ReasonCode      string
	ConflictingWith []string
	// ThesisID/MergedWith (thesis correlation): non-empty when this
	// candidate shares its real-world StructuralID with at least one other
	// live same-direction candidate — different strategies, or the same
	// strategy's resting-vs-confirmed observations, of the SAME zone/
	// level/pool, not independent competing setups. ThesisID is the
	// group's representative candidate's own ID (stable, already unique,
	// no separate hash scheme needed); MergedWith lists every OTHER
	// candidate ID in the same group. Both empty when StructuralID is
	// empty (the strategy has no persistent identity to offer — see
	// opportunity.Candidate.StructuralID's own doc comment) or unique
	// among this symbol's live set.
	ThesisID   string
	MergedWith []string
}

// ArbitrateInPlay decides the direction only among candidates price is at
// right now (see Config.InPlayATR); every other live candidate is suppressed
// as ReasonNotInPlay and re-enters the decision on the bar price reaches it.
// This is the executable-now rule the algo-bot's own arbitration applied
// before the decision moved here: without it every 24-hour-live resting zone
// above and below price contends, near-tied qualities hold the whole symbol,
// and nothing is ever selected.
//
// reference is the latest closed price; a non-positive reference or a
// disabled InPlayATR falls back to Arbitrate over the whole live set.
func ArbitrateInPlay(live []opportunity.Candidate, cfg Config, reference float64) []Decision {
	if len(live) == 0 {
		return nil
	}
	if cfg.InPlayATR <= 0 || !(reference > 0) {
		return Arbitrate(live, cfg)
	}
	// A candidate whose entry contains the current reference is executable
	// now. Waiting candidates may be close enough to watch, but they must not
	// veto an executable candidate on the opposite side: the execution policy
	// applies the same rule when it builds its decision pool. Without this
	// split, a broad ATR window turns every nearby resting supply/demand zone
	// into a cross-direction conflict and the bot can never receive a winner.
	ready := make([]opportunity.Candidate, 0, len(live))
	for _, c := range live {
		if isEntryReady(c, reference) {
			ready = append(ready, c)
		}
	}
	if len(ready) > 0 {
		result := make([]Decision, 0, len(live))
		for _, c := range live {
			if !isEntryReady(c, reference) {
				result = append(result, Decision{CandidateID: c.ID, Status: StatusSuppressed, ReasonCode: ReasonNotInPlay})
			}
		}
		result = append(result, Arbitrate(ready, cfg)...)
		sort.Slice(result, func(i, j int) bool { return result[i].CandidateID < result[j].CandidateID })
		return result
	}
	inPlay := make([]opportunity.Candidate, 0, len(live))
	result := make([]Decision, 0, len(live))
	for _, c := range live {
		if isInPlay(c, reference, cfg.InPlayATR) {
			inPlay = append(inPlay, c)
			continue
		}
		result = append(result, Decision{CandidateID: c.ID, Status: StatusSuppressed, ReasonCode: ReasonNotInPlay})
	}
	result = append(result, Arbitrate(inPlay, cfg)...)
	sort.Slice(result, func(i, j int) bool { return result[i].CandidateID < result[j].CandidateID })
	return result
}

func isEntryReady(c opportunity.Candidate, reference float64) bool {
	return c.Technical != nil && c.Entry.Low <= reference && reference <= c.Entry.High
}

// isInPlay reports whether reference is inside the candidate's entry zone or
// within inPlayATR of its candidate-observed ATR from the nearest edge. A
// candidate with no technical ATR cannot be measured and fails closed.
func isInPlay(c opportunity.Candidate, reference, inPlayATR float64) bool {
	if c.Technical == nil || !(c.Technical.ATR > 0) {
		return false
	}
	distance := 0.0
	switch {
	case reference < c.Entry.Low:
		distance = c.Entry.Low - reference
	case reference > c.Entry.High:
		distance = reference - c.Entry.High
	}
	return distance <= inPlayATR*c.Technical.ATR
}

// Arbitrate groups every live candidate for one symbol into thesis groups
// (same Direction and non-empty StructuralID — see thesisKey), then ranks
// each group's representative by Quality.Overall (opportunity.Candidate
// has no "tier" concept — that was a Python invention derived from an
// evidence-code count, not a real per-instance signal) and decides
// whether it is this evaluation's winner, a suppressed also-ran (same
// direction ranked lower, or the losing direction after a decisive win),
// or held because an opposite-direction thesis is not decisively weaker.
// Every candidate in a group inherits its representative's Status/
// ReasonCode/ConflictingWith — from a downstream consumer's perspective
// two observations of the same real-world thesis must never be treated
// differently just because one strategy re-confirmed slightly later than
// another.
//
// live must already be filtered to one symbol — Book.Live() already
// returns one symbol's candidates, and SymbolWorker owns that filtering;
// Arbitrate itself makes no symbol distinction. Deterministic: quality
// ties break on CandidateID, matching Book.Live()'s own deterministic-ID-
// order contract, so re-arbitrating the same live set always reproduces
// the same Decision set byte-for-byte.
//
// Arbitrate is pure: no quote, no account, no execution timing. Deciding
// WHEN to act on a Decision (freshness, spot-distance) is algo-bot's job —
// see this package's own doc comment.
func Arbitrate(live []opportunity.Candidate, cfg Config) []Decision {
	if len(live) == 0 {
		return nil
	}

	groups := groupByThesis(live)
	representatives := make([]opportunity.Candidate, 0, len(groups))
	for _, group := range groups {
		representatives = append(representatives, representativeOf(group))
	}

	repDecisions := arbitrateRepresentatives(representatives, cfg)
	repDecisionByID := make(map[string]Decision, len(repDecisions))
	for _, d := range repDecisions {
		repDecisionByID[d.CandidateID] = d
	}

	result := make([]Decision, 0, len(live))
	for _, group := range groups {
		rep := representativeOf(group)
		repDecision := repDecisionByID[rep.ID]
		memberIDs := make([]string, len(group))
		for i, c := range group {
			memberIDs[i] = c.ID
		}
		for _, c := range group {
			var thesisID string
			var mergedWith []string
			if len(group) > 1 {
				thesisID = rep.ID
				for _, id := range memberIDs {
					if id != c.ID {
						mergedWith = append(mergedWith, id)
					}
				}
			}
			result = append(result, Decision{
				CandidateID: c.ID, Status: repDecision.Status, ReasonCode: repDecision.ReasonCode,
				ConflictingWith: repDecision.ConflictingWith, ThesisID: thesisID, MergedWith: mergedWith,
			})
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].CandidateID < result[j].CandidateID })
	return result
}

// thesisKey groups candidates that are the same real-world technical
// object: same Direction, same non-empty StructuralID. A candidate with
// no StructuralID (the strategy has no persistent identity to offer) gets
// a key unique to itself, via its own ID, so it never merges with
// anything.
func thesisKey(c opportunity.Candidate) string {
	if c.StructuralID == "" {
		return "solo:" + c.ID
	}
	return "thesis:" + string(c.Direction) + "|" + c.StructuralID
}

func groupByThesis(live []opportunity.Candidate) map[string][]opportunity.Candidate {
	groups := make(map[string][]opportunity.Candidate)
	for _, c := range live {
		key := thesisKey(c)
		groups[key] = append(groups[key], c)
	}
	return groups
}

// representativeOf picks the group's canonical candidate — the same
// ranking `less` uses everywhere else, so "which observation speaks for
// this thesis" and "which thesis wins arbitration" apply one consistent
// rule.
func representativeOf(group []opportunity.Candidate) opportunity.Candidate {
	best := group[0]
	for _, c := range group[1:] {
		if less(c, best) {
			best = c
		}
	}
	return best
}

// arbitrateRepresentatives is Arbitrate's original single-candidate-per-
// thesis logic, operating on one representative per thesis group instead
// of raw candidates.
func arbitrateRepresentatives(representatives []opportunity.Candidate, cfg Config) []Decision {
	if len(representatives) == 1 {
		return []Decision{{CandidateID: representatives[0].ID, Status: StatusUncontested, ReasonCode: ReasonUncontested}}
	}

	ordered := make([]opportunity.Candidate, len(representatives))
	copy(ordered, representatives)
	sort.Slice(ordered, func(i, j int) bool { return less(ordered[i], ordered[j]) })

	top := ordered[0]
	opposingIndex := -1
	for i := range ordered {
		if ordered[i].Direction != top.Direction {
			opposingIndex = i
			break
		}
	}

	if opposingIndex == -1 {
		return decideAll(ordered, top.ID, ReasonRankedSingleDirection, nil)
	}

	strongestOpposing := ordered[opposingIndex]
	decisive := top.Quality.Overall-strongestOpposing.Quality.Overall >= cfg.ConflictMarginQuality
	if decisive {
		return decideAll(ordered, top.ID, ReasonRankedSingleDirection, nil)
	}
	// A near-tie in quality is not a tie in thesis: the structural bias the
	// engine already derived breaks it when exactly one side is aligned with
	// that bias. Both sides aligned, or neither, is a genuine conflict and
	// still holds.
	if winner, ok := biasTiebreak(ordered); ok {
		return decideAll(ordered, winner.ID, ReasonRankedSingleDirection, nil)
	}
	return decideAll(ordered, "", ReasonOppositeDirectionHeld, []string{top.ID, strongestOpposing.ID})
}

// biasAligned reports whether the candidate trades with the engine's
// structural bias as of its observation bar. An absent bias is not aligned.
func biasAligned(c opportunity.Candidate) bool {
	return c.Technical != nil && c.Technical.BiasDirection.IsValid() && c.Technical.BiasDirection == c.Direction
}

// biasTiebreak returns the best-ranked candidate of the one direction whose
// candidates are bias-aligned, when only one direction has any. ordered is
// already rank-sorted.
func biasTiebreak(ordered []opportunity.Candidate) (opportunity.Candidate, bool) {
	var winner opportunity.Candidate
	found := false
	for _, c := range ordered {
		if !biasAligned(c) {
			continue
		}
		if !found {
			winner, found = c, true
			continue
		}
		if c.Direction != winner.Direction {
			return opportunity.Candidate{}, false
		}
	}
	return winner, found
}

// less orders by Quality.Overall descending (higher quality ranks first),
// then CandidateID ascending as the deterministic tie-break.
func less(a, b opportunity.Candidate) bool {
	if a.Quality.Overall != b.Quality.Overall {
		return a.Quality.Overall > b.Quality.Overall
	}
	return a.ID < b.ID
}

// decideAll builds one Decision per representative: winnerID (empty when
// held) becomes StatusWinner, every other representative becomes
// StatusSuppressed or StatusConflictHeld depending on whether this batch
// resolved decisively.
func decideAll(ordered []opportunity.Candidate, winnerID, reason string, conflictingWith []string) []Decision {
	held := winnerID == ""
	decisions := make([]Decision, len(ordered))
	for i, c := range ordered {
		status := StatusSuppressed
		switch {
		case held:
			status = StatusConflictHeld
		case c.ID == winnerID:
			status = StatusWinner
		}
		decisions[i] = Decision{CandidateID: c.ID, Status: status, ReasonCode: reason, ConflictingWith: conflictingWith}
	}
	return decisions
}
