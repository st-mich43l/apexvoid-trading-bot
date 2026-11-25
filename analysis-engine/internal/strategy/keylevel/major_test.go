package keylevel

import (
	"math"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// PR-KL13: the major-level book. Prices are XAU-like; the reference (H1) ATR is 10, so a departure of 1 ref ATR is 10 and the swing rule's 1.5 ref ATR is 15.

func majorConfig() MajorConfig {
	c, err := parseMajorConfig(map[string]any{
		"reference_timeframe": "H1", "band_atr": 0.25, "departure_ref_atr": 1.0, "reaction_window_bars": 12, "swing_min_reactions": 2,
		"swing_min_span_hours": 8.0, "swing_min_max_departure_ref_atr": 1.5,
		"session_levels":    []any{"PDH", "PDL", "PWH", "PWL", "ASIA_H", "ASIA_L", "LONDON_H", "LONDON_L"},
		"major_round_steps": []any{50.0, 100.0}, "minor_round_step": 10.0, "round_min_reactions": 1, "flip_min_reactions": 1,
	})
	if err != nil {
		panic(err)
	}
	return c
}

func rx(atHours float64, departure float64) levelReaction {
	at := int64(atHours * 3600)
	return levelReaction{at: at, doneAt: at + 3600, side: 1, departure: departure}
}

func TestEachRuleQualifiesALevelAlone(t *testing.T) {
	mc := majorConfig()
	cases := map[string]struct {
		facts levelFacts
		want  []string
	}{
		"S session":          {levelFacts{intact: true, sessions: []string{"PDH"}}, []string{"S"}},
		"R round 50 + 1":     {levelFacts{intact: true, roundClass: 50, reactions: []levelReaction{rx(1, 1.1)}}, []string{"R"}},
		"R round 100 + 1":    {levelFacts{intact: true, roundClass: 100, reactions: []levelReaction{rx(1, 1.1)}}, []string{"R"}},
		"W swing":            {levelFacts{intact: true, reactions: []levelReaction{rx(0, 1.2), rx(9, 1.6)}}, []string{"W"}},
		"F flip":             {levelFacts{intact: true, flipped: true, flipKnownAt: 99}, []string{"F"}},
		"3 reactions in 7 h": {levelFacts{intact: true, reactions: []levelReaction{rx(0, 2), rx(3, 2), rx(7, 2)}}, nil},
		"largest dep 1.4":    {levelFacts{intact: true, reactions: []levelReaction{rx(0, 1.4), rx(10, 1.3)}}, nil},
		"$10 handle alone":   {levelFacts{intact: true, roundClass: 10, reactions: []levelReaction{rx(1, 1.1)}}, nil},
		"$50 no reaction":    {levelFacts{intact: true, roundClass: 50}, nil},
		"broken session":     {levelFacts{intact: false, sessions: []string{"PWH"}}, nil},
	}
	for name, c := range cases {
		got, _ := classify(c.facts, 5, mc)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: rules %v, want %v", name, got, c.want)
		}
	}
}

func TestTheLevelIsKnownWhenItsFirstRuleIsMet(t *testing.T) {
	mc := majorConfig()
	_, at := classify(levelFacts{intact: true, roundClass: 50, sessions: []string{"ASIA_H"}, reactions: []levelReaction{rx(1, 1.1)}}, 7200, mc)
	if at != 3600+3600 {
		t.Fatalf("R was met when its reaction's departure bar closed (7200), the session became knowable at 7200 too: %d", at)
	}
	_, at = classify(levelFacts{intact: true, reactions: []levelReaction{rx(0, 1.2), rx(4, 1.6), rx(9, 1.2)}}, 0, mc)
	if at != 9*3600+3600 {
		t.Fatalf("W is met at the third reaction (span 9 h): %d", at)
	}
}

// bars builds closed H1 bars from (high, low, close) triples, one hour apart.
func bars(hlc ...[3]float64) []market.Candle {
	out := make([]market.Candle, len(hlc))
	for i, v := range hlc {
		out[i] = market.Candle{Time: int64(i) * 3600, Open: v[2], High: v[0], Low: v[1], Close: v[2]}
	}
	return out
}

// A level at 3000 with a band of 2.5 (2997.5-3002.5); departure 10.
func TestAReactionNeedsTheDepartureBeforeAFarClose(t *testing.T) {
	approach := [][3]float64{{3020, 3010, 3015}, {3016, 3001, 3005}} // from above, into the band
	rally := [][3]float64{{3008, 3003, 3007}, {3014, 3006, 3013}}    // 3014 - 3002.5 = 11.5 >= 10
	got := reactionsAt(bars(append(approach, rally...)...), 2997.5, 3002.5, 10, 10, 12, 3600)
	if len(got) != 1 || got[0].side != 1 || got[0].at != 3600 || got[0].doneAt != 4*3600 || math.Abs(got[0].departure-1.15) > 1e-9 {
		t.Fatalf("one reaction from above, known when the 4th bar closed: %+v", got)
	}
	broken := [][3]float64{{3004, 2990, 2995}, {3014, 2994, 3013}} // closes below 2997.5 first
	if got := reactionsAt(bars(append(approach, broken...)...), 2997.5, 3002.5, 10, 10, 12, 3600); len(got) != 0 {
		t.Fatalf("a close beyond the far edge first is no reaction: %+v", got)
	}
	short := [][3]float64{{3008, 3003, 3007}, {3010, 3006, 3009}} // only 7.5 away
	if got := reactionsAt(bars(append(approach, short...)...), 2997.5, 3002.5, 10, 10, 12, 3600); len(got) != 0 {
		t.Fatalf("a departure under 1 ref ATR is no reaction: %+v", got)
	}
}

