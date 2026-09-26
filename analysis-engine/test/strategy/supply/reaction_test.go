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
		{Time: 1200, Open: 2022, High: 2022.5, Low: 2019, Close: 2019.5},
	}
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles: candles,
		Zones: zone.ZoneState{Zones: []zone.Zone{touched}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{
			Side: liquidity.LiquiditySellSide,
			Low: 2010, High: 2011,
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
	if err := confirmed.Validate(); err != nil {
		t.Fatalf("confirmed technical opportunity failed validation: %v", err)
	}
	if repeat := s.Evaluate(ctx); len(repeat) != 2 || repeat[1].ID != confirmed.ID {
		t.Fatalf("replay must preserve reaction identity")
	}
	ctx.Timeframes[market.M5].Candles = append(candles, market.Candle{
		Time: 1500, Open: 2019.5, High: 2019.8, Low: 2017.5, Close: 2018,
	})
	if later := s.Evaluate(ctx); len(later) != 1 {
		t.Fatalf("follow-through after an already confirmed touch must not duplicate the reaction: %+v", later)
	}
}
