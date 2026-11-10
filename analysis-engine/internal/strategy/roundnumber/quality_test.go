package roundnumber

import (
	"math"
	"sort"
	"strings"
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

func testStrategy() *Strategy {
	return &Strategy{
		cfg:     Config{PipSize: 0.1},
		budget:  strategyutil.StopBudget{MaxPips: 70, MinPips: 25, PipSize: 0.1},
		barrier: strategyutil.BarrierPolicy{BufferATR: 0.15, MaxWidthATR: 2, MaxWidthPips: 100},
	}
}

// A BUY band [4000.0, 4000.5] (proximal edge 4000.5), a structural stop 6 pips below it, the target 75 pips above.
func setupOf(kind string) Setup {
	return Setup{Kind: KindSweepReclaim, ForceATR: 1, RejectionWick: 0.4, SweepDepthATR: 0.5, Direction: market.Buy, EntryLow: 4000, EntryHigh: 4000.5, Stop: 3999.9, Target: 4008, TargetKind: kind, M5ATR: 3}
}

func scoreOf(s *Strategy, ctx *analysiscontext.MarketContext, setup Setup, price float64) map[string]float64 {
	m := quality.New(QualityModel)
	reference := setup.EntryHigh
	if setup.Direction == market.Sell {
		reference = setup.EntryLow
	}
	s.score(m, setup, qualityContext{price: price, localTrend: "up", room: s.qualityRoom(ctx, setup, reference), pipSize: 0.1})
	return m.Components()
}

func TestOnlyObservedStructureIsStructuralRoom(t *testing.T) {
	s := testStrategy()
	observed := scoreOf(s, ctxWith(), setupOf(TargetLiquidity), 4000.5)
	if observed["room_structural"] != 1 || observed["score_room"] <= 0 {
		t.Errorf("an observed target: structural %v score %v", observed["room_structural"], observed["score_room"])
	}
	projected := scoreOf(s, ctxWith(), setupOf(TargetNextHandle), 4000.5)
	if projected["room_structural"] != 0 || projected["score_room"] != 0 {
		t.Errorf("a projected or fixed target: structural %v score %v, want none", projected["room_structural"], projected["score_room"])
	}
	wall := zone.Zone{ID: "w", Kind: zone.KindSupply, Low: 4003, High: 4004, State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
	limited := scoreOf(s, ctxWith(wall), setupOf(TargetLiquidity), 4000.5)
	if limited["room_limited_by_barrier"] != 1 || limited["available_room_pips"] >= limited["target_room_pips"] {
		t.Errorf("a barrier nearer than the target must limit the room: %+v", limited)
	}
	if math.Abs(observed["structural_risk_pips"]-6) > 1e-6 || math.Abs(observed["effective_risk_pips"]-25) > 1e-6 {
		t.Errorf("risk %v / %v pips, want 6 / 25 (the floor is the denominator)", observed["structural_risk_pips"], observed["effective_risk_pips"])
	}
}

func TestEntryLocationIsDirectionAware(t *testing.T) {
	s := testStrategy()
	buy := setupOf(TargetLiquidity)
	if got := scoreOf(s, ctxWith(), buy, 3999.5)["score_entry_location"]; got != 1 {
		t.Errorf("BUY deeper in the band: %v, want 1", got)
	}
	if got := scoreOf(s, ctxWith(), buy, 4003)["score_entry_location"]; got >= 1 {
		t.Errorf("BUY above the edge: %v, want a penalty", got)
	}
	sell := buy
	sell.Direction, sell.Stop, sell.Target = market.Sell, 4000.6, 3992
	if got := scoreOf(s, ctxWith(), sell, 4001)["score_entry_location"]; got != 1 {
		t.Errorf("SELL higher in the band: %v, want 1", got)
	}
	if got := scoreOf(s, ctxWith(), sell, 3997.5)["score_entry_location"]; got >= 1 {
		t.Errorf("SELL below the edge: %v, want a penalty", got)
	}
}

func TestQualityIntegrity(t *testing.T) {
	s := testStrategy()
	var schema string
	for _, kind := range []string{TargetLiquidity, TargetNextHandle} {
		for _, price := range []float64{3999, 4000.5, 4004, math.NaN()} {
			c := scoreOf(s, ctxWith(), setupOf(kind), price)
			var names []string
			sum := 0.0
			for name, v := range c {
				if math.IsNaN(v) || math.IsInf(v, 0) {
					t.Errorf("%s: not finite", name)
				}
				if strings.HasPrefix(name, quality.Prefix) {
					if v < 0 || v > 1 {
						t.Errorf("%s = %v outside [0, 1]", name, v)
					}
					names = append(names, name)
					sum += v
				}
			}
			sort.Strings(names)
			if joined := strings.Join(names, ","); schema == "" {
				schema = joined
			} else if joined != schema {
				t.Errorf("schema changed: %s vs %s", joined, schema)
			}
			if c[quality.ModelVersionKey] != 3 {
				t.Errorf("model version %v, want 3", c[quality.ModelVersionKey])
			}
			m := quality.New(QualityModel)
			s.score(m, setupOf(kind), qualityContext{price: price, localTrend: "up", room: s.qualityRoom(ctxWith(), setupOf(kind), 4000.5), pipSize: 0.1})
			if math.Abs(m.Overall()-sum/float64(len(names))) > 1e-12 {
				t.Errorf("overall %v is not the mean %v", m.Overall(), sum/float64(len(names)))
			}
		}
	}
}
