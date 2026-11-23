package replaycapture_test

// Flip Zone v6 (Q3): the flip level, the break and the zone are read on M5, M15 and H1; the retest rejection, the stop and the entry are read on M5. The fixture is real
// production XAU bars (testdata/flip_zone_xau_2026-11-18.json: M5 from 14 Nov, M15 last 300 bars, H1 last 150, all closed at the cut):
//
//   - the SELL of 18 Nov 00:05 UTC is an M5 flip zone confirmed by an M5 rejection (published identically by v5);
//   - the BUY of 18 Nov 23:25 UTC is the same flip zone read off its M15 band; v5 published the bare touch of that zone at 23:10 (evidence candle_confirmation_optional, no
//     rejection) and v6 waits for the M5 rejection;
//   - the BUY of 18 Nov 08:50 UTC (1795003800) was a v5 bare touch that never got an M5 rejection: v6 publishes nothing there.

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func replayFlipZone(t *testing.T, capture *replaycapture.Capture, params map[string]any) []opportunity.Candidate {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	var off []string
	for _, id := range strategy.KnownIDs() {
		if id != "flip_zone" {
			off = append(off, string(id))
		}
	}
	opts := replaycapture.Options{DisabledStrategies: off}
	if params != nil {
		opts.StrategyParameters = map[string]map[string]any{"flip_zone": params}
	}
	result, err := replaycapture.Replay(doc, capture, market.M5, opts)
	if err != nil {
		t.Fatal(err)
	}
	var out []opportunity.Candidate
	for _, c := range result.Discovered {
		if c.Strategy == "flip_zone" {
			out = append(out, c)
		}
	}
	return out
}

const (
	flipFixture = "testdata/flip_zone_xau_2026-11-18.json"

	flipM5Sell      int64 = 1794960300 // M5 zone, confirmed
	flipM15Buy      int64 = 1795044300 // the same zone read on M15, confirmed by an M5 rejection
	flipV5TouchBuy  int64 = 1795043400 // v5 published the bare touch of that zone 15 minutes earlier
	flipV5NeverBuy  int64 = 1795003800 // a v5 bare touch that was never confirmed
	flipMirrorPivot       = 7600.0
)

// A setup found on each listed frame publishes, carrying the frame it was found on and an M5 rejection.
func TestFlipZonePublishesASetupFoundOnEachListedFrame(t *testing.T) {
	list := replayFlipZone(t, loadFixture(t, flipFixture), nil)
	m5 := findAt(list, flipM5Sell, market.Sell)
	m15 := findAt(list, flipM15Buy, market.Buy)
	if m5 == nil || m5.Structure == nil || m5.Structure.Timeframe != market.M5 || m5.StructureTimeframe != market.M5 || !evidenceHas(*m5, "structure_tf_m5") {
		t.Fatalf("an M5 flip zone must publish with M5: %+v", m5)
	}
	if m15 == nil || m15.Structure == nil || m15.Structure.Timeframe != market.M15 || m15.StructureTimeframe != market.M15 || !evidenceHas(*m15, "structure_tf_m15") {
		t.Fatalf("an M15 flip zone must publish with M15: %+v", m15)
	}
	for _, c := range []*opportunity.Candidate{m5, m15} {
		if err := c.Validate(); err != nil {
			t.Fatalf("a published candidate must validate: %v", err)
		}
		if !evidenceHas(*c, "m5_flip_zone_rejection_confirmed") || evidenceHas(*c, "candle_confirmation_optional") {
			t.Fatalf("every setup is confirmed by an M5 rejection: %+v", c.Evidence)
		}
	}
	if !near(m15.Entry.Low, 3728.99) || !near(m15.Entry.High, 3734) || !near(m15.Structure.Low, 3725.11) || !near(m15.Structure.High, 3734) || !near(float64(m15.Invalidation.Price), 3727.1) {
		t.Fatalf("the M15 zone, entry and stop: %+v entry %+v stop %v", m15.Structure, m15.Entry, m15.Invalidation.Price)
	}
	if !near(m5.Entry.Low, 3844.13) || !near(m5.Entry.High, 3849.13) || !near(float64(m5.Invalidation.Price), 3851.01) {
		t.Fatalf("the M5 entry and stop are the v5 ones: entry %+v stop %v", m5.Entry, m5.Invalidation.Price)
	}
}

// A zone found only on a frame that is not listed does not publish, and no candidate carries an unlisted frame.
func TestFlipZoneDoesNotPublishASetupFoundOnlyOnAnUnlistedFrame(t *testing.T) {
	list := replayFlipZone(t, loadFixture(t, flipFixture), structureFrames("M5", "H1"))
	for _, c := range list {
		if c.Structure.Timeframe == market.M15 || c.StructureTimeframe == market.M15 {
			t.Fatalf("an M15 structure was published although M15 is not listed: %+v", c.Structure)
		}
	}
	if findAt(list, flipM15Buy, market.Buy) != nil {
		t.Fatal("the M15 zone must not publish when M15 is not listed")
	}
	if findAt(list, flipM5Sell, market.Sell) == nil {
		t.Fatal("the M5 zone still publishes")
	}
}

