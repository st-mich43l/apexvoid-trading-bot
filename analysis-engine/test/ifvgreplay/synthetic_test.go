package ifvgreplay_test

import (
	"math"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/ifvg"
)

// Deterministic synthetic XAU M5 paths run through the real engine. Each fixture is
// the clean positive path with ONE fact changed. (The frozen v2's weaknesses are
// reproduced on the immutable real captures in defects_test.go: v2's zone chain
// does not form an iFVG on a path this small.)

const fixtureStart = int64(1_760_000_400)

type scribe struct {
	t    int64
	last float64
	bars []market.Candle
}

func newScribe(price float64) *scribe { return &scribe{t: fixtureStart, last: price} }

func (s *scribe) raw(open, high, low, close float64) *scribe {
	s.bars = append(s.bars, market.Candle{Time: s.t, Open: open, High: high, Low: low, Close: close, Volume: 100})
	s.last = close
	s.t += 300
	return s
}

func (s *scribe) bar(close, upper, lower float64) *scribe {
	open := s.last
	return s.raw(open, math.Max(open, close)+upper, math.Min(open, close)-lower, close)
}

func (s *scribe) filler(n int, price float64) *scribe {
	for i := 0; i < n; i++ {
		if i%2 == 0 {
			s.bar(price+0.3, 0.7, 0.7)
		} else {
			s.bar(price-0.3, 0.7, 0.7)
		}
	}
	return s
}

// fixtureOpts change one fact of the positive path.
type fixtureOpts struct {
	gapTop      float64 // low of the first gap candle (positive: 4116.0; the gap spans 4112..gapTop)
	invertClose float64 // close of the inverting candle (positive: gapTop + 2.0)
	invertUpper float64
	invertLow   float64
}

func positive() fixtureOpts {
	return fixtureOpts{gapTop: 4116.0, invertUpper: 0.3, invertLow: 4110.8}
}

// path is: filler, an opposing swing high ~4131, filler, a bearish displacement gap
// 4112..gapTop, a base below it, the inverting candle, a pullback to the gap's top
// and a bullish rejection candle that closes back above it.
func path(o fixtureOpts) []market.Candle {
	s := newScribe(4110)
	s.filler(40, 4110)
	s.bar(4110.6, 0.5, 0.5).bar(4111, 20, 0.5).bar(4110.6, 0.5, 0.5).bar(4110.2, 0.5, 0.5)
	s.filler(25, 4110)
	s.raw(4118.0, 4118.4, o.gapTop, o.gapTop+0.4)
	s.raw(4116.4, 4116.6, 4110.2, 4110.8)
	s.raw(4110.8, 4112.0, 4108.0, 4109.0)
	for i := 0; i < 3; i++ {
		s.bar(4110.5+0.3*float64(i%2), 0.6, 0.6)
	}
	invertClose := o.invertClose
	if invertClose == 0 {
		invertClose = o.gapTop + 2.0
	}
	s.raw(4111.0, invertClose+o.invertUpper, o.invertLow, invertClose)
	s.raw(o.gapTop+2.4, o.gapTop+2.6, o.gapTop+0.2, o.gapTop+1.6)
	s.raw(o.gapTop+0.8, o.gapTop+2.4, o.gapTop-0.6, o.gapTop+2.2)
	return s.bars
}

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
// distinct v3 candidates it published, checking that the engine's context and the
// offline detector read from the same candles agree on every evaluated bar.
func publishedThroughTheEngine(t *testing.T, bars []market.Candle) []opportunity.Candidate {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	params, err := ifvgParamsFor(doc, "XAU")
	if err != nil {
		t.Fatal(err)
	}
	instance, err := ifvg.New(strategy.Config{ID: ifvg.ID, Version: ifvg.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	v3 := instance.(*ifvg.Strategy)
	cfg, err := ifvg.ParseConfig(params)
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
		analysis, candidates := v3.Analyze(e.Context)
		if !sameDecision(ifvg.DetectAsOf(cfg, bars, index[e.BarTime]), analysis) {
			t.Errorf("the engine and the offline detector disagree at %d", e.BarTime)
		}
		for _, c := range candidates {
			if !seen[c.ID] {
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

func TestSyntheticPositivePathIsPublishedOnceThroughTheEngine(t *testing.T) {
	got := publishedThroughTheEngine(t, path(positive()))
	if len(got) != 1 {
		t.Fatalf("v3 must publish the clean iFVG exactly once through the engine, got %d", len(got))
	}
	c := got[0]
	if c.Strategy != ifvg.ID || c.StrategyVersion != "v3" || c.Reaction == nil || c.Reaction.ConfirmationBarTime <= c.Reaction.TouchBarTime && c.Reaction.TouchBarTime != c.Reaction.ConfirmationBarTime {
		t.Fatalf("candidate %+v", c)
	}
	if c.Reaction.TouchBarTime <= c.FormedAt {
		t.Fatalf("the retest touch (%d) must come strictly after the inversion (%d)", c.Reaction.TouchBarTime, c.FormedAt)
	}
}

func TestSyntheticHairlineInversionIsRefusedThroughTheEngine(t *testing.T) {
	o := positive()
	o.invertClose, o.invertUpper = o.gapTop+0.05, 0.1
	if got := publishedThroughTheEngine(t, path(o)); len(got) != 0 {
		t.Fatalf("v3 published %d setups off a 0.05 point inversion", len(got))
	}
}

func TestSyntheticTinyGapIsRefusedThroughTheEngine(t *testing.T) {
	o := positive()
	o.gapTop = 4112.5
	if got := publishedThroughTheEngine(t, path(o)); len(got) != 0 {
		t.Fatalf("v3 published %d setups off a 0.5 point gap", len(got))
	}
}
