// Package pdsymmetry_test proves that supply and demand read the dealing range the same way: a market and its price mirror give mirrored answers.
package pdsymmetry_test

import (
	"math"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/demand"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
)

type tick struct{ o, h, l, c float64 }

const (
	m5Seconds = 300
	startTime = 1_792_999_800 // a multiple of 900
	scale     = 0.55
	m5ATR     = 0.10 * scale
	m15ATR    = 0.30 * scale
)

func px(x float64) float64 { return 210.00 + (x-210.00)*scale }

// supplyMarket: 20 M15 candles of a zigzag (swing highs and lows 0.4 apart, no leg strong enough to be a departure), then the supply scenario of
// test/supplyv7: a base at 209.98-210.15, a strong departure down to 209.08, the rebound and the wick rejection at bar 79.
func supplyMarket() []tick {
	var path []tick
	zig := []float64{0, .2, .4, .2}
	prev := 0.0
	for k := 0; k < 20; k++ {
		v := zig[k%4]
		for i := 1; i <= 3; i++ {
			a, b := 210+prev+(v-prev)*float64(i-1)/3, 210+prev+(v-prev)*float64(i)/3
			path = append(path, tick{o: a, h: math.Max(a, b) + .01, l: math.Min(a, b) - .01, c: b})
		}
		prev = v
	}
	return append(path,
		tick{210.00, 210.03, 209.98, 210.02}, tick{210.02, 210.10, 210.01, 210.08}, tick{210.08, 210.09, 210.03, 210.05},
		tick{210.05, 210.12, 210.04, 210.10}, tick{210.10, 210.11, 210.00, 210.01}, tick{210.01, 210.03, 209.99, 210.02},
		tick{210.02, 210.06, 210.00, 210.05}, tick{210.05, 210.15, 210.04, 210.12}, tick{210.12, 210.13, 210.06, 210.08},
		tick{210.08, 210.09, 209.80, 209.82}, tick{209.82, 209.84, 209.64, 209.66}, tick{209.66, 209.68, 209.58, 209.60},
		tick{209.60, 209.62, 209.40, 209.42}, tick{209.42, 209.44, 209.24, 209.26}, tick{209.26, 209.28, 209.08, 209.10},
		tick{209.10, 209.40, 209.08, 209.38}, tick{209.38, 209.66, 209.36, 209.64}, tick{209.64, 209.90, 209.62, 209.88},
		tick{209.88, 210.00, 209.86, 209.96}, tick{209.97, 210.14, 209.90, 209.92},
	)
}

func mirror(path []tick) []tick {
	out := make([]tick, len(path))
	for i, t := range path {
		out[i] = tick{o: 420 - t.o, h: 420 - t.l, l: 420 - t.h, c: 420 - t.c}
	}
	return out
}

func candles(path []tick) []market.Candle {
	out := make([]market.Candle, len(path))
	for i, p := range path {
		out[i] = market.Candle{Time: startTime + int64(i)*m5Seconds, Open: px(p.o), High: px(p.h), Low: px(p.l), Close: px(p.c), Volume: 1}
	}
	return out
}

func aggregate(m5 []market.Candle) []market.Candle {
	var out []market.Candle
	for i := 0; i+3 <= len(m5); i += 3 {
		c := market.Candle{Time: m5[i].Time, Open: m5[i].Open, High: m5[i].High, Low: m5[i].Low, Close: m5[i+2].Close, Volume: 3}
		for _, b := range m5[i : i+3] {
			c.High, c.Low = math.Max(c.High, b.High), math.Min(c.Low, b.Low)
		}
		out = append(out, c)
	}
	return out
}

func frame(bars []market.Candle, atr float64) *analysiscontext.LegacyFrame {
	series := make([]float64, len(bars))
	for i := range series {
		series[i] = atr
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: series, DetectorATR: atr}
}

func context(path []tick, structure, bias string) *analysiscontext.MarketContext {
	m5 := candles(path)
	return &analysiscontext.MarketContext{
		Symbol: "GBPJPY",
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5:  {Timeframe: market.M5, Legacy: frame(m5, m5ATR)},
			market.M15: {Timeframe: market.M15, Legacy: frame(aggregate(m5), m15ATR)},
		},
		Legacy: &analysiscontext.LegacyRead{LocalStructure: structure, HTFBias: bias},
	}
}

