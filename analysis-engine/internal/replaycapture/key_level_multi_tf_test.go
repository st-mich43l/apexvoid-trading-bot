package replaycapture_test

// Key Level v12 (Q2): levels are found on M5, M15 and H1, the touch, the reaction, the stop and the entry are read on M5. Fixtures are real production bars:
//
//   - XAU (testdata/key_level_xau_2026-11-13.json): the SELL of 13 Nov 11:20 UTC (confirmed at 11:25) is the setup that lost under v11 (full stop, -1R, a 56.7-pip stop). v12 reads
//     the same reaction off the level's M15 band and publishes it with the same stop and the structure on M15; the BUY of 00:35 UTC exists only as an M15 level.
//   - GBPUSD (testdata/key_level_gbpusd_2026-11-13.json): the BUY of 10:50 UTC is found on an H1 level.

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

const (
	xauFixture    = "testdata/key_level_xau_2026-11-13.json"
	gbpusdFixture = "testdata/key_level_gbpusd_2026-11-13.json"

	xauSellCreated  int64 = 1794569100 // the v11 loser, SELL, formed 1794568800
	xauM15OnlyBuy   int64 = 1794530100 // BUY found only on an M15 level
	gbpusdH1BuyMade int64 = 1794567000 // BUY found on an H1 level
)

func loadFixture(t *testing.T, path string) *replaycapture.Capture {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	return capture
}

func replayKeyLevel(t *testing.T, capture *replaycapture.Capture, params map[string]any) []opportunity.Candidate {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	var off []string
	for _, id := range strategy.KnownIDs() {
		if id != "key_level" {
			off = append(off, string(id))
		}
	}
	opts := replaycapture.Options{DisabledStrategies: off}
	// These are the v12 (level_model swing) tests; XAU runs level_model major since v13 (key_level_major_test.go), so the swing model is asked for explicitly.
	merged := map[string]any{"level_model": "swing"}
	for k, v := range params {
		merged[k] = v
	}
	opts.StrategyParameters = map[string]map[string]any{"key_level": merged}
	result, err := replaycapture.Replay(doc, capture, market.M5, opts)
	if err != nil {
		t.Fatal(err)
	}
	var out []opportunity.Candidate
	for _, c := range result.Discovered {
		if c.Strategy == "key_level" {
			out = append(out, c)
		}
	}
	return out
}

func structureFrames(tfs ...string) map[string]any {
	list := make([]any, len(tfs))
	for i, tf := range tfs {
		list[i] = tf
	}
	return map[string]any{"structure_timeframes": list}
}

func findAt(list []opportunity.Candidate, createdAt int64, dir market.Direction) *opportunity.Candidate {
	for i := range list {
		if list[i].CreatedAt == createdAt && list[i].Direction == dir {
			return &list[i]
		}
	}
	return nil
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-6 }

func evidenceHas(c opportunity.Candidate, code string) bool {
	for _, e := range c.Evidence {
		if e.Code == code {
			return true
		}
	}
	return false
}

// A setup found on each listed frame publishes, and carries the frame it was found on (never the confirming M5 frame unless the level is an M5 one).
func TestKeyLevelPublishesASetupFoundOnEachListedFrame(t *testing.T) {
	xau := replayKeyLevel(t, loadFixture(t, xauFixture), nil)
	m15 := findAt(xau, xauM15OnlyBuy, market.Buy)
	if m15 == nil || m15.Structure == nil || m15.Structure.Timeframe != market.M15 || m15.StructureTimeframe != market.M15 || !evidenceHas(*m15, "structure_tf_m15") {
		t.Fatalf("the M15 level must publish with its frame: %+v", m15)
	}
	m5 := findAt(xau, 1794566700, market.Sell)
	if m5 == nil || m5.Structure.Timeframe != market.M5 || m5.StructureTimeframe != market.M5 || !evidenceHas(*m5, "structure_tf_m5") {
		t.Fatalf("an M5 level must publish with M5: %+v", m5)
	}
	gbp := replayKeyLevel(t, loadFixture(t, gbpusdFixture), nil)
	h1 := findAt(gbp, gbpusdH1BuyMade, market.Buy)
	if h1 == nil || h1.Structure.Timeframe != market.H1 || h1.StructureTimeframe != market.H1 || !evidenceHas(*h1, "structure_tf_h1") {
		t.Fatalf("an H1 level must publish with its frame: %+v", h1)
	}
	for _, c := range []*opportunity.Candidate{m15, m5, h1} {
		if err := c.Validate(); err != nil {
			t.Fatalf("a published candidate must validate: %v", err)
		}
		if !evidenceHas(*c, "m5_key_level_rejection_confirmed") || c.Reaction == nil || c.Reaction.Pattern == "" {
			t.Fatalf("every setup is confirmed by an M5 reaction: %+v", c.Evidence)
		}
	}
}

