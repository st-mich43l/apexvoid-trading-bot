package orderblock

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const (
	sweepBase = int64(1_000_000)
	sweepStep = int64(900)
	// the impulse of every sweep fixture starts here
	impulseBar = 30
)

func sweepStrategy(lookback, pivot, maxAge int) *Strategy {
	return &Strategy{cfg: Config{SweepLookbackBars: lookback, SweepPivotBars: pivot, SweepLevelMaxAgeBars: maxAge}}
}

// flatBars are n quiet candles: open = close = 100, range 99.5-100.5 (equal lows, so no bar is a fractal pivot).
func flatBars(n int) []market.Candle {
	out := make([]market.Candle, n)
	for i := range out {
		out[i] = market.Candle{Time: sweepBase + int64(i)*sweepStep, Open: 100, High: 100.5, Low: 99.5, Close: 100}
	}
	return out
}

func setBar(bars []market.Candle, i int, low, close float64) {
	bars[i].Low, bars[i].Close = low, close
	if close < bars[i].Low {
		bars[i].Low = close
	}
}

func sweepFrame(bars []market.Candle, sessions ...techniquezone.SessionRef) *analysiscontext.LegacyFrame {
	atr := make([]float64, len(bars))
	for i := range atr {
		atr[i] = 1
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: atr, Sessions: sessions}
}

// mirrored turns a BUY fixture into its SELL twin: every price p becomes 200 - p, highs and lows swap, and the low/high levels swap sides.
func mirrored(bars []market.Candle, sessions []techniquezone.SessionRef) ([]market.Candle, []techniquezone.SessionRef) {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: 200 - c.Open, High: 200 - c.Low, Low: 200 - c.High, Close: 200 - c.Close}
	}
	twin := map[string]string{"PDL": "PDH", "ASIA_L": "ASIA_H", "LONDON_L": "LONDON_H", "NY_L": "NY_H", "PWL": "PWH"}
	var refs []techniquezone.SessionRef
	for _, ref := range sessions {
		ref.Name, ref.Price = twin[ref.Name], 200-ref.Price
		refs = append(refs, ref)
	}
	return out, refs
}

// both runs a fixture as a BUY and as its mirrored SELL.
func both(t *testing.T, s *Strategy, bars []market.Candle, sessions []techniquezone.SessionRef, start int, check func(t *testing.T, buy bool, got sweep)) {
	t.Helper()
	t.Run("buy", func(t *testing.T) {
		check(t, true, s.priorSweep(sweepFrame(bars, sessions...), start, true, 1))
	})
	t.Run("sell", func(t *testing.T) {
		mb, ms := mirrored(bars, sessions)
		check(t, false, s.priorSweep(sweepFrame(mb, ms...), start, false, 1))
	})
}

func swingFixture() []market.Candle {
	bars := flatBars(40)
	setBar(bars, 10, 98.0, 99.8) // a fractal pivot low at 98.0
	return bars
}

func TestASwingLowTheImpulseTradedThroughIsASweep(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar-2, 97.5, 98.6) // trades 0.5 through 98.0 and closes back above it
	both(t, sweepStrategy(6, 2, 96), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
		if !got.Found || got.Kind != "swing" || got.Bar != impulseBar-2 || got.Depth != 0.5 || !got.Reclaimed {
			t.Fatalf("sweep = %+v", got)
		}
	})
}

func TestASweepThatClosesBeyondTheLevelIsNotReclaimed(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar, 97.5, 97.8) // the impulse's own first candle closes under the level
	both(t, sweepStrategy(6, 2, 96), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
		if !got.Found || got.Bar != impulseBar || got.Reclaimed {
			t.Fatalf("sweep = %+v", got)
		}
	})
}

func TestNothingTradedThroughIsNoSweep(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar-2, 98.2, 99)
	both(t, sweepStrategy(6, 2, 96), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
		if got.Found {
			t.Fatalf("a low above the pivot swept it: %+v", got)
		}
	})
}

func TestTheWindowIsSixBarsBeforeTheImpulseThroughItsFirstCandle(t *testing.T) {
	for _, tc := range []struct {
		bar  int
		want bool
	}{{impulseBar - 7, false}, {impulseBar - 6, true}, {impulseBar, true}, {impulseBar + 1, false}} {
		bars := swingFixture()
		setBar(bars, tc.bar, 97.5, 98.6)
		both(t, sweepStrategy(6, 2, 96), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
			if got.Found != tc.want {
				t.Fatalf("sweeping bar %d (impulse %d): found %v, want %v (%+v)", tc.bar, impulseBar, got.Found, tc.want, got)
			}
		})
	}
}

func TestAnAlreadySweptSwingIsNotALevel(t *testing.T) {
	bars := swingFixture()
	setBar(bars, 15, 97.8, 99.5)           // an earlier bar already traded through 98.0, long before the window
	setBar(bars, impulseBar-1, 97.9, 98.5) // a second trade through the same level inside the window
	both(t, sweepStrategy(6, 2, 96), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
		if got.Found {
			t.Fatalf("a level that had been traded through since its confirmation is not liquidity any more: %+v", got)
		}
	})
}

func TestASwingOlderThanTheMaximumAgeIsNotALevel(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar-1, 97.5, 98.6) // 19 bars after the pivot at 10... bar 29 - 10
	for _, tc := range []struct {
		age  int
		want bool
	}{{18, false}, {19, true}, {96, true}} {
		both(t, sweepStrategy(6, 2, tc.age), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
			if got.Found != tc.want {
				t.Fatalf("max age %d: found %v, want %v", tc.age, got.Found, tc.want)
			}
		})
	}
}

