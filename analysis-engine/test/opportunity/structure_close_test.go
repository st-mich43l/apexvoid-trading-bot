package opportunity_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Rule C of the order block definition (docs/strategies/order-block-definition.md): a live order block opportunity is withdrawn by the first close OF ITS
// STRUCTURE'S OWN timeframe beyond the structural far edge by the strategy's buffer. The real case is the SELL block of 28 Oct 2026 (4061.65-4064.67), whose
// opportunity sat "waiting for the retest" for 17 hours.

func obOpportunity(id string, direction market.Direction, tf market.Timeframe, low, high float64) opportunity.Candidate {
	c := candidate(id)
	c.Strategy = "order_block"
	c.Direction = direction
	c.ObservedTimeframe = market.M5
	c.Entry = opportunity.EntryZone{Low: low, High: high}
	if direction == market.Sell {
		c.Invalidation = market.PriceLevel{Price: market.Price(high + 4), Label: "order_block_invalidated"}
		c.Targets = []opportunity.Target{{Price: market.PriceLevel{Price: market.Price(low - 10), Label: "nearest_opposing_liquidity"}}}
	} else {
		c.Invalidation = market.PriceLevel{Price: market.Price(low - 4), Label: "order_block_invalidated"}
		c.Targets = []opportunity.Target{{Price: market.PriceLevel{Price: market.Price(high + 10), Label: "nearest_opposing_liquidity"}}}
	}
	c.Structure = &opportunity.StructureZone{Timeframe: tf, Low: low, High: high, FormedAt: 5}
	return c
}

func buffer(price float64) func(opportunity.StrategyID) (float64, bool) {
	return func(id opportunity.StrategyID) (float64, bool) { return price, id == "order_block" }
}

func observe(t *testing.T, book *opportunity.Book, c opportunity.Candidate) {
	t.Helper()
	if _, err := book.Observe(c, 11); err != nil {
		t.Fatal(err)
	}
}

func TestAClosePastTheSupplyHighPlusTheBufferWithdrawsTheOpportunityOnce(t *testing.T) {
	book := opportunity.NewBook()
	observe(t, book, obOpportunity("sell", market.Sell, market.M5, 4061.65, 4064.67))
	// the real closes of the day: inside, then 0.4 above the high (within a 0.45 buffer), then 1.13 above it
	for _, close := range []float64{4063.9, 4064.67, 4065.0, 4065.12} {
		if got := book.InvalidateByStructureClose(market.M5, close, 20, buffer(0.45)); len(got) != 0 {
			t.Fatalf("close %.2f is within the buffer and must not withdraw: %+v", close, got)
		}
	}
	got := book.InvalidateByStructureClose(market.M5, 4065.8, 30, buffer(0.45))
	if len(got) != 1 || got[0].Kind != opportunity.TransitionInvalidated || got[0].Record.Terminal.Reason != opportunity.ReasonStructureClosedThrough || got[0].Record.Terminal.At != 30 {
		t.Fatalf("a close beyond the high + buffer must withdraw with STRUCTURE_CLOSED_THROUGH: %+v", got)
	}
	if again := book.InvalidateByStructureClose(market.M5, 4066.0, 40, buffer(0.45)); len(again) != 0 {
		t.Fatalf("a withdrawn opportunity is withdrawn once: %+v", again)
	}
	if string(opportunity.ReasonStructureClosedThrough) != "STRUCTURE_CLOSED_THROUGH" {
		t.Fatal("the reason code is part of the contract")
	}
}

func TestADemandBlockIsWithdrawnByACloseBelowItsLowOnly(t *testing.T) {
	book := opportunity.NewBook()
	observe(t, book, obOpportunity("buy", market.Buy, market.M5, 4052.81, 4057.62))
	if got := book.InvalidateByStructureClose(market.M5, 4060.0, 20, buffer(0.25)); len(got) != 0 {
		t.Fatalf("a close above the block is not a break: %+v", got)
	}
	if got := book.InvalidateByStructureClose(market.M5, 4052.40, 30, buffer(0.25)); len(got) != 1 {
		t.Fatalf("a close 0.41 below the low with a 0.25 buffer is a break: %+v", got)
	}
}

func TestAnH1BlockIsOnlyWithdrawnByAnH1CloseNeverAnM5One(t *testing.T) {
	book := opportunity.NewBook()
	observe(t, book, obOpportunity("h1", market.Sell, market.H1, 157.822, 157.919))
	if got := book.InvalidateByStructureClose(market.M5, 158.5, 20, buffer(0.01)); len(got) != 0 {
		t.Fatalf("an M5 close must not retire an H1 structure: %+v", got)
	}
	if got := book.InvalidateByStructureClose(market.M15, 158.5, 21, buffer(0.01)); len(got) != 0 {
		t.Fatalf("an M15 close must not retire an H1 structure: %+v", got)
	}
	if got := book.InvalidateByStructureClose(market.H1, 157.95, 22, buffer(0.01)); len(got) != 1 {
		t.Fatalf("an H1 close beyond the H1 high + buffer retires it: %+v", got)
	}
}

func TestOnlyStrategiesWithTheRuleAndAStructureAreEverWithdrawnThisWay(t *testing.T) {
	book := opportunity.NewBook()
	other := obOpportunity("supply", market.Sell, market.M5, 100, 101)
	other.Strategy = "supply"
	noStructure := obOpportunity("legacy", market.Sell, market.M5, 100, 101)
	noStructure.Structure = nil // an opportunity published before the structure block existed
	observe(t, book, other)
	observe(t, book, noStructure)
	if got := book.InvalidateByStructureClose(market.M5, 500, 20, buffer(0)); len(got) != 0 {
		t.Fatalf("no other strategy and no structure-less opportunity is withdrawn here: %+v", got)
	}
	if got := book.InvalidateByStructureClose(market.M5, 500, 21, nil); len(got) != 0 {
		t.Fatalf("with the rule off (no buffer) nothing is withdrawn: %+v", got)
	}
	// the candidate's own invalidation rule is untouched
	if got := book.InvalidateByClose(market.M5, 500, 22); len(got) != 2 {
		t.Fatalf("InvalidateByClose still applies the candidates' own invalidation: %+v", got)
	}
}
