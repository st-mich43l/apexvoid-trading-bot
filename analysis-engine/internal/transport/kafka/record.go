package kafka

// OpportunityPayload mirrors contracts/analysis/opportunity-v1.schema.json.
// Kafka carries this neutral business event; it deliberately has no
// market-bar or tick DTOs because Redis is the market-data plane.
type OpportunityPayload struct {
	ID               string                  `json:"id"`
	Strategy         string                  `json:"strategy"`
	Symbol           string                  `json:"symbol"`
	Timeframe        string                  `json:"timeframe,omitempty"`
	Direction        string                  `json:"direction"`
	Entry            EntryZonePayload        `json:"entry"`
	Invalidation     PriceLevelPayload       `json:"invalidation"`
	Targets          []TargetPayload         `json:"targets"`
	Evidence         []EvidencePayload       `json:"evidence"`
	Quality          QualityPayload          `json:"quality"`
	AlgorithmVersion AlgorithmVersionPayload `json:"algorithm_version"`
	FormedAt         int64                   `json:"formed_at"`
	CreatedAt        int64                   `json:"created_at"`
	ExpiresAt        int64                   `json:"expires_at"`
}

type EntryZonePayload struct {
	Low  float64 `json:"low"`
	High float64 `json:"high"`
}

type PriceLevelPayload struct {
	Price float64 `json:"price"`
	Label string  `json:"label,omitempty"`
}

type TargetPayload struct {
	Price PriceLevelPayload `json:"price"`
}

type EvidencePayload struct {
	Code string `json:"code"`
}

type QualityPayload struct {
	Overall    float64            `json:"overall"`
	Components map[string]float64 `json:"components,omitempty"`
}

// AlgorithmVersion is intentionally local to transport: importing engine
// would violate the one-way dependency graph.
type AlgorithmVersionPayload struct {
	Structure string `json:"structure"`
	Liquidity string `json:"liquidity"`
}

// OpportunityInvalidatedPayload contains only opportunity lifecycle facts,
// never account risk or execution fields.
type OpportunityInvalidatedPayload struct {
	OpportunityID string `json:"opportunity_id"`
	Symbol        string `json:"symbol"`
	Strategy      string `json:"strategy"`
	ReasonCode    string `json:"reason_code"`
	InvalidatedAt int64  `json:"invalidated_at"`
}
