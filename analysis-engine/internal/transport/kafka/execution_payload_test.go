package kafka

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func TestExecutionBlockIsPublishedOnlyForASinglePriceOpportunity(t *testing.T) {
	payload := OpportunityPayload{
		ID: "opp", Strategy: "trendline", Symbol: "XAU", Direction: "BUY",
		Execution: &ExecutionPayload{Model: string(opportunity.ExecutionSinglePrice), Price: 4300.15, Timeframe: string(market.M5), ProjectedAt: 400, ValidUntil: 2200, Proximity: 0.2, SlopePerBar: 0.05},
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"execution":{"model":"single_price","price":4300.15,"timeframe":"M5","projected_at":400,"valid_until":2200,"proximity":0.2,"slope_per_bar":0.05}`} {
		if !strings.Contains(string(raw), want) {
			t.Fatalf("payload %s lacks %s", raw, want)
		}
	}
	plain, _ := json.Marshal(OpportunityPayload{ID: "opp", Strategy: "supply"})
	if strings.Contains(string(plain), "execution") {
		t.Fatalf("a zone opportunity must not carry the block: %s", plain)
	}
}
