package scalpparity_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/scalpbreakoutretest"
)

type e2eOpportunity struct {
	Direction string  `json:"direction"`
	ZoneLow   float64 `json:"zone_low"`
	ZoneHigh  float64 `json:"zone_high"`
	Invalid   float64 `json:"invalidation"`
	Target    float64 `json:"target"`
	StopPips  float64 `json:"stop_pips"`
	Source    string  `json:"source"`
	Quality   float64 `json:"quality"`
}

type e2eCase struct {
	Seed          int              `json:"seed"`
	Side          string           `json:"side"`
	Confirm       bool             `json:"confirm"`
	M5            [][4]float64     `json:"m5"`
	M1            [][4]float64     `json:"m1"`
	Opportunities []e2eOpportunity `json:"opportunities"`
}

func candles(rows [][4]float64, start, step int64) []market.Candle {
	out := make([]market.Candle, len(rows))
	for i, r := range rows {
		out[i] = market.Candle{Time: start + step*int64(i), Open: r[0], High: r[1], Low: r[2], Close: r[3], Volume: 1}
	}
	return out
}

// The end-to-end cases hand the Go strategy the same closed M5 setup window
// and M1 confirmation frame Python's discover_breakout_retest was given and
// require the same opportunity — or none when no closed M1 bar confirms the
// level.
func TestBreakoutRetestScalpEndToEndMatchesPython(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "internal", "strategy", "scalpbreakoutretest", "testdata", "source-cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		M5End int64     `json:"m5_end"`
		E2E   []e2eCase `json:"e2e"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	cfgDoc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(cfgDoc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, cfgDoc, "XAU"); err != nil {
		t.Fatal(err)
	}
	var strategyConfig strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == scalpbreakoutretest.ID {
			strategyConfig = c
		}
	}
	instance, err := scalpbreakoutretest.New(strategyConfig)
	if err != nil {
		t.Fatal(err)
	}

	opportunities, silent := 0, 0
	for _, tc := range doc.E2E {
		m5 := candles(tc.M5, doc.M5End-300*int64(len(tc.M5)), 300)
		m1 := candles(tc.M1, doc.M5End-60-60*int64(len(tc.M1)-1), 60)
		ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5}, market.M1: {Timeframe: market.M1, Candles: m1},
		}}
		got := instance.Evaluate(ctx)
		// Python evaluates both directions; compare the case's direction.
		var mine []float64
		for _, c := range got {
			if string(c.Direction) != tc.Side {
				continue
			}
			mine = append(mine, c.Entry.Low)
		}
		var want []e2eOpportunity
		for _, o := range tc.Opportunities {
			if o.Direction == tc.Side {
				want = append(want, o)
			}
		}
		if len(mine) != len(want) {
			t.Errorf("seed %d %s confirm=%v: Go %d opportunities, Python %d", tc.Seed, tc.Side, tc.Confirm, len(mine), len(want))
			continue
		}
		if len(want) == 0 {
			silent++
			continue
		}
		opportunities++
		var c = got[0]
		for _, g := range got {
			if string(g.Direction) == tc.Side {
				c = g
			}
		}
		w := want[0]
		tick := 0.01
		if math.Abs(c.Entry.Low-w.ZoneLow) > tick || math.Abs(c.Entry.High-w.ZoneHigh) > tick || math.Abs(float64(c.Invalidation.Price)-w.Invalid) > 1e-9 ||
			math.Abs(float64(c.Targets[0].Price.Price)-w.Target) > 1e-9 || math.Abs(c.Quality.Overall*100-w.Quality) > 1e-6 {
			t.Errorf("seed %d %s: Go zone %.4f-%.4f stop %.4f target %.4f quality %.4f; Python zone %.4f-%.4f stop %.4f target %.4f quality %.4f (%s)",
				tc.Seed, tc.Side, c.Entry.Low, c.Entry.High, float64(c.Invalidation.Price), float64(c.Targets[0].Price.Price), c.Quality.Overall*100,
				w.ZoneLow, w.ZoneHigh, w.Invalid, w.Target, w.Quality, w.Source)
		}
	}
	if opportunities < 2 || silent < 4 {
		t.Fatalf("the cases no longer exercise both the opportunity and the missing-confirmation paths: %d/%d", opportunities, silent)
	}
}
