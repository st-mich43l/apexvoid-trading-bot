package fvgv7_test

import (
	"math"
	"path/filepath"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// The 29 Oct 2026 GBPJPY BUY (production forensics, read-only): the M15 gap 210.856-211.006 completed at 15:15 UTC on the very candle that ran the pair up,
// and the M5 engulfing at 15:40 entered at the top of that leg. v6 published it; v7 must refuse it. The capture is the closed M5/M15/H1 bars production
// held (testdata/fvgwindow-gbpjpy-20261029.json).
func TestGBPJPY29OctBuyIsPublishedByV6AndRefusedByV7(t *testing.T) {
	doc := resolve(t)
	gapLow, gapHigh := 210.856, 211.006
	isTheGap := func(c opportunity.Candidate) bool {
		return c.Direction == market.Buy && c.Structure != nil && c.Structure.Timeframe == market.M15 &&
			math.Abs(c.Structure.Low-gapLow) < 1e-3 && math.Abs(c.Structure.High-gapHigh) < 1e-3
	}
	var v6 *opportunity.Candidate
	var v6Bar int64
	var v7Published []string
	reasons := map[string]int64{}
	var atV6 engine.Evaluation
	walk(t, doc, testdata("fvgwindow-gbpjpy-20261029.json"), func(b bar, e engine.Evaluation) {
		for i := range b.v6 {
			if isTheGap(b.v6[i]) && v6 == nil {
				c := b.v6[i]
				v6, v6Bar, atV6 = &c, b.time, e
			}
		}
		for _, c := range b.v7 {
			if isTheGap(c) {
				v7Published = append(v7Published, time.Unix(b.time, 0).UTC().Format("15:04")+" "+describe(c))
			}
		}
		if v6 != nil && b.time == v6Bar {
			for r, n := range b.refusals {
				if r.Timeframe == market.M15 {
					reasons[r.Reason] += n
				}
			}
		}
	})
	if v6 == nil {
		t.Fatal("the vendored v6 did not publish the 29 Oct GBPJPY BUY: the regression proves nothing")
	}
	at := time.Unix(v6Bar, 0).UTC()
	t.Logf("v6 publishes at %s: %s pattern=%s stop %.3f target %.3f (%s)", at.Format("15:04"), describe(*v6), v6.Reaction.Pattern, float64(v6.Invalidation.Price), float64(v6.Targets[0].Price.Price), v6.Targets[0].Price.Label)
	if at.Day() != 29 || at.Hour() != 15 || at.Minute() != 40 {
		t.Errorf("v6 first publishes the BUY at %s, production placed it at 15:40", at.Format("15:04"))
	}
	if len(v7Published) != 0 {
		t.Errorf("v7 publishes the 29 Oct BUY: %v", v7Published)
	}
	t.Logf("v7 refusals on that bar for the M15 structures: %v", reasons)
	if reasons["no_target_room"] == 0 && reasons["entry_at_leg_extreme"] == 0 {
		t.Errorf("v7 did not refuse the BUY for room or leg extension (refusals %v)", reasons)
	}
	reportGeometry(t, atV6, v6)
}

// reportGeometry measures, independently of the strategy, the numbers the refusal rests on, for the pull request: the leg in structure ATR, how far the
// entry retraced it, the nearest unbroken swing high above the entry and the room to it in units of the risk.
func reportGeometry(t *testing.T, e engine.Evaluation, c *opportunity.Candidate) {
	t.Helper()
	m5, m15 := e.Context.Timeframes[market.M5].Legacy, e.Context.Timeframes[market.M15].Legacy
	reference, stop := c.Entry.High, float64(c.Invalidation.Price)
	risk := reference - stop
	var first int
	for i, b := range m15.Bars {
		if b.Time == c.Structure.FormedAt {
			first = i - 2
		}
	}
	extreme := math.Inf(-1)
	for _, b := range m15.Bars[first:] {
		extreme = math.Max(extreme, b.High)
	}
	for _, b := range m5.Bars {
		if b.Time >= m15.Bars[first].Time {
			extreme = math.Max(extreme, b.High)
		}
	}
	origin := math.Inf(1)
	for _, b := range m15.Bars[maxi(0, first-6) : first+1] {
		origin = math.Min(origin, b.Low)
	}
	leg := extreme - origin
	atr := m15.ATR[first+2]
	nearest := math.Inf(1)
	for _, frame := range []struct {
		bars []market.Candle
	}{{m5.Bars}, {m15.Bars}} {
		for i := 2; i < len(frame.bars)-2; i++ {
			h := frame.bars[i].High
			ok := h > reference
			for j := i - 2; j <= i+2 && ok; j++ {
				if j != i && frame.bars[j].High > h {
					ok = false
				}
			}
			for j := i + 3; j < len(frame.bars) && ok; j++ {
				if frame.bars[j].Close > h {
					ok = false
				}
			}
			if ok && h < nearest {
				nearest = h
			}
		}
	}
	if extreme > reference && extreme < nearest {
		nearest = extreme
	}
	t.Logf("leg %.3f -> %.3f = %.3f (%.2f x M15 ATR %.3f); entry %.3f retraced %.1f%% of it; nearest obstacle above %.3f; risk %.3f; room_r %.2f",
		origin, extreme, leg, leg/atr, atr, reference, 100*(extreme-reference)/leg, nearest, risk, (nearest-reference)/risk)
	_ = filepath.Join
	_ = strategy.Refusal{}
}

func maxi(a, b int) int {
	if a > b {
		return a
	}
	return b
}
