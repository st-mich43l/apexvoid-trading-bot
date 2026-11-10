package shockretest

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

// A BUY band [4000.0, 4000.5] (proximal edge 4000.5), structural stop 6 pips below it, the target 75 pips above.
func setupOf(kind string) Setup {
	return Setup{
		Direction: market.Buy, LegATR: 5, Retracement: 0.55, RejectionWick: 0.4, EntryLow: 4000, EntryHigh: 4000.5,
		Stop: 3999.9, Target: 4008, TargetKind: kind, M5ATR: 3,
	}
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

// The shock extreme is observed structure; the 1.272 extension is a projection and is not room until a barrier is actually ahead.
func TestAProjectedExtensionIsNotStructuralRoom(t *testing.T) {
	s := testStrategy()
	extreme := scoreOf(s, ctxWith(), setupOf(TargetShockExtreme), 4000.5)
	projected := scoreOf(s, ctxWith(), setupOf(TargetShockExtension1272), 4000.5)
	if extreme["room_structural"] != 1 || extreme["score_room"] <= 0 {
		t.Errorf("the shock extreme: structural %v score %v, want observed structure", extreme["room_structural"], extreme["score_room"])
	}
	if projected["room_structural"] != 0 || projected["score_room"] != 0 {
		t.Errorf("the 1.272 extension: structural %v score %v, want a hypothesis with no structural room", projected["room_structural"], projected["score_room"])
	}
	// The geometry is the same price: only the provenance differs.
	if extreme["effective_room_r"] != projected["effective_room_r"] {
		t.Errorf("effective room R %v vs %v: the provenance must not change the geometry", extreme["effective_room_r"], projected["effective_room_r"])
	}
	// An actual barrier beyond the extension gives it something to stand on.
	wall := zone.Zone{ID: "w", Kind: zone.KindSupply, Low: 4020, High: 4021, State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
	with := scoreOf(s, ctxWith(wall), setupOf(TargetShockExtension1272), 4000.5)
	if with["room_structural"] != 1 || with["score_room"] <= 0 {
		t.Errorf("a barrier ahead: %+v", with)
	}
}

func TestAnOpposingBarrierLimitsTheShockRoom(t *testing.T) {
	s := testStrategy()
	wall := zone.Zone{ID: "w", Kind: zone.KindSupply, Low: 4003, High: 4004, State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
	c := scoreOf(s, ctxWith(wall), setupOf(TargetShockExtreme), 4000.5)
	if c["room_limited_by_barrier"] != 1 || c["available_room_pips"] >= c["target_room_pips"] {
		t.Errorf("barrier %v available %v target %v", c["room_limited_by_barrier"], c["available_room_pips"], c["target_room_pips"])
	}
	// 25 effective pips against a 6-pip structural stop: the floor is the denominator.
	if math.Abs(c["structural_risk_pips"]-6) > 1e-6 || math.Abs(c["effective_risk_pips"]-25) > 1e-6 {
		t.Errorf("risk %v / %v pips, want 6 / 25", c["structural_risk_pips"], c["effective_risk_pips"])
	}
}

func TestShockEntryLocationIsDirectionAware(t *testing.T) {
	s := testStrategy()
	buy := setupOf(TargetShockExtreme)
	if got := scoreOf(s, ctxWith(), buy, 3999.5)["score_entry_location"]; got != 1 { // deeper than the 4000.5 edge: not a chase
		t.Errorf("BUY deeper in the band: %v, want 1", got)
	}
	if got := scoreOf(s, ctxWith(), buy, 4003)["score_entry_location"]; got >= 1 { // 2.5 away = 0.83 ATR above the edge
		t.Errorf("BUY price above the edge: %v, want a penalty", got)
	}
	sell := Setup{Direction: market.Sell, LegATR: 5, Retracement: 0.55, RejectionWick: 0.4, EntryLow: 4000, EntryHigh: 4000.5, Stop: 4000.6, Target: 3992, TargetKind: TargetShockExtreme, M5ATR: 3}
	if got := scoreOf(s, ctxWith(), sell, 4001)["score_entry_location"]; got != 1 { // higher than the 4000 edge: not a chase
		t.Errorf("SELL higher in the band: %v, want 1", got)
	}
	if got := scoreOf(s, ctxWith(), sell, 3997.5)["score_entry_location"]; got >= 1 {
		t.Errorf("SELL price below the edge: %v, want a penalty", got)
	}
}

func TestShockQualityIntegrity(t *testing.T) {
	s := testStrategy()
	var schema string
	for _, kind := range []string{TargetShockExtreme, TargetShockExtension1272} {
		for _, price := range []float64{3999, 4000.5, 4004, math.NaN()} {
			c := scoreOf(s, ctxWith(), setupOf(kind), price)
			sum, n, names := 0.0, 0, []string{}
			_ = sum
			for name, v := range c {
				if math.IsNaN(v) || math.IsInf(v, 0) {
					t.Errorf("%s: not finite", name)
				}
				if strings.HasPrefix(name, quality.Prefix) {
					if v < 0 || v > 1 {
						t.Errorf("%s = %v outside [0, 1]", name, v)
					}
					sum += v
					n++
					names = append(names, name)
				}
			}
			if n != 6 {
				t.Errorf("%d dimensions, want a fixed six", n)
			}
			if c[quality.ModelVersionKey] != 2 {
				t.Errorf("model version %v, want 2", c[quality.ModelVersionKey])
			}
			sort.Strings(names)
			joined := strings.Join(names, ",")
			if schema == "" {
				schema = joined
			} else if joined != schema {
				t.Errorf("schema changed: %s vs %s", joined, schema)
			}
		}
	}
}

func TestShockOverallIsTheMeanOfItsScores(t *testing.T) {
	s := testStrategy()
	m := quality.New(QualityModel)
	setup := setupOf(TargetShockExtreme)
	s.score(m, setup, qualityContext{price: 4000.5, localTrend: "up", room: s.qualityRoom(ctxWith(), setup, setup.EntryHigh), pipSize: 0.1})
	sum, n := 0.0, 0
	for name, v := range m.Components() {
		if strings.HasPrefix(name, quality.Prefix) {
			sum += v
			n++
		}
	}
	if math.Abs(m.Overall()-sum/float64(n)) > 1e-12 {
		t.Errorf("overall %v, mean %v", m.Overall(), sum/float64(n))
	}
}
