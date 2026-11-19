package crt

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// Every refused sweep episode is reported once, and TakeRefusals drains the counts (strategy.RefusalReporter).
func TestRefusalsAreCountedOnceAndDrained(t *testing.T) {
	var _ strategy.RefusalReporter = (*Strategy)(nil)
	s := &Strategy{}
	s.countRefusal(string(ReasonConfluenceBelowFloor), "BUY|100|200")
	s.countRefusal(string(ReasonConfluenceBelowFloor), "BUY|100|200") // the same episode on the next bar
	s.countRefusal(string(ReasonConfluenceBelowFloor), "SELL|100|200")
	got := s.TakeRefusals()
	if n := got[strategy.Refusal{Reason: string(ReasonConfluenceBelowFloor), Timeframe: "M5"}]; n != 2 {
		t.Fatalf("counted %d, want 2 (one per episode): %+v", n, got)
	}
	if again := s.TakeRefusals(); len(again) != 0 {
		t.Fatalf("a drained reporter must be empty: %+v", again)
	}
}