// A setup found only on a frame that is not listed does not publish.
func TestKeyLevelDoesNotPublishASetupFoundOnlyOnAnUnlistedFrame(t *testing.T) {
	xau := replayKeyLevel(t, loadFixture(t, xauFixture), structureFrames("M5", "H1"))
	if c := findAt(xau, xauM15OnlyBuy, market.Buy); c != nil {
		t.Fatalf("the M15-only level must not publish when M15 is not listed: %+v", c.Structure)
	}
	// The same reaction also qualifies off an M15 level of GBPUSD, so with H1 unlisted the strategy publishes that one instead: what must never happen is a candidate
	// carrying a frame that is not listed.
	listed := replayKeyLevel(t, loadFixture(t, gbpusdFixture), structureFrames("M5", "M15"))
	for _, c := range listed {
		if c.Structure.Timeframe == market.H1 || c.StructureTimeframe == market.H1 {
			t.Fatalf("an H1 structure was published although H1 is not listed: %+v", c.Structure)
		}
	}
	h1Seen := false
	for _, c := range replayKeyLevel(t, loadFixture(t, gbpusdFixture), nil) {
		h1Seen = h1Seen || c.Structure.Timeframe == market.H1
	}
	if !h1Seen {
		t.Fatal("with H1 listed the fixture must publish an H1 structure")
	}
}

// The setup that lost under v11 (13 Nov 11:20 UTC SELL, full stop, -1R): v12 still takes it, as the M15 level, with the SAME reaction, stop and target; listing only M5
// gives the v11 band back.
func TestKeyLevelRegressionTheV11LoserIsTakenOffTheM15BandWithTheSameStop(t *testing.T) {
	capture := loadFixture(t, xauFixture)
	v12 := findAt(replayKeyLevel(t, capture, nil), xauSellCreated, market.Sell)
	if v12 == nil {
		t.Fatal("the v11 loser must still be a setup in v12")
	}
	if v12.Structure.Timeframe != market.M15 || !near(v12.Entry.Low, 3887.49) || !near(v12.Entry.High, 3892.94) || !near(v12.Structure.Low, 3886.18) || !near(v12.Structure.High, 3892.94) {
		t.Fatalf("v12 reads the M15 band: %+v entry %+v", v12.Structure, v12.Entry)
	}
	if !near(float64(v12.Invalidation.Price), 3894.49) || !near(float64(v12.Targets[0].Price.Price), 3875.07) {
		t.Fatalf("the reaction, the stop and the target are the v11 ones: stop %v target %v", v12.Invalidation.Price, v12.Targets[0].Price.Price)
	}
	v11 := findAt(replayKeyLevel(t, capture, structureFrames("M5")), xauSellCreated, market.Sell)
	if v11 == nil || v11.Structure.Timeframe != market.M5 || !near(v11.Entry.Low, 3887.64) || !near(v11.Entry.High, 3891.67) || !near(float64(v11.Invalidation.Price), 3894.49) {
		t.Fatalf("[M5] alone gives the v11 band back: %+v", v11)
	}
}

