package brreplay_test

import (
	"path/filepath"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/breakretest"
)

// defectTally is what the frozen v2 does, measured on real engine contexts.
type defectTally struct {
	candidates                     int
	constantQuality                int // Quality.Overall == 0.75 and every component == 1
	htfEvidenceWithoutAlignment    int // "htf_aligned" evidence while the engine reads no alignment
	structureEvidenceWithoutBias   int // "structural_agreement" evidence while the M5 bias disagrees
	staleKeyLevelRetest            int // key-level setup whose retest candle was more than 2 candles old
	keyLevelHiddenByTrendlineFirst int // both paths valid; only the trendline setup was published
	v3PublishedWithStaleRetest     int
	v3Published                    int
}

// TestFrozenV2DefectsReproduceOnRealData is step one of the rebuild: it pins
// the old strategy's defects on real engine contexts, and shows the v3 contract
// has none of them. The frozen v2 lives only in this test package.
func TestFrozenV2DefectsReproduceOnRealData(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	files := []string{"replay-xau-production-capture-20260921.json", "replay-gbpjpy-production-capture-20261006.json"}
	tallies := make([]defectTally, len(files))
	var wg sync.WaitGroup
	for i, file := range files {
		wg.Add(1)
		go func(i int, file string) {
			defer wg.Done()
			tally, err := tallyDefects(doc, file)
			if err != nil {
				t.Errorf("%s: %v", file, err)
				return
			}
			tallies[i] = tally
		}(i, file)
	}
	wg.Wait()
	var total defectTally
	for i, tl := range tallies {
		t.Logf("%s: %+v", files[i], tl)
		total.candidates += tl.candidates
		total.constantQuality += tl.constantQuality
		total.htfEvidenceWithoutAlignment += tl.htfEvidenceWithoutAlignment
		total.structureEvidenceWithoutBias += tl.structureEvidenceWithoutBias
		total.staleKeyLevelRetest += tl.staleKeyLevelRetest
		total.keyLevelHiddenByTrendlineFirst += tl.keyLevelHiddenByTrendlineFirst
		total.v3PublishedWithStaleRetest += tl.v3PublishedWithStaleRetest
		total.v3Published += tl.v3Published
	}
	if total.candidates == 0 {
		t.Fatal("the frozen v2 published nothing on the captures: the reproduction proves nothing")
	}
	if total.constantQuality != total.candidates {
		t.Errorf("v2 quality was meant to be a constant 0.75 on every candidate: %d of %d", total.constantQuality, total.candidates)
	}
	if total.htfEvidenceWithoutAlignment == 0 {
		t.Error("expected v2 to attach htf_aligned evidence without an HTF alignment on at least one setup")
	}
	if total.staleKeyLevelRetest == 0 {
		t.Error("expected v2 to publish at least one stale key-level retest")
	}
	t.Logf("TOTAL v2: %+v", total)
	if total.v3PublishedWithStaleRetest != 0 {
		t.Errorf("v3 published %d setups whose retest was stale", total.v3PublishedWithStaleRetest)
	}
}

func tallyDefects(doc *config.Document, file string) (defectTally, error) {
	var tally defectTally
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", file))
	if err != nil {
		return tally, err
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		return tally, err
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		return tally, err
	}
	var params map[string]any
	for _, s := range settings.Strategies {
		if s.ID == breakretest.ID {
			params = s.Parameters
		}
	}
	v2, err := newFrozenV2(strategy.Config{ID: "break_retest", Version: "v2", Enabled: true, Parameters: frozenParams(params)})
	if err != nil {
		return tally, err
	}
	instance, err := breakretest.New(strategy.Config{ID: breakretest.ID, Version: breakretest.Version, Enabled: true, Parameters: params})
	if err != nil {
		return tally, err
	}
	v3 := instance.(*breakretest.Strategy)
	seen := map[string]bool{}
	has := func(c opportunity.Candidate, code string) bool {
		for _, e := range c.Evidence {
			if e.Code == code {
				return true
			}
		}
		return false
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		tl, lv := v2.Both(e.Context)
		published := lv
		if tl != nil {
			published = tl
			if lv != nil && !seen[lv.ID] {
				tally.keyLevelHiddenByTrendlineFirst++
			}
		}
		if published != nil && !seen[published.ID] {
			seen[published.ID] = true
			c := *published
			tally.candidates++
			constant := c.Quality.Overall == 0.75 && len(c.Quality.Components) > 0
			for _, v := range c.Quality.Components {
				constant = constant && v == 1
			}
			if constant {
				tally.constantQuality++
			}
			if has(c, "htf_aligned") && (e.Context.Legacy == nil || !e.Context.Legacy.AlignedWithHTF(c.Direction)) {
				tally.htfEvidenceWithoutAlignment++
			}
			if f := e.Context.Timeframes[market.M5]; has(c, "structural_agreement") && (f == nil || f.Legacy == nil ||
				(c.Direction == market.Buy && f.Legacy.Structure != "up") || (c.Direction == market.Sell && f.Legacy.Structure != "down")) {
				tally.structureEvidenceWithoutBias++
			}
			if has(c, "m5_key_level_break") && float64(c.CreatedAt-c.FormedAt)/300 > 2 {
				tally.staleKeyLevelRetest++
			}
		}
		_, candidates := v3.Analyze(e.Context)
		for _, c := range candidates {
			if seen[c.ID] {
				continue
			}
			seen[c.ID] = true
			tally.v3Published++
			if c.Quality.Components["retest_bars_after_accept"] < 1 || (c.CreatedAt-c.FormedAt)/300 > 24+3 {
				tally.v3PublishedWithStaleRetest++
			}
		}
	}})
	return tally, err
}
