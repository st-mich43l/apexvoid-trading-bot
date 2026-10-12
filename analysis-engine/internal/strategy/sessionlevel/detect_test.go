package sessionlevel

import (
	"fmt"
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func analyse(bars []market.Candle) Analysis { return Detect(testConfig(), Input{M5: bars}) }

// episodesOf returns the episodes of one level and model.
func episodesOf(a Analysis, level, model string) []Episode {
	var out []Episode
	for _, ep := range a.Episodes {
		if ep.Level.Name == level && ep.Model == model {
			out = append(out, ep)
		}
	}
	return out
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func TestFirstTouchRejectionOfTheLondonLowIsABuy(t *testing.T) {
	bars, m := firstTouchDay(opts{})
	a := analyse(bars)
	if len(a.Setups) != 1 {
		t.Fatalf("setups = %d: %+v", len(a.Setups), a.Episodes)
	}
	s := a.Setups[0]
	if s.Direction != market.Buy || s.Level.Name != "LONDON_L" || s.Level.Kind != KindSession || s.Level.Side != SideSupport {
		t.Fatalf("setup = %+v", s)
	}
	if s.Level.Price != 4104.0 {
		t.Fatalf("level price = %v, want the London low 4104.0", s.Level.Price)
	}
	londonEnd := dayAnchor + 13*3600
	if s.Level.AvailableAt != londonEnd || s.Level.PeriodEnd != londonEnd || s.Level.PeriodStart != dayAnchor+7*3600 {
		t.Fatalf("level period = %d..%d available %d", s.Level.PeriodStart, s.Level.PeriodEnd, s.Level.AvailableAt)
	}
	if s.Level.SourceTF != market.M5 || s.Level.FormedAt != bars[m.low].Time || s.Level.Candles != londonBars || s.Level.Coverage != 1 {
		t.Fatalf("level provenance = %+v", s.Level)
	}
	if want := fmt.Sprintf("level:LONDON_L:%d", dayAnchor+7*3600); s.Level.ID != want {
		t.Fatalf("level id = %q, want %q", s.Level.ID, want)
	}
	r := s.Reaction
	if r.Model != ModelFirstTouch || r.EpisodeAt != bars[m.touch].Time || r.ConfirmedAt != bars[m.confirm].Time || s.ConfirmedAt != r.ConfirmedAt {
		t.Fatalf("reaction = %+v", r)
	}
	if r.Extreme != bars[m.touch].Low || r.BarsToConfirm != 1 || r.SweepDepthATR != 0 || r.RejectionStrength < 0.9 {
		t.Fatalf("reaction measurements = %+v", r)
	}
	// The touch did not cross the level, so it is the first touch of a fresh level.
	if !s.TouchesKnown || s.PriorTouches != 0 || s.LevelIntegrity != 1 {
		t.Fatalf("touches = %d known=%v integrity %v", s.PriorTouches, s.TouchesKnown, s.LevelIntegrity)
	}
	// Honest geometry: the stop is beyond the level AND the touch's extreme, the
	// entry band sits on the level, the target is the rally's swing high.
	if !(s.Stop < s.Level.Price && s.Stop < r.Extreme) {
		t.Fatalf("stop %v not beyond the level %v and the extreme %v", s.Stop, s.Level.Price, r.Extreme)
	}
	if s.EntryLow != s.Level.Price || !(s.EntryHigh > s.EntryLow) || !(s.Target > s.EntryHigh) {
		t.Fatalf("geometry entry %v-%v stop %v target %v", s.EntryLow, s.EntryHigh, s.Stop, s.Target)
	}
	if s.TargetKind != "swing" || !near(s.Target, bars[m.top].High) {
		t.Fatalf("target = %v (%s), want the rally's swing high %v", s.Target, s.TargetKind, bars[m.top].High)
	}
	if !near(s.RiskPips, (s.EntryHigh-s.Stop)/0.1) || !near(s.RewardRisk, (s.Target-s.EntryHigh)/(s.EntryHigh-s.Stop)) {
		t.Fatalf("risk %v pips, RR %v", s.RiskPips, s.RewardRisk)
	}
	if s.LevelAgeBars != int((bars[m.confirm].Time-londonEnd)/300) {
		t.Fatalf("level age = %d bars", s.LevelAgeBars)
	}
	eps := episodesOf(a, "LONDON_L", ModelFirstTouch)
	if len(eps) != 1 || eps[0].State != StateCandidate || eps[0].Reason != "" || eps[0].Direction != market.Buy {
		t.Fatalf("episodes = %+v", eps)
	}
}

func TestSweepAndReclaimOfTheLondonLowIsABuy(t *testing.T) {
	bars, m := sweepDay(opts{})
	a := analyse(bars)
	if len(a.Setups) != 1 {
		t.Fatalf("setups = %d: %+v", len(a.Setups), a.Episodes)
	}
	s := a.Setups[0]
	if s.Direction != market.Buy || s.Level.Name != "LONDON_L" || s.Reaction.Model != ModelSweepReclaim {
		t.Fatalf("setup = %+v", s)
	}
	r := s.Reaction
	if r.EpisodeAt != bars[m.sweep].Time || r.ConfirmedAt != bars[m.confirm].Time {
		t.Fatalf("reaction = %+v", r)
	}
	if !(r.SweepDepthATR > 0) || !(r.ReclaimDepthATR > 0) || r.PenetrationATR != 0 {
		t.Fatalf("sweep %v reclaim %v penetration %v", r.SweepDepthATR, r.ReclaimDepthATR, r.PenetrationATR)
	}
	// The stop is beyond the sweep's own wick.
	if !(s.Stop < bars[m.sweep].Low) || r.Extreme != bars[m.sweep].Low {
		t.Fatalf("stop %v extreme %v sweep low %v", s.Stop, r.Extreme, bars[m.sweep].Low)
	}
	if s.LevelIntegrity >= 1 {
		t.Fatalf("a close through the level since availability must lower the level's integrity: %v", s.LevelIntegrity)
	}
}

// A touch that pierces the level by less than the sweep threshold is still a first
// touch; its stop is beyond the pierced low.
func TestShallowPenetrationIsAFirstTouchWithTheStopBeyondIt(t *testing.T) {
	bars, m := firstTouchDay(opts{touchLow: 4103.85})
	a := analyse(bars)
	if len(a.Setups) != 1 || a.Setups[0].Reaction.Model != ModelFirstTouch {
		t.Fatalf("setups = %+v", a.Episodes)
	}
	s := a.Setups[0]
	if !(s.Reaction.PenetrationATR > 0) || !(s.Stop < bars[m.touch].Low) {
		t.Fatalf("penetration %v stop %v touch low %v", s.Reaction.PenetrationATR, s.Stop, bars[m.touch].Low)
	}
}

// A setup is reflected exactly by reflecting the prices: a SELL off the London HIGH.
func TestBearishTwinsAreSellsOffTheHigh(t *testing.T) {
	for _, tc := range []struct {
		name  string
		build func(opts) ([]market.Candle, marks)
		model string
	}{{"first touch", firstTouchDay, ModelFirstTouch}, {"sweep and reclaim", sweepDay, ModelSweepReclaim}} {
		t.Run(tc.name, func(t *testing.T) {
			bars, m := tc.build(opts{})
			buy := analyse(bars)
			sell := analyse(reflectPrices(bars, 4112))
			if len(buy.Setups) != 1 || len(sell.Setups) != 1 {
				t.Fatalf("buy %d sell %d setups", len(buy.Setups), len(sell.Setups))
			}
			b, s := buy.Setups[0], sell.Setups[0]
			if s.Direction != market.Sell || s.Level.Name != "LONDON_H" || s.Level.Side != SideResistance || s.Reaction.Model != tc.model {
				t.Fatalf("sell setup = %+v", s)
			}
			if s.Reaction.ConfirmedAt != b.Reaction.ConfirmedAt || s.Reaction.EpisodeAt != bars[m.touch].Time {
				t.Fatalf("timing differs: %+v vs %+v", s.Reaction, b.Reaction)
			}
			for name, pair := range map[string][2]float64{
				"level": {b.Level.Price, 8224 - s.Level.Price}, "entry low": {b.EntryLow, 8224 - s.EntryHigh}, "entry high": {b.EntryHigh, 8224 - s.EntryLow},
				"stop": {b.Stop, 8224 - s.Stop}, "target": {b.Target, 8224 - s.Target}, "extreme": {b.Reaction.Extreme, 8224 - s.Reaction.Extreme},
			} {
				if math.Abs(pair[0]-pair[1]) > 1e-6 {
					t.Errorf("%s: buy %v vs reflected sell %v", name, pair[0], pair[1])
				}
			}
			if !near(b.RiskPips, s.RiskPips) || !near(b.RewardRisk, s.RewardRisk) || b.PriorTouches != s.PriorTouches {
				t.Errorf("risk/reward/touches differ: %+v vs %+v", b, s)
			}
		})
	}
}

// A touch that fails the confirmation window and then sweeps is a first-touch
// attempt that ESCALATES to a sweep, which is then judged as a sweep.
func TestAFirstTouchThatSweepsEscalatesToTheSweepModel(t *testing.T) {
	bars, _ := dayPrefix(opts{})
	s := &scribe{t: bars.t, last: bars.last, bars: append([]market.Candle(nil), bars.bars...)}
	low := 4104.0
	s.raw(low+1.0, low+1.3, low+0.1, low+0.6) // touch, not confirming
	s.raw(low+0.5, low+0.7, low-0.7, low-0.3) // sweep
	s.raw(low-0.2, low+2.1, low-0.3, low+2.0) // reclaim
	a := analyse(s.bars)
	if len(a.Setups) != 1 || a.Setups[0].Reaction.Model != ModelSweepReclaim {
		t.Fatalf("setups = %+v", a.Episodes)
	}
	first := episodesOf(a, "LONDON_L", ModelFirstTouch)
	if len(first) != 1 || first[0].State != StateEscalatedToSweep {
		t.Fatalf("the touch attempt = %+v, want ESCALATED_TO_SWEEP", first)
	}
	sweep := episodesOf(a, "LONDON_L", ModelSweepReclaim)
	if len(sweep) != 1 || sweep[0].Key() == first[0].Key() {
		t.Fatalf("the sweep must be its own episode: %+v", sweep)
	}
}

func TestEpisodeIdentityIsStableAcrossLaterEvaluationsAndDistinctAcrossTouches(t *testing.T) {
	bars, m := firstTouchDay(opts{afterBars: 2})
	atConfirm := Detect(testConfig(), Input{M5: bars[:m.confirm+1]})
	later := Detect(testConfig(), Input{M5: bars})
	if len(atConfirm.Setups) != 1 || len(later.Setups) != 1 {
		t.Fatalf("setups at confirm %d, two bars later %d", len(atConfirm.Setups), len(later.Setups))
	}
	a, b := episodesOf(atConfirm, "LONDON_L", ModelFirstTouch)[0], episodesOf(later, "LONDON_L", ModelFirstTouch)[0]
	if a.Key() != b.Key() {
		t.Fatalf("the same reaction changed identity: %q vs %q", a.Key(), b.Key())
	}
	// A different touch of the same level is a different episode.
	again, mm := firstTouchDay(opts{prior: 1})
	x := episodesOf(analyse(again), "LONDON_L", ModelFirstTouch)
	keys := map[string]bool{}
	for _, ep := range x {
		keys[ep.Key()] = true
	}
	if len(x) < 2 || len(keys) != len(x) {
		t.Fatalf("touches of one level must be distinct episodes: %d episodes, %d keys (confirm at %d)", len(x), len(keys), mm.confirm)
	}
}

func TestLevelsAreCompletedSessionsOnly(t *testing.T) {
	bars, m := firstTouchDay(opts{})
	a := analyse(bars)
	names := map[string]Level{}
	for _, l := range a.Levels {
		names[l.Name] = l
	}
	for _, want := range []string{"ASIA_H", "ASIA_L", "LONDON_H", "LONDON_L"} {
		if _, ok := names[want]; !ok {
			t.Errorf("%s missing from %v", want, names)
		}
	}
	if names["ASIA_H"].Price != 4121.0 || names["ASIA_L"].Price != 4119.0 || names["LONDON_H"].Price != 4121.0 {
		t.Errorf("levels = %+v", names)
	}
	// New York has barely begun: its period never completed, so it is no level.
	if _, ok := names["NY_H"]; ok {
		t.Error("the forming New York session produced a level")
	}
	// Mid-London there is no London level yet, only the finished Asia session.
	mid := Detect(testConfig(), Input{M5: bars[:m.low+10]})
	for _, l := range mid.Levels {
		if l.Name == "LONDON_H" || l.Name == "LONDON_L" {
			t.Errorf("%s exists while London is still forming", l.Name)
		}
	}
	if len(mid.Setups) != 0 {
		t.Fatalf("setups while London forms: %+v", mid.Setups)
	}
}

func TestOpposingLevelsAreTargetsUntilPriceHasReachedThem(t *testing.T) {
	bars, _ := firstTouchDay(opts{noRally: true})
	a := analyse(bars)
	if len(a.Setups) != 1 {
		t.Fatalf("setups = %d: %+v", len(a.Setups), a.Episodes)
	}
	s := a.Setups[0]
	if s.TargetKind != "level" || s.Target != 4121.0 {
		t.Fatalf("target = %v (%s), want the unreached London high 4121", s.Target, s.TargetKind)
	}
}
