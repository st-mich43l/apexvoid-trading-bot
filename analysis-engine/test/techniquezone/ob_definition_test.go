package techniquezone_test

import (
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// An order block is a block until a CLOSE of its own frame goes through its far edge (docs/strategies/order-block-definition.md, rule A): a wick that closes
// back inside is the sweep, a close beyond the edge by more than the buffer is the break, and nothing the next bar closes forgives it.

func obBar(i int, open, high, low, close float64) market.Candle {
	return market.Candle{Time: int64(i) * 300, Open: open, High: high, Low: low, Close: close}
}

// demandBlock is a bearish candle (bars[12], body 100-101) before a rally to 106, the retracement, and a final bar the caller shapes. The block is 100-101.
func demandBlock(tail ...market.Candle) []market.Candle {
	var bars []market.Candle
	for i := 0; i < 12; i++ {
		bars = append(bars, obBar(i, 102, 102.4, 101.6, 102))
	}
	bars = append(bars, obBar(12, 101, 101.1, 99.9, 100)) // the block: last down candle before the leg
	bars = append(bars, obBar(13, 100, 102.5, 99.95, 102.4), obBar(14, 102.4, 104.5, 102.3, 104.4), obBar(15, 104.4, 106.2, 104.3, 106),
		obBar(16, 106, 106.1, 104.2, 104.5), obBar(17, 104.5, 104.6, 102.8, 103), obBar(18, 103, 103.1, 101.9, 102), obBar(19, 102, 102.1, 101.2, 101.5))
	for _, b := range tail {
		b.Time = int64(len(bars)) * 300
		bars = append(bars, b)
	}
	return bars
}

func demandZone() techniquezone.Zone {
	return techniquezone.Zone{Bottom: 100, Top: 101, Side: "demand", OriginIndex: 12, Source: "order_block", Sources: []string{"order_block"}, BreakKind: "BOS", BreakIndex: 15}
}

func atrOf(n int, v float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

func withBuffer(buffer float64) techniquezone.TechniqueSettings {
	s := techniquezone.ProductionTechniqueSettings()
	s.OBBreakBufferATR = buffer
	s.IgnoreProximity = true // judged as a fact, like a higher frame's block: where price is now is not the question here
	return s
}

func collectOB(bars []market.Candle, zone techniquezone.Zone, atr float64, series []float64, s techniquezone.TechniqueSettings) (instances []techniquezone.Instance, reasons []string) {
	last := bars[len(bars)-1].Close
	got := techniquezone.CollectInstancesWithATR(nil, []techniquezone.Zone{zone}, nil, bars, last, atr, series, s, func(in techniquezone.Instance, reason string) { reasons = append(reasons, reason) })
	return got, reasons
}

func TestARealBlockThatNoCloseBrokeIsOffered(t *testing.T) {
	bars := demandBlock(obBar(20, 101.5, 101.6, 100.4, 100.6))
	got, reasons := collectOB(bars, demandZone(), 1, atrOf(len(bars), 1), withBuffer(0.1))
	if len(got) != 1 || len(reasons) != 0 {
		t.Fatalf("an unbroken block must be offered: %d instances, refused %v", len(got), reasons)
	}
}

func TestAWickBelowTheEdgeThatClosesBackInsideIsTheSweepAndDoesNotRetire(t *testing.T) {
	bars := demandBlock(obBar(20, 101.5, 101.6, 99.2, 100.6)) // low 0.8 below the block, close inside
	got, reasons := collectOB(bars, demandZone(), 1, atrOf(len(bars), 1), withBuffer(0.1))
	if len(got) != 1 || len(reasons) != 0 {
		t.Fatalf("a sweep wick is not a break: %d instances, refused %v", len(got), reasons)
	}
}

func TestACloseThroughRetiresTheBlockEvenWhenTheNextBarClosesBackInside(t *testing.T) {
	// close 99.0, a full ATR below the far edge, then back inside at 100.5: the old judgement forgives it as a sweep-and-reclaim
	bars := demandBlock(obBar(20, 101.5, 101.6, 98.9, 99.0), obBar(21, 99.0, 100.9, 98.95, 100.5))
	series := atrOf(len(bars), 1)
	if off, _ := collectOB(bars, demandZone(), 1, series, withBuffer(0)); len(off) != 1 {
		t.Fatalf("with the rule off the shared lifecycle forgives the reclaimed break (frozen behaviour): %d instances", len(off))
	}
	on, reasons := collectOB(bars, demandZone(), 1, series, withBuffer(0.1))
	if len(on) != 0 || len(reasons) != 1 || reasons[0] != techniquezone.OBRefusedClosedThrough {
		t.Fatalf("a close through the edge must retire the block for good: %d instances, refused %v", len(on), reasons)
	}
}

func TestACloseWithinTheBufferIsNotABreak(t *testing.T) {
	bars := demandBlock(obBar(20, 101.5, 101.6, 99.8, 99.95)) // 0.05 below the low
	if got, reasons := collectOB(bars, demandZone(), 1, atrOf(len(bars), 1), withBuffer(0.1)); len(got) != 1 || len(reasons) != 0 {
		t.Fatalf("0.05 ATR is inside a 0.1 ATR buffer: %d instances, refused %v", len(got), reasons)
	}
	if got, _ := collectOB(bars, demandZone(), 1, atrOf(len(bars), 1), withBuffer(0.04)); len(got) != 0 {
		t.Fatal("0.05 ATR is outside a 0.04 ATR buffer")
	}
}

func TestTheBufferIsTheFramesATRAtTheBreakingBarNotTheCurrentOne(t *testing.T) {
	bars := demandBlock(obBar(20, 101.5, 101.6, 99.7, 99.8), obBar(21, 99.8, 101.0, 99.85, 100.6))
	series := atrOf(len(bars), 1)
	if got, _ := collectOB(bars, demandZone(), 1, series, withBuffer(0.1)); len(got) != 0 {
		t.Fatal("0.2 below the low with a 0.1 ATR buffer on ATR 1 is a break")
	}
	// the same close on a bar whose own ATR was 4 (margin 0.4) is not, whatever the current ATR is
	series[20] = 4
	if got, _ := collectOB(bars, demandZone(), 1, series, withBuffer(0.1)); len(got) != 1 {
		t.Fatal("the margin must come from the ATR at the closing bar")
	}
	// without an aligned series the scalar ATR stands in
	if got, _ := collectOB(bars, demandZone(), 1, nil, withBuffer(0.1)); len(got) != 0 {
		t.Fatal("without a series the scalar ATR is used")
	}
}

func TestASupplyBlockIsJudgedOnItsHighAndAClippedBlockOnItsStructuralEdge(t *testing.T) {
	// mirror: a supply block 100-101, a close above 101 + buffer retires it, a close below it does not
	var bars []market.Candle
	for i := 0; i < 12; i++ {
		bars = append(bars, obBar(i, 99, 99.4, 98.6, 99))
	}
	bars = append(bars, obBar(12, 100, 101.1, 99.9, 101), obBar(13, 101, 101.05, 98.0, 98.2), obBar(14, 98.2, 98.3, 96.0, 96.1), obBar(15, 96.1, 96.2, 94.0, 94.2),
		obBar(16, 94.2, 96.5, 94.1, 96.0), obBar(17, 96, 98.5, 95.9, 98.0), obBar(18, 98, 100.2, 97.9, 100.0), obBar(19, 100, 100.6, 99.5, 100.4))
	zone := techniquezone.Zone{Bottom: 100, Top: 101, Side: "supply", OriginIndex: 12, Source: "order_block", Sources: []string{"order_block"}, BreakKind: "BOS", BreakIndex: 15}
	if got, reasons := collectOB(bars, zone, 1, atrOf(len(bars), 1), withBuffer(0.1)); len(got) != 1 || len(reasons) != 0 {
		t.Fatalf("an unbroken supply block must be offered: %d, %v", len(got), reasons)
	}
	broken := append(append([]market.Candle{}, bars...), obBar(20, 100.4, 101.9, 100.3, 101.5))
	if got, reasons := collectOB(broken, zone, 1, atrOf(len(broken), 1), withBuffer(0.1)); len(got) != 0 || len(reasons) != 1 {
		t.Fatalf("a close above the supply high must retire it: %d, %v", len(got), reasons)
	}

	// clipped: a 6 wide demand block (100-106) is entered on its proximal 5 (101-106) but its far edge stays 100
	var tall []market.Candle
	for i := 0; i < 12; i++ {
		tall = append(tall, obBar(i, 108, 108.4, 107.6, 108))
	}
	tall = append(tall, obBar(12, 106, 106.1, 99.9, 100), obBar(13, 100, 109, 99.95, 108.8), obBar(14, 108.8, 112, 108.7, 111.8), obBar(15, 111.8, 114, 111.7, 113.9),
		obBar(16, 113.9, 114, 110, 110.5), obBar(17, 110.5, 110.6, 107.5, 108), obBar(18, 108, 108.1, 104, 105), obBar(19, 105, 105.1, 102.5, 103))
	wide := techniquezone.Zone{Bottom: 100, Top: 106, Side: "demand", OriginIndex: 12, Source: "order_block", Sources: []string{"order_block"}, BreakKind: "BOS", BreakIndex: 15}
	series := atrOf(len(tall)+1, 2.5)
	inside := append(append([]market.Candle{}, tall...), obBar(20, 103, 103.2, 100.4, 100.6)) // below the clipped low 101, above the structural low 100
	got, _ := collectOB(inside, wide, 2.5, series[:len(inside)], withBuffer(0.1))
	if len(got) != 1 || !got[0].EntryClipped || got[0].StructuralLow != 100 {
		t.Fatalf("a close between the clipped edge and the structural one does not break the block: %+v", got)
	}
	through := append(append([]market.Candle{}, tall...), obBar(20, 103, 103.2, 99.0, 99.2))
	if got, _ := collectOB(through, wide, 2.5, series[:len(through)], withBuffer(0.1)); len(got) != 0 {
		t.Fatal("a close below the structural far edge must retire a clipped block")
	}
}

func TestTheOrderBlockRuleChangesNoOtherTechnique(t *testing.T) {
	// A supply/demand zone and a fair value gap that price closed straight through: judged exactly the same with the order block rule on or off.
	bars := demandBlock(obBar(20, 101.5, 101.6, 98.9, 99.0), obBar(21, 99.0, 100.9, 98.95, 100.5))
	sd := techniquezone.Zone{Bottom: 100, Top: 101, Side: "demand", OriginIndex: 12, Source: "supply_demand", Sources: []string{"supply_demand"}, BreakIndex: -1}
	fvg := techniquezone.Zone{Bottom: 100.2, Top: 100.9, Side: "demand", OriginIndex: 14, Source: "bullish_fvg", Sources: []string{"bullish_fvg"}, BreakIndex: -1}
	collect := func(buffer float64) []techniquezone.Instance {
		return techniquezone.CollectInstancesWithATR([]techniquezone.Zone{sd}, []techniquezone.Zone{demandZone()}, []techniquezone.Zone{fvg}, bars, 100.5, 1, atrOf(len(bars), 1), withBuffer(buffer), nil)
	}
	nonOB := func(in []techniquezone.Instance) []techniquezone.Instance {
		var out []techniquezone.Instance
		for _, i := range in {
			if i.Technique != "order_block" {
				out = append(out, i)
			}
		}
		return out
	}
	off, on := nonOB(collect(0)), nonOB(collect(0.1))
	if !reflect.DeepEqual(off, on) {
		t.Fatalf("supply_demand/fvg/ifvg instances changed with the order block key set:\n off %+v\n on  %+v", off, on)
	}
	if len(collect(0)) == len(collect(0.1)) {
		t.Fatal("the order block itself must differ, or this test proves nothing")
	}
}
