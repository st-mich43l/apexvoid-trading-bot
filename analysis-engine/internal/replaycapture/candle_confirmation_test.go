package replaycapture_test

// require_candle_confirmation on the real EURUSD Trendline fixture of trendline_mtf_test.go: the second anchor's own candle is replaced by a candle that touches the line
// and closes at its high (no rejection), and the two bars that confirm the pivot are small bullish candles under it. The structure is the same line; only the candles
// that could confirm it are changed.

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// noConfirmationM15 is the fixture with the second anchor and the two bars after it rewritten as: touch the line, close at the high, then step down away from it
// in small candles that form no pattern.
func noConfirmationM15(t *testing.T) [][]float64 {
	s := load(t)
	i := indexOf(s.M15, s.B)
	out := make([][]float64, len(s.M15))
	for k, r := range s.M15 {
		out[k] = append([]float64(nil), r...)
	}
	set := func(k int, o, h, l, c float64) { out[k] = []float64{out[k][0], o, h, l, c} }
	set(i, 1.11950, 1.11977, 1.11947, 1.11976)
	set(i+1, 1.11935, 1.11938, 1.11920, 1.11925)
	set(i+2, 1.11925, 1.11935, 1.11915, 1.11930)
	return out[:i+3]
}

func replayNoConfirmation(t *testing.T, required bool) []opportunity.Candidate {
	s := load(t)
	c := capture(map[string][][]float64{"M15": noConfirmationM15(t), "H1": s.H1})
	got, _ := replayWithOptions(t, c, market.M15, // confluence_floor 1 keeps the star floor (a quality gate the option does not touch, and which a touch with no rejection wick may not clear) out of this
		// test of the confirmation requirement itself.
		map[string]any{"require_candle_confirmation": required, "confluence_floor": 1})
	return got
}

func TestRequiredConfirmationRefusesATouchWithoutAConfirmingCandle(t *testing.T) {
	if got := replayNoConfirmation(t, true); len(got) != 0 {
		t.Fatalf("require_candle_confirmation=true published %d opportunities (%s) with no confirming candle", len(got), ids(got))
	}
}

func TestOptionalConfirmationTradesTheTouchAndClaimsNoReaction(t *testing.T) {
	got := replayNoConfirmation(t, false)
	if len(got) == 0 {
		t.Fatal("require_candle_confirmation=false published nothing for a structurally valid second-anchor touch")
	}
	o := got[0]
	if o.Reaction != nil {
		t.Errorf("a touch with no confirming candle carries a reaction %+v: confirmation evidence must never be made up", *o.Reaction)
	}
	if !hasEvidence(o, "candle_confirmation_optional") {
		t.Errorf("evidence lacks candle_confirmation_optional")
	}
	for _, e := range o.Evidence {
		if strings.Contains(string(e.Code), "rejection_confirmed") {
			t.Errorf("evidence claims %q for a touch with no confirming candle", e.Code)
		}
	}
	if o.Direction != market.Sell {
		t.Errorf("direction %s, want SELL", o.Direction)
	}
}

func TestOptionalConfirmationKeepsAConfirmedSetupExactly(t *testing.T) {
	s := load(t)
	c := capture(map[string][][]float64{"M15": s.M15, "H1": s.H1})
	on, _ := replayWithOptions(t, c, market.M15, map[string]any{"require_candle_confirmation": true})
	off, _ := replayWithOptions(t, c, market.M15, map[string]any{"require_candle_confirmation": false})
	if len(on) != 1 || ids(on) != ids(off) {
		t.Fatalf("a setup with a real confirming candle changed with the option: true=%s false=%s", ids(on), ids(off))
	}
	if on[0].Reaction == nil || off[0].Reaction == nil || *on[0].Reaction != *off[0].Reaction {
		t.Errorf("reaction differs: true=%+v false=%+v", on[0].Reaction, off[0].Reaction)
	}
	if hasEvidence(off[0], "candle_confirmation_optional") {
		t.Errorf("a confirmed setup must not be marked candle_confirmation_optional")
	}
}
