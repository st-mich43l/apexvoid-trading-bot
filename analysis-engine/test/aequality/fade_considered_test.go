package aequality_test

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fadescalp"
)

// fadeWatch asks Fade Scalp for its account of every confirmed sweep reaction (published or refused, with the gate that refused it) on
// each evaluated bar and keeps one record per thesis (direction, pool, sweep), so a calibration can measure what each gate refused.
// The record's geometry is what the setup would have had; the simulation is the same candle-causal one every opportunity gets.
type fadeWatch struct {
	strategy   *fadescalp.Strategy
	floor, cap float64
	symbol     string
	seen       map[string]*Record
	published  map[string]bool
	order      []string
}

func newFadeWatch(t *testing.T, doc *config.Document, c capture) *fadeWatch {
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, c.symbol); err != nil {
		t.Fatal(err)
	}
	w := &fadeWatch{symbol: c.symbol, seen: map[string]*Record{}, published: map[string]bool{}}
	for _, cfg := range settings.Strategies {
		if cfg.ID != fadescalp.ID {
			continue
		}
		built, err := fadescalp.New(strategy.Config{ID: cfg.ID, Version: cfg.Version, Parameters: cfg.Parameters})
		if err != nil {
			t.Fatal(err)
		}
		w.strategy = built.(*fadescalp.Strategy)
	}
	// Fade Scalp is a range-room scalp: the instrument's scalp band when it declares one, else its structural envelope.
	if settings.InstrumentScalpStopConfigured {
		w.floor, w.cap = settings.InstrumentScalpStopMinPips, settings.InstrumentScalpStopMaxPips
	} else {
		w.floor, w.cap = settings.InstrumentStopMinPips, settings.InstrumentStopMaxPips
	}
	return w
}

func (w *fadeWatch) observe(e engine.Evaluation, r *run) {
	if w.strategy == nil || e.Timeframe != market.M5 || e.Context == nil {
		return
	}
	analysis, _ := w.strategy.Analyze(e.Context)
	for _, c := range analysis.Considered {
		key := fmt.Sprintf("%s|%.8f|%d", c.Direction, c.PoolLevel, c.SweepTime)
		if w.seen[key] != nil {
			if c.Reason == "" {
				w.published[key] = true
				w.seen[key].Shadow = ""
			}
			continue
		}
		rec := &Record{
			Symbol: w.symbol, Strategy: "fade_scalp", Version: "v3", ID: "fade:" + key, Direction: string(c.Direction), BarTF: "M5", ObservedTF: "M5",
			EvalTime: e.BarTime + 300, EntryLow: c.EntryLow, EntryHigh: c.EntryHigh, Invalidation: c.Invalidation, Shadow: c.Reason, Facts: c.Facts, Pattern: c.Pattern,
			Stars: c.Stars, HasReaction: true, RoomCover: c.RewardRisk, Repeats: 1, Sim: map[string]outcome{}, ATR: e.Context.Volatility.ATR,
			FloorPips: w.floor, CapPips: w.cap,
		}
		pip := pipSize[w.symbol]
		proximal := rec.EntryHigh
		if rec.Direction == "SELL" {
			proximal = rec.EntryLow
		}
		rec.TechRiskPips = absF(proximal-rec.Invalidation) / pip
		rec.ExecRiskPips = maxF(rec.TechRiskPips, rec.FloorPips)
		rec.Executable = rec.CapPips <= 0 || rec.TechRiskPips <= rec.CapPips+1e-9
		if c.Reason == "" {
			w.published[key] = true
		}
		w.seen[key] = rec
		w.order = append(w.order, key)
		r.shadows = append(r.shadows, rec)
	}
}

func absF(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

func maxF(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

// TestFadeConsiderations writes every confirmed Fade Scalp sweep reaction with the gate that refused it and its simulated outcome
// (AEQ_FADE_OUT=dir), plus the matched random counterparts. It decides nothing.
func TestFadeConsiderations(t *testing.T) {
	dir := os.Getenv("AEQ_FADE_OUT")
	if dir == "" {
		t.Skip("AEQ_FADE_OUT not set")
	}
	replayAll(t)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	out, err := os.Create(filepath.Join(dir, "fade.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer out.Close()
	enc := json.NewEncoder(out)
	sort.SliceStable(allShadows, func(i, j int) bool { return allShadows[i].EvalTime < allShadows[j].EvalTime })
	counts := map[string]int{}
	for _, r := range allShadows {
		counts[r.Shadow]++
		if err := enc.Encode(r); err != nil {
			t.Fatal(err)
		}
	}
	t.Logf("fade considerations by gate (\"\" is published): %v", counts)
	writeRandom(t, allShadows, filepath.Join(dir, "fade_random.jsonl"))
}
