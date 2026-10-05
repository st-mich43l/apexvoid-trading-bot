package rangeedge

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func params(minTouches, minWicks int) map[string]any {
	return legacyfixture.Params(map[string]any{
		"lookback_bars": 48.0, "minimum_touches": float64(minTouches), "minimum_wick_rejections": float64(minWicks),
		"break_closes": 2.0, "minimum_room_atr": .75, "invalidation_buffer_atr": .25, "expiry_hours": 4.0,
	})
}

func newStrategy(t *testing.T, minTouches, minWicks int) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: params(minTouches, minWicks)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func barrier(side string, level float64, touches, wicks int, score float64) techniquezone.ScalpBarrier {
	return techniquezone.ScalpBarrier{
		Side: side, Level: level, Low: level - .3, High: level + .3, Touches: touches, WickRejections: wicks,
		LastTouchIndex: 9, FirstTouchIndex: 1, Score: score, Grade: "A", ConfidenceGrade: "A", Sources: []string{"wick"}, Tested: true,
	}
}

// bars end on a bullish pin that rejects the lower edge near 98.
func bars() []market.Candle {
	out := legacyfixture.Bars(10, 100.5)
	out[9] = market.Candle{Time: 3000, Open: 98.3, High: 98.9, Low: 97.8, Close: 98.6}
	return out
}

func rangeOf(lower, upper techniquezone.ScalpBarrier) *techniquezone.ScalpRange {
	return &techniquezone.ScalpRange{Lower: lower, Upper: upper, Equilibrium: (lower.Level + upper.Level) / 2, WidthATR: upper.Level - lower.Level, Quality: lower.Score + upper.Score, State: techniquezone.RangeStateConfirmed}
}

func qualifying(mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars(), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		f.ScalpRange = rangeOf(barrier("support", 98, 4, 3, 10.5), barrier("resistance", 103, 4, 3, 10.5))
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func TestRangeEdgeBuysTheLowerEdgeAfterARejection(t *testing.T) {
	got := newStrategy(t, 2, 1).Evaluate(qualifying(nil))
	if len(got) != 1 {
		t.Fatalf("a lower-edge candidate is missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Entry.Low != 97.7 || c.Entry.High != 98.3 {
		t.Fatalf("entry = %v [%v, %v]", c.Direction, c.Entry.Low, c.Entry.High)
	}
	if c.Reaction == nil || c.Reaction.Pattern != "wick_rejection" || c.Reaction.ConfirmationBarTime != 3000 {
		t.Fatalf("the pin on the last bar is the confirmation: %+v", c.Reaction)
	}
	// A 10.5/24.5 barrier zone is two stars; the structural factors do not lift it.
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 2 {
		t.Fatalf("confluence = %+v", c.DetectorConfluence)
	}
	if float64(c.Targets[0].Price.Price) != 100.5 || float64(c.Targets[1].Price.Price) != 103 {
		t.Fatalf("targets are the equilibrium then the opposite edge: %+v", c.Targets)
	}
	if c.StructuralID != "range:support:600" {
		t.Fatalf("barrier episode identity, got %q", c.StructuralID)
	}
}

func TestRangeEdgeSellsTheUpperEdge(t *testing.T) {
	out := legacyfixture.Bars(10, 100.5)
	out[9] = market.Candle{Time: 3000, Open: 102.8, High: 103.4, Low: 102.1, Close: 102.3}
	ctx := legacyfixture.Context(out, 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.LocalStructure, r.HTFBias = "down", "down"
		f.ScalpRange = rangeOf(barrier("support", 98, 4, 3, 10.5), barrier("resistance", 103, 4, 3, 10.5))
	})
	got := newStrategy(t, 2, 1).Evaluate(ctx)
	if len(got) != 1 || got[0].Direction != market.Sell || got[0].Entry.Low != 102.7 || got[0].Entry.High != 103.3 {
		t.Fatalf("upper-edge sell missing: %+v", got)
	}
}

func TestRangeEdgeNeedsAScalpRange(t *testing.T) {
	if got := newStrategy(t, 2, 1).Evaluate(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) { f.ScalpRange = nil })); len(got) != 0 {
		t.Fatal("without a range there is no edge")
	}
	if got := newStrategy(t, 2, 1).Evaluate(&analysiscontext.MarketContext{}); len(got) != 0 {
		t.Fatal("without the detector frame there is no edge")
	}
}

func TestRangeEdgeGates(t *testing.T) {
	lower := func(f *analysiscontext.LegacyFrame, edit func(*techniquezone.ScalpBarrier)) {
		r := *f.ScalpRange
		edit(&r.Lower)
		f.ScalpRange = &r
	}
	cases := map[string]func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"barrier broke on accepted closes": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			lower(f, func(b *techniquezone.ScalpBarrier) { b.AcceptedCloses = 2 })
		},
		"too few touches": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			lower(f, func(b *techniquezone.ScalpBarrier) { b.Touches = 1 })
		},
		"no wick rejection history": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			lower(f, func(b *techniquezone.ScalpBarrier) { b.WickRejections = 0 })
		},
		"not enough room around the equilibrium": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			r := *f.ScalpRange
			r.Equilibrium = 98.5
			f.ScalpRange = &r
		},
		"price not at the barrier any more": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = legacyfixture.Bars(10, 100.5)
		},
		"below the confluence floor": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			lower(f, func(b *techniquezone.ScalpBarrier) { b.Score = 8.8 })
		},
		"no structural reaction": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = append([]market.Candle(nil), f.Bars...)
			f.Bars[9] = market.Candle{Time: 3000, Open: 98.6, High: 98.9, Low: 98.1, Close: 98.2} // bearish, no wick, no sweep
		},
	}
	s := newStrategy(t, 2, 1)
	for name, mutate := range cases {
		if got := s.Evaluate(qualifying(mutate)); len(got) != 0 {
			t.Fatalf("%s must reject: %+v", name, got)
		}
	}
}

