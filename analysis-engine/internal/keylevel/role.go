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

// StructuralRole classifies a level from the market structure that formed it and from what closed price has done since, not from a label.
//
// swingLows and swingHighs are the CONFIRMED swing lows and swing highs lying inside the level's own band as known at the decision bar (the caller counts only
// swings whose confirmation bar has closed), and minimumSwings is the fewest swings of one kind that make a level (the strategy's minimum_touches).
//
//	SUPPORT     at least minimumSwings confirmed swing lows in the band and no swing high: price reacted up from it, and only from it.
//	RESISTANCE  at least minimumSwings confirmed swing highs in the band and no swing low: price reacted down from it, and only from it.
//	AMBIGUOUS   anything else: highs and lows in one band (a level price has reacted to from both sides), or too few swings of one kind.
//	            Ambiguity is never resolved by where price happens to stand.
//
// A role breaks only on the side it defends, and only on acceptance, the closed-bar rule Role already used: breakoutAcceptBars consecutive closes beyond the band
// ending at the latest closed bar (a wick, or a sweep that closes back inside, accepts nothing). SUPPORT breaks to BROKEN_SUPPORT only when the closes are below the
// band, RESISTANCE to BROKEN_RESISTANCE only when they are above it: closes on the other side are price moving away from the level, not through it. A broken role is
// reported, never flipped: a flip belongs to Break & Retest (and Trendline), and Key Level does not trade it.
//
// acceptedBeyond reports acceptance on either side of the band whatever the role. An AMBIGUOUS level has no side to break, so the caller keeps the long-standing rule for
// it: a level price has accepted beyond is not a level to react off (Break & Retest's), and Key Level skips it.
func StructuralRole(swingLows, swingHighs, minimumSwings int, bandLow, bandHigh market.Price, closes []float64, breakoutAcceptBars int) (role RoleKind, acceptedBeyond bool) {
	required := breakoutAcceptBars
	if required < 1 {
		required = 1
	}
	if minimumSwings < 1 {
		minimumSwings = 1
	}
	above := acceptedCount(closes, func(c float64) bool { return c > float64(bandHigh) }) >= required
	below := acceptedCount(closes, func(c float64) bool { return c < float64(bandLow) }) >= required
	switch {
	case swingLows >= minimumSwings && swingHighs == 0:
		if below {
			return RoleBrokenSupport, true
		}
		return RoleSupport, above
	case swingHighs >= minimumSwings && swingLows == 0:
		if above {
			return RoleBrokenResistance, true
		}
		return RoleResistance, below
	default:
		return RoleAmbiguous, above || below
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
