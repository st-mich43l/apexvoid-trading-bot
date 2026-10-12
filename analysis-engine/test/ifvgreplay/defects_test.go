package ifvgreplay_test

import (
	"path/filepath"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

var (
	defectOnce    sync.Once
	defectResults []runResult
	defectErr     error
)

// defectRuns replays the two XAU captures once (they are immutable) and shares the
// result between the tests below.
func defectRuns() ([]runResult, error) {
	defectOnce.Do(func() {
		doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
		if err != nil {
			defectErr = err
			return
		}
		files := []struct{ file, label, role string }{
			{"replay-xau-production-capture-20260921.json", "XAU 14-21 Sep", "development"},
			{"replay-gbpjpy-production-capture-20261006.json", "GBPJPY 6 Oct", "held-out"},
		}
		defectResults = make([]runResult, len(files))
		var wg sync.WaitGroup
		errs := make([]error, len(files))
		for i, f := range files {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				defectResults[i], errs[i] = runCapture(doc, f.file, f.label, f.role)
			}(i)
		}
		wg.Wait()
		for _, err := range errs {
			if err != nil && defectErr == nil {
				defectErr = err
			}
		}
	})
	return defectResults, defectErr
}

// TestFrozenV2DefectsReproduceOnRealData is step one of the rebuild: it pins the old
// strategy's weaknesses on the immutable real captures, and shows the v3 contract has
// none of them. The frozen v2 lives only in this test package.
func TestFrozenV2DefectsReproduceOnRealData(t *testing.T) {
	results, err := defectRuns()
	if err != nil {
		t.Fatal(err)
	}
	var total v2Tally
	published, v3Published := 0, 0
	for _, res := range results {
		tl := tallyV2(res.V2Facts)
		t.Logf("%s: v2 confirmed %d (rebuilt %d), resting observations %d, defects %+v", res.Label, len(res.V2), tl.found, res.V2Resting, tl)
		total.found += tl.found
		total.weakGap += tl.weakGap
		total.weakOrigin += tl.weakOrigin
		total.insideBuffer += tl.insideBuffer
		total.quality += tl.quality
		total.touch += tl.touch
		total.stale += tl.stale
		total.any += tl.any
		published += len(res.V2)
		v3Published += len(res.V3)
	}
	if published == 0 || total.found == 0 {
		t.Fatal("the frozen v2 published nothing on the captures: the reproduction proves nothing")
	}
	// Each weakness named in the rebuild brief is present in what v2 published.
	if total.weakGap == 0 {
		t.Error("expected v2 to publish a gap narrower than minimum_gap_atr (the confirmed path never applied it)")
	}
	if total.insideBuffer == 0 {
		t.Error("expected v2 to publish an inversion whose close finished inside the buffer (a close-through by any amount)")
	}
	if total.quality == 0 {
		t.Error("expected v2 to publish an inversion by a candle with no measured displacement")
	}
	if total.touch == 0 {
		t.Error("expected v2 to publish a reaction whose touch was not after the inversion")
	}
	if total.weakOrigin == 0 {
		t.Error("expected v2 to publish a gap whose middle candle was not a displacement")
	}
	if total.stale == 0 {
		t.Error("expected v2 to publish a stale gap")
	}
	t.Logf("TOTAL v2: confirmed %d rebuilt %d any-defect %d; v3 published %d", published, total.found, total.any, v3Published)
	if v3Published == 0 {
		t.Fatal("v3 published nothing on the captures: the comparison proves nothing")
	}
	// The same independent offline measurement, applied to what v3 published.
	for _, res := range results {
		for i, f := range res.V3Facts {
			if !f.Found {
				t.Errorf("%s: v3 setup %d could not be rebuilt offline", res.Label, i)
				continue
			}
			if f.AnyV3Defect {
				t.Errorf("%s: v3 published a setup that misses its own contract: %+v", res.Label, f)
			}
		}
	}
}

// The v2 resting path applied minimum_gap_atr while the confirmed path did not: the
// same inverted gap could be filtered in one and traded in the other.
func TestV2ConfirmedPathIgnoredTheGapWidthItsRestingPathApplied(t *testing.T) {
	results, err := defectRuns()
	if err != nil {
		t.Fatal(err)
	}
	narrow := 0
	for _, res := range results {
		for _, f := range res.V2Facts {
			if f.Found && f.GapATR < 0.5 {
				narrow++
			}
		}
	}
	if narrow == 0 {
		t.Fatal("no confirmed v2 setup was narrower than the 0.5 ATR the resting path requires")
	}
	t.Logf("%d confirmed v2 setups were narrower than 0.5 ATR", narrow)
}

// The engine and the offline detector read from the same candles never disagree.
func TestEngineAndOfflineDetectorAgreeOnTheCaptures(t *testing.T) {
	results, err := defectRuns()
	if err != nil {
		t.Fatal(err)
	}
	for _, res := range results {
		if res.EngineOfflineMismatches != 0 {
			t.Errorf("%s: %d evaluations where the engine and the offline decision differ", res.Label, res.EngineOfflineMismatches)
		}
		if res.Evaluations == 0 {
			t.Errorf("%s: nothing was evaluated", res.Label)
		}
	}
	_ = market.M5
}
