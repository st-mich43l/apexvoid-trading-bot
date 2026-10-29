package engine

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func goldEnvelopeConfig() StopEnvelopeConfig {
	return StopEnvelopeConfig{
		ReactionMinRR: 1, ReactionMinPips: 40, ReactionMaxPips: 60, ReactionRoomFloorPips: 40,
		ScalpMinPips: 12, ScalpMaxPips: 45,
		TrendMinPips: 40, TrendMaxPips: 60,
		InstrumentMinPips: 50, InstrumentMaxPips: 70, InstrumentConfigured: true,
	}
}

func goldCandidate(id opportunity.StrategyID) opportunity.Candidate {
	return opportunity.Candidate{
		Strategy: id, Direction: market.Sell,
		Entry: opportunity.EntryZone{Low: 4176.60, High: 4178.08},
	}
}

// Every structural strategy takes the instrument's own envelope; only the M1 scalp keeps the scalping book.
func TestTheStructuralStrategiesTakeTheInstrumentEnvelope(t *testing.T) {
	geometry := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	for _, id := range []opportunity.StrategyID{
		"key_level", "supply", "demand", "order_block", "fvg", "ifvg", "crt", "flip_zone",
		"confluence_zone", "break_retest", "trendline",
	} {
		got := computeStopEnvelope(goldCandidate(id), geometry, goldEnvelopeConfig())
		if got == nil || got.FloorPips != 50 || got.CapPips != 70 {
			t.Errorf("%s: envelope = %+v, want the instrument's 50-70", id, got)
		}
	}
}

// The real config, every instrument: the scalp's envelope never exceeds the scalping book's 45 pips, and on an
// instrument whose structural envelope is swing-sized (gold) it is strictly tighter than the structural strategies'.
func TestTheScalpKeepsAScalpEnvelopeOnEveryInstrument(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
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
		structural := computeStopEnvelope(goldCandidate("supply"), settings.Geometry, cfg)
		got := computeStopEnvelope(goldCandidate("impulse_pullback"), settings.Geometry, cfg)
		if got == nil {
			t.Fatalf("%s impulse_pullback: no envelope", symbol)
		}
		if got.CapPips > 45 {
			t.Errorf("%s impulse_pullback: cap %.0f exceeds the scalping book's 45", symbol, got.CapPips)
		}
		if symbol == "XAU" && !(got.CapPips < structural.CapPips && got.FloorPips < structural.FloorPips) {
			t.Errorf("XAU impulse_pullback: envelope %.0f-%.0f is not tighter than the structural %.0f-%.0f", got.FloorPips, got.CapPips, structural.FloorPips, structural.CapPips)
		}
	}
}
