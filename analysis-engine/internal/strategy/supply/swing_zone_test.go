package supply

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// swingStrategy is the supply strategy with the swing-anchored zone definition and thresholds from the shipped configuration's hypotheses.
func swingStrategy(minimum structure.Strength) *Strategy {
	s := testStrategy()
	s.cfg.MaximumZoneATR = 4
	s.cfg.EntryMaxWidthPrice = 5
	s.cfg.Formation = strategyutil.ZoneFormation{
		Model: strategyutil.ZoneModelSwing, MinimumStrength: minimum, EntryCandidates: true,
		Zone: structure.ZoneConfig{
			Major: structure.MajorConfig{
				PivotLeftBars: 2, PivotRightBars: 2, MajorProminenceATR: 3, IntermediateProminenceATR: 1.5, MajorDepartureATR: 2, IntermediateDepartureATR: 1,
				DepartureBars: 8, ExternalBars: 20, RelevanceBars: 150,
			},
			DepartureGapBars: 3, BaseMaxCandles: 6, BaseMaxCandleATR: 1.5, MinimumZoneATR: 0.2, MaximumZoneATR: 4, StrongDepartureATR: 2,
		},
	}
	return s
}

// A rising staircase, three impulse bars, a five-candle consolidation whose middle candle makes the high, a four-bar bearish departure: ATR 1.0 throughout.
func majorSupplyFrame() (*analysiscontext.LegacyFrame, int) {
	var bars []market.Candle
	add := func(open, high, low, close float64) {
		bars = append(bars, market.Candle{Time: int64(1_800_000_000 + len(bars)*frameStep), Open: open, High: high, Low: low, Close: close})
	}
	price := 100.0
	for i := 0; i < 32; i++ {
		w := 0.1 + 0.02*float64((i*7)%5)
		if i%4 == 3 {
			add(price, price+w, price-2.5-3*w, price-2.5)
			price -= 2.5
		} else {
			add(price, price+1.0+w, price-w, price+1.0)
			price += 1.0
		}
	}
	for i := 0; i < 3; i++ {
		add(price, price+1.9, price-0.05, price+1.8)
		price += 1.8
	}
	swing := 0
	for i := 0; i < 5; i++ {
		top := price + 0.5
		if i == 2 {
			top, swing = price+1.1, len(bars)
		}
		add(price, top, price-0.4-0.05*float64(i), price-0.1)
	}
	for i := 0; i < 4; i++ {
		add(price, price+0.05, price-1.9, price-1.8)
		price -= 1.8
	}
	atr := make([]float64, len(bars))
	for i := range atr {
		atr[i] = 1.0
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: atr}, swing
}

