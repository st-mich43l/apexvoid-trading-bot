package fvgdefinition_test

import (
	"math"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Real XAU fair value gaps of 14-16 Oct 2026 in replay-xau-production-capture-20261022.json (bar = the first M5 bar the candidate is published on, UTC;
// the gap bounds and the bar that completed it are the candidate's own structure block, the ATR and the middle candle come from the capture's bars of
// the gap's own timeframe). They were found by replaying the capture with the definition removed (WithoutFVGDefinition) and with it, and listing every
// confirmed fvg candidate with its gap in own-timeframe ATR: the ones the definition drops, and the ones it keeps.
type fixture struct {
	name         string
	bar          string
	direction    market.Direction
	structuralID string
	timeframe    market.Timeframe
	gapLow       float64
	gapHigh      float64
	formedAt     string
	published    bool
	reason       string // what the definition refuses it for, "" when it is published
}

var fixtures = []fixture{
	// Two M15 gaps narrower than half an M15 ATR: 0.37 and 0.22 of it. Both traded under v5 and are not gaps on the chart.
	{"M15 0.37 ATR", "2026-10-14T06:10", market.Sell, "technique:fvg:sell:1791954900@M15", market.M15, 4162.73, 4164.80, "2026-10-14T05:15", false, techniquezone.FVGRefusedGapSmall},
	{"M15 0.22 ATR", "2026-10-14T00:30", market.Buy, "technique:fvg:buy:1791936900@M15", market.M15, 4157.26, 4157.99, "2026-10-14T00:15", false, techniquezone.FVGRefusedGapSmall},
	// One H1 gap whose middle candle has a body of 0.31 of an H1 ATR (its gap is 0.22 of it).
	{"H1 body 0.31 ATR", "2026-10-15T23:55", market.Buy, "technique:fvg:buy:1792090800@H1", market.H1, 4154.82, 4157.82, "2026-10-15T19:00", false, techniquezone.FVGRefusedGapSmall},
	// One real M15 gap (0.60 ATR) and one real M5 gap (1.46 ATR): published, with the same entry, invalidation and target as before.
	{"M15 0.60 ATR", "2026-10-16T04:55", market.Buy, "technique:fvg:buy:1792123200@M15", market.M15, 4170.78, 4175.77, "2026-10-16T04:00", true, ""},
	{"M5 1.46 ATR", "2026-10-14T00:25", market.Buy, "technique:fvg:buy:1791936600", market.M5, 4157.52, 4160.65, "2026-10-14T00:10", true, ""},
}

func find(set map[key]sighting, bar string, structuralID string) (sighting, bool) {
	for k, s := range set {
		if barLabel(k.bar) == bar && s.StructuralID == structuralID {
			return s, true
		}
	}
	return sighting{}, false
}

func near(a, b float64) bool { return math.Abs(a-b) < 0.0051 }

func replayBoth(t *testing.T, name string) (off, on map[key]sighting, capture *replaycapture.Capture) {
	t.Helper()
	doc := loadDoc(t)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); off, capture = replayFVG(t, doc, name, true) }()
	go func() { defer wg.Done(); on, _ = replayFVG(t, doc, name, false) }()
	wg.Wait()
	return off, on, capture
}

func TestTheDefinitionRefusesTheThinGapsAndPublishesTheRealOnesUnchanged(t *testing.T) {
	doc := loadDoc(t)
	th, atrLength := configuredThresholds(t, doc), atrLengthOf(t, doc)
	off, on, capture := replayBoth(t, "replay-xau-production-capture-20261022.json")

	for _, f := range fixtures {
		before, wasPublished := find(off, f.bar, f.structuralID)
		if !wasPublished {
			t.Fatalf("%s: v5 (definition off) must publish %v %s %s", f.name, f.direction, f.bar, f.structuralID)
		}
		if before.Direction != f.direction || before.Timeframe != f.timeframe || !near(before.GapLow, f.gapLow) || !near(before.GapHigh, f.gapHigh) || barLabel(before.FormedAt) != f.formedAt {
			t.Errorf("%s: the candidate's structure block is %s %.2f-%.2f formed %s, want %s %.2f-%.2f formed %s", f.name,
				before.Timeframe, before.GapLow, before.GapHigh, barLabel(before.FormedAt), f.timeframe, f.gapLow, f.gapHigh, f.formedAt)
		}

		// The gap in its own timeframe's ATR, from the capture's bars.
		m, ok := measureGap(t, capture, atrLength, before)
		if !ok {
			t.Fatalf("%s: cannot find the completing bar %s in the %s bars", f.name, barLabel(before.FormedAt), before.Timeframe)
		}
		if got := m.reason(th); got != f.reason {
			t.Errorf("%s: the definition refuses it for %q, want %q (gap %.2f ATR, body %.2f ATR, ratio %.2f)", f.name, got, f.reason, m.GapATR, m.BodyATR, m.BodyRatio)
		}

		after, nowPublished := find(on, f.bar, f.structuralID)
		if nowPublished != f.published {
			t.Errorf("%s: published with the definition = %v, want %v", f.name, nowPublished, f.published)
		}
		if !f.published {
			continue
		}
		if after.EntryLow != before.EntryLow || after.EntryHigh != before.EntryHigh || after.Invalidation != before.Invalidation || after.Target != before.Target {
			t.Errorf("%s: the definition moved the geometry: entry %.2f-%.2f inv %.2f target %.2f, was %.2f-%.2f inv %.2f target %.2f", f.name,
				after.EntryLow, after.EntryHigh, after.Invalidation, after.Target, before.EntryLow, before.EntryHigh, before.Invalidation, before.Target)
		}
		if m.GapATR < th.gapATR {
			t.Errorf("%s: a published gap is %.2f of its ATR, below %.2f", f.name, m.GapATR, th.gapATR)
		}
	}

	// The two M15 gaps are below half an M15 ATR, the H1 one has a middle body below half an H1 ATR.
	for _, f := range fixtures[:3] {
		s, _ := find(off, f.bar, f.structuralID)
		m, _ := measureGap(t, capture, atrLength, s)
		if f.timeframe == market.M15 && m.GapATR >= 0.5 {
			t.Errorf("%s: %.2f M15 ATR is not a thin gap", f.name, m.GapATR)
		}
		if f.timeframe == market.H1 && m.BodyATR >= 0.5 {
			t.Errorf("%s: its middle body is %.2f H1 ATR", f.name, m.BodyATR)
		}
	}
}

// The structure block is the whole gap even where the order is placed on a clipped part of it (the card then shows both).
func TestTheStructureBlockIsTheWholeGapWhereTheOrderZoneIsClipped(t *testing.T) {
	off, _, _ := replayBoth(t, "replay-xau-production-capture-20261022.json")
	s, ok := find(off, "2026-10-14T00:00", "technique:fvg:buy:1791915000")
	if !ok {
		t.Fatal("the clipped M5 gap of 14 Oct 00:00 must be published")
	}
	if s.Timeframe != market.M5 || !near(s.GapLow, 4151.62) || !near(s.GapHigh, 4157.55) || barLabel(s.FormedAt) != "2026-10-13T18:10" {
		t.Errorf("structure block %s %.2f-%.2f formed %s, want the whole M5 gap 4151.62-4157.55 formed 2026-10-13T18:10", s.Timeframe, s.GapLow, s.GapHigh, barLabel(s.FormedAt))
	}
	if !near(s.EntryLow, 4152.55) || !near(s.EntryHigh, 4157.55) {
		t.Errorf("the order zone is %.2f-%.2f, want the clipped 4152.55-4157.55", s.EntryLow, s.EntryHigh)
	}
}
