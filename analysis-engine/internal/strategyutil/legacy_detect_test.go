package strategyutil

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func settings(t *testing.T) LegacyDetectorSettings {
	t.Helper()
	s, err := ParseLegacyDetectorSettings(legacyfixture.Params(nil))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// detector binds a BUY detector at price 100.5 (the last close), ATR 1.
func detector(t *testing.T, direction market.Direction, mutate func(*analysiscontext.LegacyFrame)) *LegacyDetector {
	t.Helper()
	bars := legacyfixture.Bars(10, 100)
	bars[9] = market.Candle{Time: 3000, Open: 99.4, High: 100.8, Low: 98.5, Close: 100.5}
	ctx := legacyfixture.Context(bars, 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		if mutate != nil {
			mutate(f)
		}
	})
	d, ok := NewLegacyDetector(ctx, market.M5, direction, settings(t))
	if !ok {
		t.Fatal("detector must bind to a complete frame")
	}
	return d
}

func scoredZone(side string, low, high, score float64, source string) techniquezone.Zone {
	return techniquezone.Zone{Bottom: low, Top: high, Side: side, Source: source, Sources: []string{source}, OriginIndex: 2, BreakIndex: -1, Score: score}
}

func TestParseLegacyDetectorSettingsFailsClosedOnEveryMissingKey(t *testing.T) {
	for _, key := range LegacyDetectorParameterKeys {
		p := legacyfixture.Params(nil)
		delete(p, key)
		if _, err := ParseLegacyDetectorSettings(p); err == nil {
			t.Fatalf("a missing %q must be an error", key)
		}
	}
	p := legacyfixture.Params(map[string]any{"pip_size": 0.0})
	if _, err := ParseLegacyDetectorSettings(p); err == nil {
		t.Fatal("a non-positive pip size is invalid")
	}
}

func TestNewLegacyDetectorNeedsAWarmFrame(t *testing.T) {
	if _, ok := NewLegacyDetector(&analysiscontext.MarketContext{}, market.M5, market.Buy, settings(t)); ok {
		t.Fatal("no legacy read, no detector")
	}
	short := legacyfixture.Context(legacyfixture.Bars(4, 100), 1, nil)
	if _, ok := NewLegacyDetector(short, market.M5, market.Buy, settings(t)); ok {
		t.Fatal("fewer than five bars is not enough")
	}
	d := detector(t, market.Buy, nil)
	if d.Price != 100.5 || d.ATR != 1 {
		t.Fatalf("price/ATR = %v/%v", d.Price, d.ATR)
	}
}

func TestCandidateZonesKeepOnlyTheTradeSideAndDeduplicate(t *testing.T) {
	d := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) {
		a := scoredZone("demand", 99, 100, 5, "supply_demand")
		f.Zones = []techniquezone.Zone{a, scoredZone("supply", 101, 102, 9, "supply_demand"), scoredZone("demand", 98, 99, 4, "order_block")}
		f.OrderBlocks = []techniquezone.Zone{a, scoredZone("demand", 98, 99, 4, "order_block")} // the same two again
	})
	got := d.CandidateZones()
	if len(got) != 2 || got[0].Low() != 99 || got[1].Low() != 98 {
		t.Fatalf("candidate zones = %+v", got)
	}
}

func TestEntryValidityMatchesTheDetectorRules(t *testing.T) {
	buy := detector(t, market.Buy, nil) // price 100.5, max distance 2 ATR
	for _, tc := range []struct {
		low, high float64
		want      bool
	}{{99, 100, true}, {100.4, 101, true}, {97, 98.4, false}, {101, 102, false}, {98.5, 98.5, true}, {98.4, 98.4, false}} {
		if got := buy.EntryValid(scoredZone("demand", tc.low, tc.high, 1, "x")); got != tc.want {
			t.Fatalf("BUY zone [%v,%v] valid = %v, want %v", tc.low, tc.high, got, tc.want)
		}
	}
	sell := detector(t, market.Sell, nil)
	for _, tc := range []struct {
		low, high float64
		want      bool
	}{{100, 101, true}, {102, 103, true}, {104, 105, false}, {99, 100.4, false}} {
		if got := sell.EntryValid(scoredZone("supply", tc.low, tc.high, 1, "x")); got != tc.want {
			t.Fatalf("SELL zone [%v,%v] valid = %v, want %v", tc.low, tc.high, got, tc.want)
		}
	}
	if !buy.LevelValid(100.5) || !buy.LevelValid(99) || buy.LevelValid(101) {
		t.Fatal("a BUY level must be at or below price")
	}
	if !sell.LevelValid(100.5) || !sell.LevelValid(102) || sell.LevelValid(100) {
		t.Fatal("a SELL level must be at or above price")
	}
}

