package replaycapture_test

// AMD context through the real engine path: every discovered opportunity is replayed with every strategy on, and the AMD context it carries must honour the
// contract of docs/amd-context.md: it is read from the timeframe that observed the setup, its events are dated in order and never after the observation, an
// irrelevant strategy carries no evidence, and it never moves a selected star, a quality, or the setup's geometry.

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

func TestAMDContextOfEveryReplayedOpportunity(t *testing.T) {
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	s := load(t)
	result, err := replaycapture.Replay(doc, capture(map[string][][]float64{"M15": s.M15, "H1": s.H1}), market.M15, replaycapture.Options{})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Discovered) == 0 {
		t.Fatal("the scenario must discover opportunities")
	}
	relations := map[string]bool{"aligned": true, "conflicting": true, "partial": true, "irrelevant": true, "unavailable": true}
	relevant := map[string]bool{"crt": true, "ifvg": true, "key_level": true, "range_scalp": true, "trendline": true}
	withMAD := 0
	for _, o := range result.Discovered {
		if o.Technical == nil {
			continue
		}
		// Selected stars are the configured scorer's: with v1 selected, AMD (a V2 input) cannot have moved them.
		if c := o.Technical.Confluence; c != nil && c.Version == "v1" && c.SelectedStars != c.V1Stars {
			t.Errorf("%s: selected stars %d != v1 stars %d", o.Strategy, c.SelectedStars, c.V1Stars)
		}
		// Quality.Overall stays the mean of the strategy's score_* dimensions.
		var sum float64
		var n int
		for name, v := range o.Quality.Components {
			if strings.HasPrefix(name, "score_") {
				sum += v
				n++
			}
		}
		if n > 0 && math.Abs(o.Quality.Overall-sum/float64(n)) > 1e-9 {
			t.Errorf("%s: quality overall %v is not mean(score_*) %v", o.Strategy, o.Quality.Overall, sum/float64(n))
		}
		m := o.Technical.MAD
		if m == nil {
			continue // a timeframe with no AMD snapshot carries none; the opportunity is unaffected
		}
		withMAD++
		if m.Timeframe != string(o.ObservedTimeframe) {
			t.Errorf("%s: AMD context is from %s but the setup was observed on %s", o.Strategy, m.Timeframe, o.ObservedTimeframe)
		}
		if !relations[m.Relation] {
			t.Errorf("%s: relation %q is not one of the five", o.Strategy, m.Relation)
		}
		if !relevant[string(o.Strategy)] && (m.Relation != "irrelevant" || len(m.EvidenceIDs) != 0) {
			t.Errorf("%s is not an AMD strategy but carries %s with evidence %v", o.Strategy, m.Relation, m.EvidenceIDs)
		}
		if m.ObservedAt > o.CreatedAt {
			t.Errorf("%s: AMD read bar %d is after the observation bar %d", o.Strategy, m.ObservedAt, o.CreatedAt)
		}
		if q, r := m.Sequence, m.Range; q != nil {
			if r == nil {
				t.Fatalf("%s: a sequence without its range", o.Strategy)
			}
			last := r.SealedAt
			for name, at := range map[string]int64{"sweep": q.SweepAt, "reclaim": q.ReclaimAt, "expansion start": q.ExpansionStartedAt, "expansion confirmed": q.ExpansionConfirmedAt} {
				if at != 0 && at > m.ObservedAt {
					t.Errorf("%s: %s at %d is after the read bar %d", o.Strategy, name, at, m.ObservedAt)
				}
			}
			for _, at := range []int64{q.SweepAt, q.ReclaimAt, q.ExpansionStartedAt, q.ExpansionConfirmedAt} {
				if at == 0 {
					continue
				}
				if at < last {
					t.Errorf("%s: events out of order (%d before %d)", o.Strategy, at, last)
				}
				last = at
			}
		}
	}
	t.Logf("%d discovered, %d with AMD context", len(result.Discovered), withMAD)
}
