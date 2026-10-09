package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Production 2026-10-09: a Range Edge Scalp on XAU was filled with a 50 pip stop
// (the zone strategies' gold floor) where its own invalidation was 23 pips away.
// A range scalp takes the band the instrument declares for scalps; the other
// strategies keep the structural envelope.
func goldEnvelopeConfig(scalp bool) StopEnvelopeConfig {
	cfg := StopEnvelopeConfig{
		ReactionMinRR: 1, ReactionMinPips: 40, ReactionMaxPips: 60, ReactionRoomFloorPips: 40,
		RangeMinRR: 1, RangeRoomFloorPips: 15, ScalpMinPips: 12, ScalpMaxPips: 45,
		TrendMinPips: 40, TrendMaxPips: 60,
		InstrumentMinPips: 50, InstrumentMaxPips: 70, InstrumentConfigured: true,
	}
	if scalp {
		cfg.InstrumentScalpMinPips, cfg.InstrumentScalpMaxPips, cfg.InstrumentScalpConfigured = 15, 45, true
	}
	return cfg
}

func goldCandidate(id opportunity.StrategyID) opportunity.Candidate {
	return opportunity.Candidate{
		Strategy: id, Direction: market.Sell,
		Entry: opportunity.EntryZone{Low: 4176.60, High: 4178.08},
	}
}

func TestRangeScalpsTakeTheInstrumentScalpBandOnGold(t *testing.T) {
	geometry := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	for _, id := range []opportunity.StrategyID{"range_edge", "fade_scalp"} {
		got := computeStopEnvelope(goldCandidate(id), geometry, goldEnvelopeConfig(true))
		if got == nil || got.FloorPips != 15 || got.CapPips != 45 {
			t.Errorf("%s: envelope = %+v, want floor 15 cap 45", id, got)
		}
	}
}

func TestRangeScalpWithoutADeclaredScalpBandKeepsTheInstrumentEnvelope(t *testing.T) {
	// FX declares none: its 12-20 style band is already scalp-sized.
	geometry := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	got := computeStopEnvelope(goldCandidate("range_edge"), geometry, goldEnvelopeConfig(false))
	if got == nil || got.FloorPips != 50 || got.CapPips != 70 {
		t.Fatalf("envelope = %+v, want the instrument's 50-70", got)
	}
}

func TestTheScalpBandNeverReachesTheStructuralStrategies(t *testing.T) {
	geometry := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	for _, id := range []opportunity.StrategyID{
		"key_level", "supply", "demand", "order_block", "fvg", "ifvg", "crt", "flip_zone",
		"confluence_zone", "break_retest", "session_level", "trendline", "momentum_ride",
	} {
		got := computeStopEnvelope(goldCandidate(id), geometry, goldEnvelopeConfig(true))
		if got == nil || got.FloorPips != 50 || got.CapPips != 70 {
			t.Errorf("%s: envelope = %+v, want the instrument's 50-70", id, got)
		}
	}
}
