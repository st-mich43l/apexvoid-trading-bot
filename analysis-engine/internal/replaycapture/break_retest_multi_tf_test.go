package replaycapture_test

// Break & Retest v4 (Q4): the broken level or trendline, its break and acceptance and the protected structure are read on M5, M15 or H1; the retest and its rejection
// are read on M5. The fixtures are real production bars:
//
//   - EURUSD (testdata/break_retest_eurusd_2026-11-19.json: M5 from 14 Nov, M15 last 300 bars, H1 last 150, closed at the cut): the SELL of 17 Nov 08:35 UTC is the M15
//     reading of a broken key level; v3 published the same retest as two bare touches (08:25, evidence candle_confirmation_optional) and one confirmed 08:30 setup;
//     the SELL of 17 Nov 20:55 is an M5 break with an M5 rejection (published identically by v3).
//   - XAU (testdata/flip_zone_xau_2026-11-18.json, 14-18 Nov): Break & Retest publishes nothing on XAU, in v3 or v4; its M5 and M15 retests are refused by the instrument stop
//     envelope or for want of an opposing structure.

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

const (
	retestFixture           = "testdata/break_retest_eurusd_2026-11-19.json"
	retestM15Sell     int64 = 1794904500 // the M15 reading, confirmed by an M5 rejection
	retestM5Sell      int64 = 1794948900 // an M5 break, confirmed by an M5 rejection
	retestV3TouchSell int64 = 1794903900 // v3's bare touch of the same retest, 10 minutes earlier
	retestMirrorPivot       = 2.2
)

func replayBreakRetest(t *testing.T, capture *replaycapture.Capture, params map[string]any, rec *telemetry.Recorder) []opportunity.Candidate {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	var off []string
	for _, id := range strategy.KnownIDs() {
		if id != "break_retest" {
			off = append(off, string(id))
		}
	}
	opts := replaycapture.Options{DisabledStrategies: off, Telemetry: rec}
	if params != nil {
		opts.StrategyParameters = map[string]map[string]any{"break_retest": params}
	}
	result, err := replaycapture.Replay(doc, capture, market.M5, opts)
	if err != nil {
		t.Fatal(err)
	}
	var out []opportunity.Candidate
	for _, c := range result.Discovered {
		if c.Strategy == "break_retest" {
			out = append(out, c)
		}
	}
	return out
}

// refusals sums strategy_refused{break_retest} by frame and reason.
func refusals(rec *telemetry.Recorder) map[string]int64 {
	_, counts := rec.Snapshot()
	out := map[string]int64{}
	for _, c := range counts {
		if c.Counter == string(telemetry.CounterStrategyRefused) && c.Strategy == "break_retest" {
			out[c.Timeframe+"/"+c.Reason] += c.Count
		}
	}
	return out
}

const xauFixtureForRetest = "testdata/flip_zone_xau_2026-11-18.json"

// A setup found on each frame that publishes carries the frame it was found on and an M5 rejection.
func TestBreakRetestPublishesASetupFoundOnEachListedFrame(t *testing.T) {
	list := replayBreakRetest(t, loadFixture(t, retestFixture), nil, nil)
	m15 := findAt(list, retestM15Sell, market.Sell)
	m5 := findAt(list, retestM5Sell, market.Sell)
	if m15 == nil || m15.Structure == nil || m15.Structure.Timeframe != market.M15 || m15.StructureTimeframe != market.M15 || !evidenceHas(*m15, "structure_tf_m15") {
		t.Fatalf("an M15 break must publish with M15: %+v", m15)
	}
	if m5 == nil || m5.Structure == nil || m5.Structure.Timeframe != market.M5 || m5.StructureTimeframe != market.M5 || !evidenceHas(*m5, "structure_tf_m5") {
		t.Fatalf("an M5 break must publish with M5: %+v", m5)
	}
	for _, c := range []*opportunity.Candidate{m15, m5} {
		if err := c.Validate(); err != nil {
			t.Fatalf("a published candidate must validate: %v", err)
		}
		if !evidenceHas(*c, "m5_retest_rejection") || evidenceHas(*c, "candle_confirmation_optional") {
			t.Fatalf("every setup is confirmed by an M5 rejection: %+v", c.Evidence)
		}
	}
	if !near(m15.Entry.Low, 1.10042) || !near(m15.Entry.High, 1.10063) || !near(float64(m15.Invalidation.Price), 1.10142) {
		t.Fatalf("the M15 entry and stop: %+v %v", m15.Entry, m15.Invalidation.Price)
	}
	// The M5 setup is the v3 one: same entry band and stop.
	if !near(m5.Entry.Low, 1.10083) || !near(m5.Entry.High, 1.10104) || !near(float64(m5.Invalidation.Price), 1.1013) {
		t.Fatalf("the M5 entry and stop are the v3 ones: %+v %v", m5.Entry, m5.Invalidation.Price)
	}
}