func TestEpisodesAreDistinctOnlyWhenPriceLeftByTheDeparture(t *testing.T) {
	seq := [][3]float64{
		{3020, 3010, 3015}, {3016, 3001, 3005}, // touch from above
		{3010, 3004, 3008}, {3005, 3001, 3003}, // a small move and a second touch: price never left the band by the departure, so one episode
		{3014, 3006, 3013},                     // the departure (11.5): reaction 1, dated at the first touch
		{3030, 3016, 3028},                     // away
		{3029, 3002, 3006}, {3020, 3008, 3019}, // reaction 2
	}
	got := reactionsAt(bars(seq...), 2997.5, 3002.5, 10, 10, 12, 3600)
	if len(got) != 2 || got[0].at != 3600 || got[1].at != 6*3600 || got[0].doneAt != 5*3600 {
		t.Fatalf("two distinct episodes: %+v", got)
	}
	// Causal: the history cut before the second reaction's departure bar closed knows only the first.
	if cut := reactionsAt(bars(seq[:7]...), 2997.5, 3002.5, 10, 10, 12, 3600); len(cut) != 1 || !reflect.DeepEqual(cut[0], got[0]) {
		t.Fatalf("a truncated history gives the same earlier reactions and nothing later: %+v", cut)
	}
}

func TestAnAcceptedBreakNeedsTheConfiguredCloses(t *testing.T) {
	seq := bars([3]float64{3010, 3004, 3008}, [3]float64{3005, 2990, 2994}, [3]float64{2996, 2988, 2992}, [3]float64{2999, 2990, 2995})
	got := acceptedBreaks(seq, 2997.5, 3002.5, 2, 3600)
	if len(got) != 1 || got[0].side != -1 || got[0].at != 2*3600+3600 {
		t.Fatalf("two closes below the band accept a break, known at the second close: %+v", got)
	}
	if one := acceptedBreaks(seq[:2], 2997.5, 3002.5, 2, 3600); len(one) != 0 {
		t.Fatalf("one close is not acceptance: %+v", one)
	}
}

func TestAMergedLevelTakesTheSessionPriceNeverTheMean(t *testing.T) {
	merged := mergeLevels([]majorLevel{
		{price: 3001.0, band: 1.5, source: sourceSwingM15, tf: market.M15, facts: levelFacts{reactions: []levelReaction{rx(0, 2), rx(9, 2)}}, rules: []string{"W"}, knownAt: 50},
		{price: 3002.4, band: 2.5, source: sourceSession, tf: market.H1, facts: levelFacts{sessions: []string{"PDH"}}, rules: []string{"S"}, knownAt: 20, roleHighs: 1},
		{price: 3040, band: 2.5, source: sourceRound, tf: market.H1},
	})
	if len(merged) != 2 {
		t.Fatalf("the overlapping swing and session merge, the far handle stays apart: %d", len(merged))
	}
	m := merged[0]
	if m.price != 3002.4 || m.band != 2.5 || m.tf != market.H1 || m.source != sourceSession {
		t.Fatalf("session price, band and frame win: %+v", m)
	}
	if !reflect.DeepEqual(m.rules, []string{"S", "W"}) || m.knownAt != 20 || len(m.facts.reactions) != 2 {
		t.Fatalf("the union of facts and rules, known at the earliest: %+v", m)
	}
}

func TestSwingClustersUseConfirmedSwingsAndTheMedian(t *testing.T) {
	frame := &context.LegacyFrame{Bars: make([]market.Candle, 20), Swings: []techniquezone.Swing{
		{Price: 3000, ConfirmedIndex: 3}, {Price: 3001, ConfirmedIndex: 8}, {Price: 3010, ConfirmedIndex: 10}, {Price: 3002, ConfirmedIndex: 12},
		{Price: 3050, ConfirmedIndex: 25}, // not confirmed by the last bar (19)
	}}
	got := swingClusters(frame, 2.5)
	if !reflect.DeepEqual(got, []float64{3001, 3010}) {
		t.Fatalf("clusters %v, want the median 3001 and the lone 3010; the unconfirmed 3050 is absent", got)
	}
}

