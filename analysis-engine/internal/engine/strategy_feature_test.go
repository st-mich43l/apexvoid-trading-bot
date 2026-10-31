package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

func features(recorder *telemetry.Recorder) map[string]int64 {
	_, counts := recorder.Snapshot()
	out := map[string]int64{}
	for _, c := range counts {
		if c.Counter == string(telemetry.CounterStrategyFeature) {
			out[c.Symbol+"/"+c.Strategy+"/"+c.Timeframe+"/"+c.Reason+"="+c.Value] += c.Count
		}
	}
	return out
}

// A created opportunity that carries the v9 measures is tallied once per feature and value; one that carries none is not tallied at all.
func TestCreatedOpportunitiesAreTalliedByTheirMeasuredFeatures(t *testing.T) {
	recorder := telemetry.NewRecorder()
	w := &SymbolWorker{telemetry: recorder}
	candidate := func(strategy opportunity.StrategyID, components map[string]float64) opportunity.Candidate {
		return opportunity.Candidate{Strategy: strategy, StructureTimeframe: market.M15, Quality: opportunity.StrategyQuality{Components: components}}
	}
	w.countFeatures(candidate("fvg", map[string]float64{"prior_sweep": 1, "break_kind": -1}), "XAU")
	w.countFeatures(candidate("fvg", map[string]float64{"prior_sweep": 0, "break_kind": 0}), "XAU")
	w.countFeatures(candidate("order_block", map[string]float64{"prior_sweep": 1, "break_kind": 1}), "XAU")
	w.countFeatures(candidate("order_block", map[string]float64{"prior_sweep": 1, "break_kind": 1}), "XAU")
	w.countFeatures(candidate("key_level", map[string]float64{"score_x": 0.5}), "XAU")
	want := map[string]int64{
		"XAU/fvg/M15/prior_sweep=1": 1, "XAU/fvg/M15/prior_sweep=0": 1, "XAU/fvg/M15/break_kind=choch": 1, "XAU/fvg/M15/break_kind=none": 1,
		"XAU/order_block/M15/prior_sweep=1": 2, "XAU/order_block/M15/break_kind=bos": 2,
	}
	got := features(recorder)
	if len(got) != len(want) {
		t.Fatalf("tally = %v, want %v", got, want)
	}
	for k, n := range want {
		if got[k] != n {
			t.Errorf("%s = %d, want %d (all: %v)", k, got[k], n, got)
		}
	}
}
