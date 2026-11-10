package roundnumber

import (
	"math"
	"reflect"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// RN-1 acceptance tests: deterministic, synthetic closed M5 candles in dollars on a gold-like scale (pip 0.1, handle step 10, ATR about 3). Every candle of a
// scenario is spelled out so the rule it exercises is visible. A SELL is never written by hand: it is the reflection of the BUY about the handle (p' = 2h - p),
// so the two directions cannot drift apart.

const day0 = int64(1_699_920_000) // 2023-11-14 00:00 UTC, a multiple of 86400

// production returns the committed round_number parameters with the instrument facts the engine injects.
func production() Config {
	return Config{
		HandleStep: 10, MajorStep: 50, MaxHandleDistanceATR: 3, ATRLength: 14, ATRWindowBars: 140,
		MinimumSweepPips: 5, MaximumSweepPips: 40, MinimumReclaimPips: 2, ReclaimMaxBars: 3, AcceptanceCloses: 2, ConfirmationMode: "mss",
		StructurePivotBars: 2, StructureLookbackBars: 24, MSSMaxBars: 6, MinimumMSSBodyRatio: 0.5, MinimumMSSDisplacementATR: 0.5, MinimumMSSCloseStrength: 0.6,
		ConfirmationMaxAgeBars: 2, EntryBandPips: 15, BreakBufferPips: 10, MinimumBreakBodyRatio: 0.6, MinimumBreakRangeATR: 1.2, RetestMaxBars: 12,
		RetestTolerancePips: 5, DisplacementGradeATR: 1, MinimumWickRatio: 0.4, StopBufferPips: 5, StopBufferATR: 0.1, MinimumTargetR: 1, ExpiryHours: 2,
		SessionMomentumMinimumATR: 1, PipSize: 0.1, TradingDayRolloverHour: 21, Symbols: []string{"XAU"},
	}
}

type bar struct{ o, h, l, c float64 }

// series builds `base` flat candles (a quiet range of 3.0 around `level0`, so ATR is 3.0 and there is no swing) followed by the given candles.
func series(level0 float64, base int, tail []bar) []market.Candle {
	var out []market.Candle
	t := day0
	for i := 0; i < base; i++ {
		c := level0 + 0.05*float64(1-2*(i%2))
		out = append(out, market.Candle{Time: t, Open: level0, High: level0 + 1.5, Low: level0 - 1.5, Close: c, Volume: 1})
		t += 300
	}
	for _, b := range tail {
		out = append(out, market.Candle{Time: t, Open: b.o, High: b.h, Low: b.l, Close: b.c, Volume: 1})
		t += 300
	}
	return out
}

// mirrorAbout mirrors a series about the price `about` (a BUY scenario becomes its SELL twin); time is unchanged.
func mirrorAbout(in []market.Candle, about float64) []market.Candle {
	out := make([]market.Candle, len(in))
	for i, c := range in {
		out[i] = market.Candle{Time: c.Time, Open: 2*about - c.Open, High: 2*about - c.Low, Low: 2*about - c.High, Close: 2*about - c.Close, Volume: c.Volume}
	}
	return out
}

// The stop run through 4000: a swing high at 4009 (candle 170), a sweep that reclaims on its own candle (174), then a structure shift closing through the swing (175).
var sweepTail = []bar{
	{4005, 4009, 4004.5, 4006},       // 170 the swing high the shift must break
	{4005.5, 4006.5, 4004, 4005.2},   // 171
	{4005.5, 4006.5, 4004, 4005.2},   // 172 the swing is now confirmable
	{4005, 4005.2, 4000.8, 4001.2},   // 173 price comes down to the handle from above
	{4000.6, 4001.2, 3999.0, 4000.9}, // 174 sweeps 3999.0 (10 pips under the handle) and closes back above it, lower wick
	{4000.9, 4009.7, 4000.7, 4009.6}, // 175 structure shift: closes through 4009 with body, strength and displacement
}

// The accepted break of 4000: price lives below (3996), a forceful close above (170), two acceptance closes (171-172), the first retest rejected (173).
var breakTail = []bar{
	{3996.2, 4002.8, 3996.0, 4002.5}, // 170 break: close 4002.5 > 4001.0, body .93, range 6.8 >= 1.2 ATR
	{4002.5, 4003.4, 4002.2, 4003.0}, // 171 acceptance
	{4003.0, 4003.6, 4002.9, 4003.2}, // 172 acceptance
	{4002.0, 4002.8, 4000.9, 4002.6}, // 173 first touch of the band [3999.5, 4001.5]: bullish, closes above it, lower wick .58
}

func detectAt(cfg Config, s []market.Candle, in func(Input) Input) Analysis {
	input := Input{M5: s}
	if in != nil {
		input = in(input)
	}
	return Detect(cfg, input)
}

func only(t *testing.T, a Analysis, kind string, dir market.Direction) Setup {
	t.Helper()
	var found []Setup
	for _, s := range a.Setups {
		if s.Kind == kind && s.Direction == dir {
			found = append(found, s)
		}
	}
	if len(found) != 1 {
		t.Fatalf("setups %+v rejections %+v: want exactly one %s %s", a.Setups, a.Rejections, kind, dir)
	}
	return found[0]
}

func rejected(a Analysis, reason, kind string, dir market.Direction) bool {
	for _, r := range a.Rejections {
		if r.Reason == reason && r.Kind == kind && r.Direction == dir && r.Handle == 4000 {
			return true
		}
	}
	return false
}

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-6 {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}

func TestBuySweepReclaim(t *testing.T) {
	a := detectAt(production(), series(4005, 170, sweepTail), nil)
	s := only(t, a, KindSweepReclaim, market.Buy)
	if s.Handle != 4000 || !s.Major {
		t.Errorf("handle %v major %v: 4000 is a multiple of the 50 major step", s.Handle, s.Major)
	}
	near(t, "entry low (the handle)", s.EntryLow, 4000)
	near(t, "entry high (handle + 15 pips)", s.EntryHigh, 4001.5)
	near(t, "stop (extreme 3999.0 less the 5 pip buffer)", s.Stop, 3998.5)
	near(t, "structural risk from the proximal edge, pips", s.RiskPips, 30)
	if s.Target <= s.EntryHigh || s.TargetKind != TargetNextHandle || s.Target != 4010 {
		t.Errorf("target %v %s, want the next handle 4010", s.Target, s.TargetKind)
	}
	if !s.WickRejection || !s.Structural || s.ConfirmationAge != 0 {
		t.Errorf("facts: wick %v structural %v age %d", s.WickRejection, s.Structural, s.ConfirmationAge)
	}
	if s.RiskPips <= 0 || s.TechnicalRR < 1 {
		t.Errorf("risk %v rr %v", s.RiskPips, s.TechnicalRR)
	}
}

func TestSellSweepReclaimIsTheExactReflection(t *testing.T) {
	buy := detectAt(production(), series(4005, 170, sweepTail), nil)
	sell := detectAt(production(), mirrorAbout(series(4005, 170, sweepTail), 4000), nil)
	b, s := only(t, buy, KindSweepReclaim, market.Buy), only(t, sell, KindSweepReclaim, market.Sell)
	near(t, "entry high", s.EntryHigh, 8000-b.EntryLow)
	near(t, "entry low", s.EntryLow, 8000-b.EntryHigh)
	near(t, "stop", s.Stop, 8000-b.Stop)
	near(t, "target", s.Target, 8000-b.Target)
	near(t, "risk", s.RiskPips, b.RiskPips)
	near(t, "rr", s.TechnicalRR, b.TechnicalRR)
	if s.Stop <= s.EntryLow || s.Target >= s.EntryLow {
		t.Errorf("SELL geometry is wrong: stop %v target %v entry [%v, %v]", s.Stop, s.Target, s.EntryLow, s.EntryHigh)
	}
}

func TestBuyBreakRetest(t *testing.T) {
	cfg := production()
	a := detectAt(cfg, series(3996, 170, breakTail), nil)
	s := only(t, a, KindBreakRetest, market.Buy)
	near(t, "entry low (handle - 5 pips)", s.EntryLow, 3999.5)
	near(t, "entry high (handle + 15 pips)", s.EntryHigh, 4001.5)
	near(t, "stop (below the handle less the buffer)", s.Stop, 3999.0)
	near(t, "risk from the proximal edge, pips", s.RiskPips, 25)
	if s.Target != 4010 || !s.WickRejection || s.Engulfing {
		t.Errorf("target %v wick %v engulfing %v", s.Target, s.WickRejection, s.Engulfing)
	}
	if s.RetestBars != 1 || s.ConfirmationAge != 0 { // the retest is the first candle after the acceptance closes
		t.Errorf("retest bars %d age %d", s.RetestBars, s.ConfirmationAge)
	}
}

func TestSellBreakRetestIsTheExactReflection(t *testing.T) {
	buy := detectAt(production(), series(3996, 170, breakTail), nil)
	sell := detectAt(production(), mirrorAbout(series(3996, 170, breakTail), 4000), nil)
	b, s := only(t, buy, KindBreakRetest, market.Buy), only(t, sell, KindBreakRetest, market.Sell)
	near(t, "entry high", s.EntryHigh, 8000-b.EntryLow)
	near(t, "entry low", s.EntryLow, 8000-b.EntryHigh)
	near(t, "stop", s.Stop, 8000-b.Stop)
	near(t, "target", s.Target, 8000-b.Target)
	if !(s.Stop > s.EntryHigh && s.Target < s.EntryLow) {
		t.Errorf("SELL geometry: entry [%v, %v] stop %v target %v", s.EntryLow, s.EntryHigh, s.Stop, s.Target)
	}
}

// With candle confirmation optional the first touch of the band is the trigger even when it is not a rejection; nothing is fabricated (no wick, no engulfing).
func TestOptionalCandleConfirmationTouchOnly(t *testing.T) {
	plain := append([]bar(nil), breakTail...)
	plain[3] = bar{4003.0, 4003.2, 4001.2, 4002.4} // a bearish touch of the band: no rejection of either kind
	required := production()
	if a := detectAt(required, series(3996, 170, plain), nil); len(a.Setups) != 0 {
		t.Fatalf("a touch that does not reject must not trigger when confirmation is required: %+v", a.Setups)
	}
	optional := production()
	optional.CandleConfirmationOptional = true
	s := only(t, detectAt(optional, series(3996, 170, plain), nil), KindBreakRetest, market.Buy)
	if s.WickRejection || s.Engulfing {
		t.Errorf("optional confirmation fabricated a rejection: wick %v engulfing %v", s.WickRejection, s.Engulfing)
	}
}

func TestFailedReclaimIsRefused(t *testing.T) {
	tail := append([]bar(nil), sweepTail[:5]...)
	tail = append(tail, bar{4000.9, 4001.0, 3997.5, 3998.0}) // 175 closes back under the handle before any structure shift: the reclaim is lost
	a := detectAt(production(), series(4005, 170, tail), nil)
	if len(a.Setups) != 0 || !rejected(a, "reclaim_lost", KindSweepReclaim, market.Buy) {
		t.Errorf("setups %+v rejections %+v: want reclaim_lost and no setup", a.Setups, a.Rejections)
	}
}

func TestAcceptedBeyondTheHandleIsNotAStopRun(t *testing.T) {
	tail := append([]bar(nil), sweepTail[:4]...)
	tail = append(tail,
		bar{4000.6, 4000.9, 3998.0, 3998.8}, // 174 trades 20 pips under and closes under
		bar{3998.8, 3999.2, 3997.5, 3998.2}, // 175 a second close under: acceptance, not a sweep
		bar{3998.2, 3999.0, 3997.8, 3998.5})
	a := detectAt(production(), series(4005, 170, tail), nil)
	if len(a.Setups) != 0 || !rejected(a, "accepted_not_swept", KindSweepReclaim, market.Buy) {
		t.Errorf("setups %+v rejections %+v: want accepted_not_swept", a.Setups, a.Rejections)
	}
}

func TestFalseBreakoutIsRefused(t *testing.T) {
	tail := append([]bar(nil), breakTail[:1]...)
	tail = append(tail, bar{4002.5, 4002.7, 3998.5, 3999.0}) // 171 the first acceptance close is back under the handle
	a := detectAt(production(), series(3996, 170, tail), nil)
	if len(a.Setups) != 0 || !rejected(a, "break_failed", KindBreakRetest, market.Buy) {
		t.Errorf("setups %+v rejections %+v: want break_failed", a.Setups, a.Rejections)
	}
}

// A shift needs a swing to break: on a market with no swing high above the handle there is no structure reference and nothing is confirmed.
func TestNoStructureReferenceNoSetup(t *testing.T) {
	tail := []bar{
		{4003, 4003.5, 4000.8, 4001.2}, {4000.6, 4001.2, 3999.0, 4000.9}, // no candle is higher than the quiet range: no swing high exists
	}
	for i := 0; i < 7; i++ { // the shift window (6 bars) runs out
		tail = append(tail, bar{4001, 4003, 4000.5, 4002})
	}
	a := detectAt(production(), series(4002.5, 170, tail), nil)
	if len(a.Setups) != 0 || !rejected(a, "no_structure_reference", KindSweepReclaim, market.Buy) {
		t.Errorf("setups %+v rejections %+v: want no_structure_reference", a.Setups, a.Rejections)
	}
}

// A close through the swing without body, strength and displacement is not a structure shift.
func TestWeakShiftIsNotAStructureShift(t *testing.T) {
	tail := append([]bar(nil), sweepTail[:5]...)
	tail = append(tail, bar{4008.5, 4012.0, 4001.0, 4009.2}) // 175 closes through 4009 but with a 0.09 close strength and a 0.06 body ratio
	for i := 0; i < 6; i++ {
		tail = append(tail, bar{4007, 4008.5, 4006, 4007.5})
	}
	a := detectAt(production(), series(4005, 170, tail), nil)
	if len(a.Setups) != 0 {
		t.Errorf("a weak shift must not confirm: %+v", a.Setups)
	}
}

// The minimum reward: the target must be at least minimum_target_r x the risk away. A liquidity pool nearer than that is skipped for the next one (or the
// next handle); one exactly that far is the target.
func TestMinimumTargetDistance(t *testing.T) {
	cfg := production()
	risk := 3.0 // entry 4001.5, stop 3998.5
	liquidity := func(at float64) func(market.Direction, float64, float64) (float64, bool) {
		return func(d market.Direction, from, minimum float64) (float64, bool) {
			if d == market.Buy && at-from >= minimum-1e-9 {
				return at, true
			}
			if d == market.Sell && from-at >= minimum-1e-9 {
				return at, true
			}
			return 0, false
		}
	}
	buy := &detector{cfg: cfg, dir: market.Buy, sign: 1}
	buy.in = Input{Liquidity: liquidity(4001.5 + risk*0.99)}
	if p, kind, ok := buy.objective(4001.5, risk); !ok || kind != TargetNextHandle || p != 4010 {
		t.Errorf("a pool at 0.99R is not a 1R target: got %v %s %v, want the next handle 4010", p, kind, ok)
	}
	buy.in = Input{Liquidity: liquidity(4001.5 + risk)}
	if p, kind, ok := buy.objective(4001.5, risk); !ok || kind != TargetLiquidity || math.Abs(p-(4001.5+risk)) > 1e-9 {
		t.Errorf("a pool at exactly 1R is the target: got %v %s %v", p, kind, ok)
	}
	// SELL mirrors: oriented entry -3998.5 (the reflection of 3998.5), the pool at 3995.5 is 3.0 below it.
	sell := &detector{cfg: cfg, dir: market.Sell, sign: -1, in: Input{Liquidity: liquidity(3995.5)}}
	if p, kind, ok := sell.objective(-3998.5, risk); !ok || kind != TargetLiquidity || math.Abs(p-(-3995.5)) > 1e-9 {
		t.Errorf("SELL pool at exactly 1R: got %v %s %v", p, kind, ok)
	}
}

// The target is never behind the entry and the stop is never on the wrong side of it, for any direction.
func TestDirectionalGeometryIsAlwaysValid(t *testing.T) {
	for _, tc := range []struct {
		name string
		s    []market.Candle
		kind string
	}{
		{"buy sweep", series(4005, 170, sweepTail), KindSweepReclaim},
		{"sell sweep", mirrorAbout(series(4005, 170, sweepTail), 4000), KindSweepReclaim},
		{"buy break", series(3996, 170, breakTail), KindBreakRetest},
		{"sell break", mirrorAbout(series(3996, 170, breakTail), 4000), KindBreakRetest},
	} {
		a := detectAt(production(), tc.s, nil)
		if len(a.Setups) != 1 {
			t.Fatalf("%s: %+v / %+v", tc.name, a.Setups, a.Rejections)
		}
		s := a.Setups[0]
		risk := s.EntryHigh - s.Stop
		reward := s.Target - s.EntryHigh
		if s.Direction == market.Sell {
			risk, reward = s.Stop-s.EntryLow, s.EntryLow-s.Target
		}
		if !(risk > 0) || !(reward > 0) || s.TechnicalRR < 1 {
			t.Errorf("%s: risk %v reward %v rr %v", tc.name, risk, reward, s.TechnicalRR)
		}
	}
}

// The stop budget is the XAU envelope (50-70 pips, pip 0.1): Round Number is a technique-lane strategy (catalog is_scalp False), so the 40-pip M1 scalp limit
// does not apply to it; a stop wider than 70 pips is refused and never tightened, one at 70 is published, one under 50 is published as a floor stop.
func TestStopBudgetIsTheInstrumentEnvelope(t *testing.T) {
	b := strategyutil.StopBudget{MinPips: 50, MaxPips: 70, PipSize: 0.1}
	for _, tc := range []struct {
		pips    float64
		publish bool
		code    string
	}{{70.0, true, strategyutil.StopBudgetStructuralEvidence}, {70.5, false, ""}, {40, true, strategyutil.StopBudgetFloorEvidence}, {120, false, ""}} {
		code, ok := b.Apply(4001.5, 4001.5-tc.pips*0.1)
		if ok != tc.publish || code != tc.code {
			t.Errorf("%v pips: publish %v code %q, want %v %q", tc.pips, ok, code, tc.publish, tc.code)
		}
	}
}

// No future candle is ever read: the result as of candle k is the same whatever follows it.
func TestNoFutureCandleLeakage(t *testing.T) {
	cfg := production()
	for name, s := range map[string][]market.Candle{
		"sweep":      series(4005, 170, sweepTail),
		"sell sweep": mirrorAbout(series(4005, 170, sweepTail), 4000),
		"break":      series(3996, 170, breakTail),
		"sell break": mirrorAbout(series(3996, 170, breakTail), 4000),
	} {
		// A hostile future: after every cut the following candles are replaced by a crash and a spike.
		for k := 160; k < len(s); k++ {
			want := DetectAsOf(cfg, Input{M5: s[:k+1]}, k)
			hostile := append([]market.Candle(nil), s...)
			for j := k + 1; j < len(hostile); j++ {
				hostile[j].Open, hostile[j].High, hostile[j].Low, hostile[j].Close = 3000, 5000, 2900, 4990
			}
			got := DetectAsOf(cfg, Input{M5: hostile}, k)
			if !reflect2(want, got) {
				t.Errorf("%s: result as of %d changed with later candles:\n%+v\n%+v", name, k, want, got)
			}
		}
	}
}

// A replay is deterministic: the same candles give the same analysis, in the same order, every time.
func TestHistoricalReplayDeterminism(t *testing.T) {
	cfg := production()
	s := append(series(4005, 170, sweepTail), series(3996, 0, nil)...)
	var first []Analysis
	for run := 0; run < 3; run++ {
		var all []Analysis
		for k := 150; k < len(s); k++ {
			all = append(all, DetectAsOf(cfg, Input{M5: s}, k))
		}
		if run == 0 {
			first = all
			continue
		}
		if !reflect2(first, all) {
			t.Fatalf("run %d differs from the first", run)
		}
	}
}

func reflect2(a, b any) bool { return reflect.DeepEqual(a, b) }

// The same event seen by other strategies stays independent: Round Number reads only its own M5 candles (and the engine's liquidity), so the same candles
// give the same setup whatever a CRT or Break & Retest opportunity on them would target, and its identity never collides with theirs.
func TestSameEventNeighbourDoesNotChangeTheSetup(t *testing.T) {
	cfg := production()
	s := series(4005, 170, sweepTail)
	alone := only(t, detectAt(cfg, s, nil), KindSweepReclaim, market.Buy)
	// A neighbour's objective (a CRT opposite H1 edge, a Break & Retest pivot) at 4004: 2.5 above the entry, nearer than the 3.0 of one R, so it can never
	// become this setup's target.
	withNeighbour := only(t, detectAt(cfg, s, func(in Input) Input {
		in.Liquidity = func(d market.Direction, from, minimum float64) (float64, bool) {
			if d == market.Buy && 4004-from >= minimum {
				return 4004, true
			}
			return 0, false
		}
		return in
	}), KindSweepReclaim, market.Buy)
	if alone != withNeighbour {
		t.Errorf("a neighbouring strategy's objective changed this setup:\n%+v\n%+v", alone, withNeighbour)
	}
	candidateID := "technique:round_number:buy:4000.00"
	for _, other := range []string{"technique:crt:buy:", "technique:break_retest:buy:"} {
		if len(candidateID) >= len(other) && candidateID[:len(other)] == other {
			t.Errorf("identity %s collides with %s", candidateID, other)
		}
	}
}

// --- quality: the opposing-room rules (SQ-2) as Round Number applies them, and the change of character it owns (v3) ---

func supplyAt(low, high float64) zone.Zone {
	return zone.Zone{ID: "s", Kind: zone.KindSupply, Low: market.Price(low), High: market.Price(high), State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
}

func demandAt(low, high float64) zone.Zone {
	return zone.Zone{ID: "d", Kind: zone.KindDemand, Low: market.Price(low), High: market.Price(high), State: zone.StateFresh, OriginTime: barTime - 5000, CreatedAt: barTime - 5000}
}

func TestInsufficientRoomBeforeAnOpposingBarrier(t *testing.T) {
	s := testStrategy()
	wall := supplyAt(4001.0, 4002.0) // 5 pips above the proximal edge (4000.5), 12 pips from the stop: less than 1R of the 25-pip floor
	c := scoreOf(s, ctxWith(wall), setupOf(TargetLiquidity), 4000.5)
	if c["room_limited_by_barrier"] != 1 || c["score_room"] != 0 {
		t.Errorf("a wall 5 pips ahead must leave no room: limited %v score %v", c["room_limited_by_barrier"], c["score_room"])
	}
	open := scoreOf(s, ctxWith(), setupOf(TargetLiquidity), 4000.5)
	if open["score_room"] <= c["score_room"] {
		t.Errorf("without the wall the room must score higher: %v vs %v", open["score_room"], c["score_room"])
	}
}

func TestTargetBehindABarrierEarnsRoomOnlyUpToTheBarrier(t *testing.T) {
	s := testStrategy()
	setup := setupOf(TargetLiquidity) // entry edge 4000.5, observed target 4008
	wall := supplyAt(4004, 4005)      // between the entry and the target
	c := scoreOf(s, ctxWith(wall), setup, 4000.5)
	if !(c["available_room_pips"] < c["target_room_pips"]) || c["room_limited_by_barrier"] != 1 {
		t.Errorf("room must stop at the wall: available %v target %v limited %v", c["available_room_pips"], c["target_room_pips"], c["room_limited_by_barrier"])
	}
	projected := setupOf(TargetNextHandle) // a handle target is a hypothesis: no structural credit at all
	if p := scoreOf(s, ctxWith(), projected, 4000.5); p["score_room"] != 0 || p["room_structural"] != 0 {
		t.Errorf("a projected next handle earned room: %v / %v", p["score_room"], p["room_structural"])
	}
}

func TestWrongSideBarrierIsNotAnOpposingBarrier(t *testing.T) {
	s := testStrategy()
	setup := setupOf(TargetLiquidity) // a BUY: the opposing barrier is a supply above
	for name, z := range map[string]zone.Zone{
		"a supply wall behind the entry":   supplyAt(3994, 3995),
		"a demand wall on the target side": demandAt(4004, 4005),
		"a demand wall under the entry":    demandAt(3996, 3997),
	} {
		c := scoreOf(s, ctxWith(z), setup, 4000.5)
		if c["barrier_ahead"] != 0 || c["room_limited_by_barrier"] != 0 {
			t.Errorf("%s was treated as an opposing barrier: ahead %v limited %v", name, c["barrier_ahead"], c["room_limited_by_barrier"])
		}
	}
	sell := setup
	sell.Direction, sell.Stop, sell.Target = market.Sell, 4000.6, 3992 // a SELL: the opposing barrier is a demand below
	if c := scoreOf(s, ctxWith(supplyAt(3995, 3996)), sell, 4000); c["barrier_ahead"] != 0 {
		t.Errorf("SELL: a supply wall below the entry was treated as opposing: %v", c["barrier_ahead"])
	}
	if c := scoreOf(s, ctxWith(demandAt(3995, 3996)), sell, 4000); c["barrier_ahead"] != 1 {
		t.Errorf("SELL: a demand wall below the entry must be the opposing barrier: %v", c["barrier_ahead"])
	}
}

// frame is the M5 frame with the given candles and changes of character at the given candle indices (the direction the trade wants).
func frame(bars []market.Candle, dir market.Direction, chochAt ...int) *strategyutil.LegacyDetector {
	wanted := "up"
	if dir == market.Sell {
		wanted = "down"
	}
	var breaks []techniquezone.Break
	for _, i := range chochAt {
		breaks = append(breaks, techniquezone.Break{Kind: "CHoCH", Direction: wanted, Index: i})
	}
	return &strategyutil.LegacyDetector{Frame: &analysiscontext.LegacyFrame{Bars: bars, Breaks: breaks}, Direction: dir}
}

// The structure score reads the change of character the SETUP caused, once: the structure-shift candle (stop run) and the break candle (accepted break) are
// credited by `force` and are not credited again; a break before the sequence or after it is not this setup's.
func TestSetupOwnedChangeOfCharacter(t *testing.T) {
	bars := series(4005, 170, sweepTail) // sweep candle 174, structure shift 175
	t0 := func(i int) int64 { return bars[i].Time }
	stop := Setup{Kind: KindSweepReclaim, Direction: market.Buy, SweepTime: t0(174), ConfirmedAt: t0(175)}
	for _, tc := range []struct {
		name string
		at   []int
		want bool
	}{
		{"on the structure-shift candle itself: already credited", []int{175}, false},
		{"on the sweep candle: part of the sequence", []int{174}, true},
		{"before the sweep: the market's earlier history", []int{170}, false},
		{"after the confirmation: not this setup's", []int{176}, false},
	} {
		bars := append(append([]market.Candle(nil), bars...), market.Candle{Time: t0(175) + 300, Open: 4009, High: 4010, Low: 4008, Close: 4009.5})
		if got := setupCHoCH(frame(bars, market.Buy, tc.at...), stop); got != tc.want {
			t.Errorf("stop run, %s: %v, want %v", tc.name, got, tc.want)
		}
	}
	brk := Setup{Kind: KindBreakRetest, Direction: market.Buy, BreakTime: t0(170), ConfirmedAt: t0(173)}
	for _, tc := range []struct {
		name string
		at   []int
		want bool
	}{
		{"on the break candle itself: already credited as force", []int{170}, false},
		{"between the break and the retest", []int{171}, true},
		{"on the retest candle that confirmed", []int{173}, true},
		{"before the break", []int{169}, false},
		{"after the confirmation", []int{174}, false},
	} {
		if got := setupCHoCH(frame(bars, market.Buy, tc.at...), brk); got != tc.want {
			t.Errorf("accepted break, %s: %v, want %v", tc.name, got, tc.want)
		}
	}
	// A SELL asks for the down character only.
	if setupCHoCH(frame(bars, market.Sell, 174), Setup{Kind: KindSweepReclaim, Direction: market.Sell, SweepTime: t0(174), ConfirmedAt: t0(175)}) != true {
		t.Errorf("SELL: a down change of character in the sequence must count")
	}
	wrong := frame(bars, market.Buy, 174)
	wrong.Frame.Breaks[0].Direction = "down"
	if setupCHoCH(wrong, stop) {
		t.Errorf("BUY: a down change of character is not the trade's")
	}
}
