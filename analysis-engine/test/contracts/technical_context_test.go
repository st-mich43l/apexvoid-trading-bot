package contracts_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// goldenCandidate is a fixed, fully-specified supply-zone opportunity. The
// SAME bytes are consumed by algo-bot's decoder/adapter tests, so a hand-edit
// to either side of the S13B technical-context contract fails a build on both.
func goldenCandidate() opportunity.Candidate {
	return opportunity.Candidate{
		ID: "opp_golden_supply_xau", Strategy: "supply", StrategyVersion: "v2", Symbol: "XAU",
		ObservedTimeframe: market.M5, Direction: market.Sell,
		Entry:        opportunity.EntryZone{Low: 4352.5, High: 4356.0},
		Invalidation: market.PriceLevel{Price: 4358.75, Label: "supply_zone_invalidated"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 4344.0, Label: "nearest_sell_side_liquidity"}}},
		Evidence:     []opportunity.Evidence{{Code: "m5_supply_zone_fresh"}, {Code: "m5_supply_zone_relevance_immediate"}},
		Quality:      opportunity.StrategyQuality{Overall: 0.82, Components: map[string]float64{"zone_strength_quality": 0.9}},
		FormedAt:     1_789_960_500, CreatedAt: 1_789_961_100, ExpiresAt: 1_790_047_500,
		Technical: &opportunity.TechnicalContext{
			ATR: 3.61, ReferencePrice: 4351.9, ReferenceTime: 1_789_961_100,
			BiasDirection: market.Sell, BiasLayer: "internal",
		},
		Provenance: opportunity.AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "fixture"},
	}
}