// A structure with no M5 reaction does not publish: the confirming bar is replaced by one that never reaches the band (the Key Level setup of that bar is gone; nothing else
// is published on it).
func TestKeyLevelNeedsAnM5Reaction(t *testing.T) {
	capture := loadFixture(t, xauFixture)
	rows := capture.Timeframes["M5"]
	for i, r := range rows {
		if int64(r[0]) == xauSellCreated { // the confirming bar is the last bar of the fixture
			rows[i] = []float64{r[0], 3870, 3870.2, 3869.8, 3870, 0}
		}
	}
	for _, c := range replayKeyLevel(t, capture, nil) {
		if c.CreatedAt == xauSellCreated {
			t.Fatalf("a bar with no M5 reaction must publish nothing, got %s %s", c.Direction, c.Structure.Timeframe)
		}
	}
	untouched := replayKeyLevel(t, loadFixture(t, xauFixture), nil)
	if findAt(untouched, xauSellCreated, market.Sell) == nil {
		t.Fatal("control: the unmodified fixture does publish on that bar")
	}
}

// No look-ahead: the setup is published when its confirming M5 bar closes and not before, and nothing that was published earlier changes when history is cut.
func TestKeyLevelHasNoLookAhead(t *testing.T) {
	full := loadFixture(t, xauFixture)
	cut := loadFixture(t, xauFixture)
	for tf, rows := range cut.Timeframes {
		var kept [][]float64
		for _, r := range rows {
			if int64(r[0]) < xauSellCreated { // up to the bar before the confirming one
				kept = append(kept, r)
			}
		}
		cut.Timeframes[tf] = kept
	}
	atFull := replayKeyLevel(t, full, nil)
	atCut := replayKeyLevel(t, cut, nil)
	if findAt(atCut, xauSellCreated, market.Sell) != nil || findAt(atFull, xauSellCreated, market.Sell) == nil {
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
	if len(atCut) == 0 {
		t.Fatal("the cut replay must still publish the earlier setups")
	}
}

// BUY and SELL are exact mirrors: the M15 setup of the XAU fixture, reflected about 3900, is the same setup the other way round.
func TestKeyLevelMirroredBuyAndSell(t *testing.T) {
	capture := loadFixture(t, xauFixture)
	mirrored := loadFixture(t, xauFixture)
	for tf, rows := range mirrored.Timeframes {
		out := make([][]float64, len(rows))
		for i, r := range rows {
			out[i] = []float64{r[0], 7800 - r[1], 7800 - r[3], 7800 - r[2], 7800 - r[4], r[5]}
		}
		mirrored.Timeframes[tf] = out
	}
	orig := findAt(replayKeyLevel(t, capture, nil), xauSellCreated, market.Sell)
	mirror := findAt(replayKeyLevel(t, mirrored, nil), xauSellCreated, market.Buy)
	if orig == nil || mirror == nil {
		t.Fatalf("both directions must publish: %v %v", orig != nil, mirror != nil)
	}
	// The proximal edge, the stop and the frame mirror exactly. The far edge of the reflected band differs by under half a price unit: the level's band is built from its
	// swings and pools, which are not an exact reflection of each other.
	if mirror.Structure.Timeframe != orig.Structure.Timeframe || !near(mirror.Entry.High, 7800-orig.Entry.Low) || !near(float64(mirror.Invalidation.Price), 7800-float64(orig.Invalidation.Price)) ||
		math.Abs(mirror.Entry.Low-(7800-orig.Entry.High)) > 0.5 || math.Abs((mirror.Entry.High-mirror.Entry.Low)-(orig.Entry.High-orig.Entry.Low)) > 0.5 {
		t.Fatalf("mirror %+v / %+v / %v vs original %+v / %+v / %v", mirror.Structure, mirror.Entry, mirror.Invalidation.Price, orig.Structure, orig.Entry, orig.Invalidation.Price)
	}
}