func TestBestValidZonePrefersScoreThenDistanceThenLowerBand(t *testing.T) {
	d := detector(t, market.Buy, nil)
	low := scoredZone("demand", 99, 100, 5, "supply_demand")
	high := scoredZone("demand", 98.6, 99.4, 9, "supply_demand")
	got, _, ok := d.BestValidZone([]techniquezone.Zone{low, high})
	if !ok || got.Score != 9 {
		t.Fatalf("the higher score wins: %+v", got)
	}
	near := scoredZone("demand", 100, 100.4, 5, "supply_demand")
	got, _, _ = d.BestValidZone([]techniquezone.Zone{low, near})
	if got.Low() != 100 {
		t.Fatalf("equal score: the nearer zone wins, got %+v", got)
	}
	twin := scoredZone("demand", 99, 100, 5, "supply_demand")
	twin.Bottom, twin.Top = 98.9, 99.9
	got, _, _ = d.BestValidZone([]techniquezone.Zone{low, twin})
	if got.Low() != 98.9 && got.Low() != 99 {
		t.Fatalf("unexpected tie result %+v", got)
	}
	if _, _, ok := d.BestValidZone([]techniquezone.Zone{scoredZone("demand", 90, 91, 9, "x")}); ok {
		t.Fatal("a zone outside the entry distance is not valid")
	}
}

func TestWideZonesAreClippedToTheProximalBand(t *testing.T) {
	d := detector(t, market.Buy, nil)
	wide := scoredZone("demand", 97, 100, 8, "supply_demand") // 3 ATR wide, max is 1.5 ATR
	got, proximal, ok := d.BestValidZone([]techniquezone.Zone{wide})
	if !ok || !proximal || got.Top != 100 || math.Abs(got.Bottom-99.5) > 1e-12 {
		t.Fatalf("a BUY keeps the proximal (upper) half ATR: %+v proximal=%v", got, proximal)
	}
	d = detector(t, market.Sell, nil)
	wideSupply := scoredZone("supply", 100.5, 104, 8, "supply_demand")
	got, proximal, ok = d.BestValidZone([]techniquezone.Zone{wideSupply})
	if !ok || !proximal || got.Bottom != 100.5 || math.Abs(got.Top-101) > 1e-12 {
		t.Fatalf("a SELL keeps the proximal (lower) half ATR: %+v", got)
	}
}

func TestImbalanceEntryIsClippedToTheFVGWidthCap(t *testing.T) {
	fvg := scoredZone("demand", 90, 100, 5, "bullish_fvg")
	got, clipped := OptimizeImbalanceEntryZone(fvg, market.Buy, 5, "")
	if !clipped || got.Bottom != 95 || got.Top != 100 {
		t.Fatalf("a BUY FVG keeps its upper 5: %+v", got)
	}
	got, clipped = OptimizeImbalanceEntryZone(scoredZone("supply", 90, 100, 5, "bearish_fvg"), market.Sell, 5, "")
	if !clipped || got.Bottom != 90 || got.Top != 95 {
		t.Fatalf("a SELL FVG keeps its lower 5: %+v", got)
	}
	if _, clipped := OptimizeImbalanceEntryZone(scoredZone("demand", 90, 100, 5, "order_block"), market.Buy, 5, ""); clipped {
		t.Fatal("an order block is not an imbalance")
	}
	if _, clipped := OptimizeImbalanceEntryZone(scoredZone("demand", 90, 100, 5, "order_block"), market.Buy, 5, "FVG"); !clipped {
		t.Fatal("a structural kind naming an FVG is an imbalance")
	}
	merged := scoredZone("demand", 90, 100, 5, "order_block")
	merged.Sources = []string{"order_block", "flip_zone", "bullish_fvg"}
	if _, clipped := OptimizeImbalanceEntryZone(merged, market.Buy, 5, ""); !clipped {
		t.Fatal("any FVG member makes the zone an imbalance")
	}
	if _, clipped := OptimizeImbalanceEntryZone(fvg, market.Buy, 0, ""); clipped {
		t.Fatal("a non-positive cap disables the clip")
	}
}

