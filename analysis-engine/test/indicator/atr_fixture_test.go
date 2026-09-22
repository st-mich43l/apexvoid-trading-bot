package indicator_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Golden-master parity against real Python output — rebuild-analysis-
// engine.md §15/§31: "If Go produces a different result than Python, do
// NOT simply adjust the fixture." Regenerate the fixture (via
// scripts/export_atr_fixtures.py) only when the Python formula itself
// changes, never to make a Go bug disappear.

type fixtureCandle struct {
	Open   float64 `json:"open"`
	High   float64 `json:"high"`
	Low    float64 `json:"low"`
	Close  float64 `json:"close"`
	Volume float64 `json:"volume"`
}

type fixtureCase struct {
	Name      string          `json:"name"`
	Length    int             `json:"length"`
	Candles   []fixtureCandle `json:"candles"`
	TrueRange []*float64      `json:"true_range"`
	SimpleATR []*float64      `json:"simple_atr"`
	WilderATR []*float64      `json:"wilder_atr"`
}

type fixtureFile struct {
	Cases []fixtureCase `json:"cases"`
}

func loadFixtures(t *testing.T) fixtureFile {
	t.Helper()
	// test/indicator/ and internal/indicator/ sit at the same depth under
	// analysis-engine/ (both two path segments below it), so this
	// relative path is unchanged from when this file lived in
	// internal/indicator/.
	path := filepath.Join("..", "..", "testdata", "atr_fixtures.json")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading fixture file: %v", err)
	}
	var f fixtureFile
	if err := json.Unmarshal(data, &f); err != nil {
		t.Fatalf("parsing fixture file: %v", err)
	}
	if len(f.Cases) == 0 {
		t.Fatalf("fixture file has no cases")
	}
	return f
}

func toCandles(rows []fixtureCandle) []market.Candle {
	out := make([]market.Candle, len(rows))
	for i, r := range rows {
		out[i] = market.Candle{
			Time:   int64(i),
			Open:   r.Open,
			High:   r.High,
			Low:    r.Low,
			Close:  r.Close,
			Volume: r.Volume,
		}
	}
	return out
}

const epsilon = 1e-9

func assertSeriesEqual(t *testing.T, label string, got []float64, want []*float64) {
	t.Helper()
	if len(want) == 0 {
		if got != nil {
			t.Errorf("%s: expected nil/empty (Python returned None), got %v", label, got)
		}
		return
	}
	if len(got) != len(want) {
		t.Fatalf("%s: length mismatch: got %d want %d", label, len(got), len(want))
	}
	for i := range want {
		if want[i] == nil {
			if !math.IsNaN(got[i]) {
				t.Errorf("%s[%d]: want NaN, got %v", label, i, got[i])
			}
			continue
		}
		diff := got[i] - *want[i]
		if diff < 0 {
			diff = -diff
		}
		if diff > epsilon {
			t.Errorf("%s[%d]: got %v want %v (diff %v)", label, i, got[i], *want[i], diff)
		}
	}
}

func TestTrueRangeMatchesPython(t *testing.T) {
	f := loadFixtures(t)
	for _, c := range f.Cases {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			candles := toCandles(c.Candles)
			got := indicator.TrueRange(candles)
			assertSeriesEqual(t, "true_range", got, c.TrueRange)
		})
	}
}

func TestSimpleATRMatchesPython(t *testing.T) {
	f := loadFixtures(t)
	for _, c := range f.Cases {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			candles := toCandles(c.Candles)
			got := indicator.SimpleATR(candles, c.Length)
			assertSeriesEqual(t, "simple_atr", got, c.SimpleATR)
		})
	}
}

func TestWilderATRMatchesPython(t *testing.T) {
	f := loadFixtures(t)
	for _, c := range f.Cases {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			candles := toCandles(c.Candles)
			got, ok := indicator.WilderATR(candles, c.Length)
			if c.WilderATR == nil {
				if ok {
					t.Fatalf("wilder_atr: expected Python None (insufficient warmup), Go returned a series")
				}
				return
			}
			if !ok {
				t.Fatalf("wilder_atr: Python returned a series, Go returned (nil, false)")
			}
			assertSeriesEqual(t, "wilder_atr", got, c.WilderATR)
		})
	}
}
