package fadescalp

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func newStrategy(t *testing.T) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: legacyfixture.Params(map[string]any{
		"invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0, "chop_edge_fraction": .25, "strict_premium_discount": true,
	})})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func bars() []market.Candle {
	out := legacyfixture.Bars(10, 100)
	out[9] = market.Candle{Time: 3000, Open: 99.4, High: 100.8, Low: 98.5, Close: 100.5}
	return out
}

func equalLow(touches int) techniquezone.Pool {
	return techniquezone.Pool{Side: "sell", Level: 99.15, Band: .1, Touches: touches}
}

func grab(grade string) techniquezone.Grab {
	return techniquezone.Grab{Index: 9, Direction: "bull", Grade: grade, Pool: equalLow(3)}
}

func qualifying(mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars(), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 103, Band: .1, Touches: 1}}
		f.Grabs = []techniquezone.Grab{grab("B")}
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func TestFadeBuysAnEqualLowSweepWithAStructuralReaction(t *testing.T) {
	got := newStrategy(t).Evaluate(qualifying(nil))
	if len(got) != 1 {
		t.Fatalf("an equal-low BUY fade is missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Reaction == nil || c.Reaction.Pattern != "sweep_reclaim" {
		t.Fatalf("unexpected candidate: %+v", c)
	}
	// The unconfirmed single-touch low at 103 is the objective of the fade.
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 2 {
		t.Fatalf("factor score 10/20.5 is two stars: %+v", c.DetectorConfluence)
	}
	if float64(c.Targets[0].Price.Price) != 103 {
		t.Fatalf("the nearest opposing pool is the target, got %v", c.Targets[0].Price.Price)
	}
}

func TestFadeRejections(t *testing.T) {
	cases := map[string]func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"a swing low is not an equal level": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Pools = []techniquezone.Pool{equalLow(1)}
		},
		"pool on the wrong side": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			p := equalLow(3)
			p.Side = "buy"
			f.Pools = []techniquezone.Pool{p}
		},
		"no grab on the level": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) { f.Grabs = nil },
		"grade C grab": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs = []techniquezone.Grab{grab("C")}
		},
		"grab on a different pool": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			g := grab("B")
			g.Pool.Level = 90
			f.Grabs = []techniquezone.Grab{g}
		},
		"premium location": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "premium"}
		},
		"no direction": func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			r.LocalStructure, r.HTFBias = "range", "range"
		},
		"no reaction": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = legacyfixture.Bars(10, 104)
		},
	}
	s := newStrategy(t)
	for name, mutate := range cases {
		if got := s.Evaluate(qualifying(mutate)); len(got) != 0 {
			t.Fatalf("%s must reject: %+v", name, got)
		}
	}
}

func TestFadeInChopNeedsGradeAAtTheRangeEdge(t *testing.T) {
	s := newStrategy(t)
	chop := func(low, high float64, g string) *analysiscontext.MarketContext {
		return qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Regime = regime.State{Kind: "chop", RangeLow: low, RangeHigh: high}
			f.Grabs = []techniquezone.Grab{grab(g)}
		})
	}
	if got := s.Evaluate(chop(99, 110, "B")); len(got) != 0 {
		t.Fatal("in chop a Grade B sweep must be rejected")
	}
	if got := s.Evaluate(chop(99, 110, "A")); len(got) != 1 {
		t.Fatal("in chop a Grade A sweep at the lower edge qualifies")
	}
	if got := s.Evaluate(chop(90, 100, "A")); len(got) != 0 {
		t.Fatal("in chop a BUY away from the lower range edge must be rejected")
	}
	if got := s.Evaluate(chop(99, 110, "A")); len(got) == 1 {
		found := false
		for _, e := range got[0].Evidence {
			found = found || e.Code == "chop_edge_grade_a"
		}
		if !found {
			t.Fatal("the chop edge acceptance must be recorded as evidence")
		}
	}
}

func TestFadeSellMirrorsBuy(t *testing.T) {
	out := legacyfixture.Bars(10, 100)
	out[9] = market.Candle{Time: 3000, Open: 100.6, High: 101.5, Low: 99.2, Close: 99.5}
	ctx := legacyfixture.Context(out, 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.LocalStructure, r.HTFBias = "down", "down"
		pool := techniquezone.Pool{Side: "buy", Level: 100.85, Band: .1, Touches: 3}
		f.Pools = []techniquezone.Pool{pool, {Side: "sell", Level: 97, Band: .1, Touches: 1}}
		f.Grabs = []techniquezone.Grab{{Index: 9, Direction: "bear", Grade: "B", Pool: pool}}
	})
	got := newStrategy(t).Evaluate(ctx)
	if len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("equal-high SELL fade missing: %+v", got)
	}
}