func TestGrabLookupsAreDirectionAndPoolAware(t *testing.T) {
	d := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) {
		pool := techniquezone.Pool{Side: "sell", Level: 99.2, Band: .1, Touches: 3}
		f.Grabs = []techniquezone.Grab{
			{Index: 5, Direction: "bull", Grade: "B", Pool: pool},
			{Index: 7, Direction: "bear", Grade: "A", Pool: techniquezone.Pool{Side: "buy", Level: 99.2, Band: .1}},
			{Index: 8, Direction: "bull", Grade: "A", Pool: techniquezone.Pool{Side: "sell", Level: 90, Band: .1}},
		}
	})
	z := scoredZone("demand", 99, 100, 5, "x")
	if g := d.ZoneGrab(z); g == nil || g.Index != 5 {
		t.Fatalf("only the bull grab on a sell pool inside the zone counts: %+v", g)
	}
	if g := d.LevelGrab(99.25, 0); g == nil || g.Index != 5 {
		t.Fatalf("the level grab matches within the pool band: %+v", g)
	}
	if g := d.LevelGrab(95, 0); g != nil {
		t.Fatalf("a far level has no grab: %+v", g)
	}
	// The latest matching grab wins.
	d.Frame.Grabs = append(d.Frame.Grabs, techniquezone.Grab{Index: 9, Direction: "bull", Grade: "A", Pool: techniquezone.Pool{Side: "sell", Level: 99.4, Band: .1}})
	if g := d.ZoneGrab(z); g == nil || g.Index != 9 {
		t.Fatalf("the latest grab is preferred: %+v", g)
	}
}

func TestRecentCHoCHUsesTheLookbackWindow(t *testing.T) {
	d := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) {
		f.Breaks = []techniquezone.Break{{Kind: "CHoCH", Direction: "up", Index: 5}, {Kind: "BOS", Direction: "up", Index: 9}, {Kind: "CHoCH", Direction: "down", Index: 9}}
	})
	// With ten bars and a 3-bar lookback the window starts at bar 6.
	if d.RecentCHoCH(3) {
		t.Fatal("a CHoCH at bar 5 is outside a 3-bar lookback; a BOS or an opposite CHoCH never counts")
	}
	if !d.RecentCHoCH(4) {
		t.Fatal("a wider lookback reaches the bar-5 CHoCH")
	}
	sell := detector(t, market.Sell, func(f *analysiscontext.LegacyFrame) {
		f.Breaks = []techniquezone.Break{{Kind: "CHoCH", Direction: "down", Index: 9}}
	})
	if !sell.RecentCHoCH(3) {
		t.Fatal("a SELL looks for a down CHoCH")
	}
}

