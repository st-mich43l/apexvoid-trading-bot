package obdefinition_test

import (
	"path/filepath"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The four production trades of 28-29 Oct 2026, replayed from the real Redis bars (testdata/obwindow-*-20261029.json, closed bars only): three losing order
// blocks and one winning one. The frozen behaviour (order_block v5) must publish all four, as production did; the definition must not leave the losers live
// at their fill.

const (
	xauWindow    = "obwindow-xau-20261029.json"
	eurusdWindow = "obwindow-eurusd-20261029.json"

	xauBuyBlock    = "technique:order_block:buy:1793216100"  // 28 Oct 19:35 origin, filled 23:51, -30 pips
	xauSellBlock   = "technique:order_block:sell:1793231400" // 28 Oct 23:50 origin, filled 29 Oct 01:47, -51 pips
	xauSellOldest  = "technique:order_block:sell:1793122200" // 27 Oct 17:30 origin, published 28 Oct 01:20, filled 19:49, -48 pips
	eurusdBuyBlock = "technique:order_block:buy:1793202300"  // 28 Oct 15:45 origin, filled 18:31, +24 pips
)

var (
	replayMu    sync.Mutex
	replayCache = map[string]*replayResult{}
)

func cached(t *testing.T, window string, useShipped bool, v variant) *replayResult {
	t.Helper()
	key := window + "|" + v.name()
	if useShipped {
		key = window + "|shipped"
	}
	replayMu.Lock()
	defer replayMu.Unlock()
	if r, ok := replayCache[key]; ok {
		return r
	}
	r := replayOB(t, loadDoc(t), filepath.Join("..", "..", "testdata", window), useShipped, v)
	replayCache[key] = r
	return r
}

// sightingsOf are the bars a block was published on.
func sightingsOf(r *replayResult, structuralID string) []int64 {
	var bars []int64
	for _, s := range r.Sightings {
		if s.StructuralID == structuralID {
			bars = append(bars, s.Bar)
		}
	}
	return bars
}

func publishedFrom(r *replayResult, structuralID string, from int64) bool {
	for _, bar := range sightingsOf(r, structuralID) {
		if bar >= from {
			return true
		}
	}
	return false
}

func TestTheFrozenBehaviourPublishesAllFourProductionBlocks(t *testing.T) {
	xau, eurusd := cached(t, xauWindow, false, variant{}), cached(t, eurusdWindow, false, variant{})
	for _, c := range []struct {
		r  *replayResult
		id string
		at int64
	}{
		{xau, xauBuyBlock, at(28, 23, 45, 0)}, {xau, xauSellBlock, at(29, 1, 40, 0)}, {xau, xauSellOldest, at(28, 1, 20, 0)}, {eurusd, eurusdBuyBlock, at(28, 18, 25, 0)},
	} {
		if !publishedFrom(c.r, c.id, c.at) {
			t.Errorf("%s: v5 must publish %s at %s, as production did", c.r.Capture, c.id, barLabel(c.at))
		}
	}
}

// Trade 1: eight closes below the 4052.81 low between 22:45 and 23:40; the first, 22:45 (4052.47), is 0.34 below it and 0.13 ATR.
func TestTradeOneIsRetiredAtThe2245Close(t *testing.T) {
	xau := cached(t, xauWindow, false, variant{0.1, 2})
	if publishedFrom(xau, xauBuyBlock, at(28, 22, 45, 0)) {
		t.Fatalf("the BUY block must not publish from the 22:45 close on (it was filled at 23:51); published on %v", sightingsOf(xau, xauBuyBlock))
	}
	for _, s := range cached(t, xauWindow, false, variant{}).First {
		if s.StructuralID != xauBuyBlock {
			continue
		}
		if when, broke := brokenAt(cached(t, xauWindow, false, variant{}).Data, s, 0.1); !broke || when != at(28, 22, 45, 0) {
			t.Errorf("the first close through the low by more than 0.1 ATR is the 22:45 bar, got %v %s", broke, barLabel(when))
		}
	}
}

// Trade 2: the high 4056.43 was closed through at 01:15 (4056.83, +0.40), 01:20 (4065.80) and 01:25; price came back at 01:35 and the 01:40 rejection traded.
func TestTradeTwoIsRetiredAtTheBreakAndTheRejectionDoesNotPublish(t *testing.T) {
	xau := cached(t, xauWindow, false, variant{0.1, 2})
	if publishedFrom(xau, xauSellBlock, at(29, 1, 20, 0)) {
		t.Fatalf("the SELL block must not publish from the 01:20 close on, in particular not on the 01:40 rejection: %v", sightingsOf(xau, xauSellBlock))
	}
	base := cached(t, xauWindow, false, variant{})
	for _, s := range base.First {
		if s.StructuralID != xauSellBlock {
			continue
		}
		// 01:15 closed only 0.40 above the high: a break for a buffer under 0.077 ATR (ATR 5.17), not for 0.1 ATR (0.52), which waits for 01:20.
		if when, broke := brokenAt(base.Data, s, 0.05); !broke || when != at(29, 1, 15, 0) {
			t.Errorf("at 0.05 ATR the first break is the 01:15 close, got %v %s", broke, barLabel(when))
		}
		if when, broke := brokenAt(base.Data, s, 0.1); !broke || when != at(29, 1, 20, 0) {
			t.Errorf("at 0.1 ATR the first break is the 01:20 close, got %v %s", broke, barLabel(when))
		}
	}
}

// The 28 Oct 01:25 SELL block: the 00:00 close (4065.36) was already 0.69 above its high 4064.67. It cannot publish after that, and the opportunities that had
// been published for it before are withdrawn by rule C at that very close, hours before the 19:49 fill.
func TestTheOldestSellBlockIsWithdrawnAndNeverPublishedAfterItsBreak(t *testing.T) {
	r := cached(t, xauWindow, false, variant{0.1, 2})
	if publishedFrom(r, xauSellOldest, at(28, 0, 0, 0)) {
		t.Fatalf("the block must not publish from the 00:00 close on: %v", sightingsOf(r, xauSellOldest))
	}
	withdrawn := 0
	for _, w := range r.Withdrawn {
		if w.StructuralID == xauSellOldest {
			withdrawn++
			if w.At != at(28, 0, 0, 0) || w.Timeframe != market.M5 {
				t.Errorf("withdrawn at %s on %s, want the 00:00 M5 close", barLabel(w.At), w.Timeframe)
			}
		}
	}
	if withdrawn == 0 {
		t.Fatal("the opportunities published for this block before its break must be withdrawn by rule C")
	}
}

// The EURUSD winner is the case that decides the buffer. Its 18:20 M5 close (1.11288) is 3.6 pips = 0.95 ATR BELOW its 1.11324 low; the next bar closes back
// inside and the trade was filled at 18:31. "Any close beyond the edge, no reclaim forgiveness" retires it for every buffer below 0.95 ATR.
func TestTheEURUSDWinnerClosedThroughItsLowOnceAndOnlyAWideBufferKeepsIt(t *testing.T) {
	base := cached(t, eurusdWindow, false, variant{})
	if !publishedFrom(base, eurusdBuyBlock, at(28, 18, 25, 0)) {
		t.Fatal("v5 publishes the winner")
	}
	for _, s := range base.First {
		if s.StructuralID != eurusdBuyBlock {
			continue
		}
		when, broke := brokenAt(base.Data, s, 0.25)
		if !broke || when != at(28, 18, 20, 0) {
			t.Errorf("the only close through the low is the 18:20 bar, got %v %s", broke, barLabel(when))
		}
		if _, broke := brokenAt(base.Data, s, 0.9); !broke {
			t.Error("0.95 ATR is beyond a 0.9 ATR buffer")
		}
		if _, broke := brokenAt(base.Data, s, 1.0); broke {
			t.Error("0.95 ATR is inside a 1.0 ATR buffer")
		}
	}
	if publishedFrom(cached(t, eurusdWindow, false, variant{0.1, 2}), eurusdBuyBlock, at(28, 18, 25, 0)) {
		t.Error("with a 0.1 ATR buffer the winner is retired by its own 18:20 close")
	}
	if !publishedFrom(cached(t, eurusdWindow, false, variant{1.0, 2}), eurusdBuyBlock, at(28, 18, 25, 0)) {
		t.Error("with a 1.0 ATR buffer the winner still publishes")
	}
}

// supply_demand, fvg, ifvg and every other strategy publish exactly the same candidates whatever the order block keys are.
func TestNoOtherStrategyChangesWithTheOrderBlockKeys(t *testing.T) {
	for _, window := range []string{xauWindow, eurusdWindow} {
		base := cached(t, window, false, variant{})
		for _, v := range []variant{{0.1, 2}, {0.25, 0}, {0, 2}} {
			r := cached(t, window, false, v)
			if r.OthersSHA != base.OthersSHA || r.Others != base.Others {
				t.Errorf("%s %s: other strategies changed (%d -> %d candidates)", window, v.name(), base.Others, r.Others)
			}
		}
	}
}

// Every block the definition refuses is explained by the block's OWN frame (an M15 or H1 block by M15 or H1 closes, never by the M5 execution bars) or by the
// touch limit; nothing is refused for a reason the two rules do not state, and nothing the rules do not touch is lost.
func TestEveryRefusalIsExplainedByTheStructuresOwnFrameOrTheTouchLimit(t *testing.T) {
	v := variant{0.1, 2}
	for _, window := range []string{xauWindow, eurusdWindow} {
		base, on := cached(t, window, false, variant{}), cached(t, window, false, v)
		for id, s := range base.First {
			if _, kept := on.First[id]; kept {
				continue
			}
			_, broke := brokenAt(base.Data, s, v.Buffer)
			if !broke && int(s.Zone) <= v.Touches {
				t.Errorf("%s %s %s %s: refused although no close of its own %s frame broke it and it has %d touches", window, s.Timeframe, s.Direction, barLabel(s.Bar), s.Timeframe, int(s.Zone))
			}
		}
		for id, s := range on.First {
			if _, was := base.First[id]; !was {
				continue
			}
			if _, broke := brokenAt(base.Data, s, v.Buffer); broke {
				t.Errorf("%s %s: published although a close of its own %s frame had already broken it", window, barLabel(s.Bar), s.Timeframe)
			}
		}
	}
}

// The repository ships exactly the variant that was measured and chosen (docs/strategies/order-block-definition.md).
func TestTheShippedConfigurationIsTheChosenVariant(t *testing.T) {
	if got := shipped(t, loadDoc(t)); got != (variant{0.1, 2}) {
		t.Fatalf("strategies.order_block ships %+v, the measured choice is buffer 0.1 / touches 2", got)
	}
}

// Replayed exactly as configured, the engine equals the chosen variant replayed explicitly.
func TestTheShippedEngineEqualsTheChosenVariant(t *testing.T) {
	explicit, configured := cached(t, xauWindow, false, variant{0.1, 2}), cached(t, xauWindow, true, variant{})
	if len(explicit.First) != len(configured.First) || len(explicit.Withdrawn) != len(configured.Withdrawn) || explicit.OthersSHA != configured.OthersSHA {
		t.Fatalf("configured %d blocks/%d withdrawn, explicit variant %d/%d", len(configured.First), len(configured.Withdrawn), len(explicit.First), len(explicit.Withdrawn))
	}
	for id := range explicit.First {
		if _, ok := configured.First[id]; !ok {
			t.Fatalf("%s is published by the explicit variant but not as configured", id)
		}
	}
}
