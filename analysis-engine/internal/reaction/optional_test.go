package reaction

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// bars returns n quiet candles well above a BUY band [100, 101] followed by the given ones.
func barsAbove(n int, tail ...market.Candle) []market.Candle {
	var out []market.Candle
	for i := 0; i < n; i++ {
		out = append(out, market.Candle{Time: int64(i) * 300, Open: 103, High: 103.2, Low: 102.8, Close: 103.1})
	}
	for i, c := range tail {
		c.Time = int64(n+i) * 300
		out = append(out, c)
	}
	return out
}

// A bearish candle that dips into the band and closes at its low-ish middle: it touches, and it is no wick rejection, reclaim or engulfing candle.
var touchOnly = market.Candle{Open: 101.3, High: 101.5, Low: 100.95, Close: 101.0}

func TestATouchWithoutAConfirmingCandleIsRefusedUnlessConfirmationIsOptional(t *testing.T) {
	candles := barsAbove(5, touchOnly)
	p := Params{Direction: "BUY", Low: 100, High: 101, TouchLookback: 3}
	if got := Evaluate(candles, p); got != nil {
		t.Fatalf("required: got %+v, want no confirmation", *got)
	}
	p.AllowUnconfirmed = true
	got := Evaluate(candles, p)
	if got == nil {
		t.Fatal("optional: a touch of the band must be returned")
	}
	if got.Type != "" || got.TouchIndex != 5 || got.ConfirmationIndex != 5 {
		t.Errorf("optional: got %+v, want an unconfirmed touch (Type \"\") of the latest touch bar", *got)
	}
}

func TestAPatternIsStillReportedWhenConfirmationIsOptional(t *testing.T) {
	// A hammer through the band: lower wick >= body, bullish, closes in the upper third. The option must not downgrade it to a bare touch.
	hammer := market.Candle{Open: 100.8, High: 101.1, Low: 100.2, Close: 101.0}
	candles := barsAbove(5, hammer)
	for _, optional := range []bool{false, true} {
		got := Evaluate(candles, Params{Direction: "BUY", Low: 100, High: 101, TouchLookback: 3, AllowUnconfirmed: optional})
		if got == nil || got.Type != TypeWickRejection {
			t.Errorf("AllowUnconfirmed=%v: got %+v, want %s", optional, got, TypeWickRejection)
		}
	}
}

func TestNoTouchIsNeverInventedWhenConfirmationIsOptional(t *testing.T) {
	candles := barsAbove(6)
	if got := Evaluate(candles, Params{Direction: "BUY", Low: 100, High: 101, TouchLookback: 3, AllowUnconfirmed: true}); got != nil {
		t.Errorf("price never reached the band: got %+v, want nil", *got)
	}
}
