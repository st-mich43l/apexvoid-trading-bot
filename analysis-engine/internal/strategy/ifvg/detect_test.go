package ifvg

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func approx(a, b, tol float64) bool { return math.Abs(a-b) <= tol }

// candidateOf returns the single candidate of an analysis or fails the test.
func candidateOf(t *testing.T, a Analysis) Setup {
	t.Helper()
	if len(a.Setups) != 1 {
		t.Fatalf("want exactly one setup, got %d; episodes: %s", len(a.Setups), describe(a))
	}
	return a.Setups[0]
}

func describe(a Analysis) string {
	out := ""
	for _, ep := range a.Episodes {
		out += "[" + string(ep.Direction) + " " + ep.Gap.ID + " " + string(ep.State) + " " + ep.Reason + "] "
	}
	return out
}

// episodeWith returns the episode of the given direction whose reason matches.
func episodeWith(a Analysis, dir market.Direction, reason string) (Episode, bool) {
	for _, ep := range a.Episodes {
		if ep.Direction == dir && ep.Reason == reason {
			return ep, true
		}
	}
	return Episode{}, false
}

func TestBullishInversionRetestRejectionIsOneCandidate(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	s := candidateOf(t, Detect(testConfig(), bars))
	if s.Direction != market.Buy {
		t.Fatalf("direction %s", s.Direction)
	}
	// The original gap: a BEARISH FVG 4112-4116 formed by the third candle.
	if s.Gap.Role != "bearish" || !approx(s.Gap.Low, 4112, 1e-9) || !approx(s.Gap.High, 4116, 1e-9) || s.Gap.FormedAt != bars[m.c].Time {
		t.Fatalf("gap %+v", s.Gap)
	}
	if s.Inversion.Time != bars[m.invert].Time || s.Retest.TouchTime != bars[m.touch].Time || s.ConfirmedAt != bars[m.confirm].Time {
		t.Fatalf("lifecycle times: inversion %d touch %d confirm %d", s.Inversion.Time, s.Retest.TouchTime, s.ConfirmedAt)
	}
	if !(s.Inversion.Time > s.Gap.FormedAt && s.Retest.TouchTime > s.Inversion.Time && s.ConfirmedAt >= s.Retest.TouchTime) {
		t.Fatal("inversion, retest and rejection must come strictly in order")
	}
	// The stop sits beyond the gap's far bound and the retest extreme, below the entry band.
	if !(s.Stop < s.Gap.Low && s.Stop < s.Retest.Extreme && s.Stop < s.EntryLow) {
		t.Fatalf("stop %.3f gap low %.3f retest %.3f entry low %.3f", s.Stop, s.Gap.Low, s.Retest.Extreme, s.EntryLow)
	}
	// Entry: the proximal slice of the inverted gap (capped at 5.0), plus a tolerance above its top.
	if !approx(s.EntryLow, 4112, 1e-9) || !(s.EntryHigh > 4116) {
		t.Fatalf("entry %.3f-%.3f", s.EntryLow, s.EntryHigh)
	}
	// The target is the real swing high, not a manufactured multiple of risk.
	if !approx(s.Target, 4131, 0.6) || s.RewardRisk < 1.15 || s.TargetRoomATR < 0.55 {
		t.Fatalf("target %.3f rr %.2f room %.2f", s.Target, s.RewardRisk, s.TargetRoomATR)
	}
	if s.Retest.Touches != 1 || !s.WickRejection || s.ZoneIntegrity != 1 {
		t.Fatalf("retest facts %+v integrity %.2f", s.Retest, s.ZoneIntegrity)
	}
}

func TestMeasuredFactsAreTheObservedOnes(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	s := candidateOf(t, Detect(testConfig(), bars))
	inv := bars[m.invert]
	body := inv.Close - inv.Open
	rng := inv.High - inv.Low
	if !approx(s.Inversion.BodyRatio, body/rng, 1e-9) || !approx(s.Inversion.CloseStrength, (inv.Close-inv.Low)/rng, 1e-9) {
		t.Fatalf("inversion ratios %+v", s.Inversion)
	}
	if !approx(s.Inversion.DisplacementATR*s.Inversion.CloseBeyondATR, s.Inversion.DisplacementATR*(inv.Close-s.Gap.High)/(body/s.Inversion.DisplacementATR), 1e-6) {
		t.Fatalf("close beyond ATR inconsistent: %+v", s.Inversion)
	}
	conf := bars[m.confirm]
	crng := conf.High - conf.Low
	if !approx(s.Retest.WickRatio, (math.Min(conf.Open, conf.Close)-conf.Low)/crng, 1e-9) || !approx(s.Retest.CloseStrength, (conf.Close-conf.Low)/crng, 1e-9) {
		t.Fatalf("rejection ratios %+v", s.Retest)
	}
	if s.Retest.BarsAfterInversion != m.confirm-m.invert || s.Inversion.BarsFromGap != m.invert-m.c {
		t.Fatalf("ages %d %d", s.Retest.BarsAfterInversion, s.Inversion.BarsFromGap)
	}
}

