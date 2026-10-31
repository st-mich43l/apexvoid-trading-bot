package obv7_test

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// The block's leg starts at M15 candle 21; candle 20 (the block's own bearish candle, low 209.80) is inside the sweep window. A previous-day low just above
// it makes candle 20 a sweep of that low.
func withPreviousDayLow(ctx *analysiscontext.MarketContext) *analysiscontext.MarketContext {
	for _, tc := range ctx.Timeframes {
		tc.Legacy.Sessions = []techniquezone.SessionRef{{Name: "PDL", Price: px(209.82), AvailableAt: startTime}}
	}
	return ctx
}

func TestTheBreakKindAndTheSweepArePublishedAsEvidenceAndMeasures(t *testing.T) {
	plain := newStrategy(t, map[string]any{"confluence_floor": 1}).Evaluate(contextAt(basePath(), decisionBar, "up"))
	swept := newStrategy(t, map[string]any{"confluence_floor": 1}).Evaluate(withPreviousDayLow(contextAt(basePath(), decisionBar, "up")))
	if len(plain) != 1 || len(swept) != 1 {
		t.Fatalf("published %d / %d", len(plain), len(swept))
	}
	a, b := plain[0], swept[0]
	for _, c := range []struct {
		name string
		ev   map[string]bool
	}{{"plain", evidenceOf(a)}, {"swept", evidenceOf(b)}} {
		if !c.ev["m5_order_block_break_bos"] || c.ev["m5_order_block_break_choch"] {
			t.Errorf("%s: evidence %v, want the BOS that formed the block", c.name, c.ev)
		}
	}
	if a.Quality.Components["break_kind"] != 1 || a.Quality.Components["prior_sweep"] != 0 {
		t.Errorf("plain measures %v", a.Quality.Components)
	}
	if _, ok := a.Quality.Components["sweep_depth_atr"]; ok || evidenceOf(a)["m5_order_block_prior_sweep_pdl"] {
		t.Errorf("a block with no sweep carries one: %v %v", a.Quality.Components, evidenceOf(a))
	}
	if !evidenceOf(b)["m5_order_block_prior_sweep_pdl"] || b.Quality.Components["prior_sweep"] != 1 {
		t.Fatalf("swept: evidence %v measures %v", evidenceOf(b), b.Quality.Components)
	}
	if want := (px(209.82) - px(209.78)) / m15ATR; math.Abs(b.Quality.Components["sweep_depth_atr"]-want) > 1e-9 {
		t.Errorf("sweep_depth_atr = %v, want %v", b.Quality.Components["sweep_depth_atr"], want)
	}
	// Measured, never a gate or a factor: the decision is the same with and without the sweep.
	if a.ID != b.ID || a.Entry != b.Entry || a.Invalidation != b.Invalidation || a.Targets[0] != b.Targets[0] || *a.DetectorConfluence != *b.DetectorConfluence || a.Quality.Overall != b.Quality.Overall {
		t.Fatalf("the sweep changed the decision:\n without: %+v\n with:    %+v", a, b)
	}
}
