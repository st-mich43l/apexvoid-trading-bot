package ifvg

import (
	"math"
	"math/rand"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// genScenario builds a randomised iFVG-flavoured M5 path: the positive fixture
// with randomised gap, inversion, retest and rejection facts and a few trailing
// candles.
func genScenario(rng *rand.Rand) []market.Candle {
	o := defaultOpts()
	o.gapTop = 4114.5 + rng.Float64()*3.5
	o.middleBody = 3 + rng.Float64()*3
	o.baseBars = rng.Intn(8)
	o.invertClose = o.gapTop + 0.6 + rng.Float64()*3.4
	if rng.Intn(10) == 0 {
		o.invertClose = o.gapTop + 0.05 // a weak close: the inversion must fail
	}
	o.invertUpper = rng.Float64() * 1.2
	o.invertLow = 4110.5 + rng.Float64()*1.5
	o.earlyWeakClose = rng.Intn(10) == 0
	o.waitBars = rng.Intn(5)
	o.touchLow = o.gapTop - 0.4 + rng.Float64()*0.8
	if rng.Intn(6) == 0 {
		o.touchLow = -1
	}
	o.rejectClose = o.gapTop + 0.5 + rng.Float64()*2.5
	o.rejectLow = o.gapTop - 1.2 + rng.Float64()*0.9
	if rng.Intn(6) == 0 {
		o.rejectClose = -1
	}
	o.spikeUpper = 8 + rng.Float64()*17
	o.noSpike = rng.Intn(10) == 0
	s, _ := ifvgScribe(o)
	for n := rng.Intn(4); n > 0; n-- {
		s.bar(s.last+(rng.Float64()-0.5)*3, rng.Float64()*0.8, rng.Float64()*0.8)
	}
	return s.bars
}

func garbageAfter(bars []market.Candle, i int, rng *rand.Rand) []market.Candle {
	out := append([]market.Candle(nil), bars...)
	for k := i + 1; k < len(out); k++ {
		p := 3000 + rng.Float64()*3000
		out[k] = market.Candle{Time: out[k].Time, Open: p, High: p + rng.Float64()*50, Low: p - rng.Float64()*50, Close: p + (rng.Float64()-0.5)*20}
		out[k].High = math.Max(out[k].High, math.Max(out[k].Open, out[k].Close))
		out[k].Low = math.Min(out[k].Low, math.Min(out[k].Open, out[k].Close))
	}
	return out
}

func equalAnalysis(a, b Analysis) bool { return reflect.DeepEqual(a, b) }

// The decision at candle i must depend only on candles up to i, whatever follows.
func TestPrefixInvarianceAndNoFutureLeak(t *testing.T) {
	rng := rand.New(rand.NewSource(11))
	cfg := testConfig()
	evaluations, setups, episodes := 0, 0, 0
	for it := 0; it < 400; it++ {
		bars := genScenario(rng)
		if it%2 == 1 {
			bars = reflectPrices(bars, 4110)
		}
		for i := 60; i < len(bars); i++ {
			live := Detect(cfg, bars[:i+1])
			asOf := DetectAsOf(cfg, bars, i)
			if !equalAnalysis(live, asOf) {
				t.Fatalf("scenario %d candle %d: DetectAsOf with the future present differs from Detect on the prefix", it, i)
			}
			if mutated := DetectAsOf(cfg, garbageAfter(bars, i, rng), i); !equalAnalysis(live, mutated) {
				t.Fatalf("scenario %d candle %d: replacing the future with garbage changed the decision", it, i)
			}
			evaluations++
			setups += len(live.Setups)
			episodes += len(live.Episodes)
		}
	}
	if setups < 60 || episodes < 400 {
		t.Fatalf("the fuzz is too weak to prove anything: %d evaluations, %d episodes, %d setups", evaluations, episodes, setups)
	}
	t.Logf("%d evaluations, %d episodes, %d setups", evaluations, episodes, setups)
}

// requiredHistory is enough: a decision computed over a much longer window than
// the detector reads is identical.
func TestRequiredHistoryIsSufficient(t *testing.T) {
	rng := rand.New(rand.NewSource(5))
	cfg := testConfig()
	for it := 0; it < 40; it++ {
		o := defaultOpts()
		o.spikeUpper = 6 + rng.Float64()*14
		s := newScribe(fixtureStart, 4110)
		s.filler(400, 4110) // history far beyond the window
		lead := len(s.bars)
		bars, _ := bullishIFVG(o)
		for _, c := range bars {
			c.Time += int64(lead) * 300
			s.bars = append(s.bars, c)
		}
		all := s.bars
		for _, i := range []int{len(all) - 1, len(all) - 2, len(all) - 5} {
			normal := DetectAsOf(cfg, all, i)
			longer := analyseWindow(cfg, all[:i+1])
			if !equalAnalysis(normal, longer) {
				t.Fatalf("scenario %d candle %d: reading the whole history changed the decision", it, i)
			}
		}
	}
}

// BUY/SELL symmetry: the reflected series gives the reflected decision.
func TestMirrorSymmetry(t *testing.T) {
	rng := rand.New(rand.NewSource(3))
	cfg := testConfig()
	compared := 0
	for it := 0; it < 400; it++ {
		bars := genScenario(rng)
		ref := reflectPrices(bars, 4110)
		for i := 60; i < len(bars); i++ {
			a, b := DetectAsOf(cfg, bars, i), DetectAsOf(cfg, ref, i)
			if len(a.Setups) != len(b.Setups) || len(a.Episodes) != len(b.Episodes) {
				t.Fatalf("scenario %d candle %d: %d/%d setups, %d/%d episodes", it, i, len(a.Setups), len(b.Setups), len(a.Episodes), len(b.Episodes))
			}
			for k := range a.Setups {
				x, y := a.Setups[k], b.Setups[k]
				if x.Direction == y.Direction {
					t.Fatalf("directions not mirrored")
				}
				for name, pair := range map[string][2]float64{
					"entry low": {x.EntryLow, 8220 - y.EntryHigh}, "entry high": {x.EntryHigh, 8220 - y.EntryLow},
					"stop": {x.Stop, 8220 - y.Stop}, "target": {x.Target, 8220 - y.Target}, "rr": {x.RewardRisk, y.RewardRisk},
					"gap low": {x.Gap.Low, 8220 - y.Gap.High}, "gap high": {x.Gap.High, 8220 - y.Gap.Low},
				} {
					if math.Abs(pair[0]-pair[1]) > 1e-6 {
						t.Fatalf("scenario %d candle %d: %s %.6f vs mirrored %.6f", it, i, name, pair[0], pair[1])
					}
				}
				compared++
			}
		}
	}
	if compared < 60 {
		t.Fatalf("only %d setups compared", compared)
	}
}

// Every published setup satisfies the technical invariants of the contract.
func TestEverySetupSatisfiesTheTechnicalInvariants(t *testing.T) {
	rng := rand.New(rand.NewSource(17))
	cfg := testConfig()
	checked := 0
	for it := 0; it < 400; it++ {
		bars := genScenario(rng)
		if it%2 == 1 {
			bars = reflectPrices(bars, 4110)
		}
		for i := 60; i < len(bars); i++ {
			for _, s := range Detect(cfg, bars[:i+1]).Setups {
				checked++
				last := bars[i]
				buy := s.Direction == market.Buy
				sign := 1.0
				if !buy {
					sign = -1
				}
				if !(sign*(s.EntryLow-s.Stop) > 0 && sign*(s.Target-s.EntryHigh) > 0 && s.EntryHigh > s.EntryLow) {
					t.Fatalf("geometry not ordered for %s: %+v", s.Direction, s)
				}
				// The stop is beyond the gap's far bound and every price the retest touched.
				farBound := s.Gap.Low
				if !buy {
					farBound = s.Gap.High
				}
				if !(sign*(s.Stop-farBound) < 0) {
					t.Fatalf("stop %.3f not beyond the gap's far bound %.3f (%s)", s.Stop, farBound, s.Direction)
				}
				for _, c := range bars {
					if c.Time < s.Retest.TouchTime || c.Time > s.ConfirmedAt {
						continue
					}
					extreme := c.Low
					if !buy {
						extreme = c.High
					}
					if !(sign*(s.Stop-extreme) < 0) {
						t.Fatalf("stop %.3f is not beyond the retest extreme %.3f (%s)", s.Stop, extreme, s.Direction)
					}
				}
				proximal := s.EntryHigh
				if !buy {
					proximal = s.EntryLow
				}
				risk, reward := math.Abs(proximal-s.Stop), math.Abs(s.Target-proximal)
				if math.Abs(s.RiskPips-risk/cfg.PipSize) > 1e-6 || math.Abs(s.RewardRisk-reward/risk) > 1e-6 {
					t.Fatalf("risk/reward bookkeeping wrong: %+v", s)
				}
				if s.RiskPips > cfg.ExecutionStopMaxPips+1e-9 || s.RewardRisk < cfg.MinimumRewardRisk-1e-9 {
					t.Fatalf("a setup beyond the envelope or the RR floor was published: %+v", s)
				}
				// Time ordering: the gap formed before the inversion, the retest strictly
				// after it, the confirmation not after the last candle and still fresh.
				switch {
				case !(s.Gap.FormedAt < s.Inversion.Time):
					t.Fatalf("gap formed at or after its inversion: %+v", s)
				case !(s.Retest.TouchTime > s.Inversion.Time && s.Retest.TouchTime <= s.ConfirmedAt && s.ConfirmedAt <= last.Time):
					t.Fatalf("retest not strictly after the inversion / confirmation in the future: %+v", s)
				case (last.Time-s.ConfirmedAt)/300 > int64(cfg.ConfirmationMaxAge):
					t.Fatalf("a stale confirmation was published: %+v", s)
				case s.Inversion.BarsFromGap > cfg.MaximumInversionBars:
					t.Fatalf("a stale gap was published: %+v", s.Inversion)
				}
				// The measured inversion force and gap are real, not assumed.
				if s.Inversion.BodyRatio < cfg.MinInversionBody || s.Inversion.CloseStrength < cfg.MinInversionCloseStr || s.Inversion.DisplacementATR < cfg.MinInversionDisp {
					t.Fatalf("a weak inversion was published: %+v", s.Inversion)
				}
				if s.Gap.WidthATR < cfg.MinimumGapATR || s.Gap.OriginBodyATR < cfg.MinimumOriginBodyATR {
					t.Fatalf("a weak gap was published: %+v", s.Gap)
				}
				if s.Retest.Touches < 1 || s.ZoneIntegrity < 0 || s.ZoneIntegrity > 1 {
					t.Fatalf("retest facts out of range: %+v integrity %.3f", s.Retest, s.ZoneIntegrity)
				}
			}
		}
	}
	if checked < 60 {
		t.Fatalf("only %d setups checked", checked)
	}
	t.Logf("%d setups checked", checked)
}

// Detection is deterministic: the same candles always give the same decision, in
// either order of evaluation.
func TestDetectionIsDeterministic(t *testing.T) {
	rng := rand.New(rand.NewSource(9))
	cfg := testConfig()
	for it := 0; it < 30; it++ {
		bars := genScenario(rng)
		first := Detect(cfg, bars)
		for n := 0; n < 3; n++ {
			if !equalAnalysis(first, Detect(cfg, bars)) {
				t.Fatalf("scenario %d: repeated detection differs", it)
			}
		}
	}
}
