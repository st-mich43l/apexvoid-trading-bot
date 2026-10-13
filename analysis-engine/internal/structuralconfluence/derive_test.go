package structuralconfluence

import (
	"reflect"
	"strconv"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const base = int64(1_000_000) // open time of bar 0; one M5 bar is 300 s

func tm(i int) int64 { return base + int64(i)*300 }

// plainBars are n doji bars around 100 (no direction, so no leg or opposite
// candle is implied by them).
func plainBars(n int) []market.Candle {
	bars := make([]market.Candle, n)
	for i := range bars {
		bars[i] = market.Candle{Time: tm(i), Open: 100, High: 100.5, Low: 99.5, Close: 100}
	}
	return bars
}

func frameOf(bars []market.Candle, legs []techniquezone.Leg, breaks []techniquezone.Break, swings []techniquezone.Swing) Frame {
	return Frame{Bars: bars, Legs: legs, Breaks: breaks, Swings: swings, PeriodSeconds: 300}
}

func TestSupplyDemandIsAnchoredOnItsLeg(t *testing.T) {
	bars := plainBars(30)
	leg := techniquezone.Leg{Start: 10, End: 14, Direction: "up"}
	in := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Buy, Low: 98, High: 100, OriginIndex: 9}
	facts := DeriveFacts(frameOf(bars, []techniquezone.Leg{leg}, nil, nil), []techniquezone.Instance{in})
	if len(facts) != 1 || !facts[0].Verified {
		t.Fatalf("facts %+v", facts)
	}
	f := facts[0]
	if !reflect.DeepEqual(f.Atoms, []string{"leg:up:" + itoa(tm(10))}) {
		t.Fatalf("atoms %v", f.Atoms)
	}
	// The zone is confirmable once the leg that created it is complete: the close
	// of the leg's last candle.
	if f.AvailableAt != tm(14)+300 || f.FormedAt != tm(9) {
		t.Fatalf("available %d formed %d", f.AvailableAt, f.FormedAt)
	}
	if f.ID != "supply_demand:buy:"+itoa(tm(9))+":98.000000:100.000000" {
		t.Fatalf("id %s", f.ID)
	}
}

func TestSupplyDemandWithoutItsLegIsUnverified(t *testing.T) {
	in := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Buy, Low: 98, High: 100, OriginIndex: 9}
	facts := DeriveFacts(frameOf(plainBars(30), nil, nil, nil), []techniquezone.Instance{in})
	if len(facts) != 1 || facts[0].Verified || facts[0].UnverifiedReason != UnverifiedSupplyDemandLeg {
		t.Fatalf("a structure whose derivation cannot be reproduced must be unverified, got %+v", facts)
	}
}

func orderBlockFixture() ([]market.Candle, techniquezone.Leg) {
	bars := plainBars(30)
	// Bar 8 is the last down candle before the up leg 10..14 (bar 9 is a doji).
	bars[8] = market.Candle{Time: tm(8), Open: 101, High: 101.2, Low: 99, Close: 99.5}
	return bars, techniquezone.Leg{Start: 10, End: 14, Direction: "up"}
}

func TestOrderBlockSharesItsLegWithSupplyDemandAndWaitsForTheSwing(t *testing.T) {
	bars, leg := orderBlockFixture()
	swing := techniquezone.Swing{Index: 11, Kind: "high", Price: 105, ConfirmedIndex: 16}
	brk := techniquezone.Break{Kind: "BOS", Direction: "up", Level: 105, Index: 12}
	frame := frameOf(bars, []techniquezone.Leg{leg}, []techniquezone.Break{brk}, []techniquezone.Swing{swing})
	ob := techniquezone.Instance{Technique: SourceOrderBlock, Side: Buy, Low: 99, High: 101, OriginIndex: 8}
	sd := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Buy, Low: 98, High: 100, OriginIndex: 9}
	facts := DeriveFacts(frame, []techniquezone.Instance{ob, sd})
	if len(facts) != 2 || !facts[0].Verified || !facts[1].Verified {
		t.Fatalf("facts %+v", facts)
	}
	var gotOB Fact
	for _, f := range facts {
		if f.Source == SourceOrderBlock {
			gotOB = f
		}
	}
	// The break closed beyond a swing that is only a fractal swing once candle 16
	// has closed: the block cannot be confirmable before that, though the leg
	// ended at candle 14.
	if gotOB.AvailableAt != tm(16)+300 {
		t.Fatalf("order block available at %d, want the close of the swing's confirming candle %d", gotOB.AvailableAt, tm(16)+300)
	}
	if n := IndependentCount(facts); n != 1 {
		t.Fatalf("a supply/demand zone and an order block cut from one displacement leg are one source, got %d", n)
	}
	if regions, rejects := Find(facts, Buy, NoCutoff, two); len(regions) != 0 || len(rejects) != 1 || rejects[0].Reason != RejectSingleIndependentOrigin {
		t.Fatalf("regions %+v rejects %+v", regions, rejects)
	}
}

