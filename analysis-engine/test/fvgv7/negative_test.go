package fvgv7_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// withFrames changes the facts of the M15 and M5 frames of a scenario context.
func withFrames(ctx *analysiscontext.MarketContext, change func(f *analysiscontext.LegacyFrame)) *analysiscontext.MarketContext {
	for _, tc := range ctx.Timeframes {
		change(tc.Legacy)
	}
	return ctx
}

func noSweep(f *analysiscontext.LegacyFrame) { f.Sessions = nil }

// breakSwing is a swing high the gap's first candle (M15 candle 20) closes beyond: the impulse breaks structure, a BOS (no trend yet).
var breakSwing = techniquezone.Swing{Index: 5, Kind: "high", Price: px(210.04), ConfirmedIndex: 7}

func TestAGapWithoutAPriorSweepIsRefusedAndCounted(t *testing.T) {
	s := newStrategy(t, nil)
	got := s.Evaluate(withFrames(contextAt(basePath(), 72, "up"), noSweep))
	reasons := map[string]int64{}
	for r, n := range s.TakeRefusals() {
		reasons[r.Reason+"/"+string(r.Timeframe)] += n
	}
	if len(got) != 0 || reasons["no_prior_sweep/M15"] != 1 {
		t.Fatalf("published %d, refusals %v", len(got), reasons)
	}
	// The refusal is the last gate: it adds nothing to what the same scenario with a sweep refuses for its own reasons (another, smaller gap of the fixture).
	base := newStrategy(t, nil)
	base.Evaluate(contextAt(basePath(), 72, "up"))
	own := map[string]int64{}
	for r, n := range base.TakeRefusals() {
		own[r.Reason+"/"+string(r.Timeframe)] += n
	}
	delete(reasons, "no_prior_sweep/M15")
	if len(reasons) != len(own) {
		t.Errorf("refusals %v, want only the fixture's own %v", reasons, own)
	}
	for k, n := range own {
		if reasons[k] != n {
			t.Errorf("refusals %v, want only the fixture's own %v", reasons, own)
		}
	}
}

func TestWithTheGateOffTheSameGapPublishesAndSaysItHadNoSweep(t *testing.T) {
	gateOff := newStrategy(t, map[string]any{"require_prior_sweep": false})
	got := gateOff.Evaluate(withFrames(contextAt(basePath(), 72, "up"), noSweep))
	if len(got) != 1 {
		t.Fatalf("published %d, refusals %v", len(got), gateOff.TakeRefusals())
	}
	c := got[0]
	if c.Quality.Components["prior_sweep"] != 0 {
		t.Errorf("prior_sweep = %v, want 0", c.Quality.Components["prior_sweep"])
	}
	for _, key := range []string{"sweep_depth_atr", "sweep_reclaimed"} {
		if v, ok := c.Quality.Components[key]; ok {
			t.Errorf("%s = %v published for a gap with no sweep", key, v)
		}
	}
	for code := range evidenceOf(c) {
		if len(code) > len("m5_fvg_prior_sweep_") && code[:len("m5_fvg_prior_sweep_")] == "m5_fvg_prior_sweep_" {
			t.Errorf("evidence %q on a gap with no sweep", code)
		}
	}
}

func evidenceOf(c opportunity.Candidate) map[string]bool {
	out := map[string]bool{}
	for _, e := range c.Evidence {
		out[e.Code] = true
	}
	return out
}

func TestTheSweepIsPublishedAsEvidenceAndAsMeasures(t *testing.T) {
	got, reasons := decide(t, newStrategy(t, nil), basePath(), 72, "up")
	if len(got) != 1 {
		t.Fatalf("published %d, refusals %v", len(got), reasons)
	}
	c := got[0]
	if !evidenceOf(c)["m5_fvg_prior_sweep_pdl"] {
		t.Errorf("evidence %v, want m5_fvg_prior_sweep_pdl", evidenceOf(c))
	}
	m := c.Quality.Components
	// Bar 58 (M15 candle 19) dips to 209.90 and closes at 210.00: 0.0275 under the previous day's low 209.9725... in ATR of M15 (0.165).
	if m["prior_sweep"] != 1 || m["sweep_reclaimed"] != 1 {
		t.Errorf("measures %v", m)
	}
	if want := (px(209.95) - px(209.90)) / m15ATR; m["sweep_depth_atr"] < want-1e-9 || m["sweep_depth_atr"] > want+1e-9 {
		t.Errorf("sweep_depth_atr = %v, want %v", m["sweep_depth_atr"], want)
	}
	if m["break_kind"] != 0 {
		t.Errorf("break_kind = %v for a gap whose impulse broke nothing", m["break_kind"])
	}
}

