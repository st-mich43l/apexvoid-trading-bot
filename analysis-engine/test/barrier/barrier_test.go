package barrier_test

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
)

type goldenZone struct {
	Timeframe string  `json:"timeframe"`
	Side      string  `json:"side"`
	Low       float64 `json:"low"`
	High      float64 `json:"high"`
	ATR       float64 `json:"atr"`
	Score     float64 `json:"score"`
	Touches   int     `json:"touches"`
	Live      bool    `json:"live"`
}

type goldenCase struct {
	Name   string `json:"name"`
	Config struct {
		PipSize      float64 `json:"pip_size"`
		MaxWidthATR  float64 `json:"max_width_atr"`
		MaxWidthPips float64 `json:"max_width_pips"`
	} `json:"config"`
	Zones    []goldenZone      `json:"zones"`
	Expected []barrier.Barrier `json:"expected"`
}

// TestBuildMatchesPythonStructuralBarrierBook replays 300 randomized cases
// (touching bands, zero-width and oversized zones, cross-side conflicts, dead
// zones, out-of-scope timeframes, missing ATR) whose expected output was
// produced by the real Python zone_meets_execution_width and
// canonicalize_structural_barriers. Any drift from the Python implementation
// fails here.
func TestBuildMatchesPythonStructuralBarrierBook(t *testing.T) {
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
	if len(golden.Cases) == 0 {
		t.Fatal("empty golden")
	}
	nonEmpty := 0
	for _, c := range golden.Cases {
		zones := make([]barrier.Zone, len(c.Zones))
		for i, z := range c.Zones {
			zones[i] = barrier.Zone{
				Timeframe: z.Timeframe, Side: barrier.Side(z.Side), Low: z.Low, High: z.High,
				ATR: z.ATR, Score: z.Score, Touches: z.Touches, Live: z.Live,
			}
		}
		got := barrier.Build(zones, barrier.Config{
			PipSize: c.Config.PipSize, MaxWidthATR: c.Config.MaxWidthATR, MaxWidthPips: c.Config.MaxWidthPips,
		})
		if len(got) != len(c.Expected) {
			t.Fatalf("%s: got %d barriers, python produced %d\n got=%+v\nwant=%+v", c.Name, len(got), len(c.Expected), got, c.Expected)
		}
		if len(got) > 0 {
			nonEmpty++
		}
		for i := range got {
			g, w := got[i], c.Expected[i]
			if g.Side != w.Side || g.Tier != w.Tier || g.Touches != w.Touches ||
				math.Abs(g.Low-w.Low) > 1e-12 || math.Abs(g.High-w.High) > 1e-12 || math.Abs(g.Score-w.Score) > 1e-12 ||
				len(g.SourceTimeframes) != len(w.SourceTimeframes) {
				t.Fatalf("%s: barrier %d differs\n got=%+v\nwant=%+v", c.Name, i, g, w)
			}
			for k := range g.SourceTimeframes {
				if g.SourceTimeframes[k] != w.SourceTimeframes[k] {
					t.Fatalf("%s: barrier %d source timeframes differ: got %v want %v", c.Name, i, g.SourceTimeframes, w.SourceTimeframes)
				}
			}
		}
	}
	if nonEmpty < 100 {
		t.Fatalf("golden exercises too little: only %d non-empty cases", nonEmpty)
	}
}

func TestBuildDropsZeroWidthOversizedAndOutOfScopeZones(t *testing.T) {
	cfg := barrier.Config{PipSize: 0.0001, MaxWidthATR: 2, MaxWidthPips: 100}
	zones := []barrier.Zone{
		{Timeframe: "M5", Side: barrier.Sell, Low: 1.1350, High: 1.1350, ATR: 0.0002, Live: true},  // zero width
		{Timeframe: "M5", Side: barrier.Sell, Low: 1.1350, High: 1.1400, ATR: 0.0002, Live: true},  // > 2 ATR
		{Timeframe: "M1", Side: barrier.Sell, Low: 1.1350, High: 1.13502, ATR: 0.0001, Live: true}, // M1 out of scope
		{Timeframe: "M15", Side: barrier.Sell, Low: 1.1350, High: 1.13508, ATR: 0.0002, Live: false},
		{Timeframe: "H1", Side: barrier.Sell, Low: 1.1350, High: 1.13508, ATR: 0, Live: true}, // no ATR
		{Timeframe: "M15", Side: barrier.Sell, Low: 1.1350, High: 1.13508, ATR: 0.0002, Live: true},
	}
	got := barrier.Build(zones, cfg)
	if len(got) != 1 || got[0].SourceTimeframes[0] != "M15" {
		t.Fatalf("only the live in-scope in-width M15 zone should survive: %+v", got)
	}
}

func TestBuildMergesTouchingSameSideBandsAcrossTimeframes(t *testing.T) {
	cfg := barrier.Config{PipSize: 0.0001, MaxWidthATR: 6, MaxWidthPips: 100}
	got := barrier.Build([]barrier.Zone{
		{Timeframe: "H1", Side: barrier.Buy, Low: 1.1300, High: 1.1310, ATR: 0.0010, Score: 0.4, Touches: 2, Live: true},
		{Timeframe: "M5", Side: barrier.Buy, Low: 1.1310, High: 1.1315, ATR: 0.0003, Score: 0.9, Touches: 1, Live: true},
	}, cfg)
	if len(got) != 1 || got[0].Low != 1.1300 || got[0].High != 1.1315 || got[0].Score != 0.9 || got[0].Touches != 1 {
		t.Fatalf("touching same-side bands must merge to the union with max score and min touches: %+v", got)
	}
	if want := []string{"M5", "H1"}; len(got[0].SourceTimeframes) != 2 || got[0].SourceTimeframes[0] != want[0] || got[0].SourceTimeframes[1] != want[1] {
		t.Fatalf("source timeframes must be ordered by timeframe length: %v", got[0].SourceTimeframes)
	}
}
