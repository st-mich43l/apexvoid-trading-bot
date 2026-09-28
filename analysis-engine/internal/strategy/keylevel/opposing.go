package keylevel

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// opposingZoneContradicts ports detectors.py::_opposing_zone_contradicts
// exactly (same docstring reasoning, same result: the Zone found, or none).
//
// key_levels() (levels.py, this package's own internal/keylevel primitive)
// only ever produces kind "reaction"/"round" — never an explicit
// support/resistance label — so Role (role.go) almost always falls
// through to the ambiguous branch, and this strategy's own direction
// choice there is a naive price-position guess: price above the level
// implies support (BUY), below implies resistance (SELL), with no
// awareness of nearby structure at all. A supply/demand zone sitting
// right at a level the naive guess called "support" means that guess is
// likely wrong. This does not flip the direction outright (a coin flip
// is not better than the current one) — it only stops foreclosing the
// side that actually matches the zone, and lets confirmedReaction (via
// the "try both, keep only if exactly one confirms" mechanism in
// Evaluate) decide from real price action either way. The returned
// zone's own bounds, not just the level's narrow band, are what price
// actually has to react off of — callers widen the reaction window to
// cover it.
func opposingZoneContradicts(zones []zone.Zone, bandLow, bandHigh market.Price, naiveSide zone.Kind) (zone.Zone, bool) {
	opposingKind := zone.KindSupply
	if naiveSide == zone.KindSupply {
		opposingKind = zone.KindDemand
	}
	for _, z := range zones {
		if z.Kind != opposingKind {
			continue
		}
		// Python's "not zone.mitigated": still a real, standing barrier.
		// Invalidated/Mitigated are no longer live structure; Fresh/
		// Touched/PartiallyMitigated still are (zone/doc.go's own
		// NotInvalidated is the canonical liveness check this mirrors).
		if z.State == zone.StateInvalidated || z.State == zone.StateMitigated {
			continue
		}
		if z.Low <= bandHigh && z.High >= bandLow {
			return z, true
		}
	}
	return zone.Zone{}, false
}
