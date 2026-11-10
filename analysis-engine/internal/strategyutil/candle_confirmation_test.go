package strategyutil

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func TestRequireCandleConfirmationDefaultsToRequired(t *testing.T) {
	cases := []struct {
		name   string
		params map[string]any
		want   bool
	}{
		{"absent", map[string]any{}, true},
		{"nil map", nil, true},
		{"true", map[string]any{"require_candle_confirmation": true}, true},
		{"false", map[string]any{"require_candle_confirmation": false}, false},
		{"not a bool is not a switch", map[string]any{"require_candle_confirmation": "false"}, true},
	}
	for _, c := range cases {
		if got := RequireCandleConfirmation(c.params); got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}

func bar(i int, o, h, l, c float64) market.Candle {
	return market.Candle{Time: int64(i) * 300, Open: o, High: h, Low: l, Close: c}
}

// A bullish break of level 100 at index 0, two accepting closes, then a first retest candle into the band [99.9, 100.2] that closes ABOVE the level, bearish and
// with no lower wick: it enters the band and is no rejection (no wick, not an engulfing candle).
func retestPath() []market.Candle {
	return []market.Candle{
		bar(0, 99.8, 100.6, 99.8, 100.5),
		bar(1, 100.5, 100.8, 100.4, 100.7),
		bar(2, 100.7, 100.9, 100.6, 100.8),
		bar(3, 100.6, 100.62, 100.15, 100.16), // first retest candle: bearish, high close not above the band top
	}
}

var retestParams = LevelRetestParams{PipSize: 0.1, AcceptanceCloses: 2, RetestMaxBars: 6, BandBelow: 0.1, BandAbove: 0.2, MinWickRatio: 0.4}

func TestAFirstRetestWithoutRejectionNeedsOptionalConfirmation(t *testing.T) {
	bars := retestPath()
	if _, reason, ok := FindLevelRetest(bars, 100, 0, retestParams); ok || reason != "" {
		t.Fatalf("required: ok=%v reason=%q, want the retest still pending (it may reject on the next candle)", ok, reason)
	}
	// The next candle does not reject either: the retest is refused, as before.
	required := append(append([]market.Candle(nil), bars...), bar(4, 100.2, 100.3, 100.1, 100.12))
	if _, reason, ok := FindLevelRetest(required, 100, 0, retestParams); ok || reason != LevelReasonRetestNotRejected {
		t.Fatalf("required, two candles: ok=%v reason=%q, want %s", ok, reason, LevelReasonRetestNotRejected)
	}
	p := retestParams
	p.CandleConfirmationOptional = true
	got, reason, ok := FindLevelRetest(bars, 100, 0, p)
	if !ok || reason != "" {
		t.Fatalf("optional: ok=%v reason=%q, want the first retest candle to be the trigger", ok, reason)
	}
	if got.Retest != 3 || got.Confirm != 3 {
		t.Errorf("optional: retest %d confirm %d, want both on the first retest candle (3)", got.Retest, got.Confirm)
	}
	if got.WickRejection || got.Engulfing {
		t.Errorf("optional: WickRejection=%v Engulfing=%v for a candle that rejected nothing: confirmation must never be made up", got.WickRejection, got.Engulfing)
	}
}

func TestARealRejectionIsStillReportedWhenConfirmationIsOptional(t *testing.T) {
	bars := retestPath()
	bars[3] = bar(3, 100.2, 100.5, 99.9, 100.45) // a lower wick through the band and a close above its top
	for _, optional := range []bool{false, true} {
		p := retestParams
		p.CandleConfirmationOptional = optional
		got, _, ok := FindLevelRetest(bars, 100, 0, p)
		if !ok || !got.WickRejection {
			t.Errorf("optional=%v: ok=%v WickRejection=%v, want the wick rejection reported", optional, ok, got.WickRejection)
		}
	}
}

func TestTouchM1ExecutionIsATouchNotAConfirmation(t *testing.T) {
	// BUY at level 100, tolerance 0.05: the latest bar is bearish (no directional confirmation) but wicked into the level and closed above it.
	m1 := []market.Candle{bar(0, 101, 101.2, 100.9, 101.1), bar(1, 100.3, 100.4, 99.98, 100.1)}
	if _, ok := ConfirmM1Execution(m1, market.Buy, 100, 0.05, 2); ok {
		t.Fatal("a bearish candle must not confirm a BUY")
	}
	got, ok := TouchM1Execution(m1, market.Buy, 100, 0.05, 2)
	if !ok || got.Time != m1[1].Time {
		t.Fatalf("TouchM1Execution = %+v, %v; want the latest interacting bar", got, ok)
	}
	if _, ok := TouchM1Execution(m1[:1], market.Buy, 100, 0.05, 1); ok {
		t.Error("a bar that never reached the level is not a touch")
	}
	// A bar that closes through the level to the wrong side is not a touch.
	if _, ok := TouchM1Execution([]market.Candle{bar(0, 100.3, 100.4, 99.9, 99.95)}, market.Buy, 100, 0.05, 1); ok {
		t.Error("a close below the level is not a touch of support")
	}
}
