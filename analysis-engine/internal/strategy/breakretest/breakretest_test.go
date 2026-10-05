package breakretest

import (
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	technicaltrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func newStrategy(t *testing.T, strictPD bool) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: legacyfixture.Params(map[string]any{
		"breakout_accept_bars": 2.0, "trendline_tolerance_atr": .3, "momentum_body_fraction": .6,
		"strict_premium_discount": strictPD, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 8.0,
	})})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func candle(i int, open, high, low, closePrice float64) market.Candle {
	return market.Candle{Time: int64(i+1) * 300, Open: open, High: high, Low: low, Close: closePrice}
}

// buyBars break above 100 on two accepted closes and never touch it again
// until the final bar, which retests it and rejects (a bullish pin).
func buyBars() []market.Candle {
	bars := []market.Candle{
		candle(0, 97.8, 98.3, 97.5, 98), candle(1, 98.5, 99.2, 98.3, 99),
		candle(2, 100.8, 101.3, 100.6, 101), candle(3, 101.5, 102.2, 101.3, 102),
		candle(4, 102, 103.2, 101.8, 103), candle(5, 102.9, 103.3, 102.2, 102.3),
		candle(6, 102.3, 102.6, 101.8, 101.9), candle(7, 101.9, 102.4, 101.6, 102.1),
		candle(8, 102.1, 102.3, 101.5, 102.0),
		candle(9, 100.3, 101.2, 99.6, 101.0),
	}
	return bars
}

// mirror reflects prices around 100 so a BUY scenario becomes its SELL twin.
func mirror(bars []market.Candle) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: 200 - c.Open, High: 200 - c.Low, Low: 200 - c.High, Close: 200 - c.Close}
	}
	return out
}

func keyLevel(price float64) techniquezone.Level {
	return techniquezone.Level{Price: price, Kind: "reaction", Touches: 3, Band: .3, Strength: 3}
}

func line(kind technicaltrendline.Kind, value float64, brokenAt int64) technicaltrendline.Trendline {
	return technicaltrendline.Trendline{
		Kind: kind, Intercept: value, AnchorAIndex: 0, AnchorBIndex: 1, BrokenAt: &brokenAt,
		ValidationTouches: []technicaltrendline.ValidationTouch{{Time: 900}},
	}
}

func context(bars []market.Candle, sell bool, mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars, 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		if sell {
			r.LocalStructure, r.HTFBias = "down", "down"
		}
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func evidence(c opportunity.Candidate, code string) bool {
	for _, e := range c.Evidence {
		if e.Code == code {
			return true
		}
	}
	return false
}

func TestBuyBrokenResistanceTrendlineRetestHoldsAndRejects(t *testing.T) {
	bars := buyBars()
	got := newStrategy(t, false).Evaluate(context(bars, false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Trendlines = []technicaltrendline.Trendline{line(technicaltrendline.KindResistance, 100.2, bars[3].Time)}
	}))
	if len(got) != 1 {
		t.Fatalf("trendline break & retest missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || !strings.HasPrefix(c.StructuralID, "trendline:resistance:") || !evidence(c, "m5_trendline_retest") {
		t.Fatalf("unexpected candidate: %+v", c)
	}
	// Retest band is the line value +/- trendline_tolerance_atr * ATR.
	if c.Entry.Low != 99.9 || c.Entry.High != 100.5 {
		t.Fatalf("entry = [%v, %v], want [99.9, 100.5]", c.Entry.Low, c.Entry.High)
	}
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 3 || !c.DetectorConfluence.Factors.DisplacementGrade {
		t.Fatalf("a trendline break is displacement-graded and 3 stars: %+v", c.DetectorConfluence)
	}
	if c.Reaction != nil {
		t.Fatal("break & retest is its own thesis, not a shared reaction confirmation")
	}
}

func TestSellBrokenSupportTrendlineRetestHoldsAndRejects(t *testing.T) {
	bars := mirror(buyBars())
	got := newStrategy(t, false).Evaluate(context(bars, true, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Trendlines = []technicaltrendline.Trendline{line(technicaltrendline.KindSupport, 99.8, bars[3].Time)}
	}))
	if len(got) != 1 || got[0].Direction != market.Sell || !strings.HasPrefix(got[0].StructuralID, "trendline:support:") {
		t.Fatalf("sell trendline break & retest missing: %+v", got)
	}
}