func TestBearishTwinIsTheExactMirror(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	const center = 4000.0
	buy := candidateOf(t, Detect(testConfig(), bars))
	sell := candidateOf(t, Detect(testConfig(), reflectPrices(bars, center)))
	if sell.Direction != market.Sell || sell.Gap.Role != "bullish" {
		t.Fatalf("direction %s role %s", sell.Direction, sell.Gap.Role)
	}
	for name, pair := range map[string][2]float64{
		"entry low":  {2*center - buy.EntryHigh, sell.EntryLow},
		"entry high": {2*center - buy.EntryLow, sell.EntryHigh},
		"stop":       {2*center - buy.Stop, sell.Stop},
		"target":     {2*center - buy.Target, sell.Target},
		"gap low":    {2*center - buy.Gap.High, sell.Gap.Low},
		"gap high":   {2*center - buy.Gap.Low, sell.Gap.High},
		"extreme":    {2*center - buy.Retest.Extreme, sell.Retest.Extreme},
	} {
		if !approx(pair[0], pair[1], 1e-6) {
			t.Errorf("%s: mirrored %.6f vs %.6f", name, pair[0], pair[1])
		}
	}
	if !approx(buy.RewardRisk, sell.RewardRisk, 1e-9) || !approx(buy.RiskPips, sell.RiskPips, 1e-9) || buy.Inversion.BarsFromGap != sell.Inversion.BarsFromGap {
		t.Errorf("risk facts differ: %+v vs %+v", buy, sell)
	}
}

func TestLifecycleStatesAreReportedWhileTheSetupDevelops(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	cases := []struct {
		name  string
		until int
		want  State
	}{
		{"inversion closed, nothing after", m.invert, StateInversionConfirmed},
		{"retest touched, not yet rejected", m.touch, StateRetestTouched},
		{"rejection confirmed", m.confirm, StateCandidate},
	}
	for _, tc := range cases {
		a := DetectAsOf(testConfig(), bars, tc.until)
		found := false
		for _, ep := range a.Episodes {
			if ep.Direction == market.Buy && ep.Gap.FormedAt == bars[m.c].Time && ep.State == tc.want {
				found = true
			}
		}
		if !found {
			t.Errorf("%s: no BUY episode in %s; got %s", tc.name, tc.want, describe(a))
		}
	}
	// Before the inverting candle closes there is only a gap, not an episode.
	for _, ep := range DetectAsOf(testConfig(), bars, m.invert-1).Episodes {
		if ep.Direction == market.Buy && ep.Gap.FormedAt == bars[m.c].Time {
			t.Errorf("an episode exists before the inversion closed: %s", describe(DetectAsOf(testConfig(), bars, m.invert-1)))
		}
	}
}

func TestADeepRetestStopsBeyondTheRetestExtreme(t *testing.T) {
	o := defaultOpts()
	o.rejectLow = 4110.9 // the rejection wicks well below the gap's far bound
	bars, _ := bullishIFVG(o)
	s := candidateOf(t, Detect(testConfig(), bars))
	if !(s.Stop < 4110.9) || !approx(s.Retest.Extreme, 4110.9, 1e-9) {
		t.Fatalf("stop %.3f must sit beyond the retest extreme 4110.9 (%+v)", s.Stop, s.Retest)
	}
}

func TestRejectionOnTheTouchCandleItselfIsAValidRetest(t *testing.T) {
	o := defaultOpts()
	o.skipTouchCandle = true
	o.rejectLow = 4116.0
	bars, m := bullishIFVG(o)
	s := candidateOf(t, Detect(testConfig(), bars))
	if s.Retest.TouchTime != bars[m.confirm].Time || s.ConfirmedAt != bars[m.confirm].Time || s.Retest.TouchTime <= s.Inversion.Time {
		t.Fatalf("touch %d confirm %d inversion %d", s.Retest.TouchTime, s.ConfirmedAt, s.Inversion.Time)
	}
}

func TestEntryBandIsClippedToTheInstrumentSlice(t *testing.T) {
	cfg := testConfig()
	cfg.EntryMaxWidthPrice = 1.5
	bars, _ := bullishIFVG(defaultOpts())
	s := candidateOf(t, Detect(cfg, bars))
	if !approx(s.EntryLow, 4116-1.5, 1e-9) {
		t.Fatalf("entry low %.3f, want the proximal 1.5 slice of the gap (4114.5)", s.EntryLow)
	}
	if !(s.Stop < s.Gap.Low) {
		t.Fatalf("the stop still protects the whole gap: %.3f vs %.3f", s.Stop, s.Gap.Low)
	}
}
