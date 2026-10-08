package strategyutil

import (
	"fmt"
	"math"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// TechniqueSpec is what one zone strategy contributes to a confirmed
// technique opportunity: its identity, evidence vocabulary and stop/target
// parameters. The decision itself (whether, which side, which band) is the
// frozen publisher's, never the spec's.
type TechniqueSpec struct {
	ID, Version string
	// Evidence is the strategy's reviewed evidence vocabulary: Zone is the
	// structure code, Confirmed the confirmed-reaction code.
	ZoneEvidence, ConfirmedEvidence string
	InvalidationLabel               string
	InvalidationBufferATR           float64
	MinimumTargetDistanceATR        float64
	ExpiryHours                     float64
	Fingerprint                     string
	// Target optionally names a structural target of the decision (CRT's
	// opposite H1 extreme). Without it the target is the nearest opposing
	// liquidity pool, or a fixed reward:risk beyond the entry when none is far
	// enough.
	Target   func(*TechniqueDecision) (price float64, label string)
	Versions opportunity.AnalysisProvenance
}

// techniqueFallbackTargetR is the nominal reward:risk of an opportunity with no
// opposing liquidity to aim at; the execution policy owns the real ladder.
const techniqueFallbackTargetR = 2.0

// TechniqueCandidate builds the confirmed opportunity for a frozen publisher
// decision: the published (proximal) entry band, the structural stop beyond the
// whole structure, the nearest opposing liquidity as the nominal target, and the
// decision's own reaction and confluence as evidence.
func TechniqueCandidate(ctx *analysiscontext.MarketContext, dec *TechniqueDecision, spec TechniqueSpec) (opportunity.Candidate, bool) {
	if dec == nil {
		return opportunity.Candidate{}, false
	}
	d := dec.Detector
	atr := d.ATR
	low, high := dec.EntryLow(), dec.EntryHigh()
	structLow, structHigh := dec.Result.StructuralLow, dec.Result.StructuralHigh
	buffer := spec.InvalidationBufferATR * atr
	invalidation, reference := structLow-buffer, high
	if dec.Direction == market.Sell {
		invalidation, reference = structHigh+buffer, low
	}
	var target float64
	var targetLabel string
	if spec.Target != nil {
		target, targetLabel = spec.Target(dec)
	} else {
		var ok bool
		if target, targetLabel, ok = nearestOpposingTarget(d, dec.Direction, reference, invalidation, spec.MinimumTargetDistanceATR*atr); !ok {
			return opportunity.Candidate{}, false
		}
	}
	conf := dec.Confirmation
	setupKey := fmt.Sprintf("zone:%s:rejection:%d:%d", dec.ID, conf.TouchTime, conf.ConfirmationTime)
	stars := float64(dec.Result.Stars)
	candidate, err := Candidate(CandidateSpec{
		ID: spec.ID, Version: spec.Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: dec.Direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalidation, InvalidationLabel: spec.InvalidationLabel,
		Target: target, TargetLabel: targetLabel, Evidence: techniqueEvidence(dec, spec),
		Quality: opportunity.StrategyQuality{
			Overall:    Clamp01(stars / 3),
			Components: map[string]float64{"confluence": Clamp01(stars / 3), "reaction": 1},
		},
		FormedAt: conf.TouchTime, ConfirmedAt: conf.ConfirmationTime, ExpiryHours: spec.ExpiryHours, Fingerprint: spec.Fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the structure, not this
	// confirmation: a re-confirmation of the same structure is the same thesis.
	candidate.StructuralID = dec.ID
	if dec.Instance != nil && dec.Instance.Timeframe != "" {
		candidate.StructureTimeframe = market.Timeframe(dec.Instance.Timeframe)
	}
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: dec.ID, TouchBarTime: conf.TouchTime, ConfirmationBarTime: conf.ConfirmationTime,
		ReactionType: "rejection", Pattern: conf.Type,
	}
	candidate.DetectorConfluence = dec.Result.ConfluenceContext()
	candidate.Provenance = spec.Versions
	if candidate.Provenance.StructureVersion == "" {
		candidate.Provenance = opportunity.AnalysisProvenance{
			StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: spec.Fingerprint,
		}
	}
	return candidate, true
}

// nearestOpposingTarget is the nearest unswept opposing liquidity at least the
// minimum distance from the entry; without one, a fixed reward:risk beyond it.
// Whether the room is enough is the execution policy's decision, not a filter.
func nearestOpposingTarget(d *LegacyDetector, direction market.Direction, reference, invalidation, minimum float64) (float64, string, bool) {
	want := "buy"
	if direction == market.Sell {
		want = "sell"
	}
	best, bestDistance := 0.0, math.Inf(1)
	for _, pool := range d.Frame.Pools {
		if pool.Side != want {
			continue
		}
		distance := pool.Level - reference
		if direction == market.Sell {
			distance = reference - pool.Level
		}
		if distance >= minimum && distance < bestDistance {
			best, bestDistance = pool.Level, distance
		}
	}
	if !math.IsInf(bestDistance, 1) {
		return best, "nearest_opposing_liquidity", true
	}
	risk := math.Abs(reference - invalidation)
	if risk <= 0 {
		return 0, "", false
	}
	if direction == market.Sell {
		return reference - techniqueFallbackTargetR*risk, "fixed_reward_risk", true
	}
	return reference + techniqueFallbackTargetR*risk, "fixed_reward_risk", true
}

// techniqueEvidence is the zone and confirmation evidence, plus the higher
// timeframe the zone was built on when it is not the execution frame's.
func techniqueEvidence(dec *TechniqueDecision, spec TechniqueSpec) []string {
	evidence := []string{spec.ZoneEvidence, spec.ConfirmedEvidence}
	if dec.Instance != nil && dec.Instance.Timeframe != "" {
		evidence = append(evidence, "htf_zone_"+strings.ToLower(dec.Instance.Timeframe))
	}
	return evidence
}
