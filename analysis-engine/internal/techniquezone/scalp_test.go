package techniquezone

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// scalpConfig is the frozen production range-edge configuration (XAU scale).
func scalpConfig() ScalpConfig {
	return ScalpConfig{
		Lookback: 48, ClusterATR: .25, ClusterMinAbs: 0, ClusterPipMult: 2, MinimumTouches: 2, MinimumWickFraction: .25,
		EntryToleranceATR: .25, MaximumEdgeWidthATR: .75, MinimumWidthATR: 1, MaximumWidthATR: 6, MinimumRoomATR: .75,
		BreakCloses: 2, MinimumInsideCloses: 3, InsideLookbackBars: 24, RecentBreakoutLookback: 12,
		RecentBreakoutBufferATR: .15, RecentBreakoutMinSpanATR: .8, FallbackEnabled: true, FallbackMinConfirmations: 1,
		FallbackMinWidthATR: .8, FallbackMaxWidthATR: 8, FallbackWickFraction: .25, ProvisionalEnabled: true,
		PostImpulseEnabled: true, PostImpulseMinDisplaceATR: 3, PostImpulseMaxContractATR: 2.2, PostImpulseMinInside: 4,
		PostImpulseLookbackBars: 36, PostImpulseRecentBars: 6, PipSize: .1, RoundStep: 5,
	}
}

// bar builds a closed candle; the time is the position in the series.
func bar(i int, open, high, low, closePrice float64) market.Candle {
	return market.Candle{Time: int64(i) * 300, Open: open, High: high, Low: low, Close: closePrice}
}

// flat is a quiet wickless inside bar around mid (its wicks are below the
// minimum wick fraction, so it is not a contact).
func flat(i int, mid float64) market.Candle { return bar(i, mid-.2, mid+.25, mid-.25, mid+.2) }

// supportWick pokes below the support and closes back inside (a wick rejection).
func supportWick(i int, level float64) market.Candle {
	return bar(i, level+.5, level+.8, level-.05, level+.6)
}

// resistanceWick pokes above the resistance and closes back inside.
func resistanceWick(i int, level float64) market.Candle {
	return bar(i, level-.5, level+.05, level-.8, level-.6)
}

// twoSidedRange is a clean range between 98 and 103 with four rejected touches
// at each edge, ending with quiet bars inside it.
func twoSidedRange() []market.Candle {
	var bars []market.Candle
	add := func(c func(int) market.Candle) { bars = append(bars, c(len(bars))) }
	for cycle := 0; cycle < 4; cycle++ {
		add(func(i int) market.Candle { return flat(i, 100.5) })
		add(func(i int) market.Candle { return supportWick(i, 98) })
		add(func(i int) market.Candle { return flat(i, 100) })
		add(func(i int) market.Candle { return flat(i, 101) })
		add(func(i int) market.Candle { return resistanceWick(i, 103) })
		add(func(i int) market.Candle { return flat(i, 101.5) })
		add(func(i int) market.Candle { return flat(i, 100.5) })
		add(func(i int) market.Candle { return flat(i, 100) })
	}
	for len(bars) < 40 {
		add(func(i int) market.Candle { return flat(i, 100.5) })
	}
	return bars
}

func build(bars []market.Candle, cfg ScalpConfig) ScalpStructure {
	atr := ATRSeries(bars, 14)
	return BuildScalpStructure(bars, atr, nil, nil, 0, 0, false, cfg)
}

func barrierOn(result ScalpStructure, side string) *ScalpBarrier {
	for i := range result.Barriers {
		if result.Barriers[i].Side == side {
			return &result.Barriers[i]
		}
	}
	return nil
}

func TestCleanTwoSidedRangeIsConfirmed(t *testing.T) {
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	result := build(twoSidedRange(), cfg)
	if result.Range == nil || result.State != RangeStateConfirmed {
		t.Fatalf("expected a confirmed range, got state %q range %+v", result.State, result.Range)
	}
	lower, upper := result.Range.Lower, result.Range.Upper
	if lower.Fallback || upper.Fallback || math.Abs(lower.Level-97.95) > .2 || math.Abs(upper.Level-103.05) > .2 {
		t.Fatalf("range edges wrong: %+v / %+v", lower, upper)
	}
	if lower.Touches < 2 || lower.WickRejections < 2 || upper.Touches < 2 || upper.WickRejections < 2 {
		t.Fatalf("each edge needs two rejected touch episodes: %+v / %+v", lower, upper)
	}
	if want := (lower.Level + upper.Level) / 2; result.Range.Equilibrium != want {
		t.Fatalf("equilibrium %v, want %v", result.Range.Equilibrium, want)
	}
	if !executableEdge(lower) || !executableEdge(upper) {
		t.Fatal("both edges must be executable")
	}
}

