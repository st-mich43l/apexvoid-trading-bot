package mad_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/mad"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func bar(ts int64, o, h, l, c float64) market.Candle {
	return market.Candle{Time: ts, Open: o, High: h, Low: l, Close: c}
}

func cfg() mad.Config {
	return mad.Config{AsiaStartHour: 22, LondonStartHour: 7, AccumMinimumRQ: .8, AccumMaximumRQ: 6, ExpandBreakATR: .35, ExpandDisplacementATR: 1.25, ExpandAcceptCloses: 2, ManipMinimumPenetrationATR: .05, ManipMinimumReclaimATR: .05, PipSize: .0001}
}

func TestClassifyAsiaSweepReclaimDirectionAndConfidence(t *testing.T) {
	c := cfg()
	a := &mad.AsiaRangeSeal{DayKey: "2026-10-01", High: 105, Low: 95, Sealed: true}
	got := mad.Classify([]market.Candle{bar(1790895600, 100, 106, 98, 103)}, 2, 2, "LONDON", "range", a, 1790895600, c)
	if got.Phase != mad.PhaseManip || got.SweepSide != "high" || !got.Reclaim || got.ManipulationDirection != "SELL" {
		t.Fatalf("unexpected manipulation snapshot: %+v", got)
	}
	if got.Confidence <= 0 {
		t.Fatal("sweep/reclaim must carry confidence")
	}
}

func TestClassifyDoubleSweepIsUnclear(t *testing.T) {
	c := cfg()
	a := &mad.AsiaRangeSeal{DayKey: "2026-10-01", High: 105, Low: 95, Sealed: true}
	got := mad.Classify([]market.Candle{bar(1790895600, 100, 107, 93, 100)}, 2, 2, "LONDON", "range", a, 1790895600, c)
	if got.Phase != mad.PhaseUnclear || got.SweepSide != "both" || got.Reclaim {
		t.Fatalf("double sweep must be neutral: %+v", got)
	}
}

func TestClassifyAcceptedExpansion(t *testing.T) {
	c := cfg()
	a := &mad.AsiaRangeSeal{DayKey: "2026-10-01", High: 105, Low: 95, Sealed: true}
	got := mad.Classify([]market.Candle{
		bar(1790895600, 105, 107, 104, 106),
		bar(1790895900, 106, 108, 105, 107),
	}, 1, 1, "LONDON", "trend", a, 1790895600, c)
	if got.Phase != mad.PhaseExpand || got.ExpansionDirection != "BUY" {
		t.Fatalf("expected accepted expansion: %+v", got)
	}
}

func TestStaleAsiaSealFailsClosed(t *testing.T) {
	c := cfg()
	a := &mad.AsiaRangeSeal{DayKey: "2026-09-30", High: 105, Low: 95, Sealed: true}
	got := mad.Classify([]market.Candle{bar(1790895600, 100, 101, 99, 100)}, 1, 1, "LONDON", "range", a, 1790895600, c)
	if got.Phase != mad.PhaseUnclear || got.ReasonCode != "asia_range_stale_or_missing" {
		t.Fatalf("stale seal must fail closed: %+v", got)
	}
}