func TestOrderBlockWithoutItsBrokenSwingIsUnverified(t *testing.T) {
	bars, leg := orderBlockFixture()
	brk := techniquezone.Break{Kind: "BOS", Direction: "up", Level: 105, Index: 12}
	ob := techniquezone.Instance{Technique: SourceOrderBlock, Side: Buy, Low: 99, High: 101, OriginIndex: 8}
	facts := DeriveFacts(frameOf(bars, []techniquezone.Leg{leg}, []techniquezone.Break{brk}, nil), []techniquezone.Instance{ob})
	if facts[0].Verified || facts[0].UnverifiedReason != UnverifiedBrokenSwing {
		t.Fatalf("%+v", facts[0])
	}
}

func gapBars(older, newest int, floor, ceil float64) []market.Candle {
	bars := plainBars(30)
	bars[older].High = floor
	bars[older].Close = floor - 0.2
	bars[newest].Low = ceil
	bars[newest].Open = ceil + 0.2
	return bars
}

func TestFVGInsideTheDisplacementIsTheSameEventAsItsLeg(t *testing.T) {
	bars := gapBars(18, 20, 100, 101) // bullish gap [100,101] closed by bar 20
	leg := techniquezone.Leg{Start: 9, End: 21, Direction: "up"}
	frame := frameOf(bars, []techniquezone.Leg{leg}, nil, nil)
	fvg := techniquezone.Instance{Technique: SourceFVG, Side: Buy, Low: 100, High: 101, OriginIndex: 20}
	sd := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Buy, Low: 98, High: 100, OriginIndex: 8}
	facts := DeriveFacts(frame, []techniquezone.Instance{fvg, sd})
	if IndependentCount(facts) != 1 {
		t.Fatalf("two labels from one displacement are one source: %+v", facts)
	}
	for _, f := range facts {
		if f.Source == SourceFVG && f.AvailableAt != tm(20)+300 {
			t.Fatalf("an FVG is confirmable when the candle that closes the gap closes, got %d", f.AvailableAt)
		}
	}
	// The same gap with no displacement leg around it is its own source.
	facts = DeriveFacts(frameOf(bars, nil, nil, nil), []techniquezone.Instance{fvg})
	if len(facts) != 1 || !facts[0].Verified || len(facts[0].Atoms) != 1 {
		t.Fatalf("%+v", facts)
	}
}

func TestFVGWithSeparateOriginsFromSupplyDemandIsIndependent(t *testing.T) {
	bars := gapBars(18, 20, 100, 101)
	legs := []techniquezone.Leg{{Start: 3, End: 6, Direction: "up"}} // the zone's leg is long before the gap
	frame := frameOf(bars, legs, nil, nil)
	fvg := techniquezone.Instance{Technique: SourceFVG, Side: Buy, Low: 100, High: 101, OriginIndex: 20}
	sd := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Buy, Low: 98, High: 101, OriginIndex: 2}
	facts := DeriveFacts(frame, []techniquezone.Instance{fvg, sd})
	if IndependentCount(facts) != 2 {
		t.Fatalf("genuinely separate origins must be two sources: %+v", facts)
	}
	regions, _ := Find(facts, Buy, NoCutoff, two)
	r := single(t, regions)
	if r.Low != 100 || r.High != 101 {
		t.Fatalf("region [%v,%v], want the FVG band inside the zone", r.Low, r.High)
	}
}

func TestClippedInstanceKeepsItsStructuralBoundsAndEligibleBand(t *testing.T) {
	bars := gapBars(18, 20, 100, 110) // structural gap [100,110], clipped to its proximal slice
	frame := frameOf(bars, nil, nil, nil)
	fvg := techniquezone.Instance{Technique: SourceFVG, Side: Buy, Low: 105, High: 110, EntryClipped: true, StructuralLow: 100, StructuralHigh: 110, OriginIndex: 20}
	facts := DeriveFacts(frame, []techniquezone.Instance{fvg})
	f := facts[0]
	if !f.Verified || f.Low != 105 || f.High != 110 || f.StructuralLow != 100 || f.StructuralHigh != 110 {
		t.Fatalf("%+v", f)
	}
}

