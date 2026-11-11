package replaycapture_test

// TL-LIVE-2 on the same real EURUSD window as trendline_mtf_test.go: M5 and M15, SELL and (mirrored) BUY. Each Trendline opportunity declares the typed one-price
// execution, with the projected price read from closed bars only, a bounded validity, and the interaction corridor kept as the published entry.

import (
	"encoding/json"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

func singlePriceCases(s scenario) []struct {
	name    string
	frames  map[string][][]float64
	primary market.Timeframe
	bar     int64
} {
	start := int64(1_800_000_000)
	return []struct {
		name    string
		frames  map[string][][]float64
		primary market.Timeframe
		bar     int64
	}{
		{"M15 SELL", map[string][][]float64{"M15": s.M15, "H1": s.H1}, market.M15, 900},
		{"M15 BUY", map[string][][]float64{"M15": mirror(s.M15), "H1": mirror(s.H1)}, market.M15, 900},
		{"M5 SELL", map[string][][]float64{"M5": reread(s.M15, start, 300), "H1": reread(s.H1, start-int64(len(s.H1))*3600, 3600)}, market.M5, 300},
		{"M5 BUY", map[string][][]float64{"M5": mirror(reread(s.M15, start, 300)), "H1": mirror(reread(s.H1, start-int64(len(s.H1))*3600, 3600))}, market.M5, 300},
	}
}

func TestEveryTrendlineOpportunityDeclaresAProjectedSinglePrice(t *testing.T) {
	for _, c := range singlePriceCases(load(t)) {
		t.Run(c.name, func(t *testing.T) {
			got, opts := replayWithOptions(t, capture(c.frames), c.primary, nil)
			o := only(t, got)
			x := o.Execution
			if x == nil || o.ExecutionModel() != opportunity.ExecutionSinglePrice {
				t.Fatalf("a Trendline opportunity must declare the single-price execution: %+v", o.Execution)
			}
			// No artificial zone: the published entry is the interaction corridor (a 0.20 ATR band either side of the line), not 30 pips.
			if x.Price < o.Entry.Low || x.Price > o.Entry.High {
				t.Errorf("price %v outside the corridor %+v", x.Price, o.Entry)
			}
			// Closed bars only: the projection is for the bar AFTER the one that closed (created_at is that closed bar's open).
			if x.ProjectedAt != o.CreatedAt+c.bar || x.Timeframe != c.primary {
				t.Errorf("projected at %d on %s, want %d on %s", x.ProjectedAt, x.Timeframe, o.CreatedAt+c.bar, c.primary)
			}
			// Bounded validity: at least one bar, never longer than the frame's cap; the opportunity ends exactly when the projection goes stale.
			bars := (x.ValidUntil - x.ProjectedAt) / c.bar
			if bars < 1 || bars > 6 {
				t.Errorf("valid for %d bars", bars)
			}
			if o.ExpiresAt != x.ValidUntil {
				t.Errorf("expires %d, want valid_until %d (not the strategy's 12 hours)", o.ExpiresAt, x.ValidUntil)
			}
			if x.Proximity <= 0 {
				t.Error("proximity must be a positive distance")
			}
			if o.ZoneID == "" || o.ZoneID != o.StructuralID {
				t.Errorf("the line is the zone of the opportunity: %q vs %q", o.ZoneID, o.StructuralID)
			}
			// The Kafka envelope carries the typed block.
			raw, err := replaycapture.CreationEnvelope(o, opts)
			if err != nil {
				t.Fatal(err)
			}
			var env struct {
				Payload struct {
					Execution map[string]any `json:"execution"`
					ExpiresAt int64          `json:"expires_at"`
				} `json:"payload"`
			}
			if err := json.Unmarshal(raw, &env); err != nil {
				t.Fatal(err)
			}
			if env.Payload.Execution["model"] != "single_price" || env.Payload.Execution["price"].(float64) != x.Price || int64(env.Payload.Execution["valid_until"].(float64)) != x.ValidUntil {
				t.Errorf("envelope execution block %v", env.Payload.Execution)
			}
		})
	}
}

// With the switch off the same real path publishes the corridor as a zone and no execution block: the one-price model is a configuration, not a fork.
func TestTheSwitchOffRestoresTheZoneEnvelope(t *testing.T) {
	c := singlePriceCases(load(t))[0]
	got, _ := replayWithOptions(t, capture(c.frames), c.primary, map[string]any{"single_price_execution": false})
	o := only(t, got)
	if o.Execution != nil || o.ExecutionModel() != opportunity.ExecutionZone {
		t.Fatalf("no execution block expected: %+v", o.Execution)
	}
	if o.ExpiresAt-o.CreatedAt < 11*3600 {
		t.Errorf("the strategy's own expiry applies again: %d seconds", o.ExpiresAt-o.CreatedAt)
	}
}
