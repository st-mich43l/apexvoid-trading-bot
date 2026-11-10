package strategyutil

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const decisionBar = int64(1_700_000_000) // open time of the last closed M5 bar; the decision is its close, decisionBar + 300

var xau = ScalpRoomParams{PipSize: 0.1, ATR: 3, BarrierBufferATR: 0.15, BarrierMaxWidthATR: 2, BarrierMaxWidthPips: 100}

// wall is a supply (side "sell", resistance above price) or demand zone of the canonical zone book.
func wall(kind zone.Kind, low, high float64, state zone.State, origin, created int64) zone.Zone {
	return zone.Zone{ID: "z", Kind: kind, Low: market.Price(low), High: market.Price(high), State: state, OriginTime: origin, CreatedAt: created, TouchCount: 1, Strength: 5}
}

func contextWith(zonesByTF map[market.Timeframe][]zone.Zone) *analysiscontext.MarketContext {
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{}}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx.Timeframes[tf] = &analysiscontext.TimeframeContext{Timeframe: tf, Zones: zone.ZoneState{Zones: zonesByTF[tf], ATR: 3}}
	}
	ctx.Timeframes[market.M5].Candles = []market.Candle{{Time: decisionBar - 300}, {Time: decisionBar}}
	return ctx
}

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-6 {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}

func TestRoomWithoutABarrierIsTheFiniteObjective(t *testing.T) {
	ctx := contextWith(nil)
	buy := MeasureScalpRoom(ctx, market.Buy, 4000, 4010, 0, xau)
	near(t, "BUY room", buy.Pips, 100)
	if buy.HasBarrier() || buy.Limiter != ScalpRoomLimitedByObjective || !math.IsNaN(buy.BarrierPips) {
		t.Errorf("BUY: barrier %v limiter %q barrierPips %v, want an objective-limited room with no barrier", buy.Barrier, buy.Limiter, buy.BarrierPips)
	}
	sell := MeasureScalpRoom(ctx, market.Sell, 4000, 3990, 0, xau)
	near(t, "SELL room", sell.Pips, 100)
	if math.IsInf(buy.Pips, 0) || math.IsInf(sell.Pips, 0) {
		t.Error("room must never be unlimited")
	}
}

func TestRoomIsNeverUnlimitedWhateverIsMissing(t *testing.T) {
	for name, ctx := range map[string]*analysiscontext.MarketContext{"nil context": nil, "no timeframes": {Symbol: "XAU"}} {
		room := MeasureScalpRoom(ctx, market.Buy, 4000, 4010, 0, xau)
		if math.IsInf(room.Pips, 0) || math.IsNaN(room.Pips) {
			t.Errorf("%s: room %v is not finite", name, room.Pips)
		}
	}
	// An objective that is not ahead of the entry, or not a number, leaves no room: it is not replaced by anything.
	for _, objective := range []float64{3999, 4000, math.NaN(), math.Inf(1)} {
		if room := MeasureScalpRoom(contextWith(nil), market.Buy, 4000, objective, 0, xau); room.Pips > 0 {
			t.Errorf("objective %v: room %v, want none", objective, room.Pips)
		}
	}
}

func TestANearerBarrierLimitsTheRoomAndTheBufferIsAppliedOnce(t *testing.T) {
	supply := wall(zone.KindSupply, 4005, 4006, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	ctx := contextWith(map[market.Timeframe][]zone.Zone{market.M5: {supply}})
	room := MeasureScalpRoom(ctx, market.Buy, 4000, 4010, 0, xau)
	// Raw room 50 pips; the buffer is 0.15 ATR = 0.45 price = 4.5 pips, once.
	near(t, "raw barrier room", room.RawBarrierPips, 50)
	near(t, "buffered barrier room", room.BarrierPips, 45.5)
	near(t, "room", room.Pips, 45.5)
	if room.Limiter != ScalpRoomLimitedByBarrier {
		t.Errorf("limiter %q, want barrier", room.Limiter)
	}
	// A farther barrier does not limit a nearer objective.
	far := wall(zone.KindSupply, 4020, 4021, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	room = MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {far}}), market.Buy, 4000, 4010, 0, xau)
	near(t, "room", room.Pips, 100)
	if room.Limiter != ScalpRoomLimitedByObjective {
		t.Errorf("limiter %q, want objective", room.Limiter)
	}
}

