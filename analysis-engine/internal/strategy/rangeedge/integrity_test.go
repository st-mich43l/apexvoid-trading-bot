package rangeedge

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

func integrity() Integrity {
	return Integrity{ReclaimCloses: 2, MinimumInsideCloses: 3, PressureBars: 4, PressureMinimumRiseATR: 1, RejectionCloseStrength: .6, MaximumWidthATR: 6, PeriodSeconds: 300}
}

// candle builds a closed M5 candle at position i.
func candle(i int, open, high, low, closePrice float64) market.Candle {
	return market.Candle{Time: int64(i) * 300, Open: open, High: high, Low: low, Close: closePrice}
}

// quiet is a candle inside the range around mid.
func quiet(i int, mid float64) market.Candle { return candle(i, mid-.1, mid+.2, mid-.2, mid+.05) }

// resistanceEdge is a barrier at level (ATR is 1, tolerance .25).
func resistanceEdge(level float64, first, last, accepted int) techniquezone.ScalpBarrier {
	return techniquezone.ScalpBarrier{
		Side: "resistance", Level: level, Low: level - .2, High: level + .2, Touches: 3, WickRejections: 3,
		FirstTouchIndex: first, LastTouchIndex: last, AcceptedCloses: accepted,
	}
}

func supportEdge(level float64, first, last, accepted int) techniquezone.ScalpBarrier {
	b := resistanceEdge(level, first, last, accepted)
	b.Side = "support"
	return b
}

// rangeBars oscillates around 100 for n candles, ending on a rejection of 103: a wick
// above it that closes back inside, below the previous close.
func rangeBars(n int) []market.Candle {
	var bars []market.Candle
	for i := 0; i < n-2; i++ {
		bars = append(bars, quiet(i, 100.5+math.Sin(float64(i))*.8))
	}
	// price comes up to the edge, then the rejection closes below that close
	bars = append(bars, candle(n-2, 102.3, 102.9, 102.2, 102.7))
	return append(bars, candle(n-1, 102.6, 103.3, 102.0, 102.1))
}

func input(bars []market.Candle, edge, opposite techniquezone.ScalpBarrier) EdgeInput {
	return EdgeInput{Bars: bars, ATR: 1, Sell: true, Edge: edge, Opposite: opposite, ConfirmIndex: len(bars) - 1, GapBars: 24,
		Barriers: []techniquezone.ScalpBarrier{edge, opposite}}
}

// mirror reflects every price about an axis and flips the side of the edge, so a
// SELL of a resistance becomes a BUY of a support.
func mirror(in EdgeInput, axis float64) EdgeInput {
	out := in
	out.Sell = !in.Sell
	out.Bars = make([]market.Candle, len(in.Bars))
	for i, b := range in.Bars {
		out.Bars[i] = market.Candle{Time: b.Time, Open: axis - b.Open, High: axis - b.Low, Low: axis - b.High, Close: axis - b.Close}
	}
	flip := func(b techniquezone.ScalpBarrier) techniquezone.ScalpBarrier {
		b.Level, b.Low, b.High = axis-b.Level, axis-b.High, axis-b.Low
		if b.Side == "resistance" {
			b.Side = "support"
		} else {
			b.Side = "resistance"
		}
		return b
	}
	out.Edge, out.Opposite = flip(in.Edge), flip(in.Opposite)
	out.Barriers = nil
	for _, b := range in.Barriers {
		out.Barriers = append(out.Barriers, flip(b))
	}
	out.Broken = nil
	for _, b := range in.Broken {
		b.Level, b.Low, b.High = axis-b.Level, axis-b.High, axis-b.Low
		if b.Side == "resistance" {
			b.Side = "support"
		} else {
			b.Side = "resistance"
		}
		out.Broken = append(out.Broken, b)
	}
	return out
}

