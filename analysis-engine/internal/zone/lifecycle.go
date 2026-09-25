package zone

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// LifecycleConfig mirrors the canonical lifecycle leaves in
// config/analysis.yml's analysis.techniques section
// leaves — the exact four knobs PR #574 introduced in
// algo-bot/app/analysis/technique_geometry.py's TechniqueGeometrySettings
// (invalidation_tolerance_atr/sweep_reclaim_bars/max_break_episodes/
// retest_max_touches), carried forward at their real production values,
// not reinvented. Setting InvalidationToleranceATR/SweepReclaimBars/
// MaxBreakEpisodes/RetestMaxTouches all to 0 reproduces the pre-#574
// first-touch-kills-it behavior, same escape hatch Python's own comment
// documents.
type LifecycleConfig struct {
	InvalidationToleranceATR float64
	SweepReclaimBars         int
	MaxBreakEpisodes         int
	RetestMaxTouches         int

	// EpsilonATR is the minimum tolerance floor regardless of
	// InvalidationToleranceATR*atr — mirrors technique_geometry.py's
	// epsilon() zero-ATR/zero-tolerance guard so a degenerate config
	// never makes tolerance exactly zero.
	EpsilonATR float64
}

// State is this package's own zone lifecycle — deliberately simpler than
// ZoneWatch's 7-state execution machine (see doc.go). Derived fresh from
// TouchCount/NotInvalidated/IsSpent on every Update, never persisted
// independently.
type State uint8

const (
	StateFresh State = iota
	StateTouched
	StatePartiallyMitigated
	StateMitigated
	StateInvalidated
)

func (s State) String() string {
	switch s {
	case StateFresh:
		return "fresh"
	case StateTouched:
		return "touched"
	case StatePartiallyMitigated:
		return "partially_mitigated"
	case StateMitigated:
		return "mitigated"
	case StateInvalidated:
		return "invalidated"
	default:
		return "unknown"
	}
}

// farEdge/nearEdge give the zone's own-side boundary a break must cross
// to threaten it, and the side price returns to on reclaim.
func farEdge(z Zone) float64 {
	if z.Side == Demand {
		return float64(z.Low)
	}
	return float64(z.High)
}

// NotInvalidated is the single canonical liveness check every zone kind,
// and every future consumer (PR #579), uses. Ported from
// technique_geometry.py::not_invalidated, values and all (PR #574):
// tolerance is the greater of cfg.EpsilonATR and
// cfg.InvalidationToleranceATR*atr; a close beyond the far edge by more
// than tolerance opens a break episode; an episode reclaimed (a later
// close back on the zone's own side) within cfg.SweepReclaimBars is a
// liquidity sweep, not an invalidation, and increments the episode
// count; an episode NOT reclaimed within the window invalidates the zone
// immediately; exceeding cfg.MaxBreakEpisodes reclaimed episodes also
// invalidates it — more than a couple of forgiven sweeps means price no
// longer respects this level. candles/fromIndex must be the same causal
// window every other Update call in this package uses (only
// candles[:T]).
func NotInvalidated(candles []market.Candle, fromIndex int, z Zone, atr float64, cfg LifecycleConfig) bool {
	if fromIndex < 0 || fromIndex >= len(candles) {
		return true
	}
	tolerance := cfg.EpsilonATR
	if t := cfg.InvalidationToleranceATR * atr; t > tolerance {
		tolerance = t
	}
	edge := farEdge(z)

	episodes := 0
	i := fromIndex
	for i < len(candles) {
		c := candles[i]
		breached := false
		if z.Side == Demand && float64(c.Close) < edge-tolerance {
			breached = true
		}
		if z.Side == Supply && float64(c.Close) > edge+tolerance {
			breached = true
		}
		if !breached {
			i++
			continue
		}
		reclaimed := false
		end := i + cfg.SweepReclaimBars
		if end > len(candles)-1 {
			end = len(candles) - 1
		}
		for j := i + 1; j <= end; j++ {
			rc := candles[j]
			if z.Side == Demand && float64(rc.Close) >= edge {
				reclaimed = true
				i = j
				break
			}
			if z.Side == Supply && float64(rc.Close) <= edge {
				reclaimed = true
				i = j
				break
			}
		}
		if !reclaimed {
			return false
		}
		episodes++
		if episodes > cfg.MaxBreakEpisodes {
			return false
		}
		i++
	}
	return true
}

// IsSpent is technique_geometry.py::zone_is_spent, ported exactly: an
// untouched zone (touches == 0) is never spent — mitigation requires at
// least one touch first. Exceeding cfg.RetestMaxTouches spends it
// outright (a sanity cap). Otherwise a touched zone is spent iff it is
// no longer NotInvalidated — touching alone never kills a zone, exactly
// the PR #574 fix this whole file exists to encode. Kept as a single
// bool gate matching Python's own validate_technique_instance use (a
// simple valid/invalid check); DeriveState below inspects the same two
// underlying facts separately, since spec §17 treats "structurally
// broken" and "exhausted by retests" as different properties, not one
// collapsed flag.
func IsSpent(candles []market.Candle, fromIndex int, z Zone, touches int, atr float64, cfg LifecycleConfig) bool {
	if touches <= 0 {
		return false
	}
	if cfg.RetestMaxTouches > 0 && touches > cfg.RetestMaxTouches {
		return true
	}
	return !NotInvalidated(candles, fromIndex, z, atr, cfg)
}

// DeriveState turns TouchCount plus the two independent spent reasons
// into this package's own 5-state model:
//   - Invalidated: NotInvalidated is false — a decisive, unreclaimed (or
//     over-episode-budget) structural break. This is the "broken," not
//     just "used up," case.
//   - Mitigated: still structurally valid (NotInvalidated is true) but
//     touches have exceeded cfg.RetestMaxTouches — exhausted by retests,
//     the sanity-cap case, not a structural failure.
//   - PartiallyMitigated: touched more than once, still holding, under
//     the retest cap — "still holding," per PR #574.
//   - Touched / Fresh: 1 touch / 0 touches, still holding.
func DeriveState(candles []market.Candle, fromIndex int, z Zone, touches int, atr float64, cfg LifecycleConfig) State {
	if touches <= 0 {
		return StateFresh
	}
	if !NotInvalidated(candles, fromIndex, z, atr, cfg) {
		return StateInvalidated
	}
	if cfg.RetestMaxTouches > 0 && touches > cfg.RetestMaxTouches {
		return StateMitigated
	}
	if touches == 1 {
		return StateTouched
	}
	return StatePartiallyMitigated
}