func TestRoundClassAndScore(t *testing.T) {
	mc := majorConfig()
	for _, c := range []struct {
		lo, hi float64
		want   int
	}{{2998, 3002, 100}, {3048, 3052, 50}, {3008, 3012, 10}, {3013, 3016, 0}} {
		if got := majorRoundClass(c.lo, c.hi, mc); got != c.want {
			t.Errorf("[%v, %v]: %d, want %d", c.lo, c.hi, got, c.want)
		}
	}
	l := majorLevel{tf: market.H1, rules: []string{"S", "W"}, facts: levelFacts{sessions: []string{"ASIA_H", "PWH"}, roundClass: 50, htfZone: true,
		reactions: []levelReaction{rx(0, 2), rx(1, 2), rx(2, 2), rx(3, 2), rx(4, 2)}}}
	// PWH 4 (the strongest session) + 50 handle 2 + reactions capped 4 + W 2 + htf zone 1 + H1 1 = 14
	if got := strengthScore(l); got != 14 {
		t.Fatalf("score %d, want 14", got)
	}
}

// measure reads the departure in reference (H1) ATRs whatever frame the level lives on: an M5 level with an M5 ATR of 2 needs a 10-point departure, not 2.
func TestTheDepartureIsInReferenceATRForAnM5Level(t *testing.T) {
	s := &Strategy{cfg: Config{Major: majorConfig(), BreakoutAcceptBars: 2}}
	m5 := make([]market.Candle, 0)
	for i, v := range [][3]float64{{3020, 3010, 3015}, {3016, 3001, 3005}, {3008, 3003, 3007}, {3009, 3005, 3008}} { // only 6.5 away
		m5 = append(m5, market.Candle{Time: int64(i) * 300, Open: v[2], High: v[0], Low: v[1], Close: v[2]})
	}
	d := &strategyutil.LegacyDetector{Frame: &context.LegacyFrame{Bars: m5}, ATR: 2}
	l := majorLevel{price: 3000, band: 0.5, tf: market.M5}
	s.measure(&l, d, 10, 10_000, nil, map[string]bool{}, bookFrames{market.M5: d})
	if len(l.facts.reactions) != 0 {
		t.Fatalf("6.5 points is 0.65 ref ATR, no reaction: %+v", l.facts.reactions)
	}
}

func TestMajorConfigRefusesBadValues(t *testing.T) {
	base := func() map[string]any {
		return map[string]any{
			"reference_timeframe": "H1", "band_atr": 0.25, "departure_ref_atr": 1.0, "reaction_window_bars": 12, "swing_min_reactions": 2,
			"swing_min_span_hours": 8.0, "swing_min_max_departure_ref_atr": 1.5, "session_levels": []any{"PDH"},
			"major_round_steps": []any{50.0}, "minor_round_step": 10.0, "round_min_reactions": 1, "flip_min_reactions": 1,
		}
	}
	for name, mutate := range map[string]func(map[string]any){
		"M1 reference":  func(p map[string]any) { p["reference_timeframe"] = "M1" },
		"DAY_H session": func(p map[string]any) { p["session_levels"] = []any{"DAY_H"} },
		"zero band":     func(p map[string]any) { p["band_atr"] = 0.0 },
		"no steps":      func(p map[string]any) { p["major_round_steps"] = []any{} },
	} {
		p := base()
		mutate(p)
		if _, err := parseMajorConfig(p); err == nil {
			t.Errorf("%s was accepted", name)
		}
	}
}

// A session level is in the book only from its AvailableAt; a developing extreme (DAY_H) and an unlisted name never are.
func TestASessionLevelIsAbsentBeforeItsAvailableAt(t *testing.T) {
	s := &Strategy{cfg: Config{Major: majorConfig(), BreakoutAcceptBars: 2, StructureTimeframes: []market.Timeframe{market.H1}}}
	h1 := bars([3]float64{3010, 2990, 3000}, [3]float64{3010, 2990, 3000})
	frame := &context.LegacyFrame{Bars: h1, Sessions: []techniquezone.SessionRef{
		{Name: "PDH", Price: 3005, AvailableAt: 7200},
		{Name: "PWL", Price: 2985, AvailableAt: 99_999},
		{Name: "DAY_H", Price: 3008, AvailableAt: 1},
		{Name: "NY_H", Price: 2995, AvailableAt: 1},
	}}
	frames := bookFrames{market.H1: &strategyutil.LegacyDetector{Frame: frame, ATR: 10}}
	sessions := func(now int64) []string {
		var out []string
		for _, l := range s.buildBook(frames, 3000, now) {
			if l.source == sourceSession {
				out = append(out, l.facts.sessions...)
			}
		}
		return out
	}
	if got := sessions(7200); !reflect.DeepEqual(got, []string{"PDH"}) {
		t.Fatalf("at 7200 only PDH is knowable: %v", got)
	}
	if got := sessions(7199); len(got) != 0 {
		t.Fatalf("one second earlier nothing is: %v", got)
	}
}
