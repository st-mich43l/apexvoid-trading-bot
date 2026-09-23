package keylevel

import (
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// RoleKind is a key level's closed-bar semantic role — key_level_role.py's
// ROLE_* string constants.
type RoleKind uint8

const (
	RoleSupport RoleKind = iota
	RoleResistance
	RoleAmbiguous
	RoleBrokenSupport
	RoleBrokenResistance
)

func (r RoleKind) String() string {
	switch r {
	case RoleSupport:
		return "support"
	case RoleResistance:
		return "resistance"
	case RoleBrokenSupport:
		return "broken_support"
	case RoleBrokenResistance:
		return "broken_resistance"
	default:
		return "ambiguous"
	}
}

// Role ports key_level_role.py::classify_key_level_role exactly.
//
// kind is a free-form label, not this package's own Kind enum — Python's
// real production call sites source it from several different
// vocabularies (a structural swing kind, a separate "structural_kind"
// field, or levels.py's own "reaction"/"round"), and classify_key_level_role
// itself only ever substring-matches it against "support"/"low"/
// "resist"/"high" (case-insensitive). See doc.go.
//
// level_price is deliberately not a parameter here — Python's own
// function declares it but never reads it (confirmed by reading the full
// function body); porting an unused parameter would add dead surface
// area for no behavioral reason.
//
// Explicit support/resistance semantics (from kind) remain authoritative
// until enough consecutive closed bars accept beyond the opposite edge.
// An accepted break is deliberately reported as a BROKEN role, never
// silently reinterpreted as the opposite plain role in place — "Break &
// Retest owns any later flip; Key Level must not reinterpret it,"
// unchanged by the S2 catalog's Break & Retest -> Trendline merge
// (Trendline owns the flip reinterpretation, Key Level still only
// reports broken).
func Role(kind string, bandLow, bandHigh market.Price, closes []float64, breakoutAcceptBars int) RoleKind {
	required := breakoutAcceptBars
	if required < 1 {
		required = 1
	}
	above := acceptedCount(closes, func(c float64) bool { return c > float64(bandHigh) })
	below := acceptedCount(closes, func(c float64) bool { return c < float64(bandLow) })

	normalized := strings.ToLower(strings.TrimSpace(kind))
	explicitSupport := strings.Contains(normalized, "support") || strings.Contains(normalized, "low")
	explicitResistance := strings.Contains(normalized, "resist") || strings.Contains(normalized, "high")

	switch {
	case explicitSupport:
		if below >= required {
			return RoleBrokenSupport
		}
		return RoleSupport
	case explicitResistance:
		if above >= required {
			return RoleBrokenResistance
		}
		return RoleResistance
	case above >= required:
		return RoleBrokenResistance
	case below >= required:
		return RoleBrokenSupport
	default:
		return RoleAmbiguous
	}
}

// acceptedCount ports _accepted_count: consecutive qualifying closes
// counted from the END of the slice backward, stopping at the first
// non-qualifying one — closes must be Time-ascending (oldest first, most
// recent last), the same convention every candle/close slice in this
// codebase already follows.
func acceptedCount(closes []float64, predicate func(float64) bool) int {
	count := 0
	for i := len(closes) - 1; i >= 0; i-- {
		if !predicate(closes[i]) {
			break
		}
		count++
	}
	return count
}
