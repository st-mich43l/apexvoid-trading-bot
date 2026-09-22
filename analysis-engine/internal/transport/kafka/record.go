package kafka

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// BarIdentity is one closed bar's deterministic logical identity — used
// for deduplication/idempotency (source task §16), NOT the Kafka record
// key (key.go — the key is the canonical symbol alone). Deliberately
// keyed on CLOSE time, not open time — see
// contracts/market/bar-closed-v1.schema.json's own note on why this is
// an intentional divergence from today's Redis bars:{SYMBOL}:{TF}
// convention (which scores by bar-open time), per source task §16's
// explicit instruction.
type BarIdentity struct {
	Symbol    market.Symbol
	Timeframe market.Timeframe
	CloseTime int64 // Unix seconds
}

func (b BarIdentity) String() string {
	return fmt.Sprintf("%s|%s|%d", b.Symbol, b.Timeframe, b.CloseTime)
}

// BarClosedPayload mirrors contracts/market/bar-closed-v1.schema.json
// exactly — kept in sync by hand (ADR-007's discipline; no
// schema-to-struct generator exists yet). This is the PAYLOAD only; it
// travels inside Envelope.Payload, never published bare.
type BarClosedPayload struct {
	CanonicalSymbol string  `json:"canonical_symbol"`
	BrokerSymbol    string  `json:"broker_symbol,omitempty"`
	Timeframe       string  `json:"timeframe"`
	OpenTime        int64   `json:"open_time"`
	CloseTime       int64   `json:"close_time"`
	Open            float64 `json:"open"`
	High            float64 `json:"high"`
	Low             float64 `json:"low"`
	Close           float64 `json:"close"`
	Volume          float64 `json:"volume"`
}

// TickPayload mirrors contracts/market/tick-v1.schema.json exactly.
type TickPayload struct {
	CanonicalSymbol string  `json:"canonical_symbol"`
	BrokerSymbol    string  `json:"broker_symbol,omitempty"`
	Time            int64   `json:"time"`
	Bid             float64 `json:"bid"`
	Ask             float64 `json:"ask"`
}

// Validate applies contracts/market/tick-v1.schema.json's semantic
// rules (source task §28) that JSON Schema's structural checks alone
// cannot express: bid > 0, ask > 0, ask >= bid, both finite, a known
// (non-empty) symbol. Every failure here is a permanent contract
// failure at the call site (consumer.go), never worth retrying.
func (p TickPayload) Validate() error {
	switch {
	case p.CanonicalSymbol == "":
		return fmt.Errorf("kafka: tick payload missing canonical_symbol")
	case !market.Price(p.Bid).IsFinite() || !market.Price(p.Ask).IsFinite():
		return fmt.Errorf("kafka: tick payload has a non-finite price (bid=%v ask=%v)", p.Bid, p.Ask)
	case p.Bid <= 0:
		return fmt.Errorf("kafka: tick payload bid must be > 0, got %v", p.Bid)
	case p.Ask <= 0:
		return fmt.Errorf("kafka: tick payload ask must be > 0, got %v", p.Ask)
	case p.Ask < p.Bid:
		return fmt.Errorf("kafka: tick payload ask (%v) is below bid (%v)", p.Ask, p.Bid)
	}
	return nil
}

// OpportunityPayload mirrors contracts/analysis/opportunity-v1.schema.json.
type OpportunityPayload struct {
	ID               string                  `json:"id"`
	Strategy         string                  `json:"strategy"`
	Symbol           string                  `json:"symbol"`
	Direction        string                  `json:"direction"`
	Entry            EntryZonePayload        `json:"entry"`
	Invalidation     PriceLevelPayload       `json:"invalidation"`
	Targets          []TargetPayload         `json:"targets"`
	Evidence         []EvidencePayload       `json:"evidence"`
	Quality          QualityPayload          `json:"quality"`
	AlgorithmVersion AlgorithmVersionPayload `json:"algorithm_version"`
	CreatedAt        int64                   `json:"created_at"`
	ExpiresAt        int64                   `json:"expires_at"`
}

// EntryZonePayload mirrors opportunity.EntryZone.
type EntryZonePayload struct {
	Low  float64 `json:"low"`
	High float64 `json:"high"`
}

// PriceLevelPayload mirrors market.PriceLevel.
type PriceLevelPayload struct {
	Price float64 `json:"price"`
	Label string  `json:"label,omitempty"`
}

// TargetPayload mirrors opportunity.Target.
type TargetPayload struct {
	Price PriceLevelPayload `json:"price"`
}

// EvidencePayload mirrors opportunity.Evidence.
type EvidencePayload struct {
	Code string `json:"code"`
}

// QualityPayload mirrors opportunity.StrategyQuality.
type QualityPayload struct {
	Overall    float64            `json:"overall"`
	Components map[string]float64 `json:"components,omitempty"`
}

// AlgorithmVersionPayload mirrors engine.SnapshotVersion's two fields —
// duplicated here (rather than imported) because internal/engine is
// rank 8 and this package is rank 7; a rank-7 package must not import
// rank 8 (see adapter.go's own note on the conversion call site).
type AlgorithmVersionPayload struct {
	Structure string `json:"structure"`
	Liquidity string `json:"liquidity"`
}

// OpportunityInvalidatedPayload mirrors
// contracts/analysis/opportunity-invalidated-v1.schema.json.
type OpportunityInvalidatedPayload struct {
	OpportunityID string `json:"opportunity_id"`
	Symbol        string `json:"symbol"`
	ReasonCode    string `json:"reason_code"`
	InvalidatedAt int64  `json:"invalidated_at"`
}
