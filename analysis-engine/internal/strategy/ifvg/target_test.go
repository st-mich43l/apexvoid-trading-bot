package ifvg

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// A pivot formed AFTER the rejection must not move the target of a setup that was
// already published: the same reaction would otherwise be re-targeted (and could be
// refused) a few candles after it was shown.
func TestATargetPivotConfirmedAfterTheRejectionDoesNotMoveThePublishedTarget(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	cfg := testConfig()
	cfg.ConfirmationMaxAge = 8
	atConfirm := Detect(cfg, bars)
	if len(atConfirm.Setups) != 1 {
		t.Fatalf("setups at the confirmation = %d", len(atConfirm.Setups))
	}
	want := atConfirm.Setups[0].Target
	last := bars[m.confirm]
	// A small peak well inside the room, confirmed two candles later, and nothing else.
	extended := append([]market.Candle(nil), bars...)
	at := last.Time
	for _, c := range [][4]float64{
		{last.Close, last.Close + 5.5, last.Close - 0.2, last.Close + 5.0},
		{last.Close + 5.0, last.Close + 5.2, last.Close + 3.2, last.Close + 3.4},
		{last.Close + 3.4, last.Close + 3.6, last.Close + 2.4, last.Close + 2.8},
		{last.Close + 2.8, last.Close + 3.0, last.Close + 2.4, last.Close + 2.7},
	} {
		at += 300
		extended = append(extended, market.Candle{Time: at, Open: c[0], High: c[1], Low: c[2], Close: c[3]})
	}
	later := Detect(cfg, extended)
	if len(later.Setups) != 1 {
		t.Fatalf("setups after the later peak = %d: %+v", len(later.Setups), later.Episodes)
	}
	if got := later.Setups[0].Target; got != want {
		t.Fatalf("a pivot confirmed after the rejection moved the target from %v to %v", want, got)
	}
}