func TestBuyKeyLevelAcceptedBreakRetestsSupport(t *testing.T) {
	got := newStrategy(t, false).Evaluate(context(buyBars(), false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
	}))
	if len(got) != 1 {
		t.Fatalf("key-level break & retest missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.StructuralID != "level:reaction:100.00000000" || !evidence(c, "m5_key_level_retest") {
		t.Fatalf("unexpected candidate: %+v", c)
	}
	if c.Entry.Low >= 100 || c.Entry.High <= 100 {
		t.Fatalf("the retest band straddles the level: [%v, %v]", c.Entry.Low, c.Entry.High)
	}
	// 13 of 20.5 factor points (htf, 3 touches, wick, structure; the final bar
	// is not a strong body break): three stars.
	if c.DetectorConfluence.SelectedStars != 3 || c.DetectorConfluence.Factors.DisplacementGrade {
		t.Fatalf("confluence = %+v", c.DetectorConfluence)
	}
}

func TestSellKeyLevelAcceptedBreakRetestsResistance(t *testing.T) {
	got := newStrategy(t, false).Evaluate(context(mirror(buyBars()), true, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
	}))
	if len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("sell key-level break & retest missing: %+v", got)
	}
}

func TestTrendlinePathHasPriorityOverKeyLevels(t *testing.T) {
	bars := buyBars()
	got := newStrategy(t, false).Evaluate(context(bars, false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
		f.Trendlines = []technicaltrendline.Trendline{line(technicaltrendline.KindResistance, 100.2, bars[3].Time)}
	}))
	if len(got) != 1 || !strings.HasPrefix(got[0].StructuralID, "trendline:") {
		t.Fatalf("the trendline path is evaluated first: %+v", got)
	}
	// When the trendline does not qualify the key-level path still can.
	got = newStrategy(t, false).Evaluate(context(bars, false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
		f.Trendlines = []technicaltrendline.Trendline{line(technicaltrendline.KindSupport, 100.2, bars[3].Time)}
	}))
	if len(got) != 1 || !strings.HasPrefix(got[0].StructuralID, "level:") {
		t.Fatalf("falls through to the key level: %+v", got)
	}
}

func TestNearestTrendlineIsTriedFirst(t *testing.T) {
	bars := buyBars()
	got := newStrategy(t, false).Evaluate(context(bars, false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		far := line(technicaltrendline.KindResistance, 99.7, bars[3].Time)
		far.AnchorAIndex = 5
		f.Trendlines = []technicaltrendline.Trendline{far, line(technicaltrendline.KindResistance, 100.2, bars[3].Time)}
	}))
	if len(got) != 1 || !strings.HasSuffix(got[0].StructuralID, ":300:600") {
		t.Fatalf("the line nearest to price (100.2 vs 99.7 from 101) wins: %+v", got)
	}
}

