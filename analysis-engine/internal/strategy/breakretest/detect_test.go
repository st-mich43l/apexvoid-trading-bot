package breakretest

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func near(t *testing.T, name string, got, want, tolerance float64) {
	t.Helper()
	if math.Abs(got-want) > tolerance {
		t.Errorf("%s = %.4f, want %.4f (±%.4f)", name, got, want, tolerance)
	}
}

// only returns the single episode for a direction, failing otherwise.
func only(t *testing.T, a Analysis, direction market.Direction) Episode {
	t.Helper()
	var found []Episode
	for _, ep := range a.Episodes {
		if ep.Direction == direction {
			found = append(found, ep)
		}
	}
	if len(found) != 1 {
		t.Fatalf("%s episodes = %d, want 1: %+v", direction, len(found), found)
	}
	return found[0]
}

func TestFixtureLandmarksArePivots(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	cfg := testConfig()
	isHigh, isLow := map[int]bool{}, map[int]bool{}
	for _, p := range pivotHighs(bars, cfg.PivotBars) {
		isHigh[p.index] = true
	}
	for _, p := range pivotLows(bars, cfg.PivotBars) {
		isLow[p.index] = true
	}
	for name, i := range map[string]int{"spike": m.spike, "pivot1": m.pivot1, "pivot2": m.pivot2} {
		if !isHigh[i] {
			t.Errorf("%s (%d) is not a pivot high; high=%.2f", name, i, bars[i].High)
		}
	}
	if !isLow[m.pivotLow] {
		t.Errorf("pivotLow (%d) is not a pivot low; low=%.2f", m.pivotLow, bars[m.pivotLow].Low)
	}
	lbars, lm := bullishLine(0)
	highs := map[int]bool{}
	for _, p := range pivotHighs(lbars, cfg.PivotBars) {
		highs[p.index] = true
	}
	for name, i := range map[string]int{"spike": lm.spike, "anchorA": lm.anchorA, "anchorB": lm.anchorB} {
		if !highs[i] {
			t.Errorf("line %s (%d) is not a pivot high", name, i)
		}
	}
	for i := lm.anchorA + 1; i < lm.anchorB; i++ {
		if highs[i] {
			t.Errorf("a pivot high at %d sits between the line anchors", i)
		}
	}
}

// A BUY on a broken horizontal resistance: every landmark and every number of the
// setup is asserted from the OHLC of the fixture.
func TestPositiveBuyKeyLevel(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	a := Detect(testConfig(), bars)
	if len(a.Setups) != 1 {
		t.Fatalf("setups = %d, want 1: %+v", len(a.Setups), a.Episodes)
	}
	s := a.Setups[0]
	ep := only(t, a, market.Buy)
	if ep.State != StateCandidate || ep.Reason != "" {
		t.Fatalf("state = %s/%s, want CANDIDATE", ep.State, ep.Reason)
	}
	if s.Direction != market.Buy || s.Reference.Kind != KindKeyLevel || s.Reference.SideBefore != "below" || s.Reference.Touches != 2 {
		t.Errorf("reference/direction wrong: %+v", s)
	}
	// Level = mean of the two pivot highs 4119.9 and 4120.0.
	near(t, "level", s.Reference.PriceAtBreak, 4119.95, 1e-9)
	if s.Reference.AnchorTime != bars[m.pivot1].Time {
		t.Errorf("anchor time = %d, want first touch %d", s.Reference.AnchorTime, bars[m.pivot1].Time)
	}
	// Formed when the SECOND touch became knowable: two bars after its pivot.
	if s.Reference.FormedAt != bars[m.pivot2+2].Time {
		t.Errorf("formed at %d, want the bar that confirmed the second touch %d", s.Reference.FormedAt, bars[m.pivot2+2].Time)
	}
	if s.Break.StartTime != bars[m.brk].Time || s.Break.AcceptedAt != bars[m.accept].Time || s.Break.AcceptCloses != 2 {
		t.Errorf("break wrong: %+v", s.Break)
	}
	if s.Retest.TouchTime != bars[m.touch].Time || s.Retest.ConfirmedAt != bars[m.confirm].Time || s.ConfirmedAt != bars[m.confirm].Time {
		t.Errorf("retest wrong: %+v", s.Retest)
	}
	if !(s.Break.BodyRatio >= 0.9 && s.Break.CloseStrength >= 0.9 && s.Break.DisplacementATR > 1) {
		t.Errorf("measured break force wrong: %+v", s.Break)
	}
	// Entry band: level +/- max(1 pip, 0.15 ATR) at the confirmation candle.
	tol := math.Max(0.1, 0.15*s.ATR)
	near(t, "entry low", s.EntryLow, 4119.95-tol, 1e-9)
	near(t, "entry high", s.EntryHigh, 4119.95+tol, 1e-9)
	// Stop: below the lower of the retest extreme and the protected structure,
	// by max(1 pip, 0.25 ATR).
	if !(s.ProtectedStructure < s.Retest.Low) {
		t.Fatalf("protected structure %.2f must be below the retest low %.2f in this fixture", s.ProtectedStructure, s.Retest.Low)
	}
	near(t, "stop", s.Stop, s.ProtectedStructure-math.Max(0.1, 0.25*s.ATR), 1e-9)
	near(t, "target", s.Target, 4132, 1e-9)
	near(t, "risk pips", s.RiskPips, (s.EntryHigh-s.Stop)/0.1, 1e-9)
	near(t, "rr", s.RewardRisk, (s.Target-s.EntryHigh)/(s.EntryHigh-s.Stop), 1e-9)
	if !(s.Stop < s.EntryLow && s.EntryHigh < s.Target) {
		t.Errorf("geometry not ordered: %+v", s)
	}
}