func TestPostImpulseRangeWhenDisplacementContracts(t *testing.T) {
	result := build(twoSidedRange(), scalpConfig())
	if result.Range == nil || result.State != RangeStatePostImpulse || !result.Range.PostImpulse {
		t.Fatalf("a >3 ATR swing followed by a contracted rotation is post-impulse, got %q", result.State)
	}
	cfg := scalpConfig()
	cfg.PostImpulseMinDisplaceATR = 10
	if r := build(twoSidedRange(), cfg); r.State != RangeStateConfirmed {
		t.Fatalf("without enough displacement it stays confirmed, got %q", r.State)
	}
	// Post-impulse quality is discounted relative to the confirmed range.
	cfg.PostImpulseMinDisplaceATR = 3
	confirmed := build(twoSidedRange(), func() ScalpConfig { c := scalpConfig(); c.PostImpulseEnabled = false; return c }())
	if result.Range.Quality >= confirmed.Range.Quality {
		t.Fatalf("post-impulse quality %v must be below confirmed %v", result.Range.Quality, confirmed.Range.Quality)
	}
}

func TestMicroSwingContactCountsAsTouchButNotRejection(t *testing.T) {
	bars := twoSidedRange()
	// Replace one support wick with a bare micro swing low: a lower low than its
	// neighbours but with no lower wick (the bar opens and closes on its low).
	bars[9] = bar(9, 98.4, 98.9, 98.0, 98.2)
	bars[8], bars[10] = flat(8, 100.2), flat(10, 100.2)
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	result := build(bars, cfg)
	support := barrierOn(result, "support")
	if support == nil {
		t.Fatal("support barrier missing")
	}
	hasSwing := false
	for _, source := range support.Sources {
		hasSwing = hasSwing || source == "swing"
	}
	if !hasSwing {
		t.Fatalf("a micro swing low must contribute a swing-sourced touch, sources=%v", support.Sources)
	}
	if support.Touches != support.WickRejections+1 {
		t.Fatalf("the swing touch is a touch without a wick rejection: touches=%d wicks=%d", support.Touches, support.WickRejections)
	}
}

func TestBodyHoldRaisesScoreButNotWickEvidence(t *testing.T) {
	plain := build(twoSidedRange(), scalpConfig())
	bars := twoSidedRange()
	// A bullish bar that closes in the bottom 15% of its range on the support.
	bars[1] = bar(1, 98.0, 98.8, 97.95, 98.02)
	held := build(bars, scalpConfig())
	a, b := barrierOn(plain, "support"), barrierOn(held, "support")
	if a == nil || b == nil {
		t.Fatal("support barrier missing")
	}
	if b.BodyHolds < 1 {
		t.Fatalf("body hold not recorded: %+v", b)
	}
	// A body hold is evidence of acceptance of the edge, not a wick rejection.
	if b.WickRejections > a.WickRejections {
		t.Fatalf("body hold must not add wick rejections: %d vs %d", b.WickRejections, a.WickRejections)
	}
}

func TestAcceptedClosesInvalidateTheBarrier(t *testing.T) {
	bars := twoSidedRange()
	// Two consecutive closes beyond the support minus the entry tolerance.
	bars = append(bars, bar(len(bars), 97.6, 97.7, 97.1, 97.2), bar(len(bars)+1, 97.2, 97.3, 96.9, 97.0))
	result := build(bars, scalpConfig())
	if support := barrierOn(result, "support"); support != nil && !support.Fallback {
		t.Fatalf("a primary support with %d accepted closes must not exist: %+v", support.AcceptedCloses, support)
	}
	one := twoSidedRange()
	one = append(one, bar(len(one), 97.6, 97.7, 97.1, 97.2), flat(len(one)+1, 100))
	if support := barrierOn(build(one, scalpConfig()), "support"); support == nil || support.AcceptedCloses != 1 {
		t.Fatalf("a single accepted close does not break the barrier, got %+v", support)
	}
}