func same(t *testing.T, a, b Assessment) {
	t.Helper()
	if a.State != b.State || a.Reject != b.Reject || a.AcceptedCloses != b.AcceptedCloses || a.RetiredBreakIndex != b.RetiredBreakIndex || a.EpochIndex != b.EpochIndex ||
		math.Abs(a.BreakDistanceATR-b.BreakDistanceATR) > 1e-9 || math.Abs(a.RejectionCloseLocation-b.RejectionCloseLocation) > 1e-9 ||
		math.Abs(a.ReclaimDepthATR-b.ReclaimDepthATR) > 1e-9 || math.Abs(a.PressureRiseATR-b.PressureRiseATR) > 1e-9 || math.Abs(a.BodyATR-b.BodyATR) > 1e-9 {
		t.Fatalf("BUY and SELL must be exact mirrors:\n%+v\n%+v", a, b)
	}
}

func TestAValidUpperEdgeRejectionIsTradableAndItsMirrorIsTheLowerEdge(t *testing.T) {
	in := input(rangeBars(40), resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0))
	got := integrity().Assess(in)
	if got.Reject != "" || got.State != StateValid {
		t.Fatalf("a rejection of an intact edge is tradable: %+v", got)
	}
	if got.RejectionCloseLocation < .6 || got.ReclaimDepthATR <= 0 || got.EpochIndex != -1 {
		t.Fatalf("the rejection is measured: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
}

func TestARealSweepAndReclaimIsTheStrategysOwnThesis(t *testing.T) {
	// A close beyond the edge (one accepted close, below BreakCloses) followed by a
	// candle that closes back inside is a sweep and reclaim, not a break.
	bars := rangeBars(40)
	bars[38] = candle(38, 102.9, 103.6, 102.8, 103.4)
	bars[39] = candle(39, 103.3, 103.5, 102.0, 102.2)
	in := input(bars, resistanceEdge(103, 5, 39, 1), supportEdge(98, 4, 30, 0))
	got := integrity().Assess(in)
	if got.Reject != "" {
		t.Fatalf("a reclaimed sweep is tradable: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
}

func TestOneAcceptedCloseStillBeyondTheEdgeIsABreakPending(t *testing.T) {
	bars := rangeBars(40)
	bars[39] = candle(39, 103.0, 103.9, 102.9, 103.7)
	in := input(bars, resistanceEdge(103, 5, 39, 1), supportEdge(98, 4, 30, 0))
	got := integrity().Assess(in)
	if got.Reject != ReasonBreakPending || got.State != StateBreakPending {
		t.Fatalf("a close still beyond the edge is a break pending: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
}

func TestARejectionThatClosesBeyondTheEdgeIsNotARejection(t *testing.T) {
	bars := rangeBars(40)
	bars[39] = candle(39, 102.0, 103.6, 101.9, 103.2)
	got := integrity().Assess(input(bars, resistanceEdge(103, 5, 39, 0), supportEdge(98, 4, 30, 0)))
	if got.Reject != ReasonRejectionNotInside {
		t.Fatalf("the confirming candle must close back inside: %+v", got)
	}
}

// staircase is a market that breaks 103 at index 30 and climbs on, forming a new
// upper edge higher up each time the lookback is rebuilt.
func staircase() ([]market.Candle, techniquezone.BrokenBarrier) {
	var bars []market.Candle
	for i := 0; i < 30; i++ {
		bars = append(bars, quiet(i, 100.5+math.Sin(float64(i))*.8))
	}
	bars = append(bars, candle(30, 102.8, 104.3, 102.7, 104.1), candle(31, 104.1, 104.9, 103.9, 104.6))
	for i := 32; i < 46; i++ {
		bars = append(bars, quiet(i, 104.6+float64(i-32)*.05))
	}
	// the rejection of the rebuilt edge at 105.4
	bars = append(bars, candle(46, 105.0, 105.7, 104.8, 104.9))
	broken := techniquezone.BrokenBarrier{Side: "resistance", Level: 103, Low: 102.8, High: 103.2, Tolerance: .25,
		Touches: 3, WickRejections: 3, FirstTouchIndex: 5, LastTouchIndex: 28, BreakIndex: 31, BreakCloses: 2}
	return bars, broken
}

func TestAnEdgeRebuiltAboveAnAcceptedBreakIsTheSameAdvanceNotANewRange(t *testing.T) {
	bars, broken := staircase()
	in := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(98, 4, 30, 0)) // the old floor: stale
	in.Broken = []techniquezone.BrokenBarrier{broken}
	got := integrity().Assess(in)
	if got.Reject != ReasonRangeNotReestablish || got.State != StateBroken || got.RetiredBreakIndex != 31 {
		t.Fatalf("an edge made above a break the market accepted is retired: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
}

func TestARangeFormedAfterTheBreakIsGenuinelyNewAndTradableWithItsOwnIdentity(t *testing.T) {
	bars, broken := staircase()
	// both edges were touched after the break and price closed inside them since
	in := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(104.3, 34, 44, 0))
	in.Broken = []techniquezone.BrokenBarrier{broken}
	got := integrity().Assess(in)
	if got.Reject != "" {
		t.Fatalf("a new range, formed after the break, is tradable: %+v", got)
	}
	if got.EpochIndex != 31 {
		t.Fatalf("its identity is the break, not the moving barrier: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
}

func TestAnEdgeThePriceReclaimedIsRestored(t *testing.T) {
	bars := rangeBars(40)
	bars[34] = candle(34, 102.0, 102.1, 101.2, 101.5)
	bars[35] = candle(35, 101.5, 101.9, 101.1, 101.4)
	broken := techniquezone.BrokenBarrier{Side: "resistance", Level: 103, Tolerance: .25, Touches: 3, WickRejections: 3, FirstTouchIndex: 5, LastTouchIndex: 20, BreakIndex: 30, BreakCloses: 2}
	in := input(bars, resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0))
	in.Broken = []techniquezone.BrokenBarrier{broken}
	if got := integrity().Assess(in); got.Reject != "" {
		t.Fatalf("two closes back inside restore the edge: %+v", got)
	}
	// ...but one close back inside does not, and the fade is refused.
	short := input(bars[:35], resistanceEdge(103, 31, 34, 0), supportEdge(98, 4, 30, 0))
	for i := 31; i < 34; i++ {
		short.Bars[i] = candle(i, 103.2, 103.7, 103.1, 103.5) // the break holds
	}
	short.Bars[34] = candle(34, 102.8, 103.5, 101.9, 102.1)
	short.Broken = []techniquezone.BrokenBarrier{broken}
	if got := integrity().Assess(short); got.Reject == "" {
		t.Fatalf("one close back inside is not a reclaim: %+v", got)
	}
}

func TestAClusterRunThroughOnTheWayToTheEdgeIsNotTheRangeBreaking(t *testing.T) {
	bars := rangeBars(40)
	minor := techniquezone.BrokenBarrier{Side: "resistance", Level: 101, Tolerance: .25, Touches: 2, WickRejections: 2, FirstTouchIndex: 20, LastTouchIndex: 24, BreakIndex: 30, BreakCloses: 2}
	in := input(bars, resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0))
	in.Broken = []techniquezone.BrokenBarrier{minor}
	// a higher cluster had already formed when the minor one broke: the minor one was never the edge
	if got := integrity().Assess(in); got.Reject != "" {
		t.Fatalf("a mid-range cluster breaking is not the range edge breaking: %+v", got)
	}
}

func TestSustainedPressureIntoTheEdgeIsNotARejection(t *testing.T) {
	bars := rangeBars(40)
	for i, c := range [][4]float64{{100.9, 101.5, 100.8, 101.4}, {101.4, 102.1, 101.3, 102.0}, {102.0, 102.9, 101.9, 102.8}, {102.8, 103.3, 102.6, 102.95}} {
		bars[36+i] = candle(36+i, c[0], c[1], c[2], c[3])
	}
	in := input(bars, resistanceEdge(103, 5, 39, 0), supportEdge(98, 4, 30, 0))
	got := integrity().Assess(in)
	if got.Reject != ReasonPressureIntoEdge || got.PressureRiseATR < 1 {
		t.Fatalf("higher lows and higher closes into the edge are pressure: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
	// a confirming candle that closes lower breaks the run: a real rejection
	bars[39] = candle(39, 102.8, 103.3, 102.2, 102.4)
	if got := integrity().Assess(input(bars, resistanceEdge(103, 5, 39, 0), supportEdge(98, 4, 30, 0))); got.Reject != "" {
		t.Fatalf("a close that turns back down is a rejection: %+v", got)
	}
}

func TestAGapInTheRecentCandlesIsNeverMistakenForAFreshReaction(t *testing.T) {
	bars := rangeBars(40)
	for i := 30; i < 40; i++ {
		bars[i].Time += 3600
	}
	got := integrity().Assess(input(bars, resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0)))
	if got.Reject != ReasonDataGap {
		t.Fatalf("a gap in the recent candles fails closed: %+v", got)
	}
}

func TestAMissingATRFailsClosed(t *testing.T) {
	in := input(rangeBars(40), resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0))
	for _, atr := range []float64{0, -1, math.NaN(), math.Inf(1)} {
		in.ATR = atr
		if got := integrity().Assess(in); got.Reject != ReasonATRUnavailable {
			t.Fatalf("atr %v: %+v", atr, got)
		}
	}
}

func TestTheSameEvidenceInAnyOrderGivesTheSameVerdict(t *testing.T) {
	bars, broken := staircase()
	other := techniquezone.BrokenBarrier{Side: "support", Level: 98, Tolerance: .25, FirstTouchIndex: 4, LastTouchIndex: 28, BreakIndex: 29, BreakCloses: 2}
	a := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(98, 4, 30, 0))
	a.Broken = []techniquezone.BrokenBarrier{broken, other}
	b := a
	b.Broken = []techniquezone.BrokenBarrier{other, broken}
	same(t, integrity().Assess(a), integrity().Assess(b))
}

// --- defects found by the independent review (each reproduced before it was fixed) ---

// staircaseThenReclaimThenRebreak: a break, two closes back inside (a reclaim), then a second
// accepted run that is never reclaimed. The later event decides.
func TestAReclaimedEdgeThatBreaksAgainIsRetiredAgainAndTheEpochIsTheLatestBreak(t *testing.T) {
	var bars []market.Candle
	for i := 0; i < 30; i++ {
		bars = append(bars, quiet(i, 100.5+math.Sin(float64(i))*.8))
	}
	bars = append(bars, candle(30, 102.8, 104.3, 102.7, 104.1), candle(31, 104.1, 104.9, 103.9, 104.6)) // break at 31
	bars = append(bars, quiet(32, 101), quiet(33, 101))                                                 // reclaimed
	bars = append(bars, candle(34, 102.9, 104.3, 102.8, 104.2), candle(35, 104.2, 104.9, 104.0, 104.7)) // broken again at 35
	for i := 36; i < 45; i++ {
		bars = append(bars, quiet(i, 104.7+float64(i-36)*.03))
	}
	bars = append(bars, candle(45, 105.0, 105.7, 104.8, 104.9))
	broken := techniquezone.BrokenBarrier{Side: "resistance", Level: 103, Low: 102.8, High: 103.2, Tolerance: .25, Touches: 3, WickRejections: 3,
		FirstTouchIndex: 5, LastTouchIndex: 28, BreakIndex: 31, BreakCloses: 2}
	in := input(bars, resistanceEdge(105.4, 40, 45, 0), supportEdge(98, 4, 30, 0)) // a stale floor
	in.Broken = []techniquezone.BrokenBarrier{broken}
	got := integrity().Assess(in)
	if got.Reject != ReasonRangeNotReestablish || got.RetiredBreakIndex != 35 {
		t.Fatalf("the second break is the one that retires the edge: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
	// ...and a range formed after the second break takes its identity from it, not from the first
	fresh := input(bars, resistanceEdge(105.4, 40, 45, 0), supportEdge(104.4, 37, 44, 0))
	fresh.Broken = in.Broken
	if a := integrity().Assess(fresh); a.Reject != "" || a.EpochIndex != 35 {
		t.Fatalf("epoch must be the latest break: %+v", a)
	}
}

func TestAMidRangeClusterPriceRanThroughDoesNotRetireTheIntactTop(t *testing.T) {
	bars := rangeBars(40)
	// a 2-touch cluster at 101, crossed at 25-26 on the way to the top; the top at 103 had formed at 5 and holds
	mid := techniquezone.BrokenBarrier{Side: "resistance", Level: 101, Low: 100.8, High: 101.2, Tolerance: .25, Touches: 2, WickRejections: 2,
		FirstTouchIndex: 20, LastTouchIndex: 24, BreakIndex: 26, BreakCloses: 2}
	for i := 26; i < 39; i++ {
		bars[i] = candle(i, 101.6, 101.9, 101.4, 101.7) // it stays above 101: never reclaimed
	}
	in := input(bars, resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0))
	in.Broken = []techniquezone.BrokenBarrier{mid}
	if got := integrity().Assess(in); got.Reject != "" {
		t.Fatalf("the range top was never accepted through: %+v", got)
	}
	// control: without the top having formed before the break, the same cluster does retire the edge
	late := input(bars, resistanceEdge(103, 30, 38, 0), supportEdge(98, 4, 30, 0))
	late.Broken = []techniquezone.BrokenBarrier{mid}
	if got := integrity().Assess(late); got.Reject == "" {
		t.Fatalf("an edge formed after the break is the same advance: %+v", got)
	}
}

func TestAStaleFarBarrierDoesNotDisableTheRetiredEdgeRule(t *testing.T) {
	bars, broken := staircase()
	in := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(98, 4, 30, 0))
	in.Broken = []techniquezone.BrokenBarrier{broken}
	in.Barriers = append(in.Barriers, resistanceEdge(115, 2, 6, 0)) // 11 ATR above: another range
	if got := integrity().Assess(in); got.Reject == "" {
		t.Fatalf("a barrier far beyond the maximum range width is not the same range: %+v", got)
	}
}

func TestOneMissingCandleIsAGap(t *testing.T) {
	bars := rangeBars(40)
	for i := 37; i < 40; i++ {
		bars[i].Time += 300
	}
	if got := integrity().Assess(input(bars, resistanceEdge(103, 5, 38, 0), supportEdge(98, 4, 30, 0))); got.Reject != ReasonDataGap {
		t.Fatalf("a missing M5 candle in the reaction window fails closed: %+v", got)
	}
}

func TestACloseBeyondTheEdgeAfterTheConfirmationFailsTheReaction(t *testing.T) {
	bars := rangeBars(40)
	bars[37] = candle(37, 102.6, 103.3, 102.0, 102.1) // the rejection
	bars[38] = candle(38, 102.1, 103.4, 102.0, 103.3) // closes above the edge (inside the builder's tolerance)
	bars[39] = candle(39, 103.3, 103.5, 102.9, 103.1)
	in := input(bars, resistanceEdge(103, 5, 39, 0), supportEdge(98, 4, 30, 0))
	in.ConfirmIndex = 37
	if got := integrity().Assess(in); got.Reject != ReasonReactionFailed {
		t.Fatalf("a later close beyond the edge means the rejection did not hold: %+v", got)
	}
}

func TestAShootingStarThatClosesBackInsideIsARejectionNotPressure(t *testing.T) {
	bars := rangeBars(40)
	for i, c := range [][4]float64{{100.9, 101.5, 100.8, 101.4}, {101.4, 102.1, 101.3, 102.0}, {102.0, 102.7, 101.9, 102.6}, {102.6, 103.8, 102.5, 102.7}} {
		bars[36+i] = candle(36+i, c[0], c[1], c[2], c[3])
	}
	in := input(bars, resistanceEdge(103, 5, 39, 0), supportEdge(98, 4, 30, 0))
	got := integrity().Assess(in)
	if got.Reject != "" {
		t.Fatalf("a candle that wicks through the edge and closes back inside near its low is a sweep and reclaim: %+v", got)
	}
	same(t, got, integrity().Assess(mirror(in, 201)))
}

func TestARangeIsOnlyReestablishedByClosesAfterTheBreak(t *testing.T) {
	bars, broken := staircase()
	// closes inside both edges, but all of them BEFORE the break: nothing since
	for i := 32; i < 47; i++ {
		bars[i] = candle(i, 106.0, 106.3, 105.9, 106.1) // above the new range: not inside it
	}
	in := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(104.3, 34, 44, 0))
	in.Broken = []techniquezone.BrokenBarrier{broken}
	if got := integrity().Assess(in); got.Reject == "" {
		t.Fatalf("closes that never came back inside the new edges do not make a range: %+v", got)
	}
}

func TestTheFloorIsTheLiveBarrierNearestPriceNotTheBuildersSelectedOne(t *testing.T) {
	bars, broken := staircase()
	// the builder's selected floor is stale (formed before the break), a newer support sits under price
	in := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(98, 4, 30, 0))
	in.Broken = []techniquezone.BrokenBarrier{broken}
	in.Barriers = append(in.Barriers, supportEdge(104.3, 34, 44, 0))
	if got := integrity().Assess(in); got.Reject != "" || got.EpochIndex != 31 {
		t.Fatalf("a newer support under price re-establishes the range: %+v", got)
	}
}

func TestAnAdvanceThatBrokeSeveralClustersIsOneEpochNamedByTheEarliestBreak(t *testing.T) {
	bars, first := staircase()
	second := first
	second.Level, second.Low, second.High, second.FirstTouchIndex, second.LastTouchIndex, second.BreakIndex = 104, 103.8, 104.2, 32, 32, 33
	in := input(bars, resistanceEdge(105.4, 40, 46, 0), supportEdge(104.3, 34, 44, 0))
	in.Broken = []techniquezone.BrokenBarrier{second, first}
	if got := integrity().Assess(in); got.Reject != "" || got.EpochIndex != 31 {
		t.Fatalf("the episode is named by the earliest break, so a later break does not rename it: %+v", got)
	}
	in.Broken = []techniquezone.BrokenBarrier{second}
	if got := integrity().Assess(in); got.EpochIndex != 33 {
		t.Fatalf("with only the later break in view it names the episode: %+v", got)
	}
}

// An edge first touched by the wick of the first accepted close is made by the break, not
// before it: it cannot exempt the break that retires it.
func TestAnEdgeBornOnTheFirstAcceptedCloseDoesNotExemptItsOwnBreak(t *testing.T) {
	bars, broken := staircase() // accepted run: bars 30-31, completed at 31 (BreakCloses 2)
	born := input(bars, resistanceEdge(105.4, 30, 46, 0), supportEdge(98, 4, 30, 0))
	born.Broken = []techniquezone.BrokenBarrier{broken}
	if got := integrity().Assess(born); got.Reject == "" {
		t.Fatalf("an edge whose first contact is the first accepted close was made by the break: %+v", got)
	}
	// one bar earlier it was already forming, and the broken cluster was not the outermost edge
	before := input(bars, resistanceEdge(105.4, 29, 46, 0), supportEdge(98, 4, 30, 0))
	before.Broken = []techniquezone.BrokenBarrier{broken}
	if got := integrity().Assess(before); got.Reject != "" {
		t.Fatalf("a cluster already forming before price began accepting is the outermost edge: %+v", got)
	}
}

func TestARangeNeedsItsInsideClosesAfterTheBreakNotBefore(t *testing.T) {
	bars, broken := staircase()
	in := func(floorFirst, edgeFirst int) EdgeInput {
		e := input(bars, resistanceEdge(105.4, edgeFirst, 46, 0), supportEdge(104.3, floorFirst, 44, 0))
		e.Broken = []techniquezone.BrokenBarrier{broken}
		return e
	}
	if got := integrity().Assess(in(34, 40)); got.Reject != "" {
		t.Fatalf("control: %+v", got)
	}
	// a touch on the break bar itself is not after it (the run completed there)
	if got := integrity().Assess(in(31, 40)); got.Reject == "" {
		t.Fatalf("a floor first touched on the break bar is the break's own: %+v", got)
	}
	if got := integrity().Assess(in(34, 31)); got.Reject == "" {
		t.Fatalf("an edge first touched on the break bar is the break's own: %+v", got)
	}
	// inside closes: only those after the break count, and exactly minimum_inside_closes are enough
	few := input(append([]market.Candle(nil), bars[:34]...), resistanceEdge(105.4, 33, 33, 0), supportEdge(104.3, 33, 33, 0))
	few.Broken = []techniquezone.BrokenBarrier{broken}
	few.Bars[32], few.Bars[33] = candle(32, 104.4, 104.6, 104.3, 104.5), candle(33, 104.5, 104.7, 104.4, 104.6)
	if got := integrity().Assess(few); got.Reject == "" {
		t.Fatalf("two closes inside since the break are fewer than minimum_inside_closes (3): %+v", got)
	}
}