// H1 is a structure frame too: its episodes are read, and what does not become a setup is refused with a reason on the H1 frame (no real H1 setup is published in the
// five production captures: an H1 break needs an opposing H1 structure and a stop inside the instrument envelope).
func TestBreakRetestReadsTheH1FrameAndCountsItsRefusals(t *testing.T) {
	rec := telemetry.NewRecorder()
	replayBreakRetest(t, loadFixture(t, xauFixtureForRetest), nil, rec)
	if got := refusals(rec)["H1/no_opposing_structure"]; got == 0 {
		t.Fatalf("an H1 break with no opposing H1 structure must be refused on H1: %v", refusals(rec))
	}
	rec = telemetry.NewRecorder()
	replayBreakRetest(t, loadFixture(t, xauFixtureForRetest), structureFrames("M5", "M15"), rec)
	for key := range refusals(rec) {
		if len(key) >= 3 && key[:3] == "H1/" {
			t.Fatalf("H1 is not listed and must not be read: %s", key)
		}
	}
}

// A setup found only on a frame that is not listed does not publish, and no candidate carries an unlisted frame.
func TestBreakRetestDoesNotPublishASetupFoundOnlyOnAnUnlistedFrame(t *testing.T) {
	list := replayBreakRetest(t, loadFixture(t, retestFixture), structureFrames("M5", "H1"), nil)
	for _, c := range list {
		if c.Structure.Timeframe == market.M15 || c.StructureTimeframe == market.M15 {
			t.Fatalf("an M15 structure was published although M15 is not listed: %+v", c.Structure)
		}
	}
	// The same retest is also an M5 break, so with M15 unlisted that bar publishes the M5 reading of it, never the M15 one.
	if c := findAt(list, retestM15Sell, market.Sell); c != nil && c.StructureTimeframe != market.M5 {
		t.Fatalf("the M15 reading must not publish when M15 is not listed: %+v", c.Structure)
	}
	if findAt(list, retestM5Sell, market.Sell) == nil {
		t.Fatal("the M5 break still publishes")
	}
}

// The same retest read on M5 and on M15 is one thesis: the M15 reading publishes and the M5 one is counted as a duplicate.
func TestBreakRetestOneCandidatePerThesisTheHigherFrameFirst(t *testing.T) {
	rec := telemetry.NewRecorder()
	list := replayBreakRetest(t, loadFixture(t, retestFixture), nil, rec)
	if findAt(list, retestM15Sell, market.Sell) == nil {
		t.Fatal("the M15 reading publishes")
	}
	if refusals(rec)["M5/duplicate_thesis"] == 0 {
		t.Fatalf("the M5 reading of the same thesis is counted as a duplicate: %v", refusals(rec))
	}
	// With M15 unlisted the M5 reading is the thesis: nothing is a duplicate.
	rec = telemetry.NewRecorder()
	replayBreakRetest(t, loadFixture(t, retestFixture), structureFrames("M5"), rec)
	if n := refusals(rec)["M5/duplicate_thesis"]; n != 0 {
		t.Fatalf("one frame cannot duplicate itself: %d", n)
	}
}

// Regression against v3: its bare touches (08:25 SELL, evidence candle_confirmation_optional) no longer publish; the M15 retest publishes only when the M5 rejection
// arrives. Turning the strategy's own switch off with M5 only gives the v3 touch back.
func TestBreakRetestRegressionTheV3BareTouchIsGoneAndTheConfirmedReadingPublishes(t *testing.T) {
	capture := loadFixture(t, retestFixture)
	v4 := replayBreakRetest(t, capture, nil, nil)
	if findAt(v4, retestV3TouchSell, market.Sell) != nil {
		t.Fatal("a bare touch must not publish under require_confirmation")
	}
	v3 := replayBreakRetest(t, capture, map[string]any{"structure_timeframes": []any{"M5"}, "require_confirmation": false}, nil)
	touch := findAt(v3, retestV3TouchSell, market.Sell)
	if touch == nil || !evidenceHas(*touch, "candle_confirmation_optional") || !near(touch.Entry.Low, 1.10019) || !near(touch.Entry.High, 1.1004) || !near(float64(touch.Invalidation.Price), 1.10103) {
		t.Fatalf("with the switch off and M5 only the v3 bare touch is back: %+v", touch)
	}
}