func TestMinimumInsideClosesGateTheRangeState(t *testing.T) {
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	cfg.MinimumInsideCloses = 25
	if r := build(twoSidedRange(), cfg); r.Range != nil || r.State != RangeStateNoRange {
		t.Fatalf("more inside closes required than exist must reject the range, got %q", r.State)
	}
	cfg.MinimumInsideCloses = 24
	if r := build(twoSidedRange(), cfg); r.State != RangeStateConfirmed {
		t.Fatalf("exactly enough inside closes accepts the range, got %q", r.State)
	}
}

func TestMinimumAndMaximumRangeWidthAndRoom(t *testing.T) {
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	narrow := cfg
	narrow.MinimumWidthATR = 10
	narrow.MaximumWidthATR = 12
	if r := build(twoSidedRange(), narrow); r.Range != nil {
		t.Fatal("a range narrower than the minimum width must be rejected")
	}
	wide := cfg
	wide.MaximumWidthATR = 2
	wide.MinimumWidthATR = 1
	if r := build(twoSidedRange(), wide); r.Range != nil {
		t.Fatal("a range wider than the maximum width must be rejected")
	}
	roomy := cfg
	roomy.MinimumRoomATR = 3 // the minimum width also becomes 2*room = 6 ATR
	if r := build(twoSidedRange(), roomy); r.Range != nil {
		t.Fatal("insufficient room around the equilibrium must be rejected")
	}
}

func TestBrokenRangeAfterAcceptedDisplacement(t *testing.T) {
	bars := twoSidedRange()
	// Two decisive closes above the prior high with real span.
	bars = append(bars, bar(len(bars), 103.2, 105.0, 103.1, 104.8), bar(len(bars)+1, 104.8, 106.5, 104.6, 106.3))
	result := build(bars, scalpConfig())
	if result.Range != nil || result.State != RangeStateBroken {
		t.Fatalf("accepted breakout displacement is a broken range, got %q", result.State)
	}
}

// oneSided has resistance touches only; the support must come from the local
// extreme fallback.
func oneSided() []market.Candle {
	var bars []market.Candle
	add := func(c func(int) market.Candle) { bars = append(bars, c(len(bars))) }
	for cycle := 0; cycle < 4; cycle++ {
		add(func(i int) market.Candle { return flat(i, 100.5) })
		add(func(i int) market.Candle { return flat(i, 100) })
		add(func(i int) market.Candle { return flat(i, 101) })
		add(func(i int) market.Candle { return resistanceWick(i, 103) })
		add(func(i int) market.Candle { return flat(i, 101.5) })
		add(func(i int) market.Candle { return flat(i, 100.5) })
		add(func(i int) market.Candle { return flat(i, 99.2) })
		add(func(i int) market.Candle { return flat(i, 99.8) })
	}
	// A single low reaction on the support side.
	bars[13] = bar(13, 99.4, 99.7, 98.1, 99.3)
	for len(bars) < 40 {
		add(func(i int) market.Candle { return flat(i, 100.5) })
	}
	return bars
}

func TestFallbackSupportFromLocalExtreme(t *testing.T) {
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	result := build(oneSided(), cfg)
	support := barrierOn(result, "support")
	if support == nil || !support.Fallback {
		t.Fatalf("a controlled fallback support must be created, barriers=%+v", result.Barriers)
	}
	if support.Level != 98.1 {
		t.Fatalf("the fallback sits at the local extreme, got %v", support.Level)
	}
	if result.Range == nil || result.State != RangeStateProvisional || !result.Range.OneSided {
		t.Fatalf("one fallback edge gives a provisional one-sided range, got %q", result.State)
	}
	disabled := cfg
	disabled.FallbackEnabled = false
	if r := build(oneSided(), disabled); r.Range != nil || barrierOn(r, "support") != nil {
		t.Fatal("no fallback support may exist when the fallback is disabled")
	}
}

func TestFallbackResistanceMirrorsSupport(t *testing.T) {
	bars := oneSided()
	// Mirror the structure around 100.5: resistance becomes support and back.
	mirrored := make([]market.Candle, len(bars))
	for i, c := range bars {
		mirrored[i] = market.Candle{Time: c.Time, Open: 201 - c.Open, High: 201 - c.Low, Low: 201 - c.High, Close: 201 - c.Close}
	}
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	result := build(mirrored, cfg)
	resistance := barrierOn(result, "resistance")
	if resistance == nil || !resistance.Fallback {
		t.Fatalf("a fallback resistance must be created, barriers=%+v", result.Barriers)
	}
	if math.Abs(resistance.Level-(201-98.1)) > 1e-9 {
		t.Fatalf("the fallback sits at the local extreme, got %v", resistance.Level)
	}
}

