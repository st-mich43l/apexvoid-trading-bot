package breakretest

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
	ctx := &analysiscontext.MarketContext{Symbol: "EURUSD", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{}}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf, Zones: zone.ZoneState{ATR: 0.0008}}
	}
	ctx.Timeframes[market.M15].Zones.Zones = zones
	ctx.Timeframes[market.M5].Candles = []market.Candle{{Time: barTime}}
	return ctx
}

// EURUSD: pip 0.0001, execution stop floor 12 pips, barrier buffer 0.5 ATR.
func testStrategy() *Strategy {
	return &Strategy{cfg: Config{
		PipSize: 0.0001, ExecutionStopMinPips: 12,
		Barrier: strategyutil.BarrierPolicy{BufferATR: 0.5, MaxWidthATR: 2, MaxWidthPips: 100},
	}}
}

// A BUY band [1.1000, 1.1002]: proximal edge 1.1002, a 6-pip structural stop, the target 36 pips above (an opposing pivot).
func setupOf() Setup {
	return Setup{Direction: market.Buy, EntryLow: 1.1000, EntryHigh: 1.1002, Stop: 1.0996, Target: 1.1038, ATR: 0.0008,
		Reference: Reference{Touches: 3}}
}

func scoreOf(s *Strategy, ctx *analysiscontext.MarketContext, setup Setup, price float64) map[string]float64 {
	m := quality.New(QualityModel)
	s.score(m, setup, qualityContext{price: price, localTrend: "up", room: s.qualityRoom(ctx, setup), pipSize: s.cfg.PipSize})
	return m.Components()
}

func TestAnFXSetupIsScoredAgainstItsEffectiveStopFloor(t *testing.T) {
	s := testStrategy()
	c := scoreOf(s, ctxWith(), setupOf(), 1.1002)
	// 6 structural pips, a 12-pip floor, 36 pips of room: 3R against the stop that is run, not 6R against the structure.
	if math.Abs(c["structural_risk_pips"]-6) > 1e-6 || math.Abs(c["effective_risk_pips"]-12) > 1e-6 {
		t.Fatalf("risk %v / %v pips, want 6 / 12", c["structural_risk_pips"], c["effective_risk_pips"])
	}
	if math.Abs(c["effective_room_r"]-3) > 1e-6 || math.Abs(c["room_reward_risk"]-6) > 1e-6 {
		t.Errorf("effective %v R, structural %v R, want 3 and 6", c["effective_room_r"], c["room_reward_risk"])
	}
	if math.Abs(c["score_room"]-1) > 1e-9 {
		t.Errorf("score_room %v, want 1 (3R)", c["score_room"])
	}
	// Without the floor the same setup would score 6R: the denominator is what changed, not the stop.
	bare := testStrategy()
	bare.cfg.ExecutionStopMinPips = 0
	if got := scoreOf(bare, ctxWith(), setupOf(), 1.1002)["effective_room_r"]; math.Abs(got-6) > 1e-6 {
		t.Errorf("no floor: %v R, want 6", got)
	}
}

func TestABarrierNearerThanTheOpposingPivotLimitsTheRoom(t *testing.T) {
	s := testStrategy()
	wall := zone.Zone{ID: "w", Kind: zone.KindSupply, Low: 1.1020, High: 1.1022, State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
	c := scoreOf(s, ctxWith(wall), setupOf(), 1.1002)
	// 18 raw pips to the wall, less 0.5 x 8 pips of ATR = 14 pips; 14 / 12 = 1.17R.
	if c["room_limited_by_barrier"] != 1 || math.Abs(c["available_room_pips"]-14) > 1e-6 || math.Abs(c["effective_room_r"]-14.0/12) > 1e-6 {
		t.Errorf("limited %v available %v pips %v R", c["room_limited_by_barrier"], c["available_room_pips"], c["effective_room_r"])
	}
	inside := zone.Zone{ID: "w", Kind: zone.KindSupply, Low: 1.1001, High: 1.1010, State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
	if c := scoreOf(s, ctxWith(inside), setupOf(), 1.1002); c["score_room"] != 0 {
		t.Errorf("an entry inside an opposing wall scored room %v", c["score_room"])
	}
}

func TestDeeperBuyIsNotAChaseAndAHigherOneIs(t *testing.T) {
	s := testStrategy()
	if got := scoreOf(s, ctxWith(), setupOf(), 1.0998)["score_entry_location"]; got != 1 {
		t.Errorf("deeper in the band: %v, want 1", got)
	}
	if got := scoreOf(s, ctxWith(), setupOf(), 1.1010)["score_entry_location"]; got >= 1 {
		t.Errorf("above the edge: %v, want a penalty", got)
	}
}

func TestBreakRetestQualityIntegrity(t *testing.T) {
	s := testStrategy()
	var schema string
	for _, price := range []float64{1.0990, 1.1002, 1.1030, math.NaN()} {
		c := scoreOf(s, ctxWith(), setupOf(), price)
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
			t.Errorf("schema changed")
		}
		if c[quality.ModelVersionKey] != 2 {
			t.Errorf("model version %v", c[quality.ModelVersionKey])
		}
		m := quality.New(QualityModel)
		s.score(m, setupOf(), qualityContext{price: price, localTrend: "up", room: s.qualityRoom(ctxWith(), setupOf()), pipSize: s.cfg.PipSize})
		if math.Abs(m.Overall()-sum/float64(len(names))) > 1e-12 {
			t.Errorf("overall %v is not the mean %v", m.Overall(), sum/float64(len(names)))
		}
	}
}
