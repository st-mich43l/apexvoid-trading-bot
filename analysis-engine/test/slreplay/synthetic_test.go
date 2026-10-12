package slreplay_test

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
)

func captureOf(bars []market.Candle) *replaycapture.Capture {
	rows := make([][]float64, len(bars))
	for i, b := range bars {
		rows[i] = []float64{float64(b.Time), b.Open, b.High, b.Low, b.Close, b.Volume}
	}
	return &replaycapture.Capture{
		Version: 1, Symbol: "XAU", Columns: []string{"t", "open", "high", "low", "close", "volume"},
		Timeframes: map[string][][]float64{"M5": rows},
	}
}

// publishedThroughTheEngine replays the path through the real engine and returns the
// distinct v4 candidates it published from the candle `since` on (an earlier,
// legitimate reaction of the same level is not what a negative case is about),
// checking that the engine's context and the
// offline detector read from the same candles agree on every evaluated bar.
func publishedThroughTheEngine(t *testing.T, bars []market.Candle, since int64, override map[string]any) []opportunity.Candidate {
	t.Helper()
	doc := resolveDoc(t)
	params, err := sessionParamsFor(doc, "XAU")
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range override {
		params[k] = v
	}
	instance, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Version: sessionlevel.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	v4 := instance.(*sessionlevel.Strategy)
	cfg, err := sessionlevel.ParseConfig(params)
	if err != nil {
		t.Fatal(err)
	}
	index := map[int64]int{}
	for i, b := range bars {
		index[b.Time] = i
	}
	seen := map[string]bool{}
	var out []opportunity.Candidate
	_, err = replaycapture.Replay(doc, captureOf(bars), market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		analysis, candidates := v4.Analyze(e.Context)
		n := len(e.Context.Timeframes[market.M5].Candles)
		i := index[e.BarTime]
		from := i + 1 - n
		if from < 0 {
			from = 0
		}
		if !sameDecision(sessionlevel.DetectAsOf(cfg, sessionlevel.Input{M5: bars[from : i+1]}, i-from), analysis) {
			t.Errorf("the engine and the offline detector disagree at %d", e.BarTime)
		}
		for _, c := range candidates {
			if !seen[c.ID] && c.CreatedAt >= since {
				seen[c.ID] = true
				out = append(out, c)
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func only(t *testing.T, bars []market.Candle, direction market.Direction, override map[string]any) opportunity.Candidate {
	t.Helper()
	got := publishedThroughTheEngine(t, bars, 0, override)
	if len(got) != 1 {
		t.Fatalf("v4 must publish the clean reaction exactly once through the engine, got %d", len(got))
	}
	c := got[0]
	if c.Strategy != sessionlevel.ID || c.StrategyVersion != "v4" || c.Direction != direction || c.Reaction == nil {
		t.Fatalf("candidate %+v", c)
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Reaction.TouchBarTime < c.FormedAt || c.Reaction.ConfirmationBarTime < c.Reaction.TouchBarTime {
		t.Fatalf("the reaction (%d..%d) must follow the level's availability (%d)", c.Reaction.TouchBarTime, c.Reaction.ConfirmationBarTime, c.FormedAt)
	}
	return c
}

func TestSyntheticFirstTouchIsPublishedOnceThroughTheEngine(t *testing.T) {
	bars, m := firstTouchDay(opts{rejectClose: 4107.4, rejectOpen: 4104.9, rejectLow: 4103.85})
	c := only(t, bars, market.Buy, nil)
	if c.Reaction.TouchBarTime != bars[m.touch].Time || c.Reaction.ConfirmationBarTime != bars[m.confirm].Time {
		t.Fatalf("reaction %+v", c.Reaction)
	}
	if math.Abs(float64(c.Invalidation.Price)-4103.4) > 1 {
		t.Fatalf("invalidation %v", c.Invalidation)
	}
}

func TestSyntheticSweepReclaimIsPublishedOnceThroughTheEngine(t *testing.T) {
	bars, m := sweepDay(opts{reclaimClose: 4107.6})
	c := only(t, bars, market.Buy, nil)
	if c.Reaction.Pattern != "sweep_reclaim" || c.Reaction.TouchBarTime != bars[m.sweep].Time {
		t.Fatalf("reaction %+v", c.Reaction)
	}
}

func TestSyntheticBearishTwinIsPublishedOnceAsASell(t *testing.T) {
	bars, _ := firstTouchDay(opts{rejectClose: 4107.4, rejectOpen: 4104.9, rejectLow: 4103.85})
	// The engine's own structural and higher-timeframe reads of a reflected synthetic
	// path are not symmetrical, so the confluence floor is lowered to isolate what
	// this test is about: the SELL mapping through the real engine.
	c := only(t, reflectPrices(bars, 4112), market.Sell, map[string]any{"confluence_floor": 1.0})
	if !(float64(c.Invalidation.Price) > 4112+(4112-4104)) {
		t.Fatalf("a SELL's stop must be above its level: %+v", c.Invalidation)
	}
}

func TestSyntheticNegativesAreRefusedThroughTheEngine(t *testing.T) {
	rich := opts{rejectClose: 4107.4, rejectOpen: 4104.9, rejectLow: 4103.85}
	with := func(o opts, f func(*opts)) opts { f(&o); return o }
	for name, build := range map[string]func() ([]market.Candle, marks){
		"no fresh approach":     func() ([]market.Candle, marks) { return firstTouchDay(with(rich, func(o *opts) { o.dwell = true })) },
		"confirmation too late": func() ([]market.Candle, marks) { return firstTouchDay(with(rich, func(o *opts) { o.waitBars = 3 })) },
		"the level was swept": func() ([]market.Candle, marks) {
			return firstTouchDay(with(rich, func(o *opts) { o.priorSweep = true }))
		},
		"touched too many times": func() ([]market.Candle, marks) { return firstTouchDay(with(rich, func(o *opts) { o.prior = 2 })) },
		"sustained breakout": func() ([]market.Candle, marks) {
			return sweepDay(opts{reclaimGap: 2, gapClose: 4103.2, reclaimClose: 4107.6})
		},
		"reclaim after the window": func() ([]market.Candle, marks) { return sweepDay(opts{reclaimGap: 6, reclaimClose: 4107.6}) },
		"shallow reclaim":          func() ([]market.Candle, marks) { return sweepDay(opts{reclaimClose: 4104.03}) },
		"London still forming": func() ([]market.Candle, marks) {
			b, m := firstTouchDay(rich)
			return b[:m.low+30], m
		},
	} {
		t.Run(name, func(t *testing.T) {
			bars, m := build()
			since := int64(0)
			if m.touch > 0 && m.touch < len(bars) {
				since = bars[m.touch].Time
			}
			if got := publishedThroughTheEngine(t, bars, since, nil); len(got) != 0 {
				t.Fatalf("v4 published %d setups it must refuse: %+v", len(got), got[0].Reaction)
			}
		})
	}
}

// A reaction is published once, at its confirmation, and never again as it ages.
func TestSyntheticReactionIsPublishedAtItsConfirmationAndNeverAgain(t *testing.T) {
	bars, m := firstTouchDay(opts{rejectClose: 4107.4, rejectOpen: 4104.9, rejectLow: 4103.85, afterBars: 6})
	c := only(t, bars, market.Buy, nil)
	if c.CreatedAt != bars[m.confirm].Time {
		t.Fatalf("created at %d, want the confirmation candle %d", c.CreatedAt, bars[m.confirm].Time)
	}
}