func TestAStructureBreakByTheImpulseIsMeasuredAndNothingReadsIt(t *testing.T) {
	plain, _ := decide(t, newStrategy(t, nil), basePath(), 72, "up")
	broken := newStrategy(t, nil).Evaluate(withFrames(contextAt(basePath(), 72, "up"), func(f *analysiscontext.LegacyFrame) {
		f.Swings = []techniquezone.Swing{breakSwing}
	}))
	if len(plain) != 1 || len(broken) != 1 {
		t.Fatalf("published %d / %d", len(plain), len(broken))
	}
	a, b := plain[0], broken[0]
	if !evidenceOf(b)["m5_fvg_break_bos"] || b.Quality.Components["break_kind"] != 1 {
		t.Errorf("evidence %v break_kind %v", evidenceOf(b), b.Quality.Components["break_kind"])
	}
	if evidenceOf(a)["m5_fvg_break_bos"] || evidenceOf(a)["m5_fvg_break_choch"] {
		t.Errorf("a gap whose impulse broke nothing carries a break: %v", evidenceOf(a))
	}
	// The break is a measured fact, never a confluence factor, a gate or part of the geometry.
	if a.ID != b.ID || a.Entry != b.Entry || a.Invalidation != b.Invalidation || a.Targets[0] != b.Targets[0] || *a.DetectorConfluence != *b.DetectorConfluence {
		t.Fatalf("a structure break changed the decision:\n without: %+v\n with:    %+v", a, b)
	}
	if a.Quality.Overall != b.Quality.Overall {
		t.Errorf("a structure break changed the quality score: %v vs %v", a.Quality.Overall, b.Quality.Overall)
	}
}

func TestTheSweepIsNotAConfluenceFactorNorAScore(t *testing.T) {
	with, _ := decide(t, newStrategy(t, map[string]any{"require_prior_sweep": false}), basePath(), 72, "up")
	without := newStrategy(t, map[string]any{"require_prior_sweep": false}).Evaluate(withFrames(contextAt(basePath(), 72, "up"), noSweep))
	if len(with) != 1 || len(without) != 1 {
		t.Fatalf("published %d / %d", len(with), len(without))
	}
	a, b := with[0], without[0]
	if *a.DetectorConfluence != *b.DetectorConfluence || a.Quality.Overall != b.Quality.Overall || a.Entry != b.Entry || a.Invalidation != b.Invalidation || a.Targets[0] != b.Targets[0] {
		t.Fatalf("the sweep changed more than its own evidence and measures:\n with:    %+v\n without: %+v", a, b)
	}
}

func TestTheSweepIsDecidedFromBarsBeforeTheDecision(t *testing.T) {
	// The sweeping bar is M15 candle 19, which opens at M5 bar 57. A previous-day low finalized at that instant is a level for it; one finalized a second
	// later is not (it was not known when the candle opened).
	at := func(availableAt int64) int {
		ctx := withFrames(contextAt(basePath(), 72, "up"), func(f *analysiscontext.LegacyFrame) {
			f.Sessions = []techniquezone.SessionRef{{Name: "PDL", Price: px(209.95), AvailableAt: availableAt}}
		})
		return len(newStrategy(t, nil).Evaluate(ctx))
	}
	open := int64(startTime + 57*m5Seconds)
	if at(open) != 1 || at(open+1) != 0 {
		t.Fatalf("available at the open: %d published, one second later: %d (want 1 and 0)", at(open), at(open+1))
	}
}
