package sessionlevel

import (
	"fmt"
	"math"
	"math/rand"
	"reflect"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// genScenario builds a randomised Session Level path: one of the positive fixtures
// with randomised facts and a few trailing candles. About a third are broken on
// purpose so the fuzz covers refusals as well as publications.
func genScenario(rng *rand.Rand) []market.Candle {
	o := opts{low: 4100 + rng.Float64()*8}
	o.asia = rng.Intn(4) == 0
	o.noRally = rng.Intn(8) == 0
	o.dwell = rng.Intn(10) == 0
	o.prior = rng.Intn(3)
	o.priorSweep = rng.Intn(10) == 0
	o.afterBars = rng.Intn(4)
	var bars []market.Candle
	if rng.Intn(2) == 0 {
		o.waitBars = rng.Intn(5)
		o.weakReject = rng.Intn(4) == 0
		o.touchLow = o.low + rng.Float64()*0.6 - 0.35
		o.rejectClose = o.low + 0.3 + rng.Float64()*2.5
		bars, _ = firstTouchDay(o)
	} else {
		o.sweepLow = o.low - 0.1 - rng.Float64()*2.5
		o.sweepClose = o.low - 0.8 + rng.Float64()*1.4
		o.reclaimGap = rng.Intn(8)
		o.gapClose = o.low - 0.1 - rng.Float64()*0.9
		o.noReclaim = rng.Intn(8) == 0
		o.reclaimClose = o.low + 0.1 + rng.Float64()*2.4
		bars, _ = sweepDay(o)
	}
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: bars}
	for n := rng.Intn(4); n > 0; n-- {
		s.bar(s.last+(rng.Float64()-0.5)*3, rng.Float64()*0.8, rng.Float64()*0.8)
	}
	return s.bars
}

// randomWalk is a mean-reverting M5 walk over two and a half days that keeps
// revisiting earlier session extremes, with a break hour and a feed hole.
func randomWalk(rng *rand.Rand) ([]market.Candle, []market.Candle) {
	start := dayAnchor - 3*3600 - 24*3600
	price, anchor := 4100.0, 4100.0
	var m5 []market.Candle
	for i := 0; i < 12*24*2+130; i++ {
		t := start + int64(i)*300
		if rng.Intn(400) == 0 { // a hole in the feed
			continue
		}
		open := price
		price += rng.NormFloat64()*0.9 - 0.012*(price-anchor)
		hi := math.Max(open, price) + rng.Float64()*0.6
		lo := math.Min(open, price) - rng.Float64()*0.6
		m5 = append(m5, market.Candle{Time: t, Open: open, High: hi, Low: lo, Close: price})
		if rng.Intn(700) == 0 {
			anchor += rng.NormFloat64() * 6
		}
	}
	return m5, aggregateH1(m5)
}

