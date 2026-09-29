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

// ReasonCode is shared across every Decision one Arbitrate call returns —
// it describes the batch outcome (why the winner won, or why the symbol
// held), not a per-candidate property.
const (
	ReasonUncontested           = "uncontested"
	ReasonRankedSingleDirection = "ranked_single_direction"
	ReasonOppositeDirectionHeld = "opposite_direction_conflict"
)

// Config is Arbitrate's one tunable input.
type Config struct {
	// ConflictMarginQuality is the minimum Quality.Overall gap ([0, 1]
	// scale) the top-ranked candidate must hold over the strongest
	// opposite-direction candidate to be decisive. Below this margin,
	// arbitration holds rather than picking a side on a near-tie.
	ConflictMarginQuality float64
}

// Decision is one candidate's arbitration outcome as of one
// opportunity.Book.Live() snapshot for its symbol.
type Decision struct {
	CandidateID     string
	Status          Status
	ReasonCode      string
	ConflictingWith []string
}

// Arbitrate ranks every live candidate for one symbol by Quality.Overall
// (opportunity.Candidate has no "tier" concept — that was a Python
// invention derived from an evidence-code count, not a real per-instance
// signal; ranking here is purely on the real quality score) and decides,
// per candidate, whether it is this evaluation's winner, a suppressed
// also-ran (same direction, ranked lower, or the losing direction after a
// decisive win), or held because an opposite-direction rival is not
// decisively weaker.
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
	if len(live) == 1 {
		return []Decision{{CandidateID: live[0].ID, Status: StatusUncontested, ReasonCode: ReasonUncontested}}
	}

	ordered := make([]opportunity.Candidate, len(live))
	copy(ordered, live)
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
	return decideAll(ordered, "", ReasonOppositeDirectionHeld, []string{top.ID, strongestOpposing.ID})
}

// less orders by Quality.Overall descending (higher quality ranks first),
// then CandidateID ascending as the deterministic tie-break.
func less(a, b opportunity.Candidate) bool {
	if a.Quality.Overall != b.Quality.Overall {
		return a.Quality.Overall > b.Quality.Overall
	}
	return a.ID < b.ID
}

// decideAll builds one Decision per candidate: winnerID (empty when
// held) becomes StatusWinner, every other candidate becomes
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