func TestBreakRetestRejections(t *testing.T) {
	bars := buyBars()
	tl := line(technicaltrendline.KindResistance, 100.2, bars[3].Time)
	withLevel := func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
	}
	withLine := func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Trendlines = []technicaltrendline.Trendline{tl}
	}
	with := func(parts ...func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead) {
		return func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			for _, part := range parts {
				part(f, r)
			}
		}
	}
	withBars := func(replace func([]market.Candle) []market.Candle) func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead) {
		return func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = replace(append([]market.Candle(nil), f.Bars...))
		}
	}
	cases := map[string]func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"chop": with(withLevel, withLine, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Regime = regime.State{Kind: "chop"}
		}),
		// The retest bar touches the level and holds above it, but its close is
		// not in the upper third of its range: only the rejection gate fails.
		"no rejection on the current bar": with(withLevel, withLine, withBars(func(b []market.Candle) []market.Candle {
			b[9] = candle(9, 100.5, 101.2, 99.6, 100.6)
			return b
		})),
		"wrong trendline kind": with(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Trendlines = []technicaltrendline.Trendline{line(technicaltrendline.KindSupport, 100.2, bars[3].Time)}
		}),
		"an unbroken trendline": with(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			unbroken := tl
			unbroken.BrokenAt = nil
			f.Trendlines = []technicaltrendline.Trendline{unbroken}
		}),
		"no accepted break": with(withLevel, withBars(func(b []market.Candle) []market.Candle {
			// Closes alternate around the level, never two in a row beyond it.
			for i, c := range []float64{98, 99, 101, 99.5, 101, 99.6, 101, 99.7, 101} {
				b[i] = candle(i, c-.2, c+.4, c-.4, c)
			}
			return b
		})),
		"retest before the break": with(withLevel, withBars(func(b []market.Candle) []market.Candle {
			b[1] = candle(1, 99.6, 100.4, 99.4, 100.3)
			b[2], b[3] = candle(2, 100.6, 101.2, 100.5, 101), candle(3, 101.5, 102.2, 101.3, 102)
			b[9] = candle(9, 101.3, 101.6, 100.9, 101.5) // a rejection that does not touch the level
			return b
		})),
		"retest fails to hold (key level)": with(withLevel, withBars(func(b []market.Candle) []market.Candle {
			b[9] = candle(9, 99.2, 99.9, 98.4, 99.8)
			return b
		})),
		"retest fails to hold (trendline)": with(withLine, withBars(func(b []market.Candle) []market.Candle {
			b[9] = candle(9, 99.9, 100.4, 99.5, 100.1)
			return b
		})),
		// The level broke up and was retested earlier, then price fell back to
		// 101.45, still inside the retest band but below the level itself.
		"wrong-side key level": with(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Levels = []techniquezone.Level{keyLevel(101.5)}
		}, withBars(func(b []market.Candle) []market.Candle {
			for i, closePrice := range []float64{98, 99, 102, 103} {
				b[i] = candle(i, closePrice-.2, closePrice+.4, closePrice-.3, closePrice)
			}
			b[4] = candle(4, 103, 103.4, 102.4, 102.8)
			b[5] = candle(5, 101.9, 102.0, 101.45, 101.7) // retests 101.5 and holds
			b[6] = candle(6, 101.6, 101.7, 101.0, 101.2)
			b[7] = candle(7, 101.2, 101.3, 100.7, 100.9)
			b[8] = candle(8, 100.9, 101.0, 100.5, 100.7)
			b[9] = candle(9, 101.0, 101.5, 100.4, 101.45)
			return b
		})),
		"PD gate: premium": with(withLevel, withLine, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "premium"}
		}),
		"PD gate: equilibrium": with(withLevel, withLine, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "eq"}
		}),
		"no direction": with(withLevel, withLine, func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			r.LocalStructure, r.HTFBias = "range", "range"
		}),
	}
	s := newStrategy(t, false)
	for name, mutate := range cases {
		if got := s.Evaluate(context(buyBars(), false, mutate)); len(got) != 0 {
			t.Fatalf("%s must reject: %+v", name, got)
		}
	}
	// Control: the unmodified scenario does qualify, so each case above removes
	// exactly one requirement.
	if got := s.Evaluate(context(buyBars(), false, with(withLevel, withLine))); len(got) != 1 {
		t.Fatal("control scenario must qualify")
	}
}

func TestStrictPremiumDiscountRejectsADiscountlessBuyWhenConfigured(t *testing.T) {
	ctx := context(buyBars(), false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
		f.Range = &fib.DealingRange{Zone: "discount"}
	})
	if got := newStrategy(t, true).Evaluate(ctx); len(got) != 1 {
		t.Fatalf("strict PD allows a BUY in discount: %+v", got)
	}
	ctx.Timeframes[market.M5].Legacy.Range = &fib.DealingRange{Zone: "eq"}
	if got := newStrategy(t, true).Evaluate(ctx); len(got) != 0 {
		t.Fatal("equilibrium is rejected by every PD gate")
	}
}

func TestKeyLevelBreakNeedsTheConfiguredAcceptedCloses(t *testing.T) {
	// Two closes beyond the level are required; the same data with a single
	// close beyond must not form a break.
	bars := buyBars()
	bars[3] = candle(3, 100.4, 100.6, 99.4, 99.6)
	for i := 4; i <= 8; i++ {
		bars[i] = candle(i, 99.8, 100.0, 99.3, 99.6)
	}
	bars[9] = candle(9, 100.3, 101.2, 99.6, 101.0)
	got := newStrategy(t, false).Evaluate(context(bars, false, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{keyLevel(100)}
	}))
	if len(got) != 0 {
		t.Fatalf("a single close beyond the level is not an accepted break: %+v", got)
	}
}

func TestBreakRetestRequiresDetectorContext(t *testing.T) {
	if got := newStrategy(t, false).Evaluate(&analysiscontext.MarketContext{}); len(got) != 0 {
		t.Fatal("no frame, no candidate")
	}
	p := legacyfixture.Params(map[string]any{
		"breakout_accept_bars": 2.0, "trendline_tolerance_atr": .3, "momentum_body_fraction": .6,
		"strict_premium_discount": false, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 8.0,
	})
	delete(p, "confluence_floor")
	if _, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: p}); err == nil {
		t.Fatal("a missing detector parameter must fail closed")
	}
}
