package breakretest

import (
	"math"
	"math/rand"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// genScenario builds a randomised break-and-retest-flavoured M5 path: the
// positive fixture with randomised break, retest and rejection facts, a long
// run-in (so the full history exceeds the detector's window) and a few trailing
// candles.
func genScenario(rng *rand.Rand) []market.Candle {
	o := defaultLevel()
	o.secondTouch = rng.Intn(6) != 0
	o.breakClose = 4120.2 + rng.Float64()*4
	o.breakUpper = rng.Float64() * 1.5
	o.acceptClose = o.breakClose + (rng.Float64()-0.3)*2.5
	if rng.Intn(8) == 0 {
		o.acceptClose = 0
		o.fallBackClose = 4117 + rng.Float64()*3
	}
	o.waitBars = rng.Intn(6)
	o.touchLow = 4119.2 + rng.Float64()*2.5
	if rng.Intn(6) == 0 {
		o.touchLow = 0
	}
	o.rejectClose = o.touchLow + 1 + rng.Float64()*3
	if rng.Intn(6) == 0 {
		o.rejectClose = 0
	}
	o.spikeUpper = 3 + rng.Float64()*10
	o.noSpike = rng.Intn(10) == 0
	s, _ := levelScribe(o)
	if o.touchLow > 0 && o.touchLow < 4121 {
		// keep the touch bar's low coherent with its open
	}
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
	for it := 0; it < 120; it++ {
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
	if setups < 50 || episodes < 200 {
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
		o := defaultLevel()
		o.spikeUpper = 4 + rng.Float64()*8
		s := newScribe(fixtureStart, 4110)
		s.filler(400, 4110) // history far beyond the window
		lead := len(s.bars)
		bars, _ := bullishLevel(o)
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
	for it := 0; it < 150; it++ {
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
				} {
					if math.Abs(pair[0]-pair[1]) > 1e-6 {
						t.Fatalf("scenario %d candle %d: %s %.6f vs mirrored %.6f", it, i, name, pair[0], pair[1])
					}
				}
				compared++
			}
		}
	}
	if compared < 50 {
		t.Fatalf("only %d setups compared", compared)
	}
}

// Every published setup satisfies the technical invariants of the contract.
func TestEverySetupSatisfiesTheTechnicalInvariants(t *testing.T) {
	rng := rand.New(rand.NewSource(17))
	cfg := testConfig()
	checked := 0
	for it := 0; it < 200; it++ {
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
				// Geometry, in the trade's own orientation.
				if !(sign*(s.EntryLow-s.Stop) > 0 && sign*(s.Target-s.EntryHigh) > 0 && s.EntryHigh > s.EntryLow) {
					t.Fatalf("geometry not ordered for %s: %+v", s.Direction, s)
				}
				// The stop is beyond every price the retest and protected structure touched.
				for _, c := range bars {
					if c.Time < s.Retest.TouchTime || c.Time > s.ConfirmedAt {
						continue
					}
					extreme := c.Low
					if !buy {
						extreme = c.High
					}
					if !(sign*(s.Stop-extreme) < 0) {
						t.Fatalf("stop %.2f is not beyond the retest extreme %.2f (%s)", s.Stop, extreme, s.Direction)
					}
				}
				if !(sign*(s.Stop-s.ProtectedStructure) < 0) {
					t.Fatalf("stop %.2f not beyond the protected structure %.2f", s.Stop, s.ProtectedStructure)
				}
				// Risk and reward are those of the geometry, and the envelope holds.
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
				// Time ordering: reference formed before the break, the retest strictly
				// after the acceptance, the confirmation not after the last candle.
				switch {
				case !(s.Reference.AnchorTime <= s.Reference.FormedAt && s.Reference.FormedAt < s.Break.StartTime):
					t.Fatalf("reference not formed before the break: %+v", s.Reference)
				case !(s.Break.StartTime < s.Break.AcceptedAt || cfg.BreakoutAcceptBars == 1):
					t.Fatalf("break accepted before it started: %+v", s.Break)
				case !(s.Retest.TouchTime > s.Break.AcceptedAt && s.Retest.TouchTime <= s.ConfirmedAt && s.ConfirmedAt <= last.Time):
					t.Fatalf("retest not strictly after the acceptance / confirmation in the future: %+v", s)
				case s.Break.AcceptCloses != cfg.BreakoutAcceptBars:
					t.Fatalf("accept closes %d", s.Break.AcceptCloses)
				case (last.Time-s.ConfirmedAt)/300 > int64(cfg.ConfirmationMaxAgeBars):
					t.Fatalf("a stale confirmation was published: %+v", s)
				}
				// The measured break force is real, not assumed.
				if s.Break.BodyRatio < cfg.MinBreakBodyRatio || s.Break.CloseStrength < cfg.MinBreakCloseStrength || s.Break.DisplacementATR < cfg.MinBreakDisplacement {
					t.Fatalf("a weak break was published: %+v", s.Break)
				}
			}
		}
	}
	if checked < 50 {
		t.Fatalf("only %d setups checked", checked)
	}
	t.Logf("%d setups checked", checked)
}
