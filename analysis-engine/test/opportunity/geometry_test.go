package opportunity_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func geometryCandidate(direction market.Direction) opportunity.Candidate {
	return opportunity.Candidate{
		ID: "opp_geometry", Strategy: "supply", StrategyVersion: "v1", Symbol: "XAU",
		Direction:    direction,
		Entry:        opportunity.EntryZone{Low: 4291.211, High: 4296.869},
		Invalidation: market.PriceLevel{Price: 4298.970714, Label: "zone_break"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 4284.159928, Label: "target"}}},
		Evidence:     []opportunity.Evidence{{Code: "reaction"}},
		FormedAt:     1, CreatedAt: 2, ExpiresAt: 3,
		Quality: opportunity.StrategyQuality{Overall: 1},
		Provenance: opportunity.AnalysisProvenance{
			StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1",
			ConfigVersion: 1, ConfigFingerprint: "cfg",
		},
	}
}

func TestNormalizeGeometryKeepsSellBandProtectiveAndTickAligned(t *testing.T) {
	geometry, err := market.NewGeometry("XAU", "XAUUSD", 0.1, 2)
	if err != nil {
		t.Fatal(err)
	}
	got, err := opportunity.NormalizeGeometry(geometryCandidate(market.Sell), geometry)
	if err != nil {
		t.Fatal(err)
	}
	if got.Entry.Low != 4291.21 || got.Entry.High != 4296.87 {
		t.Fatalf("entry = %v-%v, want 4291.21-4296.87", got.Entry.Low, got.Entry.High)
	}
	if got.Invalidation.Price != 4298.98 || got.Targets[0].Price.Price != 4284.16 {
		t.Fatalf("stop/target = %v/%v, want 4298.98/4284.16", got.Invalidation.Price, got.Targets[0].Price.Price)
	}
	if got.ID != "opp_geometry" {
		t.Fatalf("normalization changed identity: %q", got.ID)
	}
}

func TestNormalizeGeometryKeepsBuyStopAndTargetOnCorrectSide(t *testing.T) {
	geometry, err := market.NewGeometry("EURUSD", "EURUSD", 0.0001, 5)
	if err != nil {
		t.Fatal(err)
	}
	c := geometryCandidate(market.Buy)
	c.Symbol = "EURUSD"
	c.Entry = opportunity.EntryZone{Low: 1.10101, High: 1.10109}
	c.Invalidation.Price = 1.10099
	c.Targets[0].Price.Price = 1.10111
	got, err := opportunity.NormalizeGeometry(c, geometry)
	if err != nil {
		t.Fatal(err)
	}
	if got.Entry.Low != 1.10101 || got.Entry.High != 1.10109 {
		t.Fatalf("entry = %v-%v, want 1.10101-1.10109", got.Entry.Low, got.Entry.High)
	}
	if got.Invalidation.Price >= market.Price(got.Entry.Low) || got.Targets[0].Price.Price <= market.Price(got.Entry.High) {
		t.Fatalf("BUY geometry lost ordering: stop=%v entry=%v-%v target=%v", got.Invalidation.Price, got.Entry.Low, got.Entry.High, got.Targets[0].Price.Price)
	}
}

func TestNormalizeGeometryRejectsSymbolMismatch(t *testing.T) {
	geometry, err := market.NewGeometry("XAU", "XAUUSD", 0.1, 2)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := opportunity.NormalizeGeometry(geometryCandidate(market.Sell), market.Geometry{Symbol: "EURUSD", PipSize: geometry.PipSize, PriceDigits: geometry.PriceDigits}); err == nil {
		t.Fatal("expected symbol mismatch")
	}
}
