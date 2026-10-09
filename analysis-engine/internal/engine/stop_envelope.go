package engine

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// stopEnvelopeKind is which of the four configured stop-distance policies a
// strategy is admitted under. Every strategy names its own kind below — there
// is no strategy group that supplies it — and TestEveryStrategyDeclaresItsOwnStopEnvelope
// fails if one of the 21 catalog IDs is missing. The values mirror algo-bot's
// protective_stop.stop_bounds_for_strategy exactly, keyed on Go's catalog IDs
// (opportunity.Candidate.Strategy): Key Level, Session Level and Trendline use
// the reaction-room envelope; Range Sweep, Impulse Pullback and Scalp Breakout
// Retest use strategies.scalping.stop.* directly (fixed min_rr 1.0); Range Edge
// and Fade Scalp use execution.range.*; every other strategy (Momentum Ride
// included, despite the name) takes the plain trend envelope, as the Python
// fallback does.
type stopEnvelopeKind int

const (
	stopEnvelopeTrend stopEnvelopeKind = iota
	stopEnvelopeReactionRoom
	stopEnvelopeM1Scalp
	stopEnvelopeRangeRoom
)

var strategyStopEnvelope = map[opportunity.StrategyID]stopEnvelopeKind{
	"key_level":             stopEnvelopeReactionRoom,
	"confluence_zone":       stopEnvelopeTrend,
	"supply":                stopEnvelopeTrend,
	"demand":                stopEnvelopeTrend,
	"order_block":           stopEnvelopeTrend,
	"fvg":                   stopEnvelopeTrend,
	"ifvg":                  stopEnvelopeTrend,
	"crt":                   stopEnvelopeTrend,
	"flip_zone":             stopEnvelopeTrend,
	"session_level":         stopEnvelopeReactionRoom,
	"trendline":             stopEnvelopeReactionRoom,
	"range_edge":            stopEnvelopeRangeRoom,
	"box_breakout":          stopEnvelopeTrend,
	"break_retest":          stopEnvelopeTrend,
	"momentum_ride":         stopEnvelopeTrend,
	"snap_back":             stopEnvelopeTrend,
	"fade_scalp":            stopEnvelopeRangeRoom,
	"liquidity_sweep":       stopEnvelopeTrend,
	"range_sweep":           stopEnvelopeM1Scalp,
	"impulse_pullback":      stopEnvelopeM1Scalp,
	"scalp_breakout_retest": stopEnvelopeM1Scalp,
}

// computeStopEnvelope mirrors algo-bot's stop_bounds_for_reaction_room
// (protective_stop.py) for this candidate's own strategy and target
// geometry, translated one-for-one:
//   - reaction-room kind: floor = max(reaction_min_pips, reaction_room_floor),
//     cap = max(floor, min(reaction_max_pips, trend_max_pips)), min_rr = reaction_min_rr.
//   - M1 scalp kind: floor = scalp_min_pips, cap = max(floor, scalp_max_pips), min_rr = 1.0.
//   - range-room kind (range_edge, fade_scalp): floor = range_room_floor_pips,
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

	kind := strategyStopEnvelope[c.Strategy]
	switch kind {
	case stopEnvelopeReactionRoom:
		floorPips = math.Max(cfg.ReactionMinPips, cfg.ReactionRoomFloorPips)
		capPips = math.Max(floorPips, math.Min(cfg.ReactionMaxPips, cfg.TrendMaxPips))
		minRR = cfg.ReactionMinRR
		source = "reaction_room"
	case stopEnvelopeM1Scalp:
		floorPips = cfg.ScalpMinPips
		capPips = math.Max(floorPips, cfg.ScalpMaxPips)
		minRR = 1.0
		source = "scalp_stop_envelope"
	case stopEnvelopeRangeRoom:
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
	// policy.  Do not apply it to the M1 scalp kind: those deliberately use
	// the dedicated scalping.stop book.  Every other Go candidate must carry
	// the same pair-specific bounds Python execution previously composed from
	// instruments.yml (EURUSD 12-20, GBPUSD 15-25, GBPJPY 22-35, USDJPY
	// 18-28, XAU 50-60).  Without this override Go silently fell back to the
	// global 40-60 trend/reaction envelope and FX plans were rejected after
	// trigger confirmation.
	if cfg.InstrumentConfigured && kind != stopEnvelopeM1Scalp {
		floorPips = cfg.InstrumentMinPips
		capPips = cfg.InstrumentMaxPips
	}
	// A range scalp on an instrument whose structural envelope is a
	// swing-sized 50-70 pips (gold) is still a scalp: it takes the band the
	// instrument declares for scalps, not the zone strategies' envelope.
	// Production 2026-10-09: a Range Edge Scalp on XAU, Go invalidation 7.6
	// pips beyond the zone, was widened to a 50 pip stop.
	if kind == stopEnvelopeRangeRoom && cfg.InstrumentScalpConfigured {
		floorPips = cfg.InstrumentScalpMinPips
		capPips = cfg.InstrumentScalpMaxPips
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
