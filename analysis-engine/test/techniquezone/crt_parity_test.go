package techniquezone_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// TestCRTInstancesMatchThePythonReferenceOnProductionBars compares Go's CRT
// discovery + validation with the Python collect_technique_instances on 360
// windows of the committed XAU capture (120 windows at each of three H1-range
// thresholds so the sweep, reclaim, half-of-range, reaction and validation
// branches are all exercised): 23 CRT instances, identical side, entry band,
// H1 anchor, clip flag and structural range.
func TestCRTInstancesMatchThePythonReferenceOnProductionBars(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "techniquezone-crt-parity.json"))
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Params struct {
			ReclaimBars        int     `json:"crt_reclaim_bars"`
			EntryMaxWidthPrice float64 `json:"crt_entry_max_width_price"`
			H1LookbackBars     int     `json:"crt_h1_lookback_bars"`
			ReactionLookback   int     `json:"reaction_lookback"`
			Pip                float64 `json:"pip"`
			Eps                float64 `json:"eps"`
			MaxZoneATR         float64 `json:"max_zone_atr"`
			Retest             int     `json:"retest"`
			Inval              float64 `json:"inval"`
			Sweep              int     `json:"sweep"`
			Episodes           int     `json:"episodes"`
		} `json:"params"`
		Cases []struct {
			MinATR    float64 `json:"min_atr"`
			Start     int     `json:"start"`
			End       int     `json:"end"`
			H1ATR     float64 `json:"h1_atr"`
			ExecATR   float64 `json:"exec_atr"`
			Instances []struct {
				Side   string
				Low    float64
				High   float64
				Origin int
				Clip   bool
				Slow   float64
				Shigh  float64
			} `json:"instances"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	capRaw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "replay-xau-production-capture-20260921.json"))
	if err != nil {
		t.Fatal(err)
	}
	var capture struct {
		Timeframes map[string][][]float64 `json:"timeframes"`
	}
	if err := json.Unmarshal(capRaw, &capture); err != nil {
		t.Fatal(err)
	}
	toBars := func(rows [][]float64) []market.Candle {
		out := make([]market.Candle, len(rows))
		for i, b := range rows {
			out[i] = market.Candle{Time: int64(b[0]), Open: b[1], High: b[2], Low: b[3], Close: b[4], Volume: b[5]}
		}
		return out
	}
	m5, h1 := toBars(capture.Timeframes["M5"]), toBars(capture.Timeframes["H1"])
	if len(golden.Cases) < 300 {
		t.Fatalf("golden unexpectedly small: %d", len(golden.Cases))
	}
	total := 0
	for ci, c := range golden.Cases {
		exec := m5[c.Start:c.End]
		closeAt := exec[len(exec)-1].Time + 300
		var closedH1 []market.Candle
		for _, b := range h1 {
			if b.Time+3600 <= closeAt {
				closedH1 = append(closedH1, b)
			}
		}
		h1ATR := techniquezone.ATRScalar(techniquezone.ATRSeries(closedH1, 14), 1)
		execATR := techniquezone.ATRScalar(techniquezone.ATRSeries(exec, 14), 1)
		if !near(h1ATR, c.H1ATR) || !near(execATR, c.ExecATR) {
			t.Fatalf("case %d ATR: go h1 %v exec %v, python h1 %v exec %v", ci, h1ATR, execATR, c.H1ATR, c.ExecATR)
		}
		got := techniquezone.CollectCRT(closedH1, exec, h1ATR, execATR, techniquezone.CRTSettings{
			MinATR: c.MinATR, ReclaimBars: golden.Params.ReclaimBars, EntryMaxWidthPrice: golden.Params.EntryMaxWidthPrice,
			H1LookbackBars: golden.Params.H1LookbackBars, ReactionLookbackBars: golden.Params.ReactionLookback,
		}, techniquezone.TechniqueSettings{
			PipSize: golden.Params.Pip, EpsilonATRFrac: golden.Params.Eps, MaxZoneATR: golden.Params.MaxZoneATR,
			RetestMaxTouches: golden.Params.Retest, InvalidationToleranceATR: golden.Params.Inval,
			SweepReclaimBars: golden.Params.Sweep, MaxBreakEpisodes: golden.Params.Episodes,
		})
		if len(got) != len(c.Instances) {
			t.Fatalf("case %d (min_atr %v): go %d CRT instances, python %d\n go %+v\n py %+v", ci, c.MinATR, len(got), len(c.Instances), got, c.Instances)
		}
		for i, in := range got {
			w := c.Instances[i]
			if in.Side != w.Side || !near(in.Low, w.Low) || !near(in.High, w.High) || in.OriginIndex != w.Origin ||
				in.EntryClipped != w.Clip || !near(in.StructuralLow, w.Slow) || !near(in.StructuralHigh, w.Shigh) {
				t.Fatalf("case %d instance[%d]:\n  go     %+v\n  python %+v", ci, i, in, w)
			}
		}
		total += len(got)
	}
	if total < 15 {
		t.Fatalf("golden no longer exercises CRT: %d instances", total)
	}
	t.Logf("%d windows: %d CRT instances identical to Python", len(golden.Cases), total)
}
