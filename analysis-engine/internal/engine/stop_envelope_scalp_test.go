package engine

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
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

// The real config, every instrument, every scalp: a scalp's envelope never exceeds
// the scalping book's 45 pips, and on an instrument whose structural envelope is
// swing-sized (gold) it is strictly tighter than the structural strategies'.
func TestEveryScalpKeepsAScalpEnvelopeOnEveryInstrument(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	scalps := []opportunity.StrategyID{"range_edge", "fade_scalp", "range_sweep", "impulse_pullback", "scalp_breakout_retest"}
	for _, symbol := range []string{"XAU", "EURUSD", "GBPUSD", "GBPJPY", "USDJPY"} {
		settings, err := LoadSettings(doc, "M5", false)
		if err != nil {
			t.Fatal(err)
		}
		if err := ApplyInstrument(&settings, doc, symbol); err != nil {
			t.Fatal(err)
		}
		cfg := settings.StopEnvelope
		cfg.InstrumentMinPips, cfg.InstrumentMaxPips, cfg.InstrumentConfigured = settings.InstrumentStopMinPips, settings.InstrumentStopMaxPips, true
		if settings.InstrumentScalpStopConfigured {
			cfg.InstrumentScalpMinPips, cfg.InstrumentScalpMaxPips, cfg.InstrumentScalpConfigured = settings.InstrumentScalpStopMinPips, settings.InstrumentScalpStopMaxPips, true
		}
		structural := computeStopEnvelope(goldCandidate("supply"), settings.Geometry, cfg)
		for _, id := range scalps {
			got := computeStopEnvelope(goldCandidate(id), settings.Geometry, cfg)
			if got == nil {
				t.Fatalf("%s %s: no envelope", symbol, id)
			}
			if got.CapPips > 45 {
				t.Errorf("%s %s: cap %.0f exceeds the scalping book's 45", symbol, id, got.CapPips)
			}
			if symbol == "XAU" && !(got.CapPips < structural.CapPips && got.FloorPips < structural.FloorPips) {
				t.Errorf("XAU %s: envelope %.0f-%.0f is not tighter than the structural %.0f-%.0f", id, got.FloorPips, got.CapPips, structural.FloorPips, structural.CapPips)
			}
		}
	}
}
