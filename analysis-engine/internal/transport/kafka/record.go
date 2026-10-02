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
	// RecoveredAt is set only when the engine backfills a still-live
	// opportunity after bootstrap. It is an additive V1 fact: the technical
	// observation remains CreatedAt, while this field records when the
	// current engine instance re-established ownership of the live candidate.
	RecoveredAt int64 `json:"recovered_at,omitempty"`
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
// so it never recomputes ATR/structure from raw OHLC. No quote, spread, or
// account fields belong here. MAD affinity and confluence are permitted only
// as explicitly named soft technical telemetry and are not execution
// decisions.
type TechnicalContextPayload struct {
	ATR              float64                      `json:"atr"`
	ReferencePrice   float64                      `json:"reference_price"`
	ReferenceTime    int64                        `json:"reference_time"`
	Bias             *BiasPayload                 `json:"bias,omitempty"`
	HigherTimeframes []HigherTimeframeBiasPayload `json:"higher_timeframes,omitempty"`
	Confirmation     *ReactionConfirmationPayload `json:"confirmation,omitempty"`
	CandleEvidence   *CandleEvidencePayload       `json:"candle_evidence,omitempty"`
	MAD              *MADContextPayload           `json:"mad,omitempty"`
	Confluence       *ConfluenceContextPayload    `json:"confluence,omitempty"`
}

// ConfluenceContextPayload mirrors opportunity.ConfluenceContext. It carries
// the engine-owned factor score and its named inputs so the Python consumer
// can apply the selected value without rerunning legacy detectors.
type ConfluenceContextPayload struct {
	Version          string                   `json:"version"`
	SelectedStars    int                      `json:"selected_stars"`
	V1Stars          int                      `json:"v1_stars"`
	V2Stars          int                      `json:"v2_stars"`
	V2Raw            float64                  `json:"v2_raw"`
	RawFactorScore   float64                  `json:"raw_factor_score"`
	ZoneQualityScore float64                  `json:"zone_quality_score"`
	MADBonus         float64                  `json:"mad_bonus"`
	Factors          ConfluenceFactorsPayload `json:"factors"`
	FibLevel         *FibonacciLevelPayload   `json:"fib_level,omitempty"`
	GradeAGrab       *LiquidityGrabPayload    `json:"grade_a_grab,omitempty"`
}

type FibonacciLevelPayload struct {
	Ratio       float64 `json:"ratio"`
	Price       float64 `json:"price"`
	Kind        string  `json:"kind"`
	DistanceATR float64 `json:"distance_atr"`
}

type LiquidityGrabPayload struct {
	PoolID      string `json:"pool_id"`
	SweptAt     int64  `json:"swept_at"`
	ReclaimedAt int64  `json:"reclaimed_at"`
}

type ConfluenceFactorsPayload struct {
	HTFAligned          bool `json:"htf_aligned"`
	Touches             int  `json:"touches"`
	WickRejection       bool `json:"wick_rejection"`
	DisplacementGrade   bool `json:"displacement_grade"`
	SessionContext      bool `json:"session_context"`
	StructuralAgreement bool `json:"structural_agreement"`
	FibTouch            bool `json:"fib_touch"`
	CHoCH               bool `json:"choch"`
}

// MADContextPayload mirrors opportunity.MADContext. It is explicitly soft
// technical telemetry; consumers must not turn affinity into an execution
// gate or reconstruct the legacy Python detector from its absence.
type MADContextPayload struct {
	Version             int      `json:"version"`
	Phase               string   `json:"phase"`
	Confidence          float64  `json:"confidence"`
	Affinity            float64  `json:"affinity"`
	Direction           string   `json:"direction,omitempty"`
	SweepSide           string   `json:"sweep_side,omitempty"`
	Reclaim             bool     `json:"reclaim"`
	RangeQualityATR     *float64 `json:"range_quality_atr,omitempty"`
	BreakDistanceATR    *float64 `json:"break_distance_atr,omitempty"`
	DisplacementATR     *float64 `json:"displacement_atr,omitempty"`
	AcceptanceCloses    *int     `json:"acceptance_closes,omitempty"`
	SweepPenetrationATR *float64 `json:"sweep_penetration_atr,omitempty"`
	ReclaimDepthATR     *float64 `json:"reclaim_depth_atr,omitempty"`
	ReasonCode          string   `json:"reason_code"`
}

type CandleEvidencePayload struct {
	Version        int                        `json:"version"`
	Direction      string                     `json:"direction"`
	Rejection      *CandleRejectionPayload    `json:"rejection,omitempty"`
	Displacement   *CandleDisplacementPayload `json:"displacement,omitempty"`
	Sequence       *CandleSequencePayload     `json:"sequence,omitempty"`
	Indecision     *CandleIndecisionPayload   `json:"indecision,omitempty"`
	BaseScore      float64                    `json:"base_score"`
	SynergyBonus   float64                    `json:"synergy_bonus"`
	FinalScore     float64                    `json:"final_score"`
	PrimaryPattern string                     `json:"primary_pattern,omitempty"`
	AllPatterns    []string                   `json:"all_patterns,omitempty"`
}

type CandleRejectionPayload struct {
	Score               float64  `json:"score"`
	Patterns            []string `json:"patterns,omitempty"`
	WickFraction        float64  `json:"wick_fraction"`
	BodyFraction        float64  `json:"body_fraction"`
	CloseLocation       float64  `json:"close_location"`
	Sweep               bool     `json:"sweep"`
	SweepPenetrationATR *float64 `json:"sweep_penetration_atr,omitempty"`
	Reclaim             bool     `json:"reclaim"`
	ReclaimDepthATR     *float64 `json:"reclaim_depth_atr,omitempty"`
}

type CandleDisplacementPayload struct {
	Score            float64  `json:"score"`
	Patterns         []string `json:"patterns,omitempty"`
	BodyATR          float64  `json:"body_atr"`
	RangeATR         float64  `json:"range_atr"`
	BodyDominance    float64  `json:"body_dominance"`
	CloseLocation    float64  `json:"close_location"`
	Reclaim          bool     `json:"reclaim"`
	ReclaimDepthATR  *float64 `json:"reclaim_depth_atr,omitempty"`
	Engulfing        bool     `json:"engulfing"`
	EngulfingQuality *float64 `json:"engulfing_quality,omitempty"`
}

type CandleSequencePayload struct {
	Score    float64  `json:"score"`
	Patterns []string `json:"patterns,omitempty"`
	Bars     int      `json:"bars"`
}

type CandleIndecisionPayload struct {
	Doji             bool    `json:"doji"`
	SpinningTop      bool    `json:"spinning_top"`
	InsideBar        bool    `json:"inside_bar"`
	BodyFraction     float64 `json:"body_fraction"`
	CompressionScore float64 `json:"compression_score"`
}

// ReactionConfirmationPayload describes a confirmed, separate zone-reaction
// opportunity. Its fields are never synthesized from the primary bias.
type ReactionConfirmationPayload struct {
	ZoneID              string `json:"zone_id"`
	TouchBarTime        int64  `json:"touch_bar_time"`
	ConfirmationBarTime int64  `json:"confirmation_bar_time"`
	ReactionType        string `json:"reaction_type"`
	// Pattern is the legacy confirmation pattern; omitted when the
	// confirmation has no named pattern.
	Pattern string `json:"pattern,omitempty"`
}

// HigherTimeframeBiasPayload is one fresh, causally closed M15/H1/H4 structure.
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
