package strategy_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

type fakeStrategy struct {
	id         strategy.StrategyID
	required   []market.Timeframe
	candidates []opportunity.Candidate
	calls      int
}

func (s *fakeStrategy) ID() strategy.StrategyID { return s.id }
func (s *fakeStrategy) RequiredTimeframes() []market.Timeframe {
	return append([]market.Timeframe(nil), s.required...)
}
func (s *fakeStrategy) Evaluate(*analysiscontext.MarketContext) []opportunity.Candidate {
	s.calls++
	return s.candidates
}

func fullConfig(enabled map[strategy.StrategyID]bool) []strategy.Config {
	configs := make([]strategy.Config, 0, len(strategy.KnownIDs()))
	for _, id := range strategy.KnownIDs() {
		configs = append(configs, strategy.Config{ID: id, Version: "v2", Enabled: enabled[id]})
	}
	return configs
}

func testContext(timeframes ...market.Timeframe) *analysiscontext.MarketContext {
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: make(map[market.Timeframe]*analysiscontext.TimeframeContext)}
	for _, tf := range timeframes {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf, Structure: structure.StructureState{}, Liquidity: liquidity.LiquidityState{}}
	}
	return ctx
}

func validCandidate(id string) opportunity.Candidate {
	return opportunity.Candidate{
		ID: id, Strategy: "key_level", StrategyVersion: "v2", Symbol: "XAU", Direction: market.Buy,
		Entry:        opportunity.EntryZone{Low: 100, High: 101},
		Invalidation: market.PriceLevel{Price: 99, Label: "structural_low"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 103, Label: "opposing_liquidity"}}},
		Evidence:     []opportunity.Evidence{{Code: "m5_key_level_reclaim"}},
		Quality:      opportunity.StrategyQuality{Overall: 0.8, Components: map[string]float64{"location_quality": 0.8}},
		CreatedAt:    100, ExpiresAt: 200,
		Provenance: opportunity.AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "fixture"},
	}
}

func TestRegistryOnlyInstantiatesEnabledStrategies(t *testing.T) {
	configs := fullConfig(map[strategy.StrategyID]bool{"key_level": true})
	instance := &fakeStrategy{id: "key_level", required: []market.Timeframe{market.M5}}
	registry, err := strategy.NewRegistry(configs, map[strategy.StrategyID]strategy.Factory{
		"key_level": func(strategy.Config) (strategy.Strategy, error) { return instance, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	got := registry.EnabledIDs()
	if len(got) != 1 || got[0] != "key_level" {
		t.Fatalf("EnabledIDs() = %v, want [key_level]", got)
	}
}

func TestRegistryFailsWhenEnabledImplementationIsNotRegistered(t *testing.T) {
	_, err := strategy.NewRegistry(fullConfig(map[strategy.StrategyID]bool{"key_level": true}), nil)
	if err == nil {
		t.Fatal("expected enabled strategy without factory to fail closed")
	}
}

func TestRegistryRejectsMissingAndUnknownConfiguration(t *testing.T) {
	configs := fullConfig(nil)
	if err := strategy.ValidateConfigs(configs[:len(configs)-1]); err == nil {
		t.Fatal("expected missing catalog entry to fail")
	}
	configs = append(configs, strategy.Config{ID: "invented_strategy", Version: "v2"})
	if err := strategy.ValidateConfigs(configs); err == nil {
		t.Fatal("expected unknown catalog entry to fail")
	}
}

func TestRegistryEvaluatesOnlyAffectedAndReadyStrategies(t *testing.T) {
	configs := fullConfig(map[strategy.StrategyID]bool{"key_level": true, "supply": true})
	keyLevel := &fakeStrategy{id: "key_level", required: []market.Timeframe{market.M5}, candidates: []opportunity.Candidate{validCandidate("opp-key-level")}}
	supply := &fakeStrategy{id: "supply", required: []market.Timeframe{market.M5, market.H1}}
	registry, err := strategy.NewRegistry(configs, map[strategy.StrategyID]strategy.Factory{
		"key_level": func(strategy.Config) (strategy.Strategy, error) { return keyLevel, nil },
		"supply":    func(strategy.Config) (strategy.Strategy, error) { return supply, nil },
	})
	if err != nil {
		t.Fatal(err)
	}

	result, err := registry.Evaluate(testContext(market.M5), market.M5)
	if err != nil {
		t.Fatal(err)
	}
	if keyLevel.calls != 1 || supply.calls != 0 {
		t.Fatalf("calls key_level=%d supply=%d, want 1/0", keyLevel.calls, supply.calls)
	}
	if len(result.Evaluated) != 1 || result.Evaluated[0] != "key_level" {
		t.Fatalf("Evaluated = %v, want [key_level]", result.Evaluated)
	}
	if len(result.Deferred) != 1 || result.Deferred[0] != "supply" {
		t.Fatalf("Deferred = %v, want [supply]", result.Deferred)
	}
	if len(result.Candidates) != 1 || result.Candidates[0].ID != "opp-key-level" {
		t.Fatalf("Candidates = %#v, want one key level candidate", result.Candidates)
	}

	result, err = registry.Evaluate(testContext(market.M5, market.H1), market.H1)
	if err != nil {
		t.Fatal(err)
	}
	if keyLevel.calls != 1 || supply.calls != 1 {
		t.Fatalf("H1 close calls key_level=%d supply=%d, want 1/1", keyLevel.calls, supply.calls)
	}
	if len(result.Evaluated) != 1 || result.Evaluated[0] != "supply" {
		t.Fatalf("H1 Evaluated = %v, want [supply]", result.Evaluated)
	}
}

func TestRegistryRejectsCandidateThatBreaksTheStrategyContract(t *testing.T) {
	configs := fullConfig(map[strategy.StrategyID]bool{"key_level": true})
	wrong := validCandidate("opp-wrong")
	wrong.Strategy = "supply"
	instance := &fakeStrategy{id: "key_level", required: []market.Timeframe{market.M5}, candidates: []opportunity.Candidate{wrong}}
	registry, err := strategy.NewRegistry(configs, map[strategy.StrategyID]strategy.Factory{
		"key_level": func(strategy.Config) (strategy.Strategy, error) { return instance, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := registry.Evaluate(testContext(market.M5), market.M5); err == nil {
		t.Fatal("expected mismatched candidate strategy to fail")
	}
}