// require_confirmation: true is the point of the regression: v5 published the bare touch at 23:10 and the touch of 08:50 that was never confirmed; v6 publishes neither,
// and the confirmed M15 reading of the same zone appears 15 minutes after the first. Turning the strategy's own switch off (with M5 only) gives the v5 touch back.
func TestFlipZoneRegressionTheV5BareTouchesAreGoneAndTheConfirmedReadingPublishes(t *testing.T) {
	capture := loadFixture(t, flipFixture)
	v6 := replayFlipZone(t, capture, nil)
	for _, at := range []int64{flipV5TouchBuy, flipV5NeverBuy} {
		if c := findAt(v6, at, market.Buy); c != nil {
			t.Fatalf("a bare touch must not publish under require_confirmation: %d %+v", at, c.Entry)
		}
	}
	if findAt(v6, flipM15Buy, market.Buy) == nil {
		t.Fatal("the confirmed reading must publish")
	}
	v5 := replayFlipZone(t, capture, map[string]any{"structure_timeframes": []any{"M5"}, "require_confirmation": false})
	touch := findAt(v5, flipV5TouchBuy, market.Buy)
	if touch == nil || !evidenceHas(*touch, "candle_confirmation_optional") || !near(touch.Entry.Low, 3728.87) || !near(touch.Entry.High, 3733.87) || !near(float64(touch.Invalidation.Price), 3727.17) {
		t.Fatalf("with the switch off and M5 only the v5 bare touch is back: %+v", touch)
	}
	if findAt(v5, flipV5NeverBuy, market.Buy) == nil {
		t.Fatal("the unconfirmed 08:50 touch is a v5 setup")
	}
}

// A structure with no M5 reaction does not publish: the confirming bar is replaced by one that never reaches the zone.
func TestFlipZoneNeedsAnM5Reaction(t *testing.T) {
	capture := loadFixture(t, flipFixture)
	rows := capture.Timeframes["M5"]
	for i, r := range rows {
		if int64(r[0]) == flipM15Buy {
			rows[i] = []float64{r[0], 3760, 3760.2, 3759.8, 3760, 0}
		}
	}
	for _, c := range replayFlipZone(t, capture, nil) {
		if c.CreatedAt == flipM15Buy {
			t.Fatalf("a bar with no M5 reaction must publish nothing, got %s %s", c.Direction, c.Structure.Timeframe)
		}
	}
	if findAt(replayFlipZone(t, loadFixture(t, flipFixture), nil), flipM15Buy, market.Buy) == nil {
		t.Fatal("control: the unmodified fixture does publish on that bar")
	}
}

// No look-ahead: the setup appears when its confirming M5 bar has closed and not before, and nothing published earlier changes when the history is cut.
func TestFlipZoneHasNoLookAhead(t *testing.T) {
	full := loadFixture(t, flipFixture)
	cut := loadFixture(t, flipFixture)
	for tf, rows := range cut.Timeframes {
		var kept [][]float64
		for _, r := range rows {
			if int64(r[0]) < flipM15Buy {
				kept = append(kept, r)
			}
		}
		cut.Timeframes[tf] = kept
	}
	atFull := replayFlipZone(t, full, nil)
	atCut := replayFlipZone(t, cut, nil)
	if findAt(atCut, flipM15Buy, market.Buy) != nil || findAt(atFull, flipM15Buy, market.Buy) == nil {
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
	if findAt(atCut, flipM5Sell, market.Sell) == nil {
		t.Fatal("the cut replay must still publish the earlier setup")
	}
}

// BUY and SELL are mirrors: the real XAU bars reflected about a pivot publish the opposite direction on the same bar, in the same frame, with the stop and the proximal
// edge reflected exactly. Only the M15 setup is asserted: the zone and level primitives are not exact reflections of each other, so most M5 zones of the fixture do not
// reappear mirrored (2 of the 8 v5 setups do); v5 shows the identical 2 of 8 on the same bars, so that asymmetry is not introduced by v6.
func TestFlipZoneMirroredBuyAndSell(t *testing.T) {
	mirrored := loadFixture(t, flipFixture)
	for tf, rows := range mirrored.Timeframes {
		out := make([][]float64, len(rows))
		for i, r := range rows {
			out[i] = []float64{r[0], flipMirrorPivot - r[1], flipMirrorPivot - r[3], flipMirrorPivot - r[2], flipMirrorPivot - r[4], r[5]}
		}
		mirrored.Timeframes[tf] = out
	}
	orig := replayFlipZone(t, loadFixture(t, flipFixture), nil)
	mir := replayFlipZone(t, mirrored, nil)
	for _, pair := range []struct {
		at       int64
		was, now market.Direction
	}{{flipM15Buy, market.Buy, market.Sell}} {
		a, b := findAt(orig, pair.at, pair.was), findAt(mir, pair.at, pair.now)
		if a == nil || b == nil {
			t.Fatalf("both directions must publish at %d: %v %v", pair.at, a != nil, b != nil)
		}
		if b.Structure.Timeframe != a.Structure.Timeframe || !near(float64(b.Invalidation.Price), flipMirrorPivot-float64(a.Invalidation.Price)) {
			t.Fatalf("mirror %+v stop %v vs original %+v stop %v", b.Structure, b.Invalidation.Price, a.Structure, a.Invalidation.Price)
		}
		edgeA, edgeB := a.Entry.High, b.Entry.Low
		if pair.was == market.Sell {
			edgeA, edgeB = a.Entry.Low, b.Entry.High
		}
		if !near(edgeB, flipMirrorPivot-edgeA) {
			t.Fatalf("the proximal edge mirrors: %v vs %v", edgeB, flipMirrorPivot-edgeA)
		}
	}
}
