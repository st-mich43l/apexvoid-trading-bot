package opportunity

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Q0: the structure-close rule belongs to the candidate, not to a strategy name. Any strategy that publishes a Structure with a timeframe and has a buffer is retired by a
// close of THAT timeframe beyond the structure's far edge; one without a buffer is never retired this way.
func structureOfStrategyCandidate(t *testing.T, strategy StrategyID, tf market.Timeframe, dir market.Direction, low, high float64) Candidate {
	t.Helper()
	id, err := DeterministicID(Identity{Strategy: strategy, StrategyVersion: "v1", Symbol: "XAUUSD", Direction: dir, SetupKey: "zone:" + string(tf) + ":" + string(dir)})
	if err != nil {
		t.Fatal(err)
	}
	c := klCandidate(t, dir, low, high)
	c.ID, c.Strategy = id, strategy
	c.Structure = &StructureZone{Timeframe: tf, Low: low, High: high, FormedAt: 100}
	if err := c.Validate(); err != nil {
		t.Fatalf("fixture invalid: %v", err)
	}
	return c
}

func TestAnyStrategyIsRetiredByAClosePastItsStructureOnTheStructuresOwnFrame(t *testing.T) {
	for _, strategy := range []StrategyID{"supply", "demand", "fvg", "flip_zone", "confluence_zone"} {
		for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
			c := structureOfStrategyCandidate(t, strategy, tf, market.Buy, 4300, 4303)
			b := liveBook(t, c)
			other := market.M5
			if tf == market.M5 {
				other = market.H1
			}
			if got := b.InvalidateByStructureClose(other, 4290, 400, buffers(0, strategy)); len(got) != 0 {
				t.Fatalf("%s %s: a close of %s must not judge a %s structure: %+v", strategy, tf, other, tf, got)
			}
			if got := b.InvalidateByStructureClose(tf, 4300.1, 500, buffers(0, strategy)); len(got) != 0 {
				t.Fatalf("%s %s: a close inside the structure keeps it live: %+v", strategy, tf, got)
			}
			got := b.InvalidateByStructureClose(tf, 4299.9, 600, buffers(0, strategy))
			if len(got) != 1 || got[0].Record.Terminal.Reason != ReasonStructureClosedThrough {
				t.Fatalf("%s %s: want one structure-close withdrawal, got %+v", strategy, tf, got)
			}
		}
	}
}

func TestAStrategyWithoutABufferIsNeverRetiredByTheStructureCloseRule(t *testing.T) {
	c := structureOfStrategyCandidate(t, "fvg", market.M15, market.Sell, 4300, 4303)
	b := liveBook(t, c)
	if got := b.InvalidateByStructureClose(market.M15, 4400, 400, buffers(0, "supply")); len(got) != 0 {
		t.Fatalf("fvg has no buffer in this table, so it keeps its opportunity: %+v", got)
	}
	if got := b.InvalidateByStructureClose(market.M15, 4400, 500, nil); len(got) != 0 {
		t.Fatalf("no buffer function, no rule: %+v", got)
	}
}
