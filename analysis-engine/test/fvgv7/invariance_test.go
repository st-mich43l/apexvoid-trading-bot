package fvgv7_test

import (
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// A decision at a closed M5 bar must not depend on any bar after it. The production capture is replayed whole and again with every timeframe cut at the
// close of a bar in the middle of the 29 Oct run; every decision up to that bar, candidates and refusals, must be identical.
func TestDecisionsNeverDependOnLaterBars(t *testing.T) {
	doc := resolve(t)
	full, err := replaycapture.Load(testdata("fvgwindow-gbpjpy-20261029.json"))
	if err != nil {
		t.Fatal(err)
	}
	rows := full.Timeframes["M5"]
	cutOpen := int64(rows[len(rows)*3/5][0])
	cutClose := cutOpen + 300

	cut := *full
	cut.Timeframes = map[string][][]float64{}
	for name, series := range full.Timeframes {
		seconds := int64(300)
		if tf := market.Timeframe(name); tf != "" {
			if m, ok := tf.Minutes(); ok {
				seconds = int64(m) * 60
			}
		}
		for _, row := range series {
			if int64(row[0])+seconds <= cutClose {
				cut.Timeframes[name] = append(cut.Timeframes[name], row)
			}
		}
	}

	type decision struct {
		candidates []opportunity.Candidate
		refusals   map[string]int64
	}
	run := func(c *replaycapture.Capture) map[int64]decision {
		out := map[int64]decision{}
		p := newPair(t, doc, c.Symbol)
		_, err := replaycapture.Replay(doc, c, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
			if e.Timeframe != market.M5 || e.Context == nil || e.BarTime > cutOpen {
				return
			}
			d := decision{candidates: p.v7.Evaluate(e.Context), refusals: map[string]int64{}}
			for r, n := range p.v7.TakeRefusals() {
				d.refusals[string(r.Timeframe)+"/"+r.Reason] += n
			}
			out[e.BarTime] = d
		}})
		if err != nil {
			t.Fatal(err)
		}
		return out
	}
	whole, truncated := run(full), run(&cut)
	if len(truncated) < 100 || len(whole) != len(truncated) {
		t.Fatalf("bars compared: whole %d, cut %d", len(whole), len(truncated))
	}
	published := 0
	for bar, want := range whole {
		got := truncated[bar]
		published += len(want.candidates)
		if !reflect.DeepEqual(want, got) {
			t.Fatalf("the decision at %d changed when later bars were removed:\nwhole: %+v\ncut:   %+v", bar, want, got)
		}
	}
	t.Logf("%d bars identical, %d candidates", len(whole), published)
}