// A structure with no M5 reaction does not publish: the confirming bar is replaced by one that never reaches the retest.
func TestBreakRetestNeedsAnM5Reaction(t *testing.T) {
	capture := loadFixture(t, retestFixture)
	rows := capture.Timeframes["M5"]
	for i, r := range rows {
		if int64(r[0]) == retestM15Sell {
			rows[i] = []float64{r[0], 1.0950, 1.09502, 1.09498, 1.0950, 0}
		}
	}
	for _, c := range replayBreakRetest(t, capture, nil, nil) {
		if c.CreatedAt == retestM15Sell {
			t.Fatalf("a bar with no M5 reaction must publish nothing, got %s %s", c.Direction, c.Structure.Timeframe)
		}
	}
	if findAt(replayBreakRetest(t, loadFixture(t, retestFixture), nil, nil), retestM15Sell, market.Sell) == nil {
		t.Fatal("control: the unmodified fixture does publish on that bar")
	}
}

// No look-ahead: the setup appears when its confirming M5 bar has closed and not before, and nothing published earlier changes when the history is cut.
func TestBreakRetestHasNoLookAhead(t *testing.T) {
	full := loadFixture(t, retestFixture)
	cut := loadFixture(t, retestFixture)
	for tf, rows := range cut.Timeframes {
		var kept [][]float64
		for _, r := range rows {
			if int64(r[0]) < retestM15Sell {
				kept = append(kept, r)
			}
		}
		cut.Timeframes[tf] = kept
	}
	atFull := replayBreakRetest(t, full, nil, nil)
	atCut := replayBreakRetest(t, cut, nil, nil)
	if findAt(atCut, retestM15Sell, market.Sell) != nil || findAt(atFull, retestM15Sell, market.Sell) == nil {
		t.Fatal("the setup must appear only once its confirming bar has closed")
	}
	published := map[string]bool{}
	for _, c := range atFull {
		published[c.ID] = true
	}
	for _, c := range atCut {
		if !published[c.ID] {
			t.Fatalf("cutting the history changed what was published earlier: %s", c.ID)
		}
	}
}

// BUY and SELL are mirrors: the real bars reflected about a pivot publish the opposite direction on the same bar, in the same frame, with the stop and the proximal edge
// reflected exactly.
func TestBreakRetestMirroredBuyAndSell(t *testing.T) {
	mirrored := loadFixture(t, retestFixture)
	for tf, rows := range mirrored.Timeframes {
		out := make([][]float64, len(rows))
		for i, r := range rows {
			out[i] = []float64{r[0], retestMirrorPivot - r[1], retestMirrorPivot - r[3], retestMirrorPivot - r[2], retestMirrorPivot - r[4], r[5]}
		}
		mirrored.Timeframes[tf] = out
	}
	orig := replayBreakRetest(t, loadFixture(t, retestFixture), nil, nil)
	mir := replayBreakRetest(t, mirrored, nil, nil)
	for _, at := range []int64{retestM15Sell, retestM5Sell} {
		a, b := findAt(orig, at, market.Sell), findAt(mir, at, market.Buy)
		if a == nil || b == nil {
			t.Fatalf("both directions must publish at %d: %v %v", at, a != nil, b != nil)
		}
		if b.Structure.Timeframe != a.Structure.Timeframe || !near(float64(b.Invalidation.Price), retestMirrorPivot-float64(a.Invalidation.Price)) ||
			!near(b.Entry.High, retestMirrorPivot-a.Entry.Low) || !near(b.Entry.Low, retestMirrorPivot-a.Entry.High) {
			t.Fatalf("mirror %+v entry %+v stop %v vs original %+v entry %+v stop %v", b.Structure, b.Entry, b.Invalidation.Price, a.Structure, a.Entry, a.Invalidation.Price)
		}
	}
}

// XAU regression: Break & Retest publishes nothing on the real XAU bars of 14-18 Nov, in v3 or v4. Its retests are refused by the instrument stop envelope (a
// structural stop wider than the XAU cap) or for want of an opposing structure, and the refusals are counted on the frame that read them.
func TestBreakRetestXAUPublishesNothingAndCountsWhy(t *testing.T) {
	rec := telemetry.NewRecorder()
	list := replayBreakRetest(t, loadFixture(t, xauFixtureForRetest), nil, rec)
	if len(list) != 0 {
		t.Fatalf("no XAU setup: %d", len(list))
	}
	got := refusals(rec)
	if got["M5/risk_exceeds_execution_envelope"] == 0 || got["M15/risk_exceeds_execution_envelope"] == 0 || got["M5/no_opposing_structure"] == 0 {
		t.Fatalf("the refusals name the envelope and the missing opposing structure on their frames: %v", got)
	}
}