func readWith(t *testing.T, s *Strategy, frame *analysiscontext.LegacyFrame) ([]instance, []band, opportunity.ZoneLiveness) {
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

func TestAMajorSwingDepartureIsAStrongTradableZone(t *testing.T) {
	frame, swing := majorSupplyFrame()
	instances, held, liveness := readWith(t, swingStrategy(structure.StrengthModerate), frame)
	if len(instances) != 1 || len(held) != 1 || len(liveness.Live) != 1 {
		t.Fatalf("one live zone expected: %d instances, %d held, %v", len(instances), len(held), liveness.Live)
	}
	in := instances[0]
	f := in.Formation
	if f == nil || f.Strength != structure.StrengthStrong || f.Swing.Class != structure.SwingMajor || f.Swing.BarIndex != swing {
		t.Fatalf("formation %+v", f)
	}
	if in.weak(swingStrategy(structure.StrengthStrong).cfg.Formation) {
		t.Fatal("a strong zone is never weak")
	}
	// The structure is the whole base, not three candles and not the entry slice.
	low, high := in.structural()
	if low != f.Low || high != f.High || f.BaseEnd-f.BaseStart+1 != 5 {
		t.Fatalf("structural %v..%v for a five-candle base %+v", low, high, f)
	}
	if in.FrameConfirmed < f.LegEnd || in.FrameConfirmed < f.Swing.BarIndex+2 {
		t.Fatalf("the zone is touchable only once its swing and its departure are closed: confirmed %d", in.FrameConfirmed)
	}
}

// The zone identity does not depend on how often the frame is read: the same bars give the same zone, verdict and ID (idempotent).
func TestSwingZoneReadsAreIdempotent(t *testing.T) {
	frame, _ := majorSupplyFrame()
	s := swingStrategy(structure.StrengthModerate)
	a, _, la := readWith(t, s, frame)
	b, _, lb := readWith(t, s, frame)
	if len(a) != 1 || len(b) != 1 || a[0].Low != b[0].Low || a[0].High != b[0].High || a[0].FrameOrigin != b[0].FrameOrigin {
		t.Fatalf("reads differ: %+v vs %+v", a, b)
	}
	for id := range la.Live {
		if _, ok := lb.Live[id]; !ok {
			t.Fatalf("zone %s lost between identical reads", id)
		}
	}
}

// A displacement with no swing under it (a shallow wobble then a leg) is weak: kept as context, never offered for entry and never higher-timeframe confluence.
func TestAWeakZoneIsContextOnly(t *testing.T) {
	var bars []market.Candle
	price := 100.0
	for i := 0; i < 30; i++ {
		d := 0.3
		if i%2 == 1 {
			d = -0.3
		}
		w := 0.1 + 0.02*float64((i*7)%5)
		bars = append(bars, market.Candle{Time: int64(1_800_000_000 + len(bars)*frameStep), Open: price, High: math.Max(price, price+d) + w, Low: math.Min(price, price+d) - w, Close: price + d})
		price += d
	}
	for i := 0; i < 3; i++ {
		bars = append(bars, market.Candle{Time: int64(1_800_000_000 + len(bars)*frameStep), Open: price, High: price + 0.05, Low: price - 1.9, Close: price - 1.8})
		price -= 1.8
	}
	atr := make([]float64, len(bars))
	for i := range atr {
		atr[i] = 1.0
	}
	frame := &analysiscontext.LegacyFrame{Bars: bars, ATR: atr}
	s := swingStrategy(structure.StrengthModerate)
	instances, held, _ := readWith(t, s, frame)
	for _, in := range instances {
		if in.Formation == nil || in.Formation.Strength != structure.StrengthWeak || !in.weak(s.cfg.Formation) {
			t.Fatalf("a base with no significant swing under it must be weak: %+v", in.Formation)
		}
	}
	if len(held) != 0 {
		t.Fatalf("a weak zone is not structure for the frame below: %v", held)
	}
}

// minimum_zone_strength is the only thing between a moderate zone and a trade: raised to strong, an intermediate swing zone is context.
func TestMinimumStrengthGatesWhatMayPublish(t *testing.T) {
	frame, _ := majorSupplyFrame()
	weakest := swingStrategy(structure.StrengthWeak)
	instances, _, _ := readWith(t, weakest, frame)
	if len(instances) != 1 {
		t.Fatalf("setup: %d instances", len(instances))
	}
	in := instances[0]
	in.Formation.Strength = structure.StrengthModerate
	if in.weak(swingStrategy(structure.StrengthModerate).cfg.Formation) {
		t.Fatal("a moderate zone meets a moderate minimum")
	}
	if !in.weak(swingStrategy(structure.StrengthStrong).cfg.Formation) {
		t.Fatal("a moderate zone is below a strong minimum")
	}
}

// The legacy definition is still there as the rollback: the same frame gives the three-candle zone and no formation.
func TestLegacyModelIsTheRollback(t *testing.T) {
	frame, _ := majorSupplyFrame()
	s := swingStrategy(structure.StrengthModerate)
	s.cfg.Formation.Model = strategyutil.ZoneModelLegacy
	instances, _, _ := readWith(t, s, frame)
	for _, in := range instances {
		if in.Formation != nil || in.weak(s.cfg.Formation) {
			t.Fatalf("the legacy model has no formation and no strength: %+v", in)
		}
	}
}

// A zone whose structure is wider than the limit is a region however narrow its entry slice: refused before clipping.
func TestASwingZoneWiderThanTheLimitIsARegion(t *testing.T) {
	frame, _ := majorSupplyFrame()
	s := swingStrategy(structure.StrengthWeak)
	s.cfg.Formation.Zone.MaximumZoneATR = 0.8
	instances, held, liveness := readWith(t, s, frame)
	if len(instances) != 0 || len(held) != 0 {
		t.Fatalf("a region is not offered: %d instances %d held", len(instances), len(held))
	}
	if len(liveness.Live) != 1 {
		t.Fatalf("it is still a live structure (nothing is retired for being a region): %+v", liveness)
	}
}

// execView is a minimal execution frame after the zone: a few quiet M5 bars, price at the proximal edge of the structure.
func execView(s *Strategy, in instance, price float64) *view {
	var bars []market.Candle
	start := in.Frame.Bars[len(in.Frame.Bars)-1].Time + frameStep
	for i := 0; i < 12; i++ {
		bars = append(bars, market.Candle{Time: start + int64(i)*300, Open: price, High: price + 0.1, Low: price - 0.1, Close: price})
	}
	return &view{frame: &analysiscontext.LegacyFrame{Bars: bars, ATR: make([]float64, len(bars))}, cfg: &s.cfg, price: price, atr: 1.0}
}

// A strong zone whose true structural stop cannot be met is refused: the stop is not tightened, the zone is not stretched and the band is not widened.
func TestAStrongZoneWithNoFeasibleEntryIsRefusedNotStretched(t *testing.T) {
	frame, _ := majorSupplyFrame()
	s := swingStrategy(structure.StrengthModerate)
	instances, _, _ := readWith(t, s, frame)
	if len(instances) != 1 {
		t.Fatalf("setup: %d", len(instances))
	}
	in := instances[0]
	structLow, structHigh := in.structural()
	s.cfg.StopBudget = strategyutil.StopBudget{MaxPips: 70, MinPips: 50, PipSize: 0.01, DeepEntryMinimumWidthPips: 200} // cap 0.70 of price
	s.cfg.EntryMinimumWidthPrice = 2.0                                                                                  // the band must stay 2.0 wide
	v := execView(s, in, structLow)
	invalidation := structHigh + 0.5
	var reason string
	d := s.planned(v, in, nil, &result{}, invalidation, false, structLow, structHigh, func(r string) { reason = r })
	if d != nil {
		t.Fatalf("no price of this zone keeps a 2.0 band within a 0.70 cap: %+v", d)
	}
	if reason != refusedStopBeyondCap && reason != refusedNoFeasibleEntry {
		t.Fatalf("refusal reason %q", reason)
	}
}

// With the budget the zone can meet, the entry is chosen inside the structure, the invalidation is exactly the one passed in, and the band is the zone from the entry.
func TestAFeasibleEntryIsInsideTheStructureAndKeepsTheStop(t *testing.T) {
	frame, _ := majorSupplyFrame()
	s := swingStrategy(structure.StrengthModerate)
	instances, _, _ := readWith(t, s, frame)
	in := instances[0]
	structLow, structHigh := in.structural()
	s.cfg.StopBudget = strategyutil.StopBudget{MaxPips: 5000, MinPips: 1, PipSize: 0.01, DeepEntryMinimumWidthPips: 1}
	v := execView(s, in, structLow)
	invalidation := structHigh + 0.5
	d := s.planned(v, in, nil, &result{}, invalidation, false, structLow, structHigh, func(r string) { t.Fatalf("refused: %s", r) })
	if d == nil {
		t.Fatal("a zone with room must publish")
	}
	if d.invalidation != invalidation {
		t.Fatalf("the structural stop must never move: %v -> %v", invalidation, d.invalidation)
	}
	if d.entryLow < structLow-1e-9 || d.entryHigh > structHigh+1e-9 || d.entryHigh <= d.entryLow {
		t.Fatalf("the band %v..%v must lie inside the structure %v..%v", d.entryLow, d.entryHigh, structLow, structHigh)
	}
	chosen, ok := d.plan.Chosen()
	if !ok || d.reference != chosen.Price || chosen.Price != d.entryLow {
		t.Fatalf("a SELL band starts at the recommended entry: %+v reference %v", chosen, d.reference)
	}
	if d.plan.Candidates[0].Kind != strategyutil.EntryProximal {
		t.Fatalf("candidates are listed shallowest first: %v", d.plan.Candidates)
	}
}

// M15 and H1 evidence reaches M5 execution on bar CLOSES: the first M5 bar that may touch a zone opens at or after the close of the structure bar by which the swing and the
// departure were both known, never on the open of that bar.
func TestStructureEvidenceIsTouchableFromTheCloseOfItsConfirmationBar(t *testing.T) {
	for _, tf := range []market.Timeframe{market.M15, market.H1} {
		minutes, _ := tf.Minutes()
		width := int64(minutes * 60)
		var structureBars, execBars []market.Candle
		for i := 0; i < 40; i++ {
			structureBars = append(structureBars, market.Candle{Time: 1_800_000_000 + int64(i)*width})
		}
		for i := 0; i < 40*minutes/5; i++ {
			execBars = append(execBars, market.Candle{Time: 1_800_000_000 + int64(i)*300})
		}
		in := instance{FrameOrigin: 20, FrameConfirmed: 25}
		bound := bind(tf, structureBars, execBars, []instance{in}, false)
		if len(bound) != 1 {
			t.Fatalf("%s: %d instances", tf, len(bound))
		}
		closeOfConfirmation := structureBars[25].Time + width
		first := execBars[bound[0].TouchableFrom].Time
		if first < closeOfConfirmation || (bound[0].TouchableFrom > 0 && execBars[bound[0].TouchableFrom-1].Time >= closeOfConfirmation) {
			t.Fatalf("%s: touchable from %d, the structure bar closes at %d", tf, first, closeOfConfirmation)
		}
	}
}
