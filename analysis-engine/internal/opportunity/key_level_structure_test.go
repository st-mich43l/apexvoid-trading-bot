package opportunity

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// klCandidate is a Key Level-shaped opportunity: an M5 reaction band published as its structure (KL11), no zone id, no swing.
func klCandidate(t *testing.T, dir market.Direction, low, high float64) Candidate {
	t.Helper()
	id, err := DeterministicID(Identity{Strategy: "key_level", StrategyVersion: "v11", Symbol: "XAUUSD", Direction: dir, SetupKey: "keylevel:test:" + string(dir)})
	if err != nil {
		t.Fatal(err)
	}
	c := Candidate{
		ID: id, Strategy: "key_level", StrategyVersion: "v11", Symbol: "XAUUSD", Direction: dir, ObservedTimeframe: market.M5,
		Entry: EntryZone{Low: low, High: high}, Invalidation: market.PriceLevel{Price: market.Price(low - 3), Label: "key_level_invalidated"},
		Targets:  []Target{{Price: market.PriceLevel{Price: market.Price(high + 20)}}},
		Evidence: []Evidence{{Code: "m5_key_level_rejection_confirmed"}}, FormedAt: 100, CreatedAt: 100, ExpiresAt: 100 + 86400,
		Structure:  &StructureZone{Timeframe: market.M5, Low: low, High: high, FormedAt: 100},
		Provenance: AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "f"},
	}
	if dir == market.Sell {
		c.Invalidation = market.PriceLevel{Price: market.Price(high + 3), Label: "key_level_invalidated"}
		c.Targets = []Target{{Price: market.PriceLevel{Price: market.Price(low - 20)}}}
	}
	if err := c.Validate(); err != nil {
		t.Fatalf("fixture invalid: %v", err)
	}
	return c
}

func buffers(margin float64, ids ...StrategyID) func(StrategyID) (float64, bool) {
	return func(id StrategyID) (float64, bool) {
		for _, want := range ids {
			if id == want {
				return margin, true
			}
		}
		return 0, false
	}
}

// A BUY is withdrawn by the first M5 CLOSE below the reaction band; a wick that closes back inside the band leaves it live. Mirror for SELL.
func TestKeyLevelIsWithdrawnByACloseThroughItsBandNotByAWick(t *testing.T) {
	cases := []struct {
		name       string
		dir        market.Direction
		inside     float64 // a close inside the band (the wick went through, the close came back)
		through    float64
		wantReason ReasonCode
	}{
		{"BUY: close below the band", market.Buy, 4300.5, 4299.9, ReasonStructureClosedThrough},
		{"SELL: close above the band", market.Sell, 4302.5, 4303.1, ReasonStructureClosedThrough},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := klCandidate(t, tc.dir, 4300, 4303)
			b := liveBook(t, c)
			if got := b.InvalidateByStructureClose(market.M5, tc.inside, 400, buffers(0, "key_level")); len(got) != 0 {
				t.Fatalf("a bar that closed inside the band must not withdraw it: %+v", got)
			}
			if len(b.Live()) != 1 {
				t.Fatal("the opportunity must stay live after a wick")
			}
			got := b.InvalidateByStructureClose(market.M5, tc.through, 700, buffers(0, "key_level"))
			if len(got) != 1 || got[0].Kind != TransitionInvalidated || got[0].Record.Terminal.Reason != tc.wantReason {
				t.Fatalf("want one withdrawal with %s, got %+v", tc.wantReason, got)
			}
			if !got[0].ShouldPublish() || len(b.Live()) != 0 {
				t.Fatal("the withdrawal must publish and leave the live book")
			}
		})
	}
}

// The buffer is the margin by which a close must pass the far edge: 0.4 beyond stays live, 0.6 beyond is withdrawn (price units here; the engine passes ATRs x ATR).
func TestKeyLevelStructureCloseBufferIsAMargin(t *testing.T) {
	c := klCandidate(t, market.Buy, 4300, 4303)
	b := liveBook(t, c)
	if got := b.InvalidateByStructureClose(market.M5, 4300-0.4, 400, buffers(0.5, "key_level")); len(got) != 0 {
		t.Fatalf("a close 0.4 beyond a 0.5 margin must stay live: %+v", got)
	}
	if got := b.InvalidateByStructureClose(market.M5, 4300-0.6, 700, buffers(0.5, "key_level")); len(got) != 1 {
		t.Fatalf("a close 0.6 beyond a 0.5 margin must withdraw: %+v", got)
	}
}

// Only the frame the structure was drawn on judges it, and a strategy with no rule keeps its opportunities.
func TestKeyLevelStructureCloseIgnoresOtherFramesAndStrategiesWithoutTheRule(t *testing.T) {
	c := klCandidate(t, market.Buy, 4300, 4303)
	b := liveBook(t, c)
	if got := b.InvalidateByStructureClose(market.M15, 4290, 400, buffers(0, "key_level")); len(got) != 0 {
		t.Fatalf("an M15 close must not judge an M5 structure: %+v", got)
	}
	if got := b.InvalidateByStructureClose(market.M5, 4290, 700, buffers(0, "order_block")); len(got) != 0 {
		t.Fatalf("a strategy without the rule must keep its opportunity: %+v", got)
	}
}

// A withdrawn opportunity never reopens: observing it again is a noop; a later confirmation is a new id.
func TestAWithdrawnKeyLevelNeverReopens(t *testing.T) {
	c := klCandidate(t, market.Buy, 4300, 4303)
	b := liveBook(t, c)
	b.InvalidateByStructureClose(market.M5, 4290, 700, buffers(0, "key_level"))
	got, err := b.Observe(c, 800)
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != TransitionNoop || got.Record.State != StateInvalidated {
		t.Fatalf("re-observing a withdrawn opportunity must be a noop, got %+v", got)
	}
	if len(b.Live()) != 0 {
		t.Fatal("a withdrawn opportunity must stay out of the live book")
	}
	next := klCandidate(t, market.Buy, 4300, 4303)
	id, err := DeterministicID(Identity{Strategy: "key_level", StrategyVersion: "v11", Symbol: "XAUUSD", Direction: market.Buy, SetupKey: "keylevel:test:BUY:rejection:900:1200"})
	if err != nil {
		t.Fatal(err)
	}
	if id == c.ID {
		t.Fatal("a later reaction at the same level must be a different opportunity")
	}
	next.ID = id
	next.CreatedAt, next.FormedAt, next.ExpiresAt = 1200, 900, 1200+86400
	next.Structure.FormedAt = 900
	if tr, err := b.Observe(next, 1200); err != nil || tr.Kind != TransitionCreated {
		t.Fatalf("a new confirmation must publish a new opportunity: %+v %v", tr, err)
	}
}
