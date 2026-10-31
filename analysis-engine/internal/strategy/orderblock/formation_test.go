package orderblock

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const (
	formationBase = int64(1_800_000_000) // a multiple of 900
	formationStep = int64(900)           // an M15 candle
)

// formationFrame is an M15 frame with a bearish candle (20), a four-candle bullish leg (21-24) and a pullback candle (25), over a flat chop.
func formationFrame(swings []techniquezone.Swing) *analysiscontext.LegacyFrame {
	var bars []market.Candle
	add := func(open, close, upper, lower float64) {
		hi, lo := open, close
		if close > open {
			hi, lo = close, open
		}
		bars = append(bars, market.Candle{Time: formationBase + int64(len(bars))*formationStep, Open: open, Close: close, High: hi + upper, Low: lo - lower})
	}
	for i := 0; i < 20; i++ {
		add(100, 100, 0.1, 0.1)
	}
	add(100, 99, 0.1, 0.1)        // 20: the block's candle, body 99-100
	add(99, 99.8, 0.05, 0.05)     // 21 \
	add(99.8, 100.6, 0.05, 0.05)  // 22  | the leg: 3.2 over four strong candles
	add(100.6, 101.4, 0.05, 0.05) // 23  |
	add(101.4, 102.2, 0.05, 0.05) // 24 /
	add(102.2, 101.5, 0.1, 0.1)   // 25: the pullback ends the leg
	atr := make([]float64, len(bars))
	for i := range atr {
		atr[i] = 1
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: atr, DetectorATR: 1, Swings: swings}
}

func formationStrategy() *Strategy {
	return &Strategy{cfg: Config{
		DisplacementATRMult: 1.5, MomentumBodyFraction: 0.6, BreakBufferATR: 0.1, MaximumZoneATR: 3, EntryMaxWidthPrice: 5, PipSize: 0.01,
		SweepLookbackBars: 6, SweepPivotBars: 2, SweepLevelMaxAgeBars: 96,
	}}
}

// A block exists from the close of the structure bar at which its break became KNOWABLE. The swing the leg breaks is confirmed at candle 24 (a wide pivot): the
// shared labelling dates the break at candle 22, the first close above it; the strategy dates it at 24, the first close above it once it is confirmed, so the
// block is not a block (and not touchable) before candle 24 has closed.
func TestABlockExistsOnlyFromTheBarItsBreakBecameKnowable(t *testing.T) {
	swing := techniquezone.Swing{Index: 19, Kind: "high", Price: 100.3, ConfirmedIndex: 24}
	frame := formationFrame([]techniquezone.Swing{swing})

	shared := techniquezone.StructureBreaks(frame.Swings, frame.Bars, false, 5)
	if len(shared) == 0 || shared[0].Index != 22 {
		t.Fatalf("the premise: the shared labelling dates the break at candle 22, got %+v", shared)
	}

	instances := formationStrategy().structureInstances(frame, market.M15, func(string) {})
	if len(instances) != 1 {
		t.Fatalf("instances = %d, want the demand block", len(instances))
	}
	in := instances[0]
	if in.Side != "buy" || in.FrameOrigin != 20 || in.FrameConfirmed != 24 || in.BreakKind != "BOS" {
		t.Fatalf("instance = %+v, want a buy block of candle 20 confirmed at 24 by a BOS", in)
	}

	// Bound to M5: nothing may touch the block before the M5 bar that opens at the close of candle 24.
	var m5 []market.Candle
	for ts := formationBase; ts < formationBase+30*formationStep; ts += 300 {
		m5 = append(m5, market.Candle{Time: ts})
	}
	bound := bind(market.M15, frame.Bars, m5, instances, false)
	closeOf24 := formationBase + 25*formationStep
	if len(bound) != 1 || m5[bound[0].TouchableFrom].Time != closeOf24 {
		t.Fatalf("touchable from %d, want the M5 bar opening at the close of candle 24 (%d)", m5[bound[0].TouchableFrom].Time, closeOf24)
	}
}

// With the swing confirmed only after the leg, the leg broke nothing that was knowable inside it: no block.
func TestALegWhoseSwingIsConfirmedAfterItIsNoBlock(t *testing.T) {
	swing := techniquezone.Swing{Index: 19, Kind: "high", Price: 100.3, ConfirmedIndex: 26}
	frame := formationFrame([]techniquezone.Swing{swing})
	if shared := techniquezone.StructureBreaks(frame.Swings, frame.Bars, false, 5); len(shared) == 0 {
		t.Fatal("the premise: the shared labelling finds a break")
	}
	if got := formationStrategy().structureInstances(frame, market.M15, func(string) {}); len(got) != 0 {
		t.Fatalf("a block was found from a break that was not knowable inside its leg: %+v", got)
	}
}

// A break against the structure as it was is a CHoCH, whatever the trend turned into afterwards; the block carries the kind.
func TestTheBlockCarriesTheKindOfItsCausalBreak(t *testing.T) {
	// Falling structure before the leg (lower highs 100.6 then 100.3, lower lows 99.2 then 98.9), then the leg breaks the last high 100.3: a CHoCH.
	swings := []techniquezone.Swing{
		{Index: 3, Kind: "high", Price: 100.6, ConfirmedIndex: 5}, {Index: 6, Kind: "low", Price: 99.2, ConfirmedIndex: 8},
		{Index: 10, Kind: "high", Price: 100.3, ConfirmedIndex: 12}, {Index: 13, Kind: "low", Price: 98.9, ConfirmedIndex: 15},
	}
	frame := formationFrame(swings)
	got := formationStrategy().structureInstances(frame, market.M15, func(string) {})
	if len(got) != 1 || got[0].BreakKind != "CHoCH" || got[0].FrameConfirmed != 22 {
		t.Fatalf("instances = %+v, want one block confirmed at 22 by a CHoCH", got)
	}
}
