package strategyutil

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// bars are 0:setup 1:touch 2:middle 3:confirmation 4:after; only the lows/highs matter.
func reactionBars() []market.Candle {
	return []market.Candle{
		{Time: 0, High: 105, Low: 103},
		{Time: 1, High: 104, Low: 100.4}, // touch
		{Time: 2, High: 104.5, Low: 99.6},
		{Time: 3, High: 106, Low: 101}, // confirmation
		{Time: 4, High: 107, Low: 90},  // after the confirmation: must never be read
	}
}

func TestReactionAnchoredInvalidationBuy(t *testing.T) {
	bars := reactionBars()
	conf := &LegacyConfirmation{TouchIndex: 1, ConfirmationIndex: 3}
	cases := []struct {
		name         string
		conf         *LegacyConfirmation
		structLow    float64
		want         float64
		wantAnchored bool
	}{
		{"reaction extreme beyond the structure anchors", conf, 100, 99.6 - 0.5, true},
		{"reaction extreme inside the structure keeps the structure", conf, 99, 99 - 0.5, false},
		{"extreme exactly at the structure is not anchored", conf, 99.6, 99.6 - 0.5, false},
		{"touch equals confirmation reads that one bar", &LegacyConfirmation{TouchIndex: 3, ConfirmationIndex: 3}, 102, 101 - 0.5, true},
		{"touch before confirmation reads every bar in between", &LegacyConfirmation{TouchIndex: 1, ConfirmationIndex: 1}, 101, 100.4 - 0.5, true},
		{"nil confirmation is unchanged", nil, 100, 100 - 0.5, false},
		{"touch after confirmation is missing evidence", &LegacyConfirmation{TouchIndex: 3, ConfirmationIndex: 1}, 100, 100 - 0.5, false},
		{"negative touch index is missing evidence", &LegacyConfirmation{TouchIndex: -1, ConfirmationIndex: 3}, 100, 100 - 0.5, false},
		{"confirmation outside the bars is missing evidence", &LegacyConfirmation{TouchIndex: 1, ConfirmationIndex: 9}, 100, 100 - 0.5, false},
	}
	for _, c := range cases {
		got, anchored := ReactionAnchoredInvalidation(bars, c.conf, market.Buy, c.structLow, 1e9, 0.5)
		if math.Abs(got-c.want) > 1e-9 || anchored != c.wantAnchored {
			t.Errorf("%s: got %v anchored=%v, want %v anchored=%v", c.name, got, anchored, c.want, c.wantAnchored)
		}
	}
}

func TestReactionAnchoredInvalidationSell(t *testing.T) {
	bars := reactionBars()
	conf := &LegacyConfirmation{TouchIndex: 1, ConfirmationIndex: 3}
	cases := []struct {
		name         string
		conf         *LegacyConfirmation
		structHigh   float64
		want         float64
		wantAnchored bool
	}{
		{"reaction extreme beyond the structure anchors", conf, 105, 106 + 0.5, true},
		{"reaction extreme inside the structure keeps the structure", conf, 108, 108 + 0.5, false},
		{"extreme exactly at the structure is not anchored", conf, 106, 106 + 0.5, false},
		{"touch equals confirmation reads that one bar", &LegacyConfirmation{TouchIndex: 3, ConfirmationIndex: 3}, 105, 106 + 0.5, true},
		{"nil confirmation is unchanged", nil, 105, 105 + 0.5, false},
	}
	for _, c := range cases {
		got, anchored := ReactionAnchoredInvalidation(bars, c.conf, market.Sell, -1e9, c.structHigh, 0.5)
		if math.Abs(got-c.want) > 1e-9 || anchored != c.wantAnchored {
			t.Errorf("%s: got %v anchored=%v, want %v anchored=%v", c.name, got, anchored, c.want, c.wantAnchored)
		}
	}
}

func TestReactionAnchoredInvalidationNeverReadsALaterBar(t *testing.T) {
	bars := reactionBars()
	conf := &LegacyConfirmation{TouchIndex: 1, ConfirmationIndex: 3}
	want, _ := ReactionAnchoredInvalidation(bars, conf, market.Buy, 100, 1e9, 0.5)
	bars[4] = market.Candle{Time: 4, High: 500, Low: 1}
	got, _ := ReactionAnchoredInvalidation(bars, conf, market.Buy, 100, 1e9, 0.5)
	if got != want {
		t.Fatalf("a bar after the confirmation changed the invalidation: %v -> %v", want, got)
	}
}

func TestReactionAnchoredInvalidationIgnoresANonFiniteExtreme(t *testing.T) {
	bars := []market.Candle{{Low: math.NaN(), High: math.NaN()}}
	conf := &LegacyConfirmation{TouchIndex: 0, ConfirmationIndex: 0}
	if got, anchored := ReactionAnchoredInvalidation(bars, conf, market.Buy, 100, 1e9, 0.5); got != 99.5 || anchored {
		t.Fatalf("got %v anchored=%v", got, anchored)
	}
}