func TestBuyAndSellAreSideCorrect(t *testing.T) {
	supply := wall(zone.KindSupply, 4005, 4006, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	demand := wall(zone.KindDemand, 3994, 3995, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	ctx := contextWith(map[market.Timeframe][]zone.Zone{market.M15: {supply, demand}})
	buy := MeasureScalpRoom(ctx, market.Buy, 4000, 4020, 0, xau)
	near(t, "BUY raw room to the supply wall's lower edge", buy.RawBarrierPips, 50)
	sell := MeasureScalpRoom(ctx, market.Sell, 4000, 3980, 0, xau)
	near(t, "SELL raw room to the demand wall's upper edge", sell.RawBarrierPips, 50)
	// A BUY ignores a demand wall below it and a SELL ignores a supply wall above it: they are not opposing.
	onlyDemand := contextWith(map[market.Timeframe][]zone.Zone{market.M15: {demand}})
	if got := MeasureScalpRoom(onlyDemand, market.Buy, 4000, 4020, 0, xau); got.HasBarrier() {
		t.Errorf("BUY saw the demand wall below it as opposing: %+v", *got.Barrier)
	}
	onlySupply := contextWith(map[market.Timeframe][]zone.Zone{market.M15: {supply}})
	if got := MeasureScalpRoom(onlySupply, market.Sell, 4000, 3980, 0, xau); got.HasBarrier() {
		t.Errorf("SELL saw the supply wall above it as opposing: %+v", *got.Barrier)
	}
}

func TestAWallBehindTheEntryIsNotAheadAndAWallTheEntrySitsInHasNoRoom(t *testing.T) {
	behind := wall(zone.KindSupply, 3990, 3995, zone.StateFresh, decisionBar-3000, decisionBar-3000) // a supply wall BELOW a BUY entry: passed
	if got := MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {behind}}), market.Buy, 4000, 4010, 0, xau); got.HasBarrier() {
		t.Errorf("a wall wholly behind the entry is not an obstacle: %+v", *got.Barrier)
	}
	containing := wall(zone.KindSupply, 3999, 4003, zone.StateFresh, decisionBar-3000, decisionBar-3000) // the entry sits inside the wall
	room := MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {containing}}), market.Buy, 4000, 4010, 0, xau)
	if !room.HasBarrier() || room.Pips > 0 {
		t.Errorf("entry inside a wall: barrier %v room %v, want a barrier and no room (there is no same-wall exemption)", room.HasBarrier(), room.Pips)
	}
	if room.RawBarrierPips >= 0 {
		t.Errorf("raw room %v, want zero or negative for an entry inside the wall", room.RawBarrierPips)
	}
}

func TestRepeatedBarriersAreOneWall(t *testing.T) {
	// The same wall seen on M5, M15 and H1 is one barrier after the book's same-side merge: the same room, not three nearer ones.
	one := wall(zone.KindSupply, 4005, 4006, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	three := contextWith(map[market.Timeframe][]zone.Zone{market.M5: {one}, market.M15: {one}, market.H1: {one}})
	a := MeasureScalpRoom(three, market.Buy, 4000, 4020, 0, xau)
	b := MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {one}}), market.Buy, 4000, 4020, 0, xau)
	near(t, "three copies", a.Pips, b.Pips)
	if len(a.Barrier.SourceTimeframes) != 3 {
		t.Errorf("merged barrier timeframes = %v, want M5, M15 and H1", a.Barrier.SourceTimeframes)
	}
}

func TestStaleAndUnusableBarriersAreIgnored(t *testing.T) {
	for _, state := range []zone.State{zone.StateMitigated, zone.StateInvalidated} {
		stale := wall(zone.KindSupply, 4005, 4006, state, decisionBar-3000, decisionBar-3000)
		if got := MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {stale}}), market.Buy, 4000, 4020, 0, xau); got.HasBarrier() {
			t.Errorf("a %s zone is not a standing wall", state)
		}
	}
	// A zone wider than the execution width gate is not an execution barrier (the book's own rule).
	wide := wall(zone.KindSupply, 4005, 4030, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	if got := MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {wide}}), market.Buy, 4000, 4040, 0, xau); got.HasBarrier() {
		t.Error("a zone wider than the width gate must not be a barrier")
	}
}