func TestASwingIsALevelOnlyFromTheBarAfterItsConfirmation(t *testing.T) {
	// A pivot at p is confirmed at p+2. The bar at p+2 cannot sweep it (it is one of its right-hand bars: trading below the pivot would unmake it) and a
	// bar after the confirmation can. The pivot sits two bars before the window's last bar.
	bars := flatBars(40)
	setBar(bars, 26, 98.0, 99.8)         // the pivot, confirmed at 28
	setBar(bars, impulseBar, 97.5, 98.6) // 30 = 28 + 2: after the confirmation
	both(t, sweepStrategy(6, 2, 96), bars, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
		if !got.Found || got.Kind != "swing" {
			t.Fatalf("a bar after the confirmation sweeps the pivot: %+v", got)
		}
	})
	// A pivot whose right-hand bars are not all closed yet (the pivot at 29 needs bars 30 and 31) is not a level for bar 30.
	late := flatBars(40)
	setBar(late, 29, 98.0, 99.8)
	setBar(late, impulseBar, 98.0, 99.0) // equal to the pivot: not through it
	both(t, sweepStrategy(6, 2, 96), late, nil, impulseBar, func(t *testing.T, buy bool, got sweep) {
		if got.Found {
			t.Fatalf("a pivot not yet confirmed at the sweeping bar is not a level: %+v", got)
		}
	})
}

func TestThePreviousDaysLowIsASweepOnceItIsFinalized(t *testing.T) {
	bars := flatBars(40)
	setBar(bars, impulseBar-1, 98.9, 99.7)
	open := bars[impulseBar-1].Time
	for _, tc := range []struct {
		name string
		at   int64
		want bool
	}{{"available at the open of the sweeping bar", open, true}, {"available a second later", open + 1, false}, {"no finalization time", 0, false}} {
		t.Run(tc.name, func(t *testing.T) {
			ref := []techniquezone.SessionRef{{Name: "PDL", Price: 99.0, AvailableAt: tc.at}}
			both(t, sweepStrategy(6, 2, 96), bars, ref, impulseBar, func(t *testing.T, buy bool, got sweep) {
				wantKind := "pdl"
				if !buy {
					wantKind = "pdh"
				}
				if got.Found != tc.want || got.Found && (got.Kind != wantKind || got.Depth < 0.0999 || got.Depth > 0.1001) {
					t.Fatalf("found %v want %v: %+v", got.Found, tc.want, got)
				}
			})
		})
	}
}

func TestOnlyAsiaAndLondonExtremesAreSessionLevels(t *testing.T) {
	bars := flatBars(40)
	setBar(bars, impulseBar-1, 98.9, 99.7)
	for name, want := range map[string]bool{"ASIA_L": true, "LONDON_L": true, "NY_L": false, "PWL": false, "DAY_L": false, "ASIA_H": false} {
		ref := []techniquezone.SessionRef{{Name: name, Price: 99.0, AvailableAt: sweepBase}}
		s := sweepStrategy(6, 2, 96)
		got := s.priorSweep(sweepFrame(bars, ref...), impulseBar, true, 1)
		if got.Found != want || got.Found && got.Kind != "session" {
			t.Errorf("%s: %+v, want found %v as a session level", name, got, want)
		}
	}
}

func TestOnOneBarThePreviousDayOutranksASessionWhichOutranksASwing(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar-2, 97.5, 98.6)
	pdl := techniquezone.SessionRef{Name: "PDL", Price: 98.5, AvailableAt: sweepBase}
	asia := techniquezone.SessionRef{Name: "ASIA_L", Price: 98.7, AvailableAt: sweepBase}
	for _, tc := range []struct {
		refs []techniquezone.SessionRef
		want string
	}{{nil, "swing"}, {[]techniquezone.SessionRef{asia}, "session"}, {[]techniquezone.SessionRef{asia, pdl}, "pdl"}} {
		got := sweepStrategy(6, 2, 96).priorSweep(sweepFrame(bars, tc.refs...), impulseBar, true, 1)
		if !got.Found || got.Kind != tc.want {
			t.Errorf("refs %v: %+v, want %s", tc.refs, got, tc.want)
		}
	}
}

func TestTheEarliestSweepingBarDecides(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar-4, 97.9, 98.7) // sweeps the swing
	setBar(bars, impulseBar-1, 96.0, 98.7) // a deeper one, later
	ref := []techniquezone.SessionRef{{Name: "PDL", Price: 97.0, AvailableAt: sweepBase}}
	got := sweepStrategy(6, 2, 96).priorSweep(sweepFrame(bars, ref...), impulseBar, true, 1)
	if !got.Found || got.Bar != impulseBar-4 || got.Kind != "swing" {
		t.Fatalf("the first sweeping bar decides, not the deepest: %+v", got)
	}
}

func TestTheSweepIsDecidedWithoutAnyLaterBar(t *testing.T) {
	bars := swingFixture()
	setBar(bars, impulseBar-2, 97.5, 98.6)
	s := sweepStrategy(6, 2, 96)
	whole := s.priorSweep(sweepFrame(bars), impulseBar, true, 1)
	// The gap's frame ends two bars after the impulse's first candle at the earliest; cut there, and then change everything after.
	cut := append([]market.Candle(nil), bars[:impulseBar+3]...)
	changed := append(cut, flatBars(10)...)
	for i := impulseBar + 3; i < len(changed); i++ {
		changed[i].Low, changed[i].High = 50, 150
	}
	if got := s.priorSweep(sweepFrame(cut), impulseBar, true, 1); got != whole {
		t.Fatalf("cut: %+v, whole: %+v", got, whole)
	}
	if got := s.priorSweep(sweepFrame(changed), impulseBar, true, 1); got != whole {
		t.Fatalf("bars after the impulse changed the sweep: %+v vs %+v", got, whole)
	}
}
