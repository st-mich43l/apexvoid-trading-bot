package momentum_test

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
)

type goldenCase struct {
	Name   string `json:"name"`
	Config struct {
		Lookback int     `json:"lookback"`
		Bull     float64 `json:"bull"`
		Bear     float64 `json:"bear"`
	} `json:"config"`
	// [time, open, high, low, close, volume]
	Candles  [][]float64 `json:"candles"`
	Expected struct {
		State        string  `json:"state"`
		Velocity     float64 `json:"velocity"`
		Acceleration float64 `json:"acceleration"`
		Lookback     int     `json:"lookback"`
	} `json:"expected"`
}

// TestClassifyMatchesPythonMomentumState replays random OHLC series whose
// expected state, velocity and acceleration were produced by the real Python
// app.analysis.momentum.momentum_state (default 14-bar simple ATR).
func TestClassifyMatchesPythonMomentumState(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_parity_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []goldenCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	seen := map[string]int{}
	accel := 0
	for _, c := range golden.Cases {
		candles := make([]market.Candle, len(c.Candles))
		for i, r := range c.Candles {
			candles[i] = market.Candle{Time: int64(r[0]), Open: r[1], High: r[2], Low: r[3], Close: r[4], Volume: r[5]}
		}
		got := momentum.Classify(candles, nil, momentum.Config{Lookback: c.Config.Lookback, BullThreshold: c.Config.Bull, BearThreshold: c.Config.Bear})
		if string(got.State) != c.Expected.State || got.Lookback != c.Expected.Lookback ||
			math.Abs(got.Velocity-c.Expected.Velocity) > 1e-9 || math.Abs(got.Acceleration-c.Expected.Acceleration) > 1e-9 {
			t.Fatalf("%s (n=%d): got %+v, python %+v", c.Name, len(candles), got, c.Expected)
		}
		seen[c.Expected.State]++
		if c.Expected.Acceleration != 0 {
			accel++
		}
	}
	if seen["bull"] < 10 || seen["bear"] < 10 || seen["neutral"] < 10 || accel < 10 {
		t.Fatalf("golden exercises too little: states=%v nonzero_accel=%d", seen, accel)
	}
}

func TestClassifyNeutralWithoutEnoughHistory(t *testing.T) {
	one := []market.Candle{{Time: 0, Open: 1, High: 2, Low: 0.5, Close: 1.5}}
	if got := momentum.Classify(one, nil, momentum.Config{Lookback: 8, BullThreshold: 0.15, BearThreshold: -0.15}); got.State != momentum.Neutral || got.Velocity != 0 {
		t.Fatalf("a single candle cannot carry a lookback: %+v", got)
	}
}

// A velocity exactly on a threshold is inclusive on both sides, like Python's
// `v >= bull_threshold` / `v <= bear_threshold`. Random goldens never land on
// the boundary, so pin it by using the series' own velocity as the threshold.
func TestClassifyThresholdsAreInclusive(t *testing.T) {
	rising := make([]market.Candle, 30)
	falling := make([]market.Candle, 30)
	for i := range rising {
		p := 100 + float64(i)*0.7
		rising[i] = market.Candle{Time: int64(i) * 3600, Open: p - 0.2, High: p + 0.5, Low: p - 0.5, Close: p}
		q := 100 - float64(i)*0.7
		falling[i] = market.Candle{Time: int64(i) * 3600, Open: q + 0.2, High: q + 0.5, Low: q - 0.5, Close: q}
	}
	never := momentum.Config{Lookback: 8, BullThreshold: math.Inf(1), BearThreshold: math.Inf(-1)}
	up := momentum.Classify(rising, nil, never).Velocity
	down := momentum.Classify(falling, nil, never).Velocity
	if up <= 0 || down >= 0 {
		t.Fatalf("fixtures must have a signed velocity: up=%v down=%v", up, down)
	}
	if got := momentum.Classify(rising, nil, momentum.Config{Lookback: 8, BullThreshold: up, BearThreshold: -up}); got.State != momentum.Bull {
		t.Fatalf("v == bull threshold must classify bull, got %+v", got)
	}
	if got := momentum.Classify(falling, nil, momentum.Config{Lookback: 8, BullThreshold: -down, BearThreshold: down}); got.State != momentum.Bear {
		t.Fatalf("v == bear threshold must classify bear, got %+v", got)
	}
}
