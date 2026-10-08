// Package boxbreakout publishes the frozen box_breakout decision: an accepted
// break of the consolidation the regime read found (a displacement close, or
// consecutive closes beyond its edge), entered either on the accepting bar itself
// when it is a displacement bar still close to the edge, or on a retest of the
// broken edge that closes back as a rejection.
//
// It does not duplicate the M1 Breakout Retest Scalp: this is the M5 detector,
// judged on the frame's regime box, and independently rollout-controlled.
package boxbreakout

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "box_breakout"
const Version = "v3"

const (
	// displacementBodyFraction/displacementRangeATR are regime.py's
	// DISPLACEMENT_BODY_FRAC and DISPLACEMENT_RANGE_ATR; reactionMaxATR is
	// detectors.REACTION_MAX_ATR.
	displacementBodyFraction = 0.6
	displacementRangeATR     = 1.0
	reactionMaxATR           = 1.0
	epsilon                  = 1e-9
)

type Strategy struct {
	invalidationATR, expiryHours float64
	maxAgeBars                   int
	legacy                       strategyutil.LegacyDetectorSettings
	fingerprint                  string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("boxbreakout: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	var err error
	for key, dst := range map[string]*float64{"invalidation_buffer_atr": &s.invalidationATR, "expiry_hours": &s.expiryHours} {
		if *dst, err = strategyutil.Float(c.Parameters, key); err != nil {
			return nil, err
		}
	}
	if s.maxAgeBars, err = strategyutil.Int(c.Parameters, "breakout_max_age_bars"); err != nil {
		return nil, err
	}
	if s.legacy, err = strategyutil.ParseLegacyDetectorSettings(c.Parameters); err != nil {
		return nil, err
	}
	if s.invalidationATR <= 0 || s.expiryHours <= 0 || s.maxAgeBars < 0 {
		return nil, fmt.Errorf("boxbreakout: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.legacy)
	if !ok {
		return nil
	}
	box := base.Frame.Regime.BoxBreak
	if box == nil {
		return nil
	}
	direction := ctx.Legacy.Direction()
	if direction == "" || (direction == market.Buy) != (box.Direction == "up") {
		return nil
	}
	bars := base.Frame.Bars
	last := len(bars) - 1
	if age := last - box.AcceptBar; age < 0 || age > s.maxAgeBars {
		return nil
	}
	d := base.WithDirection(direction)
	edge, level := box.BoxHigh, box.BoxLow
	if direction == market.Sell {
		edge, level = box.BoxLow, box.BoxHigh
	}
	kind := entryKind(d, box.AcceptBar, box.Direction, edge)
	if kind == "" {
		return nil
	}
	band := math.Max(epsilon, d.Settings.ProximalBandATR*math.Max(0, d.ATR))
	side := "demand"
	if direction == market.Sell {
		side = "supply"
	}
	zone := techniquezone.Zone{Bottom: edge - band, Top: edge + band, Side: side, OriginIndex: box.AcceptBar, Source: "box_breakout", BreakIndex: -1}
	factors := confluenceFactors(d, kind == "retest", len(d.Frame.Sessions) > 0)
	low, high := zone.Low(), zone.High()
	result := d.Finish(level, zone, factors, kind, &low, &high)
	if result == nil {
		return nil
	}
	measured := box.BoxHigh - box.BoxLow
	target := edge + measured
	if direction == market.Sell {
		target = edge - measured
	}
	dec := &strategyutil.TechniqueDecision{
		Technique: "box_breakout", Direction: direction, Detector: d, Result: result,
		ID:       fmt.Sprintf("box:%d:%.5f:%.5f:%s", bars[box.AcceptBar].Time, box.BoxLow, box.BoxHigh, box.Direction),
		FormedAt: bars[box.AcceptBar].Time, ConfirmedAt: bars[last].Time,
	}
	candidate, ok := strategyutil.TechniqueCandidate(ctx, dec, strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, ZoneEvidence: "m5_box_compression", ConfirmedEvidence: "m5_breakout_accepted",
		InvalidationLabel: "box_reentry", InvalidationBufferATR: s.invalidationATR, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		Target: func(*strategyutil.TechniqueDecision) (float64, string) { return target, "box_measured_move" },
	})
	if !ok {
		return nil
	}
	if kind == "retest" {
		candidate.Evidence = append(candidate.Evidence, opportunity.Evidence{Code: "m5_box_retest"})
	}
	return []opportunity.Candidate{candidate}
}

// entryKind mirrors _box_entry_kind: "retest" when the latest bar is the first
// retest of the broken edge after the accepting bar and rejects; "proximal" when
// the latest bar is the accepting bar, a displacement bar within reach of the
// edge; otherwise no entry.
func entryKind(d *strategyutil.LegacyDetector, acceptBar int, boxDirection string, edge float64) string {
	bars := d.Frame.Bars
	current := len(bars) - 1
	want := "retest_support"
	if d.Direction == market.Sell {
		want = "retest_resistance"
	}
	if retest, found := techniquezone.FindRetest(bars, edge, 1, d.Settings.PipSize); found &&
		retest.Source == want && retest.OriginIndex == current && current > acceptBar && d.Rejection() {
		return "retest"
	}
	if current != acceptBar {
		return ""
	}
	bar := bars[current]
	span := bar.High - bar.Low
	directional := bar.Close > bar.Open
	if boxDirection != "up" {
		directional = bar.Close < bar.Open
	}
	if !(span > 0 && d.ATR > 0 && directional && math.Abs(bar.Close-bar.Open) >= displacementBodyFraction*span && span >= displacementRangeATR*d.ATR) {
		return ""
	}
	if math.Abs(d.Price-edge) > reactionMaxATR*d.ATR+epsilon {
		return ""
	}
	return "proximal"
}

func confluenceFactors(d *strategyutil.LegacyDetector, wick, sessionContext bool) confluence.Factors {
	return confluence.Factors{
		HTFAligned: d.HTFAligned(), WickRejection: wick, DisplacementGrade: true, StructuralAgreement: true, SessionContext: sessionContext,
	}
}