// aggregateH1 builds complete hourly candles from M5 (all twelve periods present).
func aggregateH1(m5 []market.Candle) []market.Candle {
	var out []market.Candle
	for i := 0; i < len(m5); {
		hour := m5[i].Time - m5[i].Time%3600
		j := i
		c := market.Candle{Time: hour, Open: m5[i].Open, High: m5[i].High, Low: m5[i].Low}
		for j < len(m5) && m5[j].Time < hour+3600 {
			c.High, c.Low, c.Close = math.Max(c.High, m5[j].High), math.Min(c.Low, m5[j].Low), m5[j].Close
			j++
		}
		if j-i == 12 && m5[i].Time == hour {
			out = append(out, c)
		}
		i = j
	}
	return out
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
		for i := 100; i < len(bars); i += 1 + rng.Intn(3) {
			live := Detect(cfg, Input{M5: bars[:i+1]})
			asOf := DetectAsOf(cfg, Input{M5: bars}, i)
			if !equalAnalysis(live, asOf) {
				t.Fatalf("scenario %d candle %d: DetectAsOf with the future present differs from Detect on the prefix", it, i)
			}
			if mutated := DetectAsOf(cfg, Input{M5: garbageAfter(bars, i, rng)}, i); !equalAnalysis(live, mutated) {
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

// The same, with hourly candles: a decision may read only the hourly candles that had
// closed, and replacing every later one (or one still forming) changes nothing.
func TestPrefixInvarianceWithHourlyCandles(t *testing.T) {
	rng := rand.New(rand.NewSource(17))
	cfg := testConfig()
	levels, withDay := 0, 0
	for it := 0; it < 8; it++ {
		m5, h1 := randomWalk(rng)
		for i := 12*24 + 20; i < len(m5); i += 7 + rng.Intn(20) {
			now := m5[i].Time + 300
			var closed []market.Candle
			for _, c := range h1 {
				if c.Time+3600 <= now {
					closed = append(closed, c)
				}
			}
			live := Detect(cfg, Input{M5: m5[:i+1], H1: closed})
			asOf := DetectAsOf(cfg, Input{M5: m5, H1: h1}, i)
			if !equalAnalysis(live, asOf) {
				t.Fatalf("walk %d candle %d: the hourly candles after the evaluation changed the decision", it, i)
			}
			garbage := append([]market.Candle(nil), h1...)
			for k := range garbage {
				if garbage[k].Time+3600 > now {
					garbage[k].High, garbage[k].Low = 9999, 1
				}
			}
			if mutated := DetectAsOf(cfg, Input{M5: m5, H1: garbage}, i); !equalAnalysis(live, mutated) {
				t.Fatalf("walk %d candle %d: an unclosed hourly candle changed the decision", it, i)
			}
			levels += len(live.Levels)
			for _, l := range live.Levels {
				if l.Kind == KindDay {
					withDay++
					break
				}
			}
		}
	}
	if levels == 0 || withDay == 0 {
		t.Fatalf("no previous-day level was ever built (%d levels)", levels)
	}
}

// requiredHistory is enough, and nothing older than it matters: rewriting every
// candle before the window leaves the decision unchanged.
func TestRequiredHistoryIsSufficientAndOlderCandlesDoNotMatter(t *testing.T) {
	rng := rand.New(rand.NewSource(5))
	cfg := testConfig()
	need := cfg.requiredHistory()
	for it := 0; it < 3; it++ {
		m5, _ := randomWalk(rng)
		long := append([]market.Candle(nil), m5...)
		lead := &scribe{t: m5[0].Time - int64(need+300)*300, last: m5[0].Open}
		lead.filler(need+300, m5[0].Open)
		all := append(lead.bars, long...)
		for _, i := range []int{len(all) - 1, len(all) - 50, len(all) - 400} {
			normal := DetectAsOf(cfg, Input{M5: all}, i)
			altered := append([]market.Candle(nil), all...)
			for k := 0; k < i+1-need; k++ {
				altered[k].High += 7
				altered[k].Low -= 7
			}
			if !equalAnalysis(normal, DetectAsOf(cfg, Input{M5: altered}, i)) {
				t.Fatalf("walk %d candle %d: candles older than the required history changed the decision", it, i)
			}
		}
	}
}

func TestDecisionsAreDeterministic(t *testing.T) {
	rng := rand.New(rand.NewSource(23))
	for it := 0; it < 30; it++ {
		bars := genScenario(rng)
		first := analyse(bars)
		for k := 0; k < 3; k++ {
			if !equalAnalysis(first, analyse(bars)) {
				t.Fatalf("scenario %d: two runs differ", it)
			}
		}
	}
}

// signature is an episode with direction and price removed: what BUY and SELL twins
// must agree on.
func signature(ep Episode) string {
	s := fmt.Sprintf("%s|%d|%s|%s|%s|%d", ep.Level.Kind, ep.Level.PeriodStart, ep.Model, ep.State, ep.Reason, ep.EpisodeAt)
	if ep.Setup != nil {
		st := ep.Setup
		s += fmt.Sprintf("|conf %d risk %.6f rr %.6f touches %d/%v targetKind %s age %d integrity %.6f", st.ConfirmedAt, st.RiskPips, st.RewardRisk, st.PriorTouches, st.TouchesKnown, st.TargetKind, st.LevelAgeBars, st.LevelIntegrity)
	}
	return s
}

func signatures(a Analysis) []string {
	var out []string
	for _, ep := range a.Episodes {
		out = append(out, signature(ep))
	}
	sort.Strings(out)
	return out
}

// BUY/SELL symmetry: the reflected series gives the reflected decision.
func TestMirrorSymmetry(t *testing.T) {
	rng := rand.New(rand.NewSource(3))
	cfg := testConfig()
	compared, withSetup := 0, 0
	for it := 0; it < 150; it++ {
		bars := genScenario(rng)
		for _, i := range []int{len(bars) - 1, len(bars) - 2, len(bars) - 4} {
			buy := Detect(cfg, Input{M5: bars[:i+1]})
			sell := Detect(cfg, Input{M5: reflectPrices(bars[:i+1], 4110)})
			if !reflect.DeepEqual(signatures(buy), signatures(sell)) {
				t.Fatalf("scenario %d candle %d: the reflected series decides differently\nbuy  %v\nsell %v", it, i, signatures(buy), signatures(sell))
			}
			if len(buy.Levels) != len(sell.Levels) {
				t.Fatalf("scenario %d: levels %d vs %d", it, len(buy.Levels), len(sell.Levels))
			}
			for _, l := range buy.Levels {
				mirrorName := l.Name[:len(l.Name)-1]
				if l.Side == SideSupport {
					mirrorName += "H"
				} else {
					mirrorName += "L"
				}
				var found bool
				for _, r := range sell.Levels {
					if r.Name == mirrorName && r.PeriodStart == l.PeriodStart && math.Abs(r.Price-(2*4110-l.Price)) < 1e-6 {
						found = true
					}
				}
				if !found {
					t.Fatalf("scenario %d: level %s %v has no reflected twin ", it, l.Name, l.Price)
				}
			}
			compared++
			withSetup += len(buy.Setups)
			for k := range buy.Setups {
				b, s := buy.Setups[k], sell.Setups[k]
				if s.Direction == b.Direction || math.Abs(b.Stop-(8220-s.Stop)) > 1e-6 || math.Abs(b.Target-(8220-s.Target)) > 1e-6 {
					t.Fatalf("scenario %d: setup geometry is not reflected: %+v vs %+v", it, b, s)
				}
			}
		}
	}
	if compared < 400 || withSetup < 40 {
		t.Fatalf("too little compared: %d comparisons, %d setups", compared, withSetup)
	}
}

// Every published setup satisfies the technical invariants, in the fuzz.
func TestPublishedSetupsHoldTheirInvariants(t *testing.T) {
	rng := rand.New(rand.NewSource(29))
	cfg := testConfig()
	published := 0
	check := func(a Analysis, now int64, label string) {
		for _, s := range a.Setups {
			published++
			buy := s.Direction == market.Buy
			if buy && !(s.Stop < s.Level.Price && s.Stop < s.Reaction.Extreme && s.EntryLow <= s.EntryHigh && s.EntryHigh < s.Target) {
				t.Fatalf("%s: BUY geometry %+v", label, s)
			}
			if !buy && !(s.Stop > s.Level.Price && s.Stop > s.Reaction.Extreme && s.EntryLow <= s.EntryHigh && s.EntryLow > s.Target) {
				t.Fatalf("%s: SELL geometry %+v", label, s)
			}
			if s.Reaction.EpisodeAt < s.Level.AvailableAt || s.ConfirmedAt < s.Reaction.EpisodeAt || s.ConfirmedAt >= now {
				t.Fatalf("%s: causal order violated: available %d episode %d confirmed %d now %d", label, s.Level.AvailableAt, s.Reaction.EpisodeAt, s.ConfirmedAt, now)
			}
			if s.Level.AvailableAt != s.Level.PeriodEnd || s.Level.PeriodEnd > s.Reaction.EpisodeAt {
				t.Fatalf("%s: a level was used before its defining period completed: %+v", label, s.Level)
			}
			if s.RiskPips > cfg.ExecutionStopMaxPips+1e-9 || s.RewardRisk < cfg.MinimumRewardRisk-1e-9 || s.TargetRoomATR < cfg.MinimumTargetRoomATR-1e-9 {
				t.Fatalf("%s: limits violated: %+v", label, s)
			}
			if s.Level.Coverage < cfg.SessionMinCoverage-1e-9 && s.Level.Kind == KindSession {
				t.Fatalf("%s: a session level with coverage %v", label, s.Level.Coverage)
			}
			if s.Reaction.Model == ModelSweepReclaim && !(s.Reaction.SweepDepthATR > 0) {
				t.Fatalf("%s: a sweep without depth: %+v", label, s.Reaction)
			}
			if !s.TouchesKnown && s.PriorTouches != 0 {
				t.Fatalf("%s: a touch count claimed without history: %+v", label, s)
			}
		}
	}
	for it := 0; it < 200; it++ {
		bars := genScenario(rng)
		if it%2 == 1 {
			bars = reflectPrices(bars, 4110)
		}
		for i := 100; i < len(bars); i++ {
			check(Detect(cfg, Input{M5: bars[:i+1]}), bars[i].Time+300, fmt.Sprintf("scenario %d candle %d", it, i))
		}
	}
	for it := 0; it < 3; it++ {
		m5, h1 := randomWalk(rng)
		for i := 300; i < len(m5); i += 5 {
			check(DetectAsOf(cfg, Input{M5: m5, H1: h1}, i), m5[i].Time+300, fmt.Sprintf("walk %d candle %d", it, i))
		}
	}
	if published < 60 {
		t.Fatalf("only %d setups published in the fuzz", published)
	}
}
