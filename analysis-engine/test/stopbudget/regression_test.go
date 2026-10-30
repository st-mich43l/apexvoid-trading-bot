package stopbudget_test

import (
	"math"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
)

// Real confirmed XAU setups of the committed capture, one per case the budget distinguishes. riskPips is measured from the proximal entry edge to the
// published invalidation (cap 70, floor 50) on the engine without the budget; class is what the budget must do with it.
type realCase struct {
	strategy, direction string
	confirmTime         int64
	riskPips            float64
	class               string
}

const xauCapture = "replay-xau-production-capture-20261012.json"

var realCases = []realCase{
	// refused: the honest structural stop is wider than the 70 pip cap
	{"key_level", "BUY", 1790857500, 84.6, "refused"},  // 10-01 12:25 Key Level BUY, stop 84.6 pips from the proximal edge
	{"key_level", "SELL", 1790861700, 86.8, "refused"}, // 10-01 13:35
	{"demand", "BUY", 1790839800, 96.7, "refused"},     // 10-01 07:30 demand
	{"demand", "BUY", 1790840400, 81.9, "refused"},     // 10-01 07:40 demand
	// structural: inside 50-70
	{"key_level", "SELL", 1790847600, 59.2, "structural"}, // 10-01 09:40
	{"demand", "BUY", 1790840700, 58.5, "structural"},     // 10-01 07:45
	{"demand", "BUY", 1790841000, 65.2, "structural"},     // 10-01 07:50
	// order_block is not here: v7 finds blocks on M15 and H1 and this XAU 60.8 pip block was an M5 block (test/obv7).
	// floor: tighter than 50, execution widens it
	{"key_level", "BUY", 1791238800, 40.9, "floor"},  // 10-05 22:20
	{"key_level", "SELL", 1791239400, 40.2, "floor"}, // 10-05 22:30
}

func TestRealXAUCapturedSetupsAreRefusedKeptOrMarkedByTheBudget(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	before := replayOne(t, doc, xauCapture, false)
	after := replayOne(t, doc, xauCapture, true)
	type occurrence struct {
		bar int64
		id  string
	}
	afterAt := map[occurrence]record{}
	for _, r := range after {
		afterAt[occurrence{r.BarTime, r.ID}] = r
	}
	for _, want := range realCases {
		found := false
		for _, b := range before {
			if b.Strategy != want.strategy || b.Direction != want.direction || b.ConfirmTime != want.confirmTime || math.Abs(b.RiskPips-want.riskPips) > 0.15 {
				continue
			}
			found = true
			if b.Class != want.class {
				t.Errorf("%s %s %d: class %s, want %s (risk %.1f)", want.strategy, want.direction, want.confirmTime, b.Class, want.class, b.RiskPips)
			}
			a, published := afterAt[occurrence{b.BarTime, b.ID}]
			switch want.class {
			case "refused":
				if published {
					t.Errorf("%s %s %d: %.1f pips is over the 70 pip cap yet it was published", want.strategy, want.direction, want.confirmTime, b.RiskPips)
				}
			default:
				code := "stop_budget_structural"
				if want.class == "floor" {
					code = "stop_budget_floor"
				}
				if !published || !has(a.Evidence, code) {
					t.Errorf("%s %s %d: want it published with %s, got %v (published %v)", want.strategy, want.direction, want.confirmTime, code, a.Evidence, published)
				} else if a.Invalidation != b.Invalidation || a.EntryLow != b.EntryLow || a.EntryHigh != b.EntryHigh {
					t.Errorf("%s %s %d: the budget moved the entry or the invalidation", want.strategy, want.direction, want.confirmTime)
				}
			}
		}
		if !found {
			t.Errorf("%s %s at %d (%.1f pips) was not found in the capture", want.strategy, want.direction, want.confirmTime, want.riskPips)
		}
	}
}