func TestFallbackNeedsEnoughConfirmations(t *testing.T) {
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	cfg.FallbackMinConfirmations = 4
	if r := build(oneSided(), cfg); barrierOn(r, "support") != nil || r.Range != nil {
		t.Fatal("a fallback without enough independent confirmations must not be created")
	}
}

func TestProvisionalRangeCanBeDisabled(t *testing.T) {
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	cfg.ProvisionalEnabled = false
	if r := build(oneSided(), cfg); r.Range != nil || r.State != RangeStateNoRange {
		t.Fatalf("with provisional ranges disabled a one-sided range must not form, got %q", r.State)
	}
}

func TestPrimaryBarrierNeedsTwoWickRejectionsNotJustTouches(t *testing.T) {
	bars := twoSidedRange()
	// Turn three of the four support wicks into bare micro-swing lows: the
	// support cluster keeps four touch episodes but only one wick rejection.
	for _, i := range []int{9, 17, 25} {
		bars[i-1], bars[i], bars[i+1] = flat(i-1, 100.2), bar(i, 98.4, 98.9, 98.0, 98.2), flat(i+1, 100.2)
	}
	cfg := scalpConfig()
	cfg.PostImpulseEnabled = false
	cfg.FallbackEnabled = false
	result := build(bars, cfg)
	if support := barrierOn(result, "support"); support != nil {
		t.Fatalf("touches without two wick rejections must not form a primary barrier: %+v", support)
	}
	if result.Range != nil {
		t.Fatal("a one-sided structure without the fallback has no range")
	}
}

// mirrorBars reflects a market about an axis: every high becomes a low.
func mirrorBars(bars []market.Candle, axis float64) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, b := range bars {
		out[i] = market.Candle{Time: b.Time, Open: axis - b.Open, High: axis - b.Low, Low: axis - b.High, Close: axis - b.Close}
	}
	return out
}

func brokenOn(result ScalpStructure, side string) *BrokenBarrier {
	for i := range result.Broken {
		if result.Broken[i].Side == side {
			return &result.Broken[i]
		}
	}
	return nil
}

func TestAnEdgePriceAcceptedThroughIsKeptAsBroken(t *testing.T) {
	bars := twoSidedRange()
	first := len(bars)
	// Two consecutive closes below the support less the entry tolerance.
	bars = append(bars, bar(first, 97.6, 97.7, 97.1, 97.2), bar(first+1, 97.2, 97.3, 96.9, 97.0))
	result := build(bars, scalpConfig())
	broken := brokenOn(result, "support")
	if broken == nil {
		t.Fatalf("the support price accepted through must be kept: %+v", result.Broken)
	}
	if broken.BreakIndex != first+1 {
		t.Fatalf("the break is the bar on which the accepted run reached BreakCloses (%d), got %d", first+1, broken.BreakIndex)
	}
	if math.Abs(broken.Level-97.95) > .2 || broken.Touches < 2 || broken.WickRejections < 2 || broken.Tolerance <= 0 {
		t.Fatalf("the broken edge carries what the builder knew of it: %+v", broken)
	}
	if brokenOn(result, "resistance") != nil {
		t.Fatalf("the untouched resistance is not broken: %+v", result.Broken)
	}
}

func TestAResistanceBreaksAsTheExactMirrorOfASupport(t *testing.T) {
	bars := twoSidedRange()
	first := len(bars)
	bars = append(bars, bar(first, 97.6, 97.7, 97.1, 97.2), bar(first+1, 97.2, 97.3, 96.9, 97.0))
	down := build(bars, scalpConfig())
	up := build(mirrorBars(bars, 201), scalpConfig())
	a, b := brokenOn(down, "support"), brokenOn(up, "resistance")
	if a == nil || b == nil {
		t.Fatalf("both sides must keep the broken edge: %+v / %+v", down.Broken, up.Broken)
	}
	if a.BreakIndex != b.BreakIndex || a.Touches != b.Touches || a.WickRejections != b.WickRejections ||
		a.FirstTouchIndex != b.FirstTouchIndex || a.LastTouchIndex != b.LastTouchIndex || math.Abs(a.Level-(201-b.Level)) > 1e-9 {
		t.Fatalf("a resistance must break as the mirror of a support: %+v vs %+v", a, b)
	}
}

