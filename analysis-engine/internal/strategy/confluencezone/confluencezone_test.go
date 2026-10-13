package confluencezone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
)

// One confirmation is one opportunity. On real GBPUSD data the same confirming
// candle was seen by three touches with slightly different supporting structures
// (three episodes over the same price band), and which of them was published
// changed between evaluations, so one reaction was published under two opportunity
// IDs with an identical entry, stop and target. The identity is the side and the
// confirming candle; the episode (the thesis) is not part of it.
func TestOneConfirmationIsOneOpportunityWhateverEpisodeSupportsIt(t *testing.T) {
	a := Setup{Direction: market.Buy, ConfirmTime: 1791847500, TouchTime: 1791847500,
		Region: structuralconfluence.Region{EpisodeID: "cz3:buy:e4149495074c2199"}}
	b := Setup{Direction: market.Buy, ConfirmTime: 1791847500, TouchTime: 1791846900,
		Region: structuralconfluence.Region{EpisodeID: "cz3:buy:13f8f045bb8d61a1"}}
	if setupKey(a) != setupKey(b) {
		t.Fatalf("one confirmation, two keys: %q and %q", setupKey(a), setupKey(b))
	}
	c := b
	c.ConfirmTime++
	if setupKey(c) == setupKey(b) {
		t.Fatal("two confirmations must be two opportunities")
	}
	d := b
	d.Direction = market.Sell
	if setupKey(d) == setupKey(b) {
		t.Fatal("the two sides must never share an opportunity")
	}
}
