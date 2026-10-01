package kafka

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"

// OpportunityPayloadFromCandidate is the single producer-side adapter from
// the transport-independent strategy domain into the neutral Kafka event.
func OpportunityPayloadFromCandidate(c opportunity.Candidate, algo AlgorithmVersion) OpportunityPayload {
	return opportunityPayloadFromCandidate(c, algo, 0)
}

// OpportunityPayloadFromRecoveredCandidate is the explicit post-bootstrap
// lifecycle-recovery form. It never changes the candidate's technical
// CreatedAt; RecoveredAt is a separate transport fact consumed only by the
// recovery-safe Algo Bot path.
func OpportunityPayloadFromRecoveredCandidate(c opportunity.Candidate, algo AlgorithmVersion, recoveredAt int64) OpportunityPayload {
	return opportunityPayloadFromCandidate(c, algo, recoveredAt)
}

func opportunityPayloadFromCandidate(c opportunity.Candidate, algo AlgorithmVersion, recoveredAt int64) OpportunityPayload {
	targets := make([]TargetPayload, len(c.Targets))
	for i, t := range c.Targets {
		targets[i] = TargetPayload{Price: PriceLevelPayload{Price: float64(t.Price.Price), Label: t.Price.Label}}
	}
	evidence := make([]EvidencePayload, len(c.Evidence))
	for i, e := range c.Evidence {
		evidence[i] = EvidencePayload{Code: e.Code}
	}
	var technical *TechnicalContextPayload
	if c.Technical != nil {
		technical = &TechnicalContextPayload{
			ATR: c.Technical.ATR, ReferencePrice: c.Technical.ReferencePrice, ReferenceTime: c.Technical.ReferenceTime,
		}
		if c.Technical.BiasDirection.IsValid() {
			technical.Bias = &BiasPayload{Direction: string(c.Technical.BiasDirection), Layer: c.Technical.BiasLayer}
		}
		if c.Technical.Confirmation != nil {
			r := c.Technical.Confirmation
			technical.Confirmation = &ReactionConfirmationPayload{
				ZoneID: r.ZoneID, TouchBarTime: r.TouchBarTime, ConfirmationBarTime: r.ConfirmationBarTime, ReactionType: r.ReactionType, Pattern: r.Pattern,
			}
		}
		for _, higher := range c.Technical.HigherTimeframes {
			technical.HigherTimeframes = append(technical.HigherTimeframes, HigherTimeframeBiasPayload{
				Timeframe: string(higher.Timeframe), Direction: string(higher.Direction), Layer: higher.Layer, ReferenceTime: higher.ReferenceTime,
			})
		}
	}
	var stopEnvelope *StopEnvelopePayload
	if c.StopEnvelope != nil {
		stopEnvelope = &StopEnvelopePayload{
			FloorPips: c.StopEnvelope.FloorPips, CapPips: c.StopEnvelope.CapPips,
			DesiredMinimumPips: c.StopEnvelope.DesiredMinimumPips, Source: c.StopEnvelope.Source,
		}
	}
	return OpportunityPayload{
		TechnicalContext: technical, StopEnvelope: stopEnvelope,
		ID: c.ID, Strategy: string(c.Strategy), Symbol: string(c.Symbol), Timeframe: string(c.ObservedTimeframe), Direction: string(c.Direction),
		Entry:        EntryZonePayload{Low: c.Entry.Low, High: c.Entry.High},
		Invalidation: PriceLevelPayload{Price: float64(c.Invalidation.Price), Label: c.Invalidation.Label},
		Targets:      targets, Evidence: evidence,
		Quality:          QualityPayload{Overall: c.Quality.Overall, Components: c.Quality.Components},
		AlgorithmVersion: AlgorithmVersionPayload{Structure: algo.Structure, Liquidity: algo.Liquidity},
		FormedAt:         c.FormedAt, CreatedAt: c.CreatedAt, ExpiresAt: c.ExpiresAt, RecoveredAt: recoveredAt,
	}
}
