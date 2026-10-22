package crt

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func buyCRT() Setup {
	return Setup{
		Direction: market.Buy,
		Anchor:    Anchor{Low: 100, High: 104, RangeATR: 2.0},
		Sweep:     Sweep{DepthATR: 0.5},
		Reclaim:   Reclaim{DepthATR: 0.5, BarsAfterSweep: 2},
		Shift:     &StructureShift{DisplacementATR: 1.2, BarsAfterReclaim: 2},
		EntryLow:  100.3, EntryHigh: 100.8, Stop: 99.2, Target: 104, M5ATR: 1,
	}
}

func scoreCRT(t *testing.T, setup Setup, qc qualityContext) (float64, map[string]float64) {
	t.Helper()
	m := quality.New(QualityModel)
	(&Strategy{}).score(m, setup, qc)
	if err := quality.Verify(m.Overall(), m.Components()); err != nil {
		t.Fatal(err)
	}
	return m.Overall(), m.Components()
}

var goodMarket = qualityContext{price: 100.9, localTrend: "up", htfAligned: true}

func TestAClearManipulationReclaimAndShiftOutscoresAShallowWickThatBarelyReclaims(t *testing.T) {
	strong := buyCRT()

	medium := buyCRT()
	medium.Sweep.DepthATR = 0.12
	medium.Reclaim = Reclaim{DepthATR: 0.2, BarsAfterSweep: 6}
	medium.Shift = &StructureShift{DisplacementATR: 0.5, BarsAfterReclaim: 0} // the reclaim candle is the shift candle
	medium.Anchor.RangeATR = 1.0
	medium.EntryLow, medium.EntryHigh, medium.Target = 101.5, 102.0, 103.0

	weak := buyCRT()
	weak.Sweep.DepthATR = 0.02
	weak.Reclaim = Reclaim{DepthATR: 0.02, BarsAfterSweep: 11}
	weak.Shift = nil
	weak.Anchor.RangeATR = 0.4
	weak.EntryLow, weak.EntryHigh, weak.Target = 103.2, 103.6, 103.9 // at the wrong end of the range, no room

	s, _ := scoreCRT(t, strong, goodMarket)
	m, _ := scoreCRT(t, medium, qualityContext{price: 102.5, localTrend: "range"})
	w, wc := scoreCRT(t, weak, qualityContext{price: 104.5, localTrend: "down"})
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.2 || m > 0.65 || w > 0.15 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestTheSweepIsRewardedOnceAndTheShiftCandleOnce(t *testing.T) {
	_, base := scoreCRT(t, buyCRT(), goodMarket)
	deeper := buyCRT()
	deeper.Sweep.DepthATR = 0.1
	_, after := scoreCRT(t, deeper, goodMarket)
	for name, v := range base {
		if len(name) > 6 && name[:6] == "score_" && name != "score_sweep_depth" && math.Abs(after[name]-v) > 1e-12 {
			t.Errorf("the sweep depth moved %s", name)
		}
	}
	// A shift that is the reclaim candle itself is one confirmation, not two: half credit.
	same, later := buyCRT(), buyCRT()
	same.Shift = &StructureShift{DisplacementATR: 1.2, BarsAfterReclaim: 0}
	_, sc := scoreCRT(t, same, goodMarket)
	_, lc := scoreCRT(t, later, goodMarket)
	if math.Abs(sc["score_structure_shift"]*2-lc["score_structure_shift"]) > 1e-12 {
		t.Fatalf("shift on the reclaim candle %v vs a later independent shift %v", sc["score_structure_shift"], lc["score_structure_shift"])
	}
	none := buyCRT()
	none.Shift = nil
	if _, nc := scoreCRT(t, none, goodMarket); nc["score_structure_shift"] != 0 {
		t.Fatalf("no shift is no credit: %v", nc["score_structure_shift"])
	}
}

func TestACrtOnTheWrongEndOfTheRangeOrWithoutRoomScoresLow(t *testing.T) {
	in := buyCRT()
	in.EntryLow, in.EntryHigh = 103.0, 103.5 // a buy near the top of the range
	_, c := scoreCRT(t, in, goodMarket)
	if c["score_range_position"] != 0 {
		t.Fatalf("a buy at the top of the range has no range thesis: %v", c["score_range_position"])
	}
	flat := buyCRT()
	flat.Target = flat.EntryHigh + 0.1
	if _, c := scoreCRT(t, flat, goodMarket); c["score_room"] != 0 {
		t.Fatalf("no room scored: %v", c["score_room"])
	}
}

func TestSellMirrorsBuyOnAnyPriceScale(t *testing.T) {
	_, buy := scoreCRT(t, buyCRT(), goodMarket)
	sell := Setup{
		Direction: market.Sell,
		Anchor:    Anchor{Low: -104, High: -100, RangeATR: 2.0}, // the mirror image, negated prices are only a test device for symmetry
		Sweep:     Sweep{DepthATR: 0.5}, Reclaim: Reclaim{DepthATR: 0.5, BarsAfterSweep: 2},
		Shift:    &StructureShift{DisplacementATR: 1.2, BarsAfterReclaim: 2},
		EntryLow: -100.8, EntryHigh: -100.3, Stop: -99.2, Target: -104, M5ATR: 1,
	}
	_, got := scoreCRT(t, sell, qualityContext{price: -100.9, localTrend: "down", htfAligned: true})
	for name, want := range buy {
		if len(name) > 6 && name[:6] == "score_" && math.Abs(got[name]-want) > 1e-9 {
			t.Errorf("sell %s = %v, buy %v", name, got[name], want)
		}
	}
}