func build(t *testing.T, id strategy.StrategyID) strategy.Strategy {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "GBPJPY"); err != nil {
		t.Fatal(err)
	}
	for _, c := range settings.Strategies {
		if c.ID != id {
			continue
		}
		params := map[string]any{}
		for k, v := range c.Parameters {
			params[k] = v
		}
		params["structure_timeframes"] = []any{"M15"}
		params["confluence_floor"] = 1
		c.Parameters = params
		if id == "supply" {
			s, err := supply.New(c)
			if err != nil {
				t.Fatal(err)
			}
			return s
		}
		s, err := demand.New(c)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	t.Fatalf("no %s in the configuration", id)
	return nil
}

func evidence(c opportunity.Candidate) map[string]bool {
	out := map[string]bool{}
	for _, e := range c.Evidence {
		out[e.Code] = true
	}
	return out
}

// A supply zone in the premium half of the range and its price mirror, a demand zone in the discount half, earn the same points, report mirrored positions
// and publish mirrored candidates.
func TestSupplyAndDemandReadThePremiumDiscountOfAMirroredMarketAlike(t *testing.T) {
	sell := build(t, "supply").Evaluate(context(supplyMarket(), "down", "down"))
	buy := build(t, "demand").Evaluate(context(mirror(supplyMarket()), "up", "up"))
	if len(sell) != 1 || len(buy) != 1 {
		t.Fatalf("published %d SELL / %d BUY on a market and its mirror", len(sell), len(buy))
	}
	s, b := sell[0], buy[0]
	if !evidence(s)["m5_supply_zone_premium"] || !evidence(b)["m5_demand_zone_discount"] {
		t.Fatalf("premium / discount evidence: %v / %v", evidence(s), evidence(b))
	}
	ps, pb := s.Quality.Components["pd_position"], b.Quality.Components["pd_position"]
	if ps <= 0.5 || math.Abs(ps+pb-1) > 1e-9 {
		t.Fatalf("pd_position %.6f (supply) and %.6f (demand): the supply zone's proximal edge is above the equilibrium and the two sum to 1", ps, pb)
	}
	if s.DetectorConfluence.SelectedStars != b.DetectorConfluence.SelectedStars {
		t.Fatalf("stars %d vs %d on a market and its mirror", s.DetectorConfluence.SelectedStars, b.DetectorConfluence.SelectedStars)
	}
	if got := math.Abs((s.Entry.Low + b.Entry.High) - 2*px(210)); got > 1e-9 {
		t.Fatalf("the entry bands are not mirrors: %v %v", s.Entry, b.Entry)
	}
}

// The two awards are score inputs, never gates: with the points switched off both strategies still publish the same setups.
func TestThePremiumDiscountPointsAreNeverAGate(t *testing.T) {
	for _, id := range []strategy.StrategyID{"supply", "demand"} {
		doc, _ := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
		settings, _ := engine.LoadSettings(doc, market.M5, false)
		if err := engine.ApplyInstrument(&settings, doc, "GBPJPY"); err != nil {
			t.Fatal(err)
		}
		for _, c := range settings.Strategies {
			if c.ID != id {
				continue
			}
			params := map[string]any{}
			for k, v := range c.Parameters {
				params[k] = v
			}
			params["structure_timeframes"] = []any{"M15"}
			params["confluence_floor"] = 1
			params["pd_position_score"] = 0.0
			c.Parameters = params
			path, structure := supplyMarket(), "down"
			var s strategy.Strategy
			if id == "supply" {
				s, _ = supply.New(c)
			} else {
				s, _ = demand.New(c)
				path, structure = mirror(path), "up"
			}
			got := s.Evaluate(context(path, structure, structure))
			if len(got) != 1 || evidence(got[0])["m5_supply_zone_premium"] || evidence(got[0])["m5_demand_zone_discount"] {
				t.Fatalf("%s with pd_position_score 0: published %d, evidence %v", id, len(got), got)
			}
		}
	}
}