func TestReactionUsesSeparateTouchAndConfirmationWindows(t *testing.T) {
	d := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) {
		f.Bars = append([]market.Candle(nil), f.Bars...)
		// A touch of the band on bar 5 only; a bullish pin on the last bar.
		for i := range f.Bars {
			f.Bars[i] = market.Candle{Time: int64(i+1) * 300, Open: 103, High: 103.5, Low: 102.5, Close: 103}
		}
		f.Bars[5] = market.Candle{Time: 1800, Open: 99.5, High: 100.2, Low: 98.8, Close: 100.1}
		f.Bars[9] = market.Candle{Time: 3000, Open: 102.2, High: 103.4, Low: 101.0, Close: 103.2}
	})
	if got := d.Reaction(99, 100, nil); got != nil {
		t.Fatalf("with a 3-bar touch window the bar-5 touch is invisible: %+v", got)
	}
	// A wider touch window finds the older touch; the confirming bar is still
	// searched in the last 3 bars only (here the final bullish pin).
	if got := d.ReactionWithLookbacks(99, 100, nil, 6, 3); got == nil || got.Type != "wick_rejection" || got.TouchIndex != 5 || got.ConfirmationIndex != 9 {
		t.Fatalf("a pin on the last bar confirms against the bar-5 touch: %+v", got)
	}
	// A confirmation bar outside its window cannot confirm, whatever the touch window.
	if got := d.ReactionWithLookbacks(99, 100, nil, 6, 1); got == nil || got.ConfirmationIndex != 9 {
		t.Fatalf("a one-bar confirmation window still sees the last bar: %+v", got)
	}
	// A graded grab takes priority over the candle-shape patterns.
	grab := &techniquezone.Grab{Index: 9, Grade: "B"}
	if got := d.ReactionWithLookbacks(99, 100, grab, 6, 3); got == nil || got.Type != "sweep_reclaim" {
		t.Fatalf("a graded grab on the confirming bar is a sweep reclaim: %+v", got)
	}
	if got := d.ReactionWithLookbacks(99, 100, &techniquezone.Grab{Index: 9, Grade: "C"}, 6, 3); got == nil || got.Type == "sweep_reclaim" {
		t.Fatalf("only A and B grabs confirm: %+v", got)
	}
}

func TestFinishAppliesLevelEntryAndConfluenceGates(t *testing.T) {
	d := detector(t, market.Buy, nil)
	factors := confluence.Factors{HTFAligned: true, Touches: 3, WickRejection: true}
	z := scoredZone("demand", 99, 100, 0, "x") // factor scoring: 4+3+3 = 10/20.5 = two stars
	if r := d.Finish(100, z, factors, "", nil, nil); r == nil || r.Stars != 2 {
		t.Fatalf("expected a two-star result, got %+v", r)
	}
	if r := d.Finish(101, z, factors, "", nil, nil); r != nil {
		t.Fatal("a BUY level above price is invalid")
	}
	if r := d.Finish(100, scoredZone("demand", 90, 91, 0, "x"), factors, "", nil, nil); r != nil {
		t.Fatal("an entry beyond the maximum distance is invalid")
	}
	if r := d.Finish(100, z, confluence.Factors{HTFAligned: true}, "", nil, nil); r != nil {
		t.Fatal("4/20.5 is one star, below the floor of two")
	}
	// A touched zone is capped at two stars even with a very high score.
	touched := scoredZone("demand", 99, 100, 24, "x")
	touched.Touches = 1
	if r := d.Finish(100, touched, factors, "", nil, nil); r == nil || r.Stars != 2 {
		t.Fatalf("a touched zone is capped at 2: %+v", r)
	}
	fresh := scoredZone("demand", 99, 100, 24, "x")
	if r := d.Finish(100, fresh, factors, "", nil, nil); r == nil || r.Stars != 3 {
		t.Fatalf("a 24/24.5 fresh zone is three stars: %+v", r)
	}
}

func TestFinishRecordsTheClippedEntryAndKeepsStructuralBounds(t *testing.T) {
	d := detector(t, market.Buy, nil)
	fvg := scoredZone("demand", 90.0, 100.0, 12, "bullish_fvg")
	low, high := 90.0, 100.0
	r := d.Finish(100, fvg, confluence.Factors{HTFAligned: true}, "", &low, &high)
	if r == nil {
		t.Fatal("the clipped entry is valid")
	}
	if r.Zone.Bottom != 95 || r.Zone.Top != 100 {
		t.Fatalf("entry clipped to the proximal 5: %+v", r.Zone)
	}
	if r.StructuralLow != 90 || r.StructuralHigh != 100 {
		t.Fatalf("the full FVG remains the structural band: %v-%v", r.StructuralLow, r.StructuralHigh)
	}
}

func TestFinishFibonacciTouchLiftsTheStars(t *testing.T) {
	// 7.0 of zone score is 1 star; a retracement touch adds 2.5 -> 9.5/24.5 = .388 (still 1);
	// a score of 7.2 + 2.5 = 9.7/24.5 = .396 crosses the two-star cut.
	ladder := fib.Ladder(90, 110, true)
	d := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) { f.FibLadder = ladder })
	z := scoredZone("demand", 99, 100, 7.2, "x")
	// The 0.5 level of 90..110 is 100: the zone key sits on it.
	r := d.Finish(100, z, confluence.Factors{}, "", nil, nil)
	if r == nil || !r.FibTouch || r.Stars != 2 {
		t.Fatalf("a fib touch must lift 7.2 to two stars: %+v", r)
	}
	d.Settings.FibonacciEnabled = false
	if r := d.Finish(100, z, confluence.Factors{}, "", nil, nil); r != nil {
		t.Fatalf("without fibonacci the zone is one star and fails the floor: %+v", r)
	}
}

