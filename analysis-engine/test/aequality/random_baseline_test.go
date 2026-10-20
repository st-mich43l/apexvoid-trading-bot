package aequality_test

import (
	"encoding/json"
	"hash/fnv"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"testing"
)

// TestMatchedRandomBaseline gives every replayed opportunity its matched random counterparts: the same instrument and direction, the
// same technical risk (so the same executable risk after the envelope floor), the same cost, ladder and 24-hour horizon, entered at the
// close of a randomly chosen bar of the same capture instead of at the strategy's signal. A signal has to beat these, not zero.
//
//	AEQ_RANDOM_OUT=/tmp/aeq go test ./test/aequality -run TestMatchedRandomBaseline
//
// Hypothetical outcomes only: closed candles, constant assumed spread, one leg at the proximal edge.
func TestMatchedRandomBaseline(t *testing.T) {
	dir := os.Getenv("AEQ_RANDOM_OUT")
	if dir == "" {
		t.Skip("AEQ_RANDOM_OUT not set")
	}
	const draws = 40
	records := replayAll(t)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	out, err := os.Create(filepath.Join(dir, "random.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer out.Close()
	enc := json.NewEncoder(out)
	type row struct {
		ID      string  `json:"id"`
		Filled  int     `json:"filled"`
		MeanR   float64 `json:"mean_r"`
		Win1R   float64 `json:"win_1r"`
		Stops   float64 `json:"stop_share"`
		Targets int     `json:"targets"`
	}
	for _, r := range records {
		series, ok := recBars[r.ID]
		if !ok || len(series.bars) < 600 || r.TechRiskPips <= 0 {
			continue
		}
		pip := pipSize[r.Symbol]
		h := fnv.New64a()
		_, _ = h.Write([]byte(r.ID))
		rng := rand.New(rand.NewSource(int64(h.Sum64())))
		horizon := int(24 * 3600 / series.seconds)
		lo, hi := 200, len(series.bars)-horizon-2
		if hi <= lo {
			continue
		}
		var sum float64
		filled, wins, stops := 0, 0, 0
		for d := 0; d < draws; d++ {
			i := lo + rng.Intn(hi-lo)
			px := series.bars[i].Close
			synth := *r
			synth.Sim = nil
			synth.EvalTime = series.bars[i].Time + series.seconds
			synth.EntryLow, synth.EntryHigh = px, px
			if r.Direction == "BUY" {
				synth.Invalidation = px - r.TechRiskPips*pip
			} else {
				synth.Invalidation = px + r.TechRiskPips*pip
			}
			synth.ExpiresAt = 0
			o := simulate(&synth, series.bars, series.index, series.seconds, "ladder", spread[r.Symbol])
			switch o.Result {
			case "target", "stop", "stop_be", "timeout":
				filled++
				sum += o.R
				if o.R >= 1 {
					wins++
				}
				if o.Result == "stop" {
					stops++
				}
			}
		}
		if filled == 0 {
			continue
		}
		if err := enc.Encode(row{ID: r.ID, Filled: filled, MeanR: math.Round(sum/float64(filled)*1e5) / 1e5, Win1R: float64(wins) / float64(filled),
			Stops: float64(stops) / float64(filled), Targets: draws}); err != nil {
			t.Fatal(err)
		}
	}
}
