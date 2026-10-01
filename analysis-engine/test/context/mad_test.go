package context_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/mad"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func TestBuildCarriesCanonicalMADContext(t *testing.T) {
	candles := []market.Candle{
		{Time: 1790895600, Open: 100, High: 107, Low: 98, Close: 103},
	}
	cfg := mad.Config{AsiaStartHour: 22, LondonStartHour: 7, AccumMinimumRQ: .8, AccumMaximumRQ: 6, ExpandBreakATR: .35, ExpandDisplacementATR: 1.25, ExpandAcceptCloses: 2, ManipMinimumPenetrationATR: .05, ManipMinimumReclaimATR: .05, PipSize: .0001}
	ctx := context.Build("XAU", "M5", map[market.Timeframe]context.TimeframeInput{"M5": {
		Candles: candles, Structure: structure.StructureState{}, Liquidity: liquidity.LiquidityState{}, Zones: zone.ZoneState{}, ATR: 2, MADConfig: cfg,
	}})
	if ctx.MAD.Version != mad.Version {
		t.Fatalf("expected MAD v%d, got %+v", mad.Version, ctx.MAD)
	}
	if ctx.Timeframes["M5"].MAD.Phase == "" {
		t.Fatal("timeframe must carry MAD snapshot")
	}
}
