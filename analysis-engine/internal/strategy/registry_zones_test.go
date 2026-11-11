package strategy

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// zoneStrategy reports a zone verdict after every evaluation, as the zone-family strategies do.
type zoneStrategy struct {
	id      StrategyID
	verdict opportunity.ZoneLiveness
	pending bool
}

func (z *zoneStrategy) ID() StrategyID                         { return z.id }
func (z *zoneStrategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }
func (z *zoneStrategy) Evaluate(*analysiscontext.MarketContext) []opportunity.Candidate {
	z.pending = true
	return nil
}
func (z *zoneStrategy) TakeZoneLiveness() (opportunity.ZoneLiveness, bool) {
	if !z.pending {
		return opportunity.ZoneLiveness{}, false
	}
	z.pending = false
	return z.verdict, true
}

// The registry hands each reporting strategy's verdict to the engine once per evaluation, and only for a strategy that reported.
func TestRegistryCarriesTheZoneVerdictOfEachReportingStrategy(t *testing.T) {
	zone := &zoneStrategy{id: "supply", verdict: opportunity.ZoneLiveness{
		Frames: map[market.Timeframe]struct{}{market.M15: {}}, Terminal: map[string]opportunity.ReasonCode{"zone:A": opportunity.ReasonZoneInvalidated},
	}}
	plain := &allFrames{id: "key_level"}
	var configs []Config
	for _, id := range knownIDs {
		configs = append(configs, Config{ID: id, Version: "v1", Enabled: id == zone.id || id == plain.id})
	}
	registry, err := NewRegistry(configs, map[StrategyID]Factory{
		zone.id:  func(Config) (Strategy, error) { return zone, nil },
		plain.id: func(Config) (Strategy, error) { return plain, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	result, err := registry.Evaluate(contextWith(market.M5, market.M15), market.M5)
	if err != nil {
		t.Fatal(err)
	}
	got, ok := result.Zones[zone.id]
	if !ok || len(result.Zones) != 1 {
		t.Fatalf("exactly the reporting strategy's verdict must be carried: %+v", result.Zones)
	}
	if reason, finished := got.Verdict("zone:A", market.M15); !finished || reason != opportunity.ReasonZoneInvalidated {
		t.Fatalf("verdict lost in transit: %q %v", reason, finished)
	}
	// Drained: a later evaluation that reads nothing reports nothing.
	zone.pending = false
	result, err = registry.Evaluate(contextWith(market.M15), market.M15)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Zones) != 0 {
		t.Fatalf("a verdict must be reported once: %+v", result.Zones)
	}
}