func goldenEnvelope(t *testing.T, c opportunity.Candidate) []byte {
	t.Helper()
	payload, err := kafka.Encode(kafka.OpportunityPayloadFromCandidate(c, kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"}))
	if err != nil {
		t.Fatal(err)
	}
	envelope := kafka.Envelope{
		EventID: "evt-golden-1", EventType: "analysis.opportunity.v1", EventVersion: 1,
		OccurredAt: 1_789_961_100, ProducedAt: 1_789_961_101, Producer: "apexvoid-analysis-engine",
		CorrelationID: "corr-golden-1", ConfigVersion: 3, ConfigFingerprint: "fixture", Payload: payload,
	}
	raw, err := kafka.Encode(envelope)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestTechnicalContext_GoldenEnvelopeMatchesTheSharedFixtureAndSchema(t *testing.T) {
	raw := goldenEnvelope(t, goldenCandidate())
	path := filepath.Join(repoContractPath("analysis", "examples"), "opportunity-v1-technical-context.json")
	if os.Getenv("UPDATE_GOLDEN") == "1" {
		var pretty any
		_ = json.Unmarshal(raw, &pretty)
		out, _ := json.MarshalIndent(pretty, "", "  ")
		if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("shared golden fixture missing (run with UPDATE_GOLDEN=1): %v", err)
	}
	var gotAny, wantAny any
	if err := json.Unmarshal(raw, &gotAny); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(want, &wantAny); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(gotAny, wantAny) {
		t.Errorf("Go encoder output drifted from the shared golden fixture consumed by algo-bot:\n got: %s\nwant: %s", raw, want)
	}

	// The payload inside the envelope must also satisfy the published schema.
	var env struct {
		Payload json.RawMessage `json:"payload"`
	}
	_ = json.Unmarshal(raw, &env)
	var payload any
	_ = json.Unmarshal(env.Payload, &payload)
	if err := compileSchema(t, repoContractPath("analysis", "opportunity-v1.schema.json")).Validate(payload); err != nil {
		t.Errorf("golden payload fails opportunity-v1 schema: %v", err)
	}
}

func TestTechnicalContext_IsOmittedWhenTheEngineHasNoFacts(t *testing.T) {
	c := goldenCandidate()
	c.Technical = nil
	payload := kafka.OpportunityPayloadFromCandidate(c, kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"})
	encoded, _ := kafka.Encode(payload)
	var generic map[string]any
	_ = json.Unmarshal(encoded, &generic)
	if _, present := generic["technical_context"]; present {
		t.Fatalf("absent facts must be omitted, never emitted as a placeholder: %s", encoded)
	}
	validateGo(t, compileSchema(t, repoContractPath("analysis", "opportunity-v1.schema.json")), payload)
}

func TestTechnicalContext_BiasIsOmittedWhenThereIsNoConfirmedBias(t *testing.T) {
	c := goldenCandidate()
	c.Technical.BiasDirection, c.Technical.BiasLayer = "", ""
	encoded, _ := kafka.Encode(kafka.OpportunityPayloadFromCandidate(c, kafka.AlgorithmVersion{}))
	var generic struct {
		TechnicalContext map[string]any `json:"technical_context"`
	}
	_ = json.Unmarshal(encoded, &generic)
	if _, present := generic.TechnicalContext["bias"]; present {
		t.Fatalf("no confirmed bias must not be emitted as a neutral guess: %s", encoded)
	}
}

func TestOpportunitySchema_RejectsMalformedTechnicalContext(t *testing.T) {
	schema := compileSchema(t, repoContractPath("analysis", "opportunity-v1.schema.json"))
	base := func() map[string]any {
		var env struct {
			Payload map[string]any `json:"payload"`
		}
		_ = json.Unmarshal(goldenEnvelope(t, goldenCandidate()), &env)
		return env.Payload
	}
	for name, mutate := range map[string]func(map[string]any){
		"zero ATR":           func(p map[string]any) { p["technical_context"].(map[string]any)["atr"] = 0 },
		"negative reference": func(p map[string]any) { p["technical_context"].(map[string]any)["reference_price"] = -1 },
		"missing atr":        func(p map[string]any) { delete(p["technical_context"].(map[string]any), "atr") },
		"unknown field":      func(p map[string]any) { p["technical_context"].(map[string]any)["spread"] = 0.1 },
		"neutral bias": func(p map[string]any) {
			p["technical_context"].(map[string]any)["bias"].(map[string]any)["direction"] = "NEUTRAL"
		},
		"account leakage": func(p map[string]any) { p["technical_context"].(map[string]any)["balance"] = 1000 },
	} {
		payload := base()
		mutate(payload)
		if err := schema.Validate(payload); err == nil {
			t.Errorf("%s: schema accepted a malformed technical_context", name)
		}
	}
}

func TestTechnicalContext_HigherTimeframesAreCausalAndSchemaValid(t *testing.T) {
	c := goldenCandidate()
	c.Technical.HigherTimeframes = []opportunity.HigherTimeframeBias{
		{Timeframe: market.H1, Direction: market.Sell, Layer: "major", ReferenceTime: c.CreatedAt - 3900},
		{Timeframe: market.H4, Direction: market.Buy, Layer: "intermediate", ReferenceTime: c.CreatedAt - 18000},
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	payload := kafka.OpportunityPayloadFromCandidate(c, kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"})
	if len(payload.TechnicalContext.HigherTimeframes) != 2 {
		t.Fatalf("two independent higher-timeframe reads required, got %+v", payload.TechnicalContext)
	}
	validateGo(t, compileSchema(t, repoContractPath("analysis", "opportunity-v1.schema.json")), payload)

	c.Technical.HigherTimeframes = append(c.Technical.HigherTimeframes,
		opportunity.HigherTimeframeBias{Timeframe: market.H1, Direction: market.Buy, Layer: "micro", ReferenceTime: c.CreatedAt - 3900})
	if err := c.Validate(); err == nil {
		t.Fatal("duplicate HTF source must fail validation")
	}
}

func TestTechnicalContext_ConfirmedReactionIsAdditiveAndSchemaValid(t *testing.T) {
	c := goldenCandidate()
	c.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: "zone:confirmed", TouchBarTime: c.CreatedAt - 300,
		ConfirmationBarTime: c.CreatedAt, ReactionType: "rejection",
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	confirmed := *c.Reaction
	c.Technical.Confirmation = &confirmed
	payload := kafka.OpportunityPayloadFromCandidate(c, kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"})
	if payload.TechnicalContext.Confirmation == nil || payload.TechnicalContext.Confirmation.ZoneID != "zone:confirmed" {
		t.Fatalf("real zone reaction must survive Kafka adapter: %+v", payload.TechnicalContext)
	}
	validateGo(t, compileSchema(t, repoContractPath("analysis", "opportunity-v1.schema.json")), payload)

	c.Reaction.ConfirmationBarTime = c.CreatedAt + 300
	if err := c.Validate(); err == nil {
		t.Fatal("future confirmation must fail")
	}
}