func TestInversionBelongsToTheDisplacementThatInvertedIt(t *testing.T) {
	// A bullish gap [100,101] closed by bar 12 is inverted by bar 15 closing
	// below it; bar 15 sits in a down leg 14..17. A sell structure cut from that
	// same down leg is one event with the inversion.
	bars := gapBars(10, 12, 100, 101)
	bars[15].Close = 99
	bars[15].Open = 100.4
	legs := []techniquezone.Leg{{Start: 14, End: 17, Direction: "down"}}
	frame := frameOf(bars, legs, nil, nil)
	ifvg := techniquezone.Instance{Technique: SourceIFVG, Side: Sell, Low: 100, High: 101, OriginIndex: 15}
	supply := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Sell, Low: 100.5, High: 103, OriginIndex: 13}
	facts := DeriveFacts(frame, []techniquezone.Instance{ifvg, supply})
	for _, f := range facts {
		if !f.Verified {
			t.Fatalf("%+v", f)
		}
	}
	if IndependentCount(facts) != 1 {
		t.Fatalf("an inversion and a zone from the same displacement are one source: %+v", facts)
	}
	var inv Fact
	for _, f := range facts {
		if f.Source == SourceIFVG {
			inv = f
		}
	}
	if inv.AvailableAt != tm(15)+300 {
		t.Fatalf("an iFVG is confirmable when its inversion candle closes, got %d", inv.AvailableAt)
	}
	wantParent := FactID(SourceFVG, Buy, tm(12), 100, 101)
	if inv.ParentID != wantParent {
		t.Fatalf("parent %q, want %q", inv.ParentID, wantParent)
	}
}

func TestIFVGAndItsOwnFVGAreOneSource(t *testing.T) {
	bars := gapBars(10, 12, 100, 101)
	bars[15].Close = 99
	frame := frameOf(bars, nil, nil, nil)
	ifvg := techniquezone.Instance{Technique: SourceIFVG, Side: Sell, Low: 100, High: 101, OriginIndex: 15}
	fvg := techniquezone.Instance{Technique: SourceFVG, Side: Buy, Low: 100, High: 101, OriginIndex: 12}
	facts := DeriveFacts(frame, []techniquezone.Instance{ifvg, fvg})
	if len(facts) != 2 || IndependentCount(facts) != 1 {
		t.Fatalf("a gap and the iFVG made of it share the gap's identity: %+v", facts)
	}
}

func TestIFVGWithoutItsGapIsUnverified(t *testing.T) {
	ifvg := techniquezone.Instance{Technique: SourceIFVG, Side: Sell, Low: 100, High: 101, OriginIndex: 15}
	facts := DeriveFacts(frameOf(plainBars(30), nil, nil, nil), []techniquezone.Instance{ifvg})
	if facts[0].Verified || facts[0].UnverifiedReason != UnverifiedParentGap {
		t.Fatalf("%+v", facts[0])
	}
}

func TestCRTAndUnknownTechniquesAreUnverified(t *testing.T) {
	frame := frameOf(plainBars(30), nil, nil, nil)
	crt := techniquezone.Instance{Technique: SourceCRT, Side: Buy, Low: 98, High: 100, OriginIndex: 3}
	other := techniquezone.Instance{Technique: "breaker", Side: Buy, Low: 98, High: 100, OriginIndex: 3}
	outside := techniquezone.Instance{Technique: SourceFVG, Side: Buy, Low: 98, High: 100, OriginIndex: 99}
	facts := DeriveFacts(frame, []techniquezone.Instance{crt, other, outside})
	reasons := map[string]bool{}
	for _, f := range facts {
		if f.Verified {
			t.Fatalf("must not verify %+v", f)
		}
		reasons[f.UnverifiedReason] = true
	}
	for _, want := range []string{UnverifiedCRT, UnverifiedTechnique, UnverifiedOriginOutsideWindow} {
		if !reasons[want] {
			t.Fatalf("missing reason %s in %v", want, reasons)
		}
	}
}

func TestDeriveFactsIsOrderIndependentAndDeduplicates(t *testing.T) {
	bars := gapBars(18, 20, 100, 101)
	frame := frameOf(bars, []techniquezone.Leg{{Start: 3, End: 6, Direction: "up"}}, nil, nil)
	a := techniquezone.Instance{Technique: SourceFVG, Side: Buy, Low: 100, High: 101, OriginIndex: 20}
	b := techniquezone.Instance{Technique: SourceSupplyDemand, Side: Buy, Low: 98, High: 101, OriginIndex: 2}
	one := DeriveFacts(frame, []techniquezone.Instance{a, b})
	two := DeriveFacts(frame, []techniquezone.Instance{b, a, a})
	if !reflect.DeepEqual(one, two) {
		t.Fatalf("%+v vs %+v", one, two)
	}
}

func itoa(v int64) string { return strconv.FormatInt(v, 10) }
