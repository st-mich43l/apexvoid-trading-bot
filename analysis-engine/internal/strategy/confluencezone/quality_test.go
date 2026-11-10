package confluencezone

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const barTime = int64(1_700_000_000)

func ctxWith(zones ...zone.Zone) *analysiscontext.MarketContext {
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{}}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf, Zones: zone.ZoneState{ATR: 3}}
	}
	ctx.Timeframes[market.M15].Zones.Zones = zones
	ctx.Timeframes[market.M5].Candles = []market.Candle{{Time: barTime}}
	return ctx
}

// XAU: pip 0.1, execution floor 25 pips, barrier buffer 0.15 ATR. A SELL band [4000.0, 4000.5] (proximal edge 4000.0), a 6-pip stop above it, the liquidity target 75 pips below.
func testStrategy() *Strategy {
	return &Strategy{cfg: Config{PipSize: 0.1, ExecutionStopMinPips: 25, Barrier: strategyutil.BarrierPolicy{BufferATR: 0.15, MaxWidthATR: 2, MaxWidthPips: 100}}}
}

func sellSetup() Setup {
	return Setup{Direction: market.Sell, EntryLow: 4000, EntryHigh: 4000.5, Stop: 4000.6, Target: 3992.5, ATR: 3}
}

func TestConfluenceRoomIsAgainstTheEffectiveRiskAndTheNearestBarrier(t *testing.T) {
	s := testStrategy()
	r := s.qualityRoom(ctxWith(), sellSetup())
	// 6 structural pips, 25 effective: 75 / 25 = 3R (not 12.5R).
	m := quality.New(QualityModel)
	strategyutil.ScoreRoom(m, r, 0.1)
	c := m.Components()
	if math.Abs(c["structural_risk_pips"]-6) > 1e-6 || math.Abs(c["effective_risk_pips"]-25) > 1e-6 || math.Abs(c["effective_room_r"]-3) > 1e-6 {
		t.Errorf("risk %v / %v pips, %v R, want 6 / 25 and 3R", c["structural_risk_pips"], c["effective_risk_pips"], c["effective_room_r"])
	}
	if c["room_structural"] != 1 {
		t.Errorf("the nearest opposing liquidity is observed structure")
	}
	// A demand wall 40 raw pips below the entry (the opposing wall of a SELL) limits it to 35.5 pips: 1.42R.
	wall := zone.Zone{ID: "w", Kind: zone.KindDemand, Low: 3995.9, High: 3996, State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
	limited := s.qualityRoom(ctxWith(wall), sellSetup())
	if limited.Room.Limiter != strategyutil.ScalpRoomLimitedByBarrier || math.Abs(limited.Room.Pips-35.5) > 1e-6 || math.Abs(limited.Geometry.EffectiveRoomR-35.5/25) > 1e-6 {
		t.Errorf("%+v", limited.Room)
	}
}

func TestConfluenceTargetBehindTheEntryHasNoRoom(t *testing.T) {
	s := testStrategy()
	setup := sellSetup()
	setup.Target = 4005 // above a SELL entry
	if r := s.qualityRoom(ctxWith(), setup); r.Geometry.Valid || r.Geometry.Score != 0 {
		t.Errorf("%+v", r.Geometry)
	}
}