func TestNothingAfterTheDecisionIsRead(t *testing.T) {
	// A wall created after the last closed bar did not exist at the decision: it cannot limit the room.
	future := wall(zone.KindSupply, 4002, 4003, zone.StateFresh, decisionBar+300, decisionBar+600)
	ctx := contextWith(map[market.Timeframe][]zone.Zone{market.M5: {future}})
	if got := MeasureScalpRoom(ctx, market.Buy, 4000, 4020, 0, xau); got.HasBarrier() {
		t.Errorf("a zone created after the decision limited the room: %+v", *got.Barrier)
	}
	// One created at the decision's own close existed.
	known := wall(zone.KindSupply, 4002, 4003, zone.StateFresh, decisionBar, decisionBar+300)
	if got := MeasureScalpRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M5: {known}}), market.Buy, 4000, 4020, 0, xau); !got.HasBarrier() {
		t.Error("a zone created at the close of the decision bar must count")
	}
}

func TestTheCandidatesOwnStructureIsNotItsOpposingBarrier(t *testing.T) {
	born := wall(zone.KindSupply, 4005, 4006, zone.StateFresh, decisionBar, decisionBar) // born of the bar that made the candidate
	older := wall(zone.KindSupply, 4012, 4013, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	ctx := contextWith(map[market.Timeframe][]zone.Zone{market.M5: {born, older}})
	room := MeasureScalpRoom(ctx, market.Buy, 4000, 4020, decisionBar, xau)
	near(t, "raw room to the older wall", room.RawBarrierPips, 120)
	room = MeasureScalpRoom(ctx, market.Buy, 4000, 4020, 0, xau)
	near(t, "raw room with no cutoff", room.RawBarrierPips, 50)
}

func TestPipUnitsFollowTheInstrument(t *testing.T) {
	fx := ScalpRoomParams{PipSize: 0.0001, ATR: 0.0008, BarrierBufferATR: 0.5, BarrierMaxWidthATR: 2, BarrierMaxWidthPips: 100}
	supply := wall(zone.KindSupply, 1.1050, 1.1052, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	ctx := contextWith(map[market.Timeframe][]zone.Zone{market.M5: {supply}})
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx.Timeframes[tf].Zones.ATR = 0.0008
	}
	room := MeasureScalpRoom(ctx, market.Buy, 1.1000, 1.1100, 0, fx)
	near(t, "raw room (pips)", room.RawBarrierPips, 50)
	near(t, "buffered room (pips)", room.BarrierPips, 46) // 0.5 x 8 pips ATR = 4 pips
}

func TestSupportingBarrierIsTheTradesOwnSideOnly(t *testing.T) {
	demand := wall(zone.KindDemand, 3996, 3999, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	supply := wall(zone.KindSupply, 3996, 3999, zone.StateFresh, decisionBar-3000, decisionBar-3000)
	if _, ok := SupportingBarrier(contextWith(map[market.Timeframe][]zone.Zone{market.M15: {demand}}), market.Buy, 3995, 4000, 0, xau); !ok {
		t.Error("a demand wall under a BUY probe is structural support")
	}
	if _, ok := SupportingBarrier(contextWith(map[market.Timeframe][]zone.Zone{market.M15: {supply}}), market.Buy, 3995, 4000, 0, xau); ok {
		t.Error("a supply wall is not support for a BUY")
	}
}

func TestScalpTargetIsBoundedByTheRoomItIsGiven(t *testing.T) {
	ladder := []float64{2, 1}
	cases := []struct {
		name string
		room float64
		want float64 // selected target pips, 0 = none fits
	}{
		{"plenty of room: 2R", 100, 60},
		{"exactly 2R", 60, 60},
		{"between 1R and 2R: only 1R fits", 45, 30},
		{"exactly 1R", 30, 30},
		{"below 1R: nothing fits", 29.9, 0},
		{"no room", 0, 0},
		{"negative room", -10, 0},
	}
	for _, c := range cases {
		for _, direction := range []market.Direction{market.Buy, market.Sell} {
			price, pips, ok := ScalpTarget(direction, 4000, c.room, 30, 10, 0.1, ladder)
			if (c.want > 0) != ok || ok && math.Abs(pips-c.want) > 1e-9 {
				t.Errorf("%s %s: target %v ok=%v, want %v pips", c.name, direction, pips, ok, c.want)
			}
			if ok {
				wantPrice := 4000 + c.want*0.1
				if direction == market.Sell {
					wantPrice = 4000 - c.want*0.1
				}
				near(t, c.name+" target price", price, wantPrice)
			}
		}
	}
}
