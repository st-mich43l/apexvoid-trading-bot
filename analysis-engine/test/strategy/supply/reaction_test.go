package supply_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func TestSupply_PublishesDistinctCausallyConfirmedReaction(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	touched := freshSupplyZone("zone1", 2020, 2022)
	touched.State = zone.StateTouched
	touched.BreakIndex = 0
	touchedAt := int64(1200)
	touched.LastTouchedAt = &touchedAt
	touched.TouchCount = 1
	candles := []market.Candle{
		{Time: 600, Open: 2025, High: 2026, Low: 2024, Close: 2025},
		{Time: 900, Open: 2024, High: 2025, Low: 2023, Close: 2024},
		{Time: 1200, Open: 2022, High: 2022.5, Low: 2019, Close: 2020},
	}
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   candles,
		Zones:     zone.ZoneState{Zones: []zone.Zone{touched}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{
			Side: liquidity.LiquiditySellSide,
			Low:  2010, High: 2011,
		}}},
	}
	found := s.Evaluate(ctx)
	if len(found) != 2 || found[0].Reaction != nil || found[1].Reaction == nil {
		t.Fatalf("expected resting observation plus one confirmed reaction, got %+v", found)
	}
	confirmed := found[1]
	if confirmed.ID == found[0].ID || confirmed.Reaction.ZoneID != "zone1" ||
		confirmed.Reaction.TouchBarTime != 1200 || confirmed.Reaction.ConfirmationBarTime != 1200 ||
		confirmed.Reaction.ReactionType != "rejection" {
		t.Fatalf("confirmation must have a separate causal identity: %+v", confirmed)
	}
	// Distinct opportunity IDs (a resting observation vs. its later
	// confirmed reaction), but the SAME real-world zone: a thesis
	// correlator must see these as one structural object, not two.
	if confirmed.StructuralID != found[0].StructuralID || confirmed.StructuralID != "zone1" {
		t.Fatalf("resting and confirmed observations of the same zone must share StructuralID, got resting=%q confirmed=%q", found[0].StructuralID, confirmed.StructuralID)
	}
	if err := confirmed.Validate(); err != nil {
		t.Fatalf("confirmed technical opportunity failed validation: %v", err)
	}
	if repeat := s.Evaluate(ctx); len(repeat) != 2 || repeat[1].ID != confirmed.ID {
		t.Fatalf("replay must preserve reaction identity")
	}
	ctx.Timeframes[market.M5].Candles = append(candles, market.Candle{
		Time: 1500, Open: 2019.5, High: 2019.8, Low: 2017.5, Close: 2018,
	})
	// The shared confirmation keeps seeing the same reaction for its lookback
	// window; that must stay the SAME opportunity (the book deduplicates by
	// ID), never a second reaction for the same touch.
	for _, c := range s.Evaluate(ctx) {
		if c.Reaction != nil && c.ID != confirmed.ID {
			t.Fatalf("follow-through after an already confirmed touch must not create a second reaction: %+v", c.Reaction)
		}
	}
}