// The SELL is the exact price mirror of the BUY.
func TestPositiveSellIsTheMirror(t *testing.T) {
	bars, _ := bullishLevel(defaultLevel())
	buy := Detect(testConfig(), bars).Setups
	sell := Detect(testConfig(), reflectPrices(bars, 4110)).Setups
	if len(buy) != 1 || len(sell) != 1 {
		t.Fatalf("buy=%d sell=%d setups", len(buy), len(sell))
	}
	b, s := buy[0], sell[0]
	if s.Direction != market.Sell || s.Reference.SideBefore != "above" {
		t.Fatalf("not a sell above the structure: %+v", s)
	}
	const tol = 1e-9
	near(t, "entry low", s.EntryLow, 8220-b.EntryHigh, tol)
	near(t, "entry high", s.EntryHigh, 8220-b.EntryLow, tol)
	near(t, "stop", s.Stop, 8220-b.Stop, tol)
	near(t, "target", s.Target, 8220-b.Target, tol)
	near(t, "level", s.Reference.PriceAtBreak, 8220-b.Reference.PriceAtBreak, tol)
	near(t, "rr", s.RewardRisk, b.RewardRisk, tol)
	near(t, "risk pips", s.RiskPips, b.RiskPips, tol)
	if s.ConfirmedAt != b.ConfirmedAt || s.Break != b.Break {
		t.Errorf("times/measures differ: %+v vs %+v", s.Break, b.Break)
	}
	if !(s.Stop > s.EntryHigh && s.EntryLow > s.Target) {
		t.Errorf("sell geometry not ordered: %+v", s)
	}
}

// The line is evaluated at each candle's own position. With quiet candles after
// the confirmation the line has moved, yet the touch, the confirmation and the
// entry band are those of the retest candles, not of the last candle.
func TestTrendlineIsEvaluatedAtCandleTime(t *testing.T) {
	bars, m := bullishLine(2)
	a := Detect(testConfig(), bars)
	if len(a.Setups) != 1 {
		t.Fatalf("setups = %d: %+v", len(a.Setups), a.Episodes)
	}
	s := a.Setups[0]
	if s.Reference.Kind != KindTrendline || s.Reference.SideBefore != "below" || s.Reference.Touches < 2 {
		t.Fatalf("reference wrong: %+v", s.Reference)
	}
	near(t, "slope per candle", s.Reference.Slope, m.slope, 1e-9)
	near(t, "line at the break", s.Reference.PriceAtBreak, m.lineAt(m.brk), 1e-9)
	if s.Retest.TouchTime != bars[m.touch].Time || s.ConfirmedAt != bars[m.confirm].Time {
		t.Errorf("retest times wrong: %+v", s.Retest)
	}
	// The band is centred on the line at the CONFIRMATION candle.
	centre := (s.EntryLow + s.EntryHigh) / 2
	near(t, "entry centre", centre, m.lineAt(m.confirm), 1e-9)
	last := len(bars) - 1
	if math.Abs(m.lineAt(last)-m.lineAt(m.confirm)) < 0.3 {
		t.Fatalf("fixture does not separate the line at the last candle from the line at the confirmation")
	}
	if math.Abs(centre-m.lineAt(last)) < 0.3 {
		t.Errorf("entry centre %.2f follows the line at the last candle %.2f", centre, m.lineAt(last))
	}
}