func TestStrongBodyBreakAndRejectionReadTheLatestBar(t *testing.T) {
	d := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) {
		f.Bars = append([]market.Candle(nil), f.Bars...)
		f.Bars[9] = market.Candle{Time: 3000, Open: 100, High: 102, Low: 99.9, Close: 101.8}
		f.Swings = []techniquezone.Swing{{Index: 3, Kind: "high", Price: 100}}
	})
	if !d.StrongBodyBreak(.6) {
		t.Fatal("a 0.86 body closing above the last swing high is a strong body break")
	}
	if d.StrongBodyBreak(.9) {
		t.Fatal("the body fraction threshold must be honoured")
	}
	d.Frame.Swings = []techniquezone.Swing{{Index: 3, Kind: "high", Price: 101.9}}
	if d.StrongBodyBreak(.6) {
		t.Fatal("a close below the swing high is not a break")
	}
	d.Frame.Swings = nil
	if !d.StrongBodyBreak(.6) {
		t.Fatal("with no swing high the body test alone decides")
	}
	pin := detector(t, market.Buy, func(f *analysiscontext.LegacyFrame) {
		f.Bars = append([]market.Candle(nil), f.Bars...)
		f.Bars[9] = market.Candle{Time: 3000, Open: 100.3, High: 101.2, Low: 99.6, Close: 101.0}
	})
	if !pin.Rejection() {
		t.Fatal("a bullish pin is a BUY rejection")
	}
	if detector(t, market.Sell, func(f *analysiscontext.LegacyFrame) {
		f.Bars = append([]market.Candle(nil), f.Bars...)
		f.Bars[9] = market.Candle{Time: 3000, Open: 100.3, High: 101.2, Low: 99.6, Close: 101.0}
	}).Rejection() {
		t.Fatal("the same bar is not a SELL rejection")
	}
}

func TestStructuralDirectionAndGatesUseTheDetectorContractRead(t *testing.T) {
	ctx := legacyfixture.Context(legacyfixture.Bars(10, 100), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.LocalStructure, r.HTFBias = "down", "up"
		f.Range = &fib.DealingRange{Zone: "premium"}
		f.Regime = regime.State{Kind: "chop", RangeLow: 90, RangeHigh: 110}
	})
	if got := StructuralDirection(ctx); got != market.Sell {
		t.Fatalf("counter-trend is allowed: the local structure decides, got %q", got)
	}
	ctx.Legacy.AllowCounterTrend = false
	if got := StructuralDirection(ctx); got != market.Buy {
		t.Fatalf("with counter-trend off the higher timeframe decides, got %q", got)
	}
	ctx.Legacy.LocalStructure, ctx.Legacy.HTFBias = "range", "unknown"
	if got := StructuralDirection(ctx); got.IsValid() {
		t.Fatalf("an undecided read has no direction, got %q", got)
	}
	tf := ctx.Timeframes[market.M5]
	if PremiumDiscountAllows(tf, market.Buy, false) || !PremiumDiscountAllows(tf, market.Sell, false) {
		t.Fatal("premium forbids a loose BUY and allows a loose SELL")
	}
	if !InChop(tf) {
		t.Fatal("the detector-contract regime is chop")
	}
	if !ChopEdgeAllows(tf, market.Sell, 108, 109, .25) || ChopEdgeAllows(tf, market.Sell, 100, 101, .25) {
		t.Fatal("a SELL must sit in the upper quarter of the chop range")
	}
	// Without a frame the canonical read is used.
	canonical := &analysiscontext.MarketContext{Bias: analysiscontext.BiasContext{Direction: market.Buy}}
	if StructuralDirection(canonical) != market.Buy {
		t.Fatal("a context without the frame falls back to the canonical bias")
	}
}
