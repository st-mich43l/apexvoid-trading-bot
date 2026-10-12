package slreplay_test

import (
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

var (
	defectOnce    sync.Once
	defectResults []runResult
	defectErr     error
)

// defectRuns replays three immutable captures once and shares the result between the
// tests below.
func defectRuns() ([]runResult, error) {
	defectOnce.Do(func() {
		doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
		if err != nil {
			defectErr = err
			return
		}
		files := []struct{ file, label, role string }{
			{"replay-xau-production-capture-20260921.json", "XAU 14-21 Sep", "development"},
			{"replay-xau-production-capture-20261012.json", "XAU 1-12 Oct", "extended"},
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

// TestFrozenV3DefectsReproduceOnRealData is step one of the rebuild: it pins the old
// strategy's weaknesses on the immutable real captures, measured by an audit that
// shares no code with the production detector, and shows the v4 contract has none of
// them. The frozen v3 lives only in this test package.
func TestFrozenV3DefectsReproduceOnRealData(t *testing.T) {
	results, err := defectRuns()
	if err != nil {
		t.Fatal(err)
	}
	var total v2Tally
	published, v4Published := 0, 0
	for _, res := range results {
		tl := tallyV2(res.V2Facts)
		t.Logf("%s: v3 published %d (located %d): %+v", res.Label, len(res.V2), tl.found, tl)
		total.found += tl.found
		total.notCurrent += tl.notCurrent
		total.superseded += tl.superseded
		total.incomplete += tl.incomplete
		total.swept += tl.swept
		total.touchMismatch += tl.touchMismatch
		total.t0 += tl.t0
		total.t1 += tl.t1
		total.t3 += tl.t3
		total.old += tl.old
		total.any += tl.any
		published += len(res.V2)
		v4Published += len(res.V4)
	}
	if published == 0 || total.found == 0 {
		t.Fatal("the frozen v3 published nothing on the captures: the reproduction proves nothing")
	}
	// Each weakness named in the rebuild brief is present in what v3 published.
	if total.touchMismatch == 0 || total.t0+total.t1+total.t3 == 0 {
		t.Error("expected v3 to publish setups whose level did not have the 2 touches it claimed (the count was hard-coded)")
	}
	if total.superseded == 0 {
		t.Error("expected v3 to trade a level an older occurrence of which a newer period had replaced")
	}
	// The brief also names "a swept level qualifies on a generic reclaim not tied to the
	// sweep". On these captures v3's reclaim-type confirmation always followed a sweep
	// within the reclaim window (0 untied cases), so that weakness is NOT reproduced
	// here: it is a structural gap in v3 (nothing verified the link) that the data does
	// not show costing a trade. It is reported as such in the documentation.
	if total.old == 0 {
		t.Error("expected v3 to trade a level older than a day")
	}
	t.Logf("TOTAL v3: published %d located %d not-current %d (superseded %d, incomplete %d), untied sweeps %d, touches != 2: %d (0: %d, 1: %d, 3+: %d), older than 24h %d, any defect %d; v4 published %d",
		published, total.found, total.notCurrent, total.superseded, total.incomplete, total.swept, total.touchMismatch, total.t0, total.t1, total.t3, total.old, total.any, v4Published)
	if v4Published == 0 {
		t.Fatal("v4 published nothing on the captures: the comparison proves nothing")
	}
	// The same independent audit, applied to what v4 published.
	checked := 0
	for _, res := range results {
		for i, f := range res.V4Facts {
			checked++
			switch {
			case f.Status != "current":
				t.Errorf("%s: v4 setup %d traded a level that was %s", res.Label, i, f.Status)
			case f.SweptUntied:
				t.Errorf("%s: v4 setup %d accepted a reclaim not tied to a sweep", res.Label, i)
			case !f.PeriodComplete:
				t.Errorf("%s: v4 setup %d traded a level of an incomplete period", res.Label, i)
			case f.AgeHours > map[string]float64{"session": 30, "day": 72, "week": 168}[f.Kind]+1:
				t.Errorf("%s: v4 setup %d traded a %s level %.1f h old", res.Label, i, f.Kind, f.AgeHours)
			}
			// The touch count it publishes must equal the independent count.
			if f.TouchesKnown && f.AuditedTouches != f.PriorTouches+1 {
				t.Errorf("%s: v4 setup %d publishes %d prior touches, the independent count is %d in total", res.Label, i, f.PriorTouches, f.AuditedTouches)
			}
			if !f.TouchesKnown && f.AuditedTouches >= 0 {
				t.Errorf("%s: v4 setup %d claims no touch history while the candles show one", res.Label, i)
			}
		}
	}
	if checked != v4Published {
		t.Errorf("audited %d v4 setups of %d published", checked, v4Published)
	}
}

// The v3 session reference carried no period, no availability and no timestamps at
// all: nothing downstream could tell a complete level from a partial one.
func TestV3SessionReferenceCarriesNoTimestamps(t *testing.T) {
	typ := reflect.TypeOf(techniquezone.SessionRef{})
	var fields []string
	for i := 0; i < typ.NumField(); i++ {
		fields = append(fields, typ.Field(i).Name)
	}
	if strings.Join(fields, ",") != "Name,Price,Swept" {
		t.Fatalf("SessionRef fields = %v; the defect this test documents is that it has no period or timestamps", fields)
	}
}

// The engine and the offline detector read from the same candles never disagree.
func TestEngineAndOfflineDetectorAgreeOnTheCaptures(t *testing.T) {
	results, err := defectRuns()
	if err != nil {
		t.Fatal(err)
	}
	for _, res := range results {
		if res.EngineOfflineMismatches != 0 {
			t.Errorf("%s: the engine and the offline detector disagreed on %d of %d evaluations", res.Label, res.EngineOfflineMismatches, res.Evaluations)
		}
	}
}

// TestTheFiveLosingXAUSessionTradesAreAccountedFor replays each of them through the
// v4 builder with the data that existed at the plan's candle and pins what it says.
// v4 refuses every one, each for a reason the candles show; none of them filled before
// its confirmation and none reached +1R before its stop.
func TestTheFiveLosingXAUSessionTradesAreAccountedFor(t *testing.T) {
	vs := verdicts(t)
	if len(vs) != 5 {
		t.Fatalf("verdicts = %d", len(vs))
	}
	for _, v := range vs {
		t.Log(v.String())
		if !v.Replayable {
			t.Fatalf("%s is not replayable from the committed capture: %v", v.Trade.Label, v.Notes)
		}
		if v.V4Setup {
			t.Errorf("%s: v4 would still have published this setup", v.Trade.Label)
		}
		if v.FilledBeforeConfirm {
			t.Errorf("%s: the position filled before its confirmation", v.Trade.Label)
		}
		if !(v.MFE < 1) {
			t.Errorf("%s: the trade reached %.2fR before its stop", v.Trade.Label, v.MFE)
		}
	}
	has := func(v tradeVerdict, state string) bool {
		for _, e := range v.V4Episodes {
			if strings.Contains(e, state) {
				return true
			}
		}
		return false
	}
	// 2 Oct NY_H and 6 Oct PDL: price had already broken the level and accepted the
	// break; the "reaction" traded against a sustained breakout.
	for _, i := range []int{0, 4} {
		if !has(vs[i], "BREAKOUT_ACCEPTED") || !has(vs[i], "level_already_swept") {
			t.Errorf("%s: expected an accepted breakout and an already-swept level, got %v", vs[i].Trade.Label, vs[i].V4Episodes)
		}
	}
	// 5 Oct NY_L and 6 Oct ASIA_L: levels days old, replaced by newer periods (or by
	// a weekend with none).
	for _, i := range []int{1, 3} {
		if vs[i].LevelAgeH < 72 || !(vs[i].Status == "superseded" || vs[i].Status == "incomplete:no_candles_in_period") {
			t.Errorf("%s: expected a level older than 72 h and no longer current, got age %.1f status %s", vs[i].Trade.Label, vs[i].LevelAgeH, vs[i].Status)
		}
	}
	// 6 Oct LONDON_L: the level had been broken hours earlier by 4.8 ATR and price was
	// recovering into it from below; no fresh episode existed.
	if len(vs[2].V4Episodes) != 0 || vs[2].NearestApproachATR > -3 {
		t.Errorf("%s: expected price far beyond a broken level and no live episode, got %v / %.2f ATR", vs[2].Trade.Label, vs[2].V4Episodes, vs[2].NearestApproachATR)
	}
}
