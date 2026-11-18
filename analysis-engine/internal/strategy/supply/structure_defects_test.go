package supply

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Regression tests of the SD-1 audit. Each one failed on the code before the fix and states what a supply zone must do.

// wideSupplyFrame is a 3.0-wide supply base (4297-4300) and a strong three-bar departure down, then `after`.
func wideSupplyFrame(after ...market.Candle) *analysiscontext.LegacyFrame {
	var bars []market.Candle
	add := func(open, high, low, close float64) {
		bars = append(bars, market.Candle{Time: int64(1_800_000_000 + len(bars)*frameStep), Open: open, High: high, Low: low, Close: close})
	}
	for i := 0; i < 25; i++ {
		if i%2 == 0 {
			add(4298.0, 4300, 4297, 4298.6)
		} else {
			add(4298.6, 4300, 4297, 4298.0)
		}
	}
	add(4297.0, 4297.1, 4293.9, 4294.0) // the departure: three strong bearish bars
	add(4294.0, 4294.1, 4290.9, 4291.0)
	add(4291.0, 4291.1, 4287.9, 4288.0)
	bars = append(bars, after...)
	atr := make([]float64, len(bars))
	for i := range atr {
		atr[i] = 1.5
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: atr}
}

// appendBars adds bars to a frame with the frame's ATR.
func appendBars(frame *analysiscontext.LegacyFrame, bars ...[4]float64) {
	for _, b := range bars {
		frame.Bars = append(frame.Bars, market.Candle{Time: 1_800_000_000 + int64(len(frame.Bars))*frameStep, Open: b[0], High: b[1], Low: b[2], Close: b[3]})
		frame.ATR = append(frame.ATR, 1.5)
	}
}

func clippedStrategy() *Strategy {
	s := testStrategy()
	s.cfg.EntryMaxWidthPrice = 1.0 // the entry band is the proximal 1.0 of a 3.0 zone
	return s
}

func read(t *testing.T, s *Strategy, frame *analysiscontext.LegacyFrame) ([]instance, []band, opportunity.ZoneLiveness) {
	t.Helper()
	verdict := s.zones.Begin()
	instances, held := s.structureInstances(frame, market.M15, nil, nil, verdict)
	verdict.Publish()
	liveness, ok := s.TakeZoneLiveness()
	if !ok {
		t.Fatal("no verdict")
	}
	return instances, held, liveness
}

// 1. A zone is invalidated against its structural far boundary, not against the proximal slice that is tradable.
func TestClippedEntryBandDoesNotInvalidateAgainstItsOwnSlice(t *testing.T) {
	frame := wideSupplyFrame()
	// Price sits inside the structural zone, above the clipped slice (4297-4298) and below the far edge (4300): it is still inside the zone, not accepted beyond it.
	for i := 0; i < 12; i++ {
		appendBars(frame, [4]float64{4298.9, 4299.4, 4298.6, 4299.2})
	}
	_, _, liveness := read(t, clippedStrategy(), frame)
	for id := range liveness.Terminal {
		t.Fatalf("zone %s was retired although price never closed beyond the structural far edge (4300): %+v", id, liveness.Terminal[id])
	}
	if len(liveness.Live) != 1 {
		t.Fatalf("the zone must stay live: %+v", liveness)
	}
}

// 2. The maximum zone width is judged on the complete structural zone, before the entry band is clipped.
func TestMaximumZoneWidthIsJudgedBeforeClipping(t *testing.T) {
	frame := wideSupplyFrame()
	s := clippedStrategy()
	s.cfg.MaximumZoneATR = 1.5 // 2.25 price units: a 3.0-wide structure is a region, though its clipped 1.0 slice would pass
	instances, _, liveness := read(t, s, frame)
	if len(instances) != 0 {
		t.Fatalf("an over-wide structure must not be offered through its clipped slice: %+v", instances)
	}
	if len(liveness.Live) != 1 {
		t.Fatalf("the structure itself is still a live fact (its opportunities are not retired): %+v", liveness)
	}
}

// 3. A terminal or spent zone gives no higher-timeframe confluence to the frames below.
func TestTerminalZoneDoesNotScoreAsHigherTimeframeStructure(t *testing.T) {
	live := wideSupplyFrame()
	_, held, _ := read(t, clippedStrategy(), live)
	if len(held) != 1 {
		t.Fatalf("a live zone is held for the frame below: %v", held)
	}
	dead := wideSupplyFrame()
	for i := 0; i < 12; i++ { // accepted above the far edge and never reclaimed
		appendBars(dead, [4]float64{4302.5, 4303.5, 4302.2, 4303.0})
	}
	_, held, liveness := read(t, clippedStrategy(), dead)
	if len(liveness.Terminal) != 1 {
		t.Fatalf("the fixture's zone must be terminal: %+v", liveness)
	}
	if len(held) != 0 {
		t.Fatalf("an invalidated zone must not be offered as higher-timeframe confluence: %v", held)
	}
}

// 4. Break and reclaim are judged only on bars that exist: a breach whose reclaim window is still open is pending, not terminal, and a terminal verdict is never undone.
func TestBreachEpisodeLifecycleHasNoLookAhead(t *testing.T) {
	above := [4]float64{4302.5, 4303.5, 4302.2, 4303.0}  // closes 3.0 above the far edge: a breach
	inside := [4]float64{4299.0, 4299.5, 4298.5, 4299.0} // back inside
	s := clippedStrategy()

	// The breach is the last bar: nothing says yet whether it is reclaimed. Not terminal.
	frame := wideSupplyFrame()
	appendBars(frame, above)
	instances, _, liveness := read(t, s, frame)
	if len(liveness.Terminal) != 0 {
		t.Fatalf("a breach with its reclaim window still open is pending, not terminal: %+v", liveness.Terminal)
	}
	if len(liveness.Live) != 1 {
		t.Fatalf("a pending zone is neither retired nor forgotten: %+v", liveness)
	}
	if len(instances) != 0 {
		t.Fatalf("a pending breach is not offered for new entries: %+v", instances)
	}

	// The reclaim arrives within the window: the zone is live again and the same ID was never retired.
	reclaimed := wideSupplyFrame()
	appendBars(reclaimed, above, above, inside)
	_, _, liveness = read(t, s, reclaimed)
	if len(liveness.Terminal) != 0 || len(liveness.Live) != 1 {
		t.Fatalf("a reclaimed breach leaves the zone live: %+v", liveness)
	}

	// Never reclaimed for the whole window: terminal.
	dead := wideSupplyFrame()
	for i := 0; i < sweepReclaimBars+1; i++ {
		appendBars(dead, above)
	}
	_, _, liveness = read(t, s, dead)
	if len(liveness.Terminal) != 1 {
		t.Fatalf("a breach never reclaimed inside its window is terminal: %+v", liveness)
	}

	// A reclaim after the window has closed does not resurrect it: the verdict only ever moves forward.
	late := wideSupplyFrame()
	for i := 0; i < sweepReclaimBars+1; i++ {
		appendBars(late, above)
	}
	appendBars(late, inside, inside, inside)
	_, _, liveness = read(t, s, late)
	if len(liveness.Terminal) != 1 {
		t.Fatalf("a terminal zone must stay terminal: %+v", liveness)
	}
}
