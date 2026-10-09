package crtreplay_test

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// frozenV2 is CRT v2 exactly as it shipped: the frozen Python publisher's
// decision (strategyutil.ConfirmedTechnique over the technique instances that
// techniquezone.DiscoverCRT/CollectCRT build) with the v2 stop and target
// rules. It is kept here, outside production, only so the replay certification
// can run the old and the new logic over the SAME contexts and report the
// difference. Do not import it from production code.
type frozenV2 struct {
	legacy                       strategyutil.LegacyDetectorSettings
	invalidationATR, expiryHours float64
	fingerprint                  string
}

func newFrozenV2(params map[string]any) (*frozenV2, error) {
	legacy, err := strategyutil.ParseLegacyDetectorSettings(params)
	if err != nil {
		return nil, err
	}
	// v2 shipped these two values (config/analysis.yml before v3).
	return &frozenV2{legacy: legacy, invalidationATR: 0.25, expiryHours: 8.0, fingerprint: "frozen-v2"}, nil
}

func (f *frozenV2) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	return strategyutil.ConfirmedTechnique(ctx, f.legacy, strategyutil.TechniqueCRT, "", strategyutil.TechniqueSpec{
		ID: "crt", Version: "v2", ZoneEvidence: "h1_impulse_range", ConfirmedEvidence: "m5_range_sweep_reclaim",
		InvalidationLabel: "crt_reclaim_failed", InvalidationBufferATR: f.invalidationATR, ExpiryHours: f.expiryHours, Fingerprint: f.fingerprint,
		Target: func(dec *strategyutil.TechniqueDecision) (float64, string) {
			if dec.Direction == "SELL" {
				return dec.Instance.StructuralLow, "opposite_h1_range"
			}
			return dec.Instance.StructuralHigh, "opposite_h1_range"
		},
	})
}