// Stable identity: the same event keeps one candidate identity across the fresh
// candles that follow it; a second, later event gets a different one.
func TestEpisodeIdentityIsStable(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	cfg := testConfig()
	first := Detect(cfg, bars[:m.confirm+1]).Setups[0]
	later := Detect(cfg, append(append([]market.Candle(nil), bars...), market.Candle{Time: bars[len(bars)-1].Time + 300, Open: 4122.6, High: 4123.0, Low: 4122.4, Close: 4122.8})).Setups[0]
	if first.Reference.ID != later.Reference.ID || first.Break.StartTime != later.Break.StartTime || first.ConfirmedAt != later.ConfirmedAt {
		t.Errorf("identity moved between fresh candles: %+v vs %+v", first, later)
	}
	other, _ := bullishLevel(levelOptions{secondTouch: true, breakClose: 4122.4, breakUpper: 0.3, acceptClose: 4123.4, touchLow: 4120.1, rejectClose: 4122.6, spikeUpper: 8, waitBars: 3})
	moved := Detect(cfg, other).Setups
	if len(moved) != 1 || moved[0].Break.StartTime != first.Break.StartTime || moved[0].ConfirmedAt == first.ConfirmedAt {
		t.Errorf("a later confirmation of the same break must keep the break and change the confirmation: %+v", moved)
	}
}

// The lifecycle, observed prefix by prefix on the positive fixture.
func TestLifecycleStatesPrefixByPrefix(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	cfg := testConfig()
	stateAt := func(i int) (State, string) {
		a := DetectAsOf(cfg, bars, i)
		for _, ep := range a.Episodes {
			if ep.Direction == market.Buy {
				return ep.State, ep.Reason
			}
		}
		return "", ""
	}
	cases := []struct {
		at   int
		want State
	}{
		{m.brk - 1, ""},
		{m.brk, StateBreakPending},
		{m.accept, StateBreakAccepted},
		{m.touch, StateRetestTouched},
		{m.confirm, StateCandidate},
	}
	for _, c := range cases {
		if got, reason := stateAt(c.at); got != c.want {
			t.Errorf("as of candle %d: state %q (%s), want %q", c.at, got, reason, c.want)
		}
	}
}

// FX scale: the same structure at EURUSD prices (pip 0.0001, ATR about 5 pips).
func TestPositiveBuyAtFXScale(t *testing.T) {
	bars, _ := bullishLevel(defaultLevel())
	const base, factor = 1.0850, 0.00025
	scaled := make([]market.Candle, len(bars))
	for i, c := range bars {
		f := func(p float64) float64 { return base + (p-4110)*factor }
		scaled[i] = market.Candle{Time: c.Time, Open: f(c.Open), High: f(c.High), Low: f(c.Low), Close: f(c.Close)}
	}
	cfg := testConfig()
	cfg.PipSize, cfg.ExecutionStopMaxPips = 0.0001, 40
	a := Detect(cfg, scaled)
	if len(a.Setups) != 1 {
		t.Fatalf("setups = %d: %+v", len(a.Setups), a.Episodes)
	}
	s := a.Setups[0]
	if s.RiskPips < 5 || s.RiskPips > 40 {
		t.Errorf("risk %.1f pips is not an FX-scale risk", s.RiskPips)
	}
	near(t, "level", s.Reference.PriceAtBreak, base+(4119.95-4110)*factor, 1e-9)
}
