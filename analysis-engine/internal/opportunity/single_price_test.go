package opportunity

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// spCandidate is a Trendline BUY observed at t=100 with a corridor 4299.60-4300.40 and a one-price execution at 4300.15 for the next M5 bar.
func spCandidate(t *testing.T) Candidate {
	t.Helper()
	c := zoneCandidate(t, "trendline", "line", "trendline:M5:support:1:2", market.M5)
	c.Entry = EntryZone{Low: 4299.60, High: 4300.40}
	c.Invalidation = market.PriceLevel{Price: 4294, Label: "trendline_close_violation"}
	c.CreatedAt, c.FormedAt, c.ExpiresAt = 100, 100, 100+12*3600
	c.Execution = &SinglePriceExecution{Price: 4300.15, Timeframe: market.M5, ProjectedAt: 400, ValidUntil: 400 + 6*300, Proximity: 0.2, SlopePerBar: 0.05}
	if err := c.Validate(); err != nil {
		t.Fatalf("fixture invalid: %v", err)
	}
	return c
}

func nan() float64 { return math.NaN() }

func TestSinglePriceContractIsEnforced(t *testing.T) {
	cases := map[string]func(*Candidate){
		"a strategy that was not approved may not declare it": func(c *Candidate) { c.Strategy = "supply" },
		"price outside the interaction corridor":              func(c *Candidate) { c.Execution.Price = 4301 },
		"price not finite":                                    func(c *Candidate) { c.Execution.Price = nan() },
		"price beyond the invalidation":                       func(c *Candidate) { c.Invalidation.Price = 4300.30 },
		"projection not after the observation (look-ahead)":   func(c *Candidate) { c.Execution.ProjectedAt = c.CreatedAt },
		"valid until not after the projection":                func(c *Candidate) { c.Execution.ValidUntil = c.Execution.ProjectedAt },
		"valid beyond the life of the opportunity":            func(c *Candidate) { c.Execution.ValidUntil = c.ExpiresAt + 1 },
		"unknown timeframe":                                   func(c *Candidate) { c.Execution.Timeframe = "M7" },
		"negative proximity":                                  func(c *Candidate) { c.Execution.Proximity = -1 },
	}
	for name, mutate := range cases {
		c := spCandidate(t)
		clone := *c.Execution
		c.Execution = &clone
		mutate(&c)
		if err := c.Validate(); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if c := spCandidate(t); c.ExecutionModel() != ExecutionSinglePrice {
		t.Error("a candidate with an Execution block is single-price")
	}
	if c := zoneCandidate(t, "supply", "z", "zone:A", market.M15); c.ExecutionModel() != ExecutionZone {
		t.Error("a candidate without one is traded as a zone")
	}
}

// The projected price is stale from ValidUntil: the opportunity ends there even though the strategy's own TTL is 12 hours, so an unfilled order is withdrawn.
func TestOpportunityExpiresWhenItsProjectedPriceGoesStale(t *testing.T) {
	c := spCandidate(t)
	c.CreatedAt, c.ExpiresAt = 50, 50+12*3600 // formation-anchored, as a strategy builds it
	got, err := AtFirstObservation(c, 100)
	if err != nil {
		t.Fatal(err)
	}
	if got.ExpiresAt != got.Execution.ValidUntil || got.ExpiresAt != 400+6*300 {
		t.Fatalf("expires %d, want the projection's valid_until %d", got.ExpiresAt, got.Execution.ValidUntil)
	}
	b := NewBook()
	if _, err := b.Observe(got, 100); err != nil {
		t.Fatal(err)
	}
	if expired := b.Expire(got.ExpiresAt - 1); len(expired) != 0 {
		t.Fatal("still executable before valid_until")
	}
	expired := b.Expire(got.ExpiresAt)
	if len(expired) != 1 || expired[0].Kind != TransitionExpired || !expired[0].ShouldPublish() {
		t.Fatalf("must expire (and publish) exactly at valid_until: %+v", expired)
	}
	// A zone-mode candidate keeps the strategy's own TTL.
	z := zoneCandidate(t, "supply", "z", "zone:A", market.M15)
	zoned, err := AtFirstObservation(z, z.CreatedAt)
	if err != nil || zoned.ExpiresAt != z.ExpiresAt {
		t.Fatalf("a zone candidate's expiry must not change: %d vs %d (%v)", zoned.ExpiresAt, z.ExpiresAt, err)
	}
}

// Rounding to the broker tick moves the price by at most half a tick, never out of the corridor, and never touches a price the strategy did not project.
func TestNormalizeRoundsTheExecutablePriceToTheNearestTick(t *testing.T) {
	geometry := market.Geometry{Symbol: "XAUUSD", PipSize: 0.1, PriceDigits: 2}
	c := spCandidate(t)
	c.Symbol = "XAUUSD"
	c.Execution.Price = 4300.1537
	c.Entry = EntryZone{Low: 4299.6037, High: 4300.4037}
	got, err := NormalizeGeometry(c, geometry)
	if err != nil {
		t.Fatal(err)
	}
	if got.Execution.Price != 4300.15 {
		t.Fatalf("price %v, want the nearest tick 4300.15", got.Execution.Price)
	}
	if got.Execution.Price < got.Entry.Low || got.Execution.Price > got.Entry.High {
		t.Fatal("price left the corridor")
	}
	if got.Execution.ValidUntil != c.Execution.ValidUntil || got.Execution.ProjectedAt != c.Execution.ProjectedAt {
		t.Fatal("rounding must not change the timing")
	}
	if c.Execution.Price != 4300.1537 {
		t.Fatal("the input candidate must not be mutated")
	}
}

func TestSinglePriceStrategiesAreOnlyTrendline(t *testing.T) {
	if len(SinglePriceStrategies) != 1 || !SinglePriceStrategies["trendline"] {
		t.Fatalf("only Trendline is approved: %v", SinglePriceStrategies)
	}
	c := spCandidate(t)
	c.Strategy = "key_level"
	if err := c.Validate(); err == nil || !strings.Contains(err.Error(), "may not declare") {
		t.Fatalf("another strategy must be refused by name of the rule: %v", err)
	}
}
