package context_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func TestDeriveBias_PrefersTheHighestLayerWithADefinitiveTrend(t *testing.T) {
	// Source task §36: bias is derived from structure, preferring the
	// highest layer that actually has a read — Major here is Range (no
	// opinion), so Intermediate's Bearish read must win, NOT Micro's
	// Bullish one (an even lower layer that happens to also have an
	// opinion must never outrank Intermediate).
	state := structure.StructureState{
		Major:        structure.LayerState{Layer: structure.StructureMajor, Trend: structure.TrendRange},
		Intermediate: structure.LayerState{Layer: structure.StructureIntermediate, Trend: structure.TrendBearish},
		Internal:     structure.LayerState{Layer: structure.StructureInternal, Trend: structure.TrendRange},
		Micro:        structure.LayerState{Layer: structure.StructureMicro, Trend: structure.TrendBullish},
	}
	bias := context.DeriveBias(state)
	if bias.Direction != market.Sell || bias.Layer != structure.StructureIntermediate {
		t.Errorf("expected Sell/Intermediate (the highest layer with a real read), got %v/%v", bias.Direction, bias.Layer)
	}
}

func TestDeriveBias_AllRangeIsUnknown(t *testing.T) {
	state := structure.StructureState{} // every layer defaults to TrendUnknown
	bias := context.DeriveBias(state)
	if bias.Trend != structure.TrendUnknown {
		t.Errorf("expected TrendUnknown with no definitive layer anywhere, got %v", bias.Trend)
	}
}

func TestBuild_PreservesMultiTimeframeDisagreement(t *testing.T) {
	// Source task §37: "H1 bearish, M15 bearish, M5 bullish internal
	// pullback... do NOT flatten this immediately into bias = neutral" —
	// every timeframe's own read must survive in Timeframes, even though
	// Bias only reflects the primary one.
	h1 := structure.StructureState{Major: structure.LayerState{Trend: structure.TrendBearish}}
	m5 := structure.StructureState{Major: structure.LayerState{Trend: structure.TrendBullish}}
	h1Zones := zone.ZoneState{Zones: []zone.Zone{{ID: "h1-demand", Kind: zone.KindDemand}}}
	m5Zones := zone.ZoneState{Zones: []zone.Zone{{ID: "m5-supply", Kind: zone.KindSupply}}}

	ctx := context.Build("XAU", "M5", map[market.Timeframe]context.TimeframeInput{
		"H1": {Structure: h1, Liquidity: liquidity.LiquidityState{}, Zones: h1Zones, ATR: 3.0},
		"M5": {Structure: m5, Liquidity: liquidity.LiquidityState{}, Zones: m5Zones, ATR: 1.5},
	})

	if len(ctx.Timeframes) != 2 {
		t.Fatalf("expected both timeframes preserved, got %d", len(ctx.Timeframes))
	}
	if ctx.Timeframes["H1"].Structure.Major.Trend != structure.TrendBearish {
		t.Error("H1's own bearish read must be preserved, not overwritten")
	}
	if ctx.Timeframes["M5"].Structure.Major.Trend != structure.TrendBullish {
		t.Error("M5's own bullish read must be preserved, not overwritten")
	}
	if ctx.Timeframes["H1"].Zones.Zones[0].ID != "h1-demand" || ctx.Zones.State.Zones[0].ID != "m5-supply" {
		t.Error("zone reads must preserve each timeframe and expose the primary timeframe")
	}
	// Convenience fields reflect the PRIMARY (M5) timeframe only.
	if ctx.Structure.Timeframe != "M5" || ctx.Structure.State.Major.Trend != structure.TrendBullish {
		t.Error("Context.Structure must reflect the primary timeframe's own read")
	}
	if ctx.Bias.Direction != market.Buy {
		t.Errorf("Bias must be derived from the PRIMARY timeframe (M5, bullish), got %v", ctx.Bias.Direction)
	}
	if ctx.Volatility.ATR != 1.5 {
		t.Errorf("Volatility.ATR must be the primary timeframe's ATR, got %v", ctx.Volatility.ATR)
	}
}

func TestBuild_MissingPrimaryTimeframeLeavesConvenienceFieldsZero(t *testing.T) {
	ctx := context.Build("XAU", "H4", map[market.Timeframe]context.TimeframeInput{
		"M5": {Structure: structure.StructureState{}, Liquidity: liquidity.LiquidityState{}},
	})
	if ctx.Bias.Trend != structure.TrendUnknown {
		t.Error("with no primary-timeframe data yet, Bias must stay at its zero value, not fabricate a read")
	}
}
