package supply

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

const frameStep = 900 // M15

func testStrategy() *Strategy {
	return &Strategy{cfg: Config{
		DisplacementATRMult: 1.5, MomentumBodyFraction: 0.6, MaximumZoneATR: 3, EntryMaxWidthPrice: 5, PipSize: 0.1, RoundStep: 5, StructurePivotBars: 2,
	}}
}

// supplyFrame is a flat 4299-4301 base, then a strong three-bar departure down (the supply zone 4299-4301), then `after`.
func supplyFrame(after ...market.Candle) *analysiscontext.LegacyFrame {
	var bars []market.Candle
	add := func(open, high, low, close float64) {
		bars = append(bars, market.Candle{Time: int64(1_800_000_000 + len(bars)*frameStep), Open: open, High: high, Low: low, Close: close})
	}
	for i := 0; i < 25; i++ { // a base that alternates and ends bullish, so no leg forms inside it and the departure starts clean
		if i%2 == 0 {
			add(4299.6, 4301, 4299, 4300.4)
		} else {
			add(4300.4, 4301, 4299, 4299.6)
		}
	}
	add(4300.0, 4300.1, 4296.9, 4297.0) // the departure: three strong bearish bars
	add(4297.0, 4297.1, 4293.9, 4294.0)
	add(4294.0, 4294.1, 4290.9, 4291.0)
	bars = append(bars, after...)
	atr := make([]float64, len(bars))
	for i := range atr {
		atr[i] = 1.5
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: atr}
}

func bar(frame *analysiscontext.LegacyFrame, n int, open, high, low, close float64) market.Candle {
	return market.Candle{Time: 1_800_000_000 + int64(len(frame.Bars)+n)*frameStep, Open: open, High: high, Low: low, Close: close}
}

// run evaluates the frame and returns the verdict with the ID of the one zone the frame holds.
func run(t *testing.T, frame *analysiscontext.LegacyFrame) (opportunity.ZoneLiveness, string) {
	t.Helper()
	s := testStrategy()
	verdict := s.zones.Begin()
	instances, _ := s.structureInstances(frame, market.M15, nil, nil, verdict)
	verdict.Publish()
	liveness, ok := s.TakeZoneLiveness()
	if !ok {
		t.Fatal("the strategy read a frame and must report a verdict")
	}
	_ = instances
	id := ""
	for key := range liveness.Live {
		id = key
	}
	for key := range liveness.Terminal {
		id = key
	}
	return liveness, id
}

// A zone price has moved away from (remote, dormant) is still a live zone: its opportunity must not be retired for distance.
func TestZoneThatPriceLeftIsLiveNotInvalidated(t *testing.T) {
	frame := supplyFrame()
	for i := 0; i < 30; i++ { // price drifts on far below the zone, alternating small bars
		open, close := 4290.0, 4290.4
		if i%2 == 1 {
			open, close = 4290.4, 4290.0
		}
		frame.Bars = append(frame.Bars, market.Candle{Time: 1_800_000_000 + int64(len(frame.Bars))*frameStep, Open: open, High: 4290.6, Low: 4289.8, Close: close})
		frame.ATR = append(frame.ATR, 1.5)
	}
	liveness, id := run(t, frame)
	if _, live := liveness.Live[id]; !live {
		t.Fatalf("a remote supply zone must be live: %+v", liveness)
	}
	if reason, finished := liveness.Verdict(id, market.M15); finished {
		t.Fatalf("a zone price merely left must not be finished: %s", reason)
	}
}

// A close above the zone that is never reclaimed invalidates it: the opportunity on it must end even though no stop was touched.
func TestZoneThatPriceAcceptedAboveIsInvalidated(t *testing.T) {
	frame := supplyFrame()
	for i := 0; i < 12; i++ { // price accepted above the zone (closes above its high by more than the tolerance, never back inside)
		open, close := 4303.0, 4303.5
		if i%2 == 1 {
			open, close = 4303.5, 4303.0
		}
		frame.Bars = append(frame.Bars, market.Candle{Time: 1_800_000_000 + int64(len(frame.Bars))*frameStep, Open: open, High: 4304, Low: 4302.5, Close: close})
		frame.ATR = append(frame.ATR, 1.5)
	}
	liveness, id := run(t, frame)
	reason, finished := liveness.Verdict(id, market.M15)
	if !finished || reason != opportunity.ReasonZoneInvalidated {
		t.Fatalf("want ZONE_INVALIDATED, got %q finished=%v (%+v)", reason, finished, liveness)
	}
}

// A zone the strategy no longer finds at all has run out: expired. A frame the strategy never read says nothing.
func TestZoneThatLeftTheFrameExpiresAndAnUnreadFrameSaysNothing(t *testing.T) {
	frame := supplyFrame()
	_, id := run(t, frame)
	if id == "" {
		t.Fatal("the fixture must hold one supply zone")
	}
	empty := &analysiscontext.LegacyFrame{}
	for i := 0; i < 40; i++ { // a frame with only a quiet range: no departure, so no zone
		empty.Bars = append(empty.Bars, market.Candle{Time: 1_800_000_000 + int64(i)*frameStep, Open: 4300, High: 4300.4, Low: 4299.6, Close: 4300.1})
		empty.ATR = append(empty.ATR, 1.5)
	}
	liveness, _ := run(t, empty)
	if reason, finished := liveness.Verdict(id, market.M15); !finished || reason != opportunity.ReasonZoneExpired {
		t.Fatalf("a zone the strategy no longer holds must read expired, got %q finished=%v", reason, finished)
	}
	if _, finished := liveness.Verdict(id, market.H1); finished {
		t.Fatal("a frame the strategy did not read must never retire anything")
	}
}