func gradeAGrab() techniquezone.Grab {
	return techniquezone.Grab{Index: 9, Direction: "bull", Grade: "A", Pool: techniquezone.Pool{Side: "sell", Level: 98.1, Band: .1, Touches: 3}}
}

func TestGradeAGrabWaivesTheTouchAndWickMinimums(t *testing.T) {
	// The configured minimums (3 touches, 2 wicks) are not met by a barrier with
	// two touches and one wick, unless a Grade A sweep points into the edge.
	thin := func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		r := *f.ScalpRange
		r.Lower.Touches, r.Lower.WickRejections = 2, 1
		f.ScalpRange = &r
	}
	s := newStrategy(t, 3, 2)
	if got := s.Evaluate(qualifying(thin)); len(got) != 0 {
		t.Fatal("two touches and one wick fail the stricter minimums")
	}
	withGrab := func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		thin(f, r)
		f.Grabs = []techniquezone.Grab{gradeAGrab()}
	}
	got := s.Evaluate(qualifying(withGrab))
	if len(got) != 1 || !got[0].DetectorConfluence.Factors.DisplacementGrade {
		t.Fatalf("a Grade A grab waives both minimums and is displacement evidence: %+v", got)
	}
	// One touch is never enough, even with the grab.
	one := func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		withGrab(f, r)
		rr := *f.ScalpRange
		rr.Lower.Touches = 1
		f.ScalpRange = &rr
	}
	if got := s.Evaluate(qualifying(one)); len(got) != 0 {
		t.Fatal("the exception still requires two touches")
	}
	// A Grade B grab does not waive anything.
	b := func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		thin(f, r)
		g := gradeAGrab()
		g.Grade = "B"
		f.Grabs = []techniquezone.Grab{g}
	}
	if got := s.Evaluate(qualifying(b)); len(got) != 0 {
		t.Fatal("only Grade A waives the minimums")
	}
}

// The frozen detector returns _finish's answer for the first edge that passes
// every gate and reaction. If that edge fails the confluence floor the other
// edge is not tried.
func TestFirstQualifyingEdgeDecidesEvenWhenItFailsTheFloor(t *testing.T) {
	out := legacyfixture.Bars(10, 100.5)
	// An older bearish pin at the upper edge and a final bullish pin at the lower
	// edge: both edges confirm, and the lower (nearer) one is tried first.
	out[8] = market.Candle{Time: 2700, Open: 102.8, High: 103.4, Low: 102.2, Close: 102.3}
	out[9] = market.Candle{Time: 3000, Open: 98.3, High: 98.9, Low: 97.8, Close: 98.6}
	build := func(lowerScore float64) *analysiscontext.MarketContext {
		// A wide ATR keeps the upper entry within the maximum entry distance.
		return legacyfixture.Context(out, 3, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.ScalpRange = rangeOf(barrier("support", 98, 4, 3, lowerScore), barrier("resistance", 103, 4, 3, 10.5))
		})
	}
	s := newStrategy(t, 2, 1)
	if got := s.Evaluate(build(10.5)); len(got) != 1 || got[0].Direction != market.Buy {
		t.Fatalf("the nearer lower edge qualifies: %+v", got)
	}
	if got := s.Evaluate(build(8.8)); len(got) != 0 {
		t.Fatalf("the lower edge fails the floor and the upper edge is not tried: %+v", got)
	}
	// Control: a lower edge that fails a *gate* (here: too few touches) is skipped,
	// and the upper edge then qualifies. Only a failure of the final
	// qualification stops the detector at the first edge.
	gated := legacyfixture.Context(out, 3, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.ScalpRange = rangeOf(barrier("support", 98, 1, 3, 10.5), barrier("resistance", 103, 4, 3, 10.5))
	})
	if got := s.Evaluate(gated); len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("after a gate failure the other edge is evaluated: %+v", got)
	}
}

func TestRangeEdgeHTFRangeBiasCountsAsAligned(t *testing.T) {
	aligned := func(htf string) bool {
		got := newStrategy(t, 2, 1).Evaluate(qualifying(func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) { r.HTFBias = htf }))
		return len(got) == 1 && got[0].DetectorConfluence.Factors.HTFAligned
	}
	if !aligned("up") || !aligned("range") {
		t.Fatal("an aligned or ranging higher timeframe sets htf_aligned for a range edge")
	}
	if aligned("down") {
		t.Fatal("an opposing higher timeframe must not set htf_aligned")
	}
}

func TestRangeEdgeRequiresDetectorSettings(t *testing.T) {
	p := params(2, 1)
	delete(p, "pip_size")
	if _, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: p}); err == nil {
		t.Fatal("a missing injected detector parameter must fail closed")
	}
}
