package engine

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Family classification mirrors algo-bot's strategy_taxonomy.py exactly,
// but keyed on Go's own 19 catalog IDs (opportunity.Candidate.Strategy)
// instead of the sprawling legacy display-name taxonomy with its many
// aliases — Go only ever produces these 19 IDs, so it needs none of
// that. Membership verified against a live catalog dump: REACTION_
// STRATEGIES = {Key Level, Session Level, Trendline}; the scalp-room-
// synced set is RANGE_STRATEGIES (Range Edge Scalp is the only Go-
// catalog member) union M1_SCALP_STRATEGIES (Breakout Retest Scalp,
// Impulse Pullback Scalp, Range Sweep Scalp — their own catalog IDs
// below); every other catalog strategy (including Momentum Ride,
// despite the name) falls to the plain trend-family default, exactly
// as protective_stop.stop_bounds_for_strategy's own fallback does.
var reactionFamilyStrategies = map[opportunity.StrategyID]bool{
	"key_level": true, "session_level": true, "trendline": true,
}

// m1ScalpStopStrategies use strategies.scalping.stop.* directly (fixed
// min_rr=1.0) — algo-bot's own is_m1_scalp_strategy family.
var m1ScalpStopStrategies = map[opportunity.StrategyID]bool{
	"range_sweep": true, "impulse_pullback": true, "scalp_breakout_retest": true,
}

// rangeRoomSyncedStrategies use execution.range.* — algo-bot's own
// RANGE_STRATEGIES family (Go only ever emits range_edge from it).
var rangeRoomSyncedStrategies = map[opportunity.StrategyID]bool{
	"range_edge": true,
}

// computeStopEnvelope mirrors algo-bot's stop_bounds_for_reaction_room
// (protective_stop.py) for this candidate's own strategy family and
// target geometry, translated one-for-one:
//   - reaction family: floor = max(reaction_min_pips, reaction_room_floor),
//     cap = max(floor, min(reaction_max_pips, trend_max_pips)), min_rr = reaction_min_rr.
//   - M1 scalp family: floor = scalp_min_pips, cap = max(floor, scalp_max_pips), min_rr = 1.0.
//   - range-room-synced family (range_edge): floor = range_room_floor_pips,
//     cap = max(floor, trend_max_pips), min_rr = range_min_rr.
//   - everything else: floor = trend_min_pips, cap = trend_max_pips, no
//     per-instance pinning (algo-bot's own "strategy_default" fallback
//     never computes desired/primary_tp_pips either) — DesiredMinimumPips
//     equals FloorPips.
//
// Returns nil (never a fabricated zero-value) when the candidate's own
// target geometry cannot yield a positive pip distance — the same
// fail-closed shape Technical/Reaction already use.
func computeStopEnvelope(c opportunity.Candidate, geometry market.Geometry, cfg StopEnvelopeConfig) *opportunity.StopEnvelope {
	var floorPips, capPips, minRR float64
	var source string
	pinToTarget := true

	switch {
	case reactionFamilyStrategies[c.Strategy]:
		floorPips = math.Max(cfg.ReactionMinPips, cfg.ReactionRoomFloorPips)
		capPips = math.Max(floorPips, math.Min(cfg.ReactionMaxPips, cfg.TrendMaxPips))
		minRR = cfg.ReactionMinRR
		source = "reaction_room"
	case m1ScalpStopStrategies[c.Strategy]:
		floorPips = cfg.ScalpMinPips
		capPips = math.Max(floorPips, cfg.ScalpMaxPips)
		minRR = 1.0
		source = "scalp_stop_envelope"
	case rangeRoomSyncedStrategies[c.Strategy]:
		floorPips = cfg.RangeRoomFloorPips
		capPips = math.Max(floorPips, cfg.TrendMaxPips)
		minRR = cfg.RangeMinRR
		source = "scalp_room"
	default:
		floorPips = cfg.TrendMinPips
		capPips = cfg.TrendMaxPips
		source = "strategy_default"
		pinToTarget = false
	}
	// The resolved instrument envelope is the authoritative per-symbol
	// policy.  Do not apply it to M1 scalp families: those deliberately use
	// the dedicated scalping.stop book.  Every other Go candidate must carry
	// the same pair-specific bounds Python execution previously composed from
	// instruments.yml (EURUSD 12-20, GBPUSD 15-25, GBPJPY 22-35, USDJPY
	// 18-28, XAU 50-60).  Without this override Go silently fell back to the
	// global 40-60 trend/reaction envelope and FX plans were rejected after
	// trigger confirmation.
	if cfg.InstrumentConfigured && !m1ScalpStopStrategies[c.Strategy] {
		floorPips = cfg.InstrumentMinPips
		capPips = cfg.InstrumentMaxPips
	}
	if floorPips <= 0 {
		return nil
	}
	if capPips < floorPips {
		capPips = floorPips
	}

	desiredMinimumPips := floorPips
	if pinToTarget {
		primaryPips := primaryTargetPips(c, geometry)
		if primaryPips > 0 && minRR > 0 {
			desired := math.Ceil(primaryPips / minRR)
			if desired > desiredMinimumPips {
				desiredMinimumPips = desired
			}
		}
	}
	if desiredMinimumPips > capPips {
		desiredMinimumPips = capPips
	}

	return &opportunity.StopEnvelope{
		FloorPips: floorPips, CapPips: capPips, DesiredMinimumPips: desiredMinimumPips, Source: source,
	}
}

// primaryTargetPips is the largest entry-to-target pip distance among the
// candidate's own targets — algo-bot's own primary_tp_pips_from_match,
// recomputed from Candidate.Targets directly instead of a pre-serialized
// StrategyMatch.targets_pips list.
func primaryTargetPips(c opportunity.Candidate, geometry market.Geometry) float64 {
	if geometry.PipSize <= 0 {
		return 0
	}
	proximal := c.Entry.High
	if c.Direction == market.Sell {
		proximal = c.Entry.Low
	}
	best := 0.0
	for _, target := range c.Targets {
		distance := float64(target.Price.Price) - proximal
		if c.Direction == market.Sell {
			distance = proximal - float64(target.Price.Price)
		}
		if distance <= 0 {
			continue
		}
		pips := math.Round(geometry.PipsBetween(proximal+distance, proximal))
		if pips > best {
			best = pips
		}
	}
	return best
}
