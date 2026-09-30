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
	// TechnicalContext is the additive V1 policy-input block (S13B). Omitted
	// when the engine could not produce it; consumers must then fail closed.
	TechnicalContext *TechnicalContextPayload `json:"technical_context,omitempty"`
	// StopEnvelope is the additive Phase 4 policy-input block. Omitted when
	// the engine could not produce a positive floor for this candidate's
	// strategy family; consumers fall back to their own legacy envelope
	// lookup rather than fabricating one.
	StopEnvelope *StopEnvelopePayload `json:"stop_envelope,omitempty"`
}

// StopEnvelopePayload mirrors opportunity.StopEnvelope — see that type's
// own doc comment for why DesiredMinimumPips is a raw fact, not a final
// decision.
type StopEnvelopePayload struct {
	FloorPips          float64 `json:"floor_pips"`
	CapPips            float64 `json:"cap_pips"`
	DesiredMinimumPips float64 `json:"desired_minimum_pips"`
	Source             string  `json:"source"`
}

// TechnicalContextPayload carries engine-owned facts an execution policy needs
// so it never recomputes ATR/structure from raw OHLC. No quote, spread, account
// or confluence-score fields belong here.
type TechnicalContextPayload struct {
	ATR              float64                      `json:"atr"`
	ReferencePrice   float64                      `json:"reference_price"`
	ReferenceTime    int64                        `json:"reference_time"`
	Bias             *BiasPayload                 `json:"bias,omitempty"`
	HigherTimeframes []HigherTimeframeBiasPayload `json:"higher_timeframes,omitempty"`
	Confirmation     *ReactionConfirmationPayload `json:"confirmation,omitempty"`
}

// ReactionConfirmationPayload describes a confirmed, separate zone-reaction
// opportunity. Its fields are never synthesized from the primary bias.
type ReactionConfirmationPayload struct {
	ZoneID              string `json:"zone_id"`
	TouchBarTime        int64  `json:"touch_bar_time"`
	ConfirmationBarTime int64  `json:"confirmation_bar_time"`
	ReactionType        string `json:"reaction_type"`
}

// HigherTimeframeBiasPayload is one fresh, causally closed H1/H4 structure.
type HigherTimeframeBiasPayload struct {
	Timeframe     string `json:"timeframe"`
	Direction     string `json:"direction"`
	Layer         string `json:"layer"`
	ReferenceTime int64  `json:"reference_time"`
}

// BiasPayload is the engine's confirmed structural bias; absent means none.
type BiasPayload struct {
	Direction string `json:"direction"`
	Layer     string `json:"layer"`
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

// ArbitrationDecisionPayload mirrors
// contracts/analysis/opportunity-arbitration-v1.schema.json. Republished
// whenever internal/arbitration.Decision changes for an opportunity_id —
// a time-varying relationship between live candidates, deliberately kept
// off the Created-event-happens-once analysis.opportunity.v1 envelope
// (see internal/engine's arbitration-publish wiring for why).
type ArbitrationDecisionPayload struct {
	OpportunityID   string   `json:"opportunity_id"`
	Symbol          string   `json:"symbol"`
	Status          string   `json:"status"`
	ReasonCode      string   `json:"reason_code"`
	ConflictingWith []string `json:"conflicting_with,omitempty"`
	// ThesisID/MergedWith: this opportunity's cross-strategy thesis
	// correlation (internal/arbitration.Decision's own fields) — present
	// only when this opportunity shares its real-world structural identity
	// with at least one other live candidate.
	ThesisID   string   `json:"thesis_id,omitempty"`
	MergedWith []string `json:"merged_with,omitempty"`
	DecidedAt  int64    `json:"decided_at"`
}