func TestOneAcceptedCloseIsNotABreakAndAnUntouchedRangeHasNoneBroken(t *testing.T) {
	if got := build(twoSidedRange(), scalpConfig()).Broken; len(got) != 0 {
		t.Fatalf("a clean range has no broken edge: %+v", got)
	}
	one := twoSidedRange()
	one = append(one, bar(len(one), 97.6, 97.7, 97.1, 97.2), flat(len(one)+1, 100))
	if got := build(one, scalpConfig()).Broken; len(got) != 0 {
		t.Fatalf("a single accepted close is not a break: %+v", got)
	}
}

func TestABreakIsReclaimedOnlyByConsecutiveClosesBackInside(t *testing.T) {
	bars := twoSidedRange()
	bars = append(bars, bar(len(bars), 97.6, 97.7, 97.1, 97.2), bar(len(bars)+1, 97.2, 97.3, 96.9, 97.0))
	broken := brokenOn(build(bars, scalpConfig()), "support")
	if broken == nil {
		t.Fatal("no broken support")
	}
	if BreakReclaimed(bars, *broken, 2) {
		t.Fatal("price has not come back: not reclaimed")
	}
	one := append(append([]market.Candle(nil), bars...), bar(len(bars), 97.2, 98.9, 97.1, 98.7))
	if BreakReclaimed(one, *broken, 2) {
		t.Fatal("one close back inside is not a reclaim of two")
	}
	two := append(append([]market.Candle(nil), one...), bar(len(one), 98.7, 99.1, 98.5, 98.9))
	if !BreakReclaimed(two, *broken, 2) {
		t.Fatal("two consecutive closes back inside reclaim the edge")
	}
	broke := append(append([]market.Candle(nil), one...), bar(len(one), 98.7, 98.8, 96.9, 97.0), bar(len(one)+1, 98.7, 99.1, 98.5, 98.9))
	if BreakReclaimed(broke, *broken, 2) {
		t.Fatal("a close that falls back out resets the run")
	}
	// The answer at a bar never depends on a later one.
	if BreakReclaimed(two[:len(one)], *broken, 2) {
		t.Fatal("the prefix ending before the second close must still say not reclaimed")
	}
}

func TestAReclaimedEdgeThatBreaksAgainIsRetiredByTheLatestBreak(t *testing.T) {
	bars := twoSidedRange()
	first := len(bars)
	bars = append(bars, bar(first, 97.6, 97.7, 97.1, 97.2), bar(first+1, 97.2, 97.3, 96.9, 97.0)) // break
	broken := brokenOn(build(bars, scalpConfig()), "support")
	if broken == nil || broken.BreakCloses != 2 {
		t.Fatalf("the builder keeps the break rule it applied: %+v", broken)
	}
	back := append(append([]market.Candle(nil), bars...), bar(len(bars), 97.2, 98.9, 97.1, 98.7), bar(len(bars)+1, 98.7, 99.1, 98.5, 98.9))
	again := append(append([]market.Candle(nil), back...), bar(len(back), 98.9, 99.0, 97.0, 97.2), bar(len(back)+1, 97.2, 97.3, 96.8, 97.0))
	if reclaimed, latest := BreakStatus(back, *broken, 2); !reclaimed || latest != first+1 {
		t.Fatalf("after the reclaim the edge is restored: %v %d", reclaimed, latest)
	}
	if reclaimed, latest := BreakStatus(again, *broken, 2); reclaimed || latest != len(back)+1 {
		t.Fatalf("a second accepted run retires it again and the latest break is that one: %v %d (want %d)", reclaimed, latest, len(back)+1)
	}
	if BreakReclaimed(again, *broken, 2) {
		t.Fatal("BreakReclaimed must follow the latest event")
	}
}

// Every break the builder reports completed on a candle it was given: the structure at a
// candle contains nothing from after it, whatever the prefix.
func TestNoBrokenEdgeIsEverDatedAfterTheCandlesGiven(t *testing.T) {
	bars := twoSidedRange()
	bars = append(bars, bar(len(bars), 97.6, 97.7, 97.1, 97.2), bar(len(bars)+1, 97.2, 97.3, 96.9, 97.0), flat(len(bars)+2, 98.5), flat(len(bars)+3, 98.6))
	for n := 20; n <= len(bars); n++ {
		for _, b := range build(bars[:n], scalpConfig()).Broken {
			if b.BreakIndex < 0 || b.BreakIndex >= n || b.LastTouchIndex >= n || b.FirstTouchIndex >= n {
				t.Fatalf("prefix %d reports a break dated %d (touches %d-%d)", n, b.BreakIndex, b.FirstTouchIndex, b.LastTouchIndex)
			}
		}
	}
}
