package liquiditysweep

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func newStrategy(t *testing.T, own map[string]any) strategy.Strategy {
	t.Helper()
	params := map[string]any{
		"minimum_rejection_body_atr": .3, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0,
		"strict_premium_discount": true,
	}
	for k, v := range own {
		params[k] = v
	}
	s, err := New(strategy.Config{ID: ID, Version: Version, Parameters: legacyfixture.Params(params)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// A lone-extreme sell-side pool at 99.0 (one touch) swept and reclaimed on the
// last bar by a bullish displacement candle; the frozen frame carries the grab
// (grade B) on that bar. ATR is 1.
func sweepContext(mutate func(*analysiscontext.TimeframeContext, *analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	bars := legacyfixture.Bars(10, 100)
	bars[9] = market.Candle{Time: bars[9].Time, Open: 98.9, High: 100.5, Low: 98.6, Close: 100.2}
	ctx := legacyfixture.Context(bars, 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.HTFBias = "up"
		f.Grabs = []techniquezone.Grab{{Index: 9, Direction: "bull", Grade: "B", Pool: techniquezone.Pool{Side: "sell", Level: 99.0, Band: .15, Touches: 1}}}
	})
	ctx.Volatility.ATR = 1
	tf := ctx.Timeframes[market.M5]
	if mutate != nil {
		mutate(tf, tf.Legacy, ctx.Legacy)
	}
	return ctx
}

func TestALoneExtremeSweepGradedBWithAnAlignedBiasIsABuy(t *testing.T) {
	got := newStrategy(t, nil).Evaluate(sweepContext(nil))
	if len(got) != 1 || got[0].Direction != market.Buy {
		t.Fatalf("want one BUY, got %+v", got)
	}
	c := got[0]
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars < 2 || !c.DetectorConfluence.Factors.HTFAligned || c.DetectorConfluence.Factors.Touches != 1 {
		t.Fatalf("the sweep must carry the frozen detector's confluence: %+v", c.DetectorConfluence)
	}
	// The stop is beyond the sweep extreme (the reclaim bar's wick low 98.6).
	if float64(c.Invalidation.Price) != 98.35 {
		t.Fatalf("stop %v, want 98.35 (wick low minus the buffer)", c.Invalidation.Price)
	}
}

func TestSweepRejections(t *testing.T) {
	cases := map[string]func(*analysiscontext.TimeframeContext, *analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"an equal-level pool belongs to Fade Scalp": func(_ *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs[0].Pool.Touches = 2
		},
		"a marginal close (grade C) is not a sweep": func(_ *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs[0].Grade = "C"
		},
		"a grab on an earlier bar is not this bar's sweep": func(_ *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs[0].Index = 8
		},
		"an induced grab is not a sweep": func(_ *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs[0].Inducement = true
		},
		"a buy in premium (reversal location)": func(_ *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "premium"}
		},
		"a counter-bias grade B sweep lacks the confluence floor": func(_ *analysiscontext.TimeframeContext, _ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			r.HTFBias, r.LocalStructure = "down", "down"
		},
		"the reclaim bar is not a displacement body": func(tf *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			tf.Candles[9] = market.Candle{Time: tf.Candles[9].Time, Open: 100.0, High: 100.5, Low: 98.8, Close: 100.1}
		},
	}
	for name, mutate := range cases {
		if got := newStrategy(t, nil).Evaluate(sweepContext(mutate)); len(got) != 0 {
			t.Errorf("%s: want no candidate, got %+v", name, got)
		}
	}
}

func TestACounterBiasGradeALoneExtremeSweepStillMissesTheConfluenceFloor(t *testing.T) {
	got := newStrategy(t, nil).Evaluate(sweepContext(func(_ *analysiscontext.TimeframeContext, f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.HTFBias, r.LocalStructure = "down", "down"
		f.Grabs[0].Grade = "A"
	}))
	// 1 touch + wick + displacement = 7 of 20.5 factor points is one star: even a
	// grade A counter-bias sweep of a lone extreme does not reach the floor.
	if len(got) != 0 {
		t.Fatalf("a counter-bias lone-extreme sweep must not reach the confluence floor: %+v", got)
	}
}
