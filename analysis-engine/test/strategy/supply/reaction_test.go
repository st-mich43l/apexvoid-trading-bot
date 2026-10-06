package supply_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func TestSupply_RestingZoneAloneNeverPublishesAConfirmedReaction(t *testing.T) {
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
	// The resting zone is only an observation. A confirmed reaction is the
	// frozen technique publisher's decision on the frame's technique instances
	// (internal/strategyutil/technique_decision.go, proven bar by bar against the
	// Python oracle in test/techniqueparity), so a context without that frame
	// publishes no confirmation however the zone's bars look.
	found := s.Evaluate(ctx)
	if len(found) != 1 || found[0].Reaction != nil {
		t.Fatalf("expected only the resting observation without a technique frame, got %+v", found)
	}
	if repeat := s.Evaluate(ctx); len(repeat) != 1 || repeat[0].ID != found[0].ID {
		t.Fatalf("replay must preserve the observation identity")
	}
}
