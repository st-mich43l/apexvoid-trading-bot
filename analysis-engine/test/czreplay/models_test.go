package czreplay_test

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Decision is one model's confirmed opportunity at one evaluated candle.
type Decision struct {
	Model       string  `json:"model"`
	Symbol      string  `json:"symbol"`
	Direction   string  `json:"direction"`
	EvalTime    int64   `json:"eval_time"`
	TouchTime   int64   `json:"touch_time"`
	ConfirmTime int64   `json:"confirm_time"`
	EntryLow    float64 `json:"entry_low"`
	EntryHigh   float64 `json:"entry_high"`
	Stop        float64 `json:"stop"`
	Target      float64 `json:"target"`
	TargetKind  string  `json:"target_kind,omitempty"`
	Pattern     string  `json:"pattern,omitempty"`
	Stars       int     `json:"stars,omitempty"`
	ATR         float64 `json:"atr,omitempty"`
	RiskPips    float64 `json:"risk_pips"`
	StopATR     float64 `json:"stop_atr,omitempty"`
	RewardRisk  float64 `json:"reward_risk"`
	// Executable is whether the Algo Bot could place it: its risk is within the
	// instrument's execution stop cap (a plan beyond it is refused).
	Executable bool   `json:"executable"`
	Key        string `json:"key"`

	// Structural diagnostics of the frozen models' members.
	Independent       int     `json:"independent_sources,omitempty"`
	LateMember        bool    `json:"member_not_confirmable_before_touch,omitempty"`
	SingleSource      bool    `json:"single_independent_source,omitempty"`
	UnionOverOverlap  float64 `json:"union_over_overlap,omitempty"`
	OppositeColourSet bool    `json:"opposite_colour_confirmation,omitempty"`
	// FailedAtPublication: a candle after the confirming one had already closed
	// beyond the confirming candle's far extreme when the decision was published.
	FailedAtPublication bool `json:"reaction_already_failed_at_publication,omitempty"`

	Single Outcome `json:"single_target"`
	Ladder Outcome `json:"ladder"`
}

func (d *Decision) proximal() float64 {
	if d.Direction == "SELL" {
		return d.EntryLow
	}
	return d.EntryHigh
}

func fillGeometry(d *Decision, pip float64) {
	risk := d.proximal() - d.Stop
	reward := d.Target - d.proximal()
	if d.Direction == "SELL" {
		risk, reward = d.Stop-d.proximal(), d.proximal()-d.Target
	}
	d.RiskPips = risk / pip
	if d.ATR > 0 {
		d.StopATR = risk / d.ATR
	}
	if risk > 0 {
		d.RewardRisk = reward / risk
	}
}

// frozenSpec is Confluence Zone v2's published contract (the constants of the
// frozen technique publisher), kept here only to rebuild Model B's opportunity.
func frozenSpec(fingerprint string) strategyutil.TechniqueSpec {
	return strategyutil.TechniqueSpec{
		ID: "confluence_zone", Version: "v2", ZoneEvidence: "m5_distinct_zone_overlap", ConfirmedEvidence: "m5_confluence_reaction",
		InvalidationLabel: "confluence_overlap_failed", InvalidationBufferATR: 0.5, MinimumTargetDistanceATR: 1.0, ExpiryHours: 12, Fingerprint: fingerprint,
	}
}

func lastBarTime(src *strategyutil.TechniqueSource) int64 {
	bars := src.Detector.Frame.Bars
	return bars[len(bars)-1].Time
}

func framesOf(src *strategyutil.TechniqueSource) structuralconfluence.Frame {
	f := src.Detector.Frame
	return structuralconfluence.Frame{Bars: f.Bars, Legs: f.Legs, Breaks: f.Breaks, Swings: f.Swings, PeriodSeconds: 300}
}

func directionName(d market.Direction) string { return string(d) }

// modelB is the Python-parity v2 decision exactly as production published it: the
// best confirmed band of the zone chain's instances (the union of the members,
// confirmed against the union, v2 stop and target), with the structural facts of
// its members measured for the defect statistics.
func modelB(ctx *analysiscontext.MarketContext, legacy strategyutil.LegacyDetectorSettings, fingerprint string, pip float64) *Decision {
	src, ok := strategyutil.NewTechniqueSource(ctx, market.M5, legacy)
	if !ok {
		return nil
	}
	dec := src.ConfluenceZone()
	if dec == nil || dec.Band == nil || dec.Confirmation == nil {
		return nil
	}
	cand, ok := strategyutil.TechniqueCandidate(ctx, dec, frozenSpec(fingerprint))
	if !ok {
		return nil
	}
	d := &Decision{
		Model: "B", Direction: directionName(dec.Direction), EvalTime: lastBarTime(src),
		TouchTime: dec.Confirmation.TouchTime, ConfirmTime: dec.Confirmation.ConfirmationTime,
		EntryLow: cand.Entry.Low, EntryHigh: cand.Entry.High, Stop: float64(cand.Invalidation.Price), Target: float64(cand.Targets[0].Price.Price),
		TargetKind: cand.Targets[0].Price.Label, Pattern: dec.Confirmation.Type, Stars: dec.Result.Stars, ATR: src.Detector.ATR,
	}
	d.Key = fmt.Sprintf("B|%s|%d|%d|%.4f", d.Direction, d.ConfirmTime, d.TouchTime, d.EntryLow)
	var members []structuralconfluence.Fact
	frame := framesOf(src)
	for _, m := range dec.Band.Members {
		members = append(members, structuralconfluence.DeriveFacts(frame, []techniquezone.Instance{src.Instances[m]})...)
	}
	for _, f := range members {
		if f.AvailableAt > dec.Confirmation.TouchTime {
			d.LateMember = true
		}
	}
	d.Independent = structuralconfluence.IndependentCount(members)
	d.SingleSource = d.Independent < 2
	side := structuralconfluence.Buy
	if dec.Direction == market.Sell {
		side = structuralconfluence.Sell
	}
	regions, _ := structuralconfluence.Find(members, side, structuralconfluence.NoCutoff, structuralconfluence.Params{MinimumFacts: 2})
	widest := 0.0
	for _, r := range regions {
		widest = math.Max(widest, r.Width())
	}
	if widest > 0 {
		d.UnionOverOverlap = (dec.Band.High - dec.Band.Low) / widest
	}
	// The grab branch of the frozen confirmation ignores the candle's colour.
	for _, b := range src.Detector.Frame.Bars {
		if b.Time == dec.Confirmation.ConfirmationTime && dec.Confirmation.Type == "sweep_reclaim" {
			bullish := b.Close > b.Open
			d.OppositeColourSet = (dec.Direction == market.Sell && bullish) || (dec.Direction == market.Buy && !bullish)
		}
	}
	fillGeometry(d, pip)
	return d
}

// modelC keeps v2's confirmation, stop and target but takes its zone from the v3
// structural engine: the independent-source region (never the union), with only
// structures confirmable before the touch. It isolates what the structure
// (independence, intersection, causality) changes on its own.
func modelC(ctx *analysiscontext.MarketContext, legacy strategyutil.LegacyDetectorSettings, fingerprint string, pip float64, minOverlapPips, minOverlapATR float64) (decisions []*Decision, causalityRejected int) {
	src, ok := strategyutil.NewTechniqueSource(ctx, market.M5, legacy)
	if !ok {
		return nil, 0
	}
	base := src.Detector
	facts := structuralconfluence.DeriveFacts(framesOf(src), src.Instances)
	params := structuralconfluence.Params{MinimumFacts: 2, MinimumWidth: math.Max(minOverlapPips*pip, minOverlapATR*base.ATR)}
	byID := map[string]structuralconfluence.Fact{}
	for _, f := range facts {
		byID[f.ID] = f
	}
	for _, side := range []string{structuralconfluence.Buy, structuralconfluence.Sell} {
		regions, _ := structuralconfluence.Find(facts, side, structuralconfluence.NoCutoff, params)
		direction, zoneSide := market.Buy, "demand"
		if side == structuralconfluence.Sell {
			direction, zoneSide = market.Sell, "supply"
		}
		d := base.WithDirection(direction)
		var best *strategyutil.TechniqueDecision
		var bestRegion structuralconfluence.Region
		for _, r := range regions {
			var support []structuralconfluence.Fact
			outLow, outHigh, score := math.Inf(1), math.Inf(-1), 0.0
			touches := 0
			for _, id := range r.Support {
				f := byID[id]
				support = append(support, f)
				outLow, outHigh = math.Min(outLow, f.Low), math.Max(outHigh, f.High)
				score = math.Max(score, f.Score)
				if f.Touches > touches {
					touches = f.Touches
				}
			}
			score += 2.5 * float64(len(r.Groups)-1)
			zone := techniquezone.Zone{Bottom: r.Low, Top: r.High, Side: zoneSide, Source: "confluence", BreakIndex: -1, Score: score, Touches: touches}
			if !d.EntryValid(zone) {
				continue
			}
			conf := d.Reaction(r.Low, r.High, d.ZoneGrab(zone))
			if conf == nil {
				continue
			}
			late := false
			for _, f := range support {
				if f.AvailableAt > conf.TouchTime {
					late = true
				}
			}
			if late {
				causalityRejected++
				continue
			}
			factors := strategyutil.FactorsForConfirmation(constantFactors(d, touches, score), conf.Type)
			low, high := outLow, outHigh
			result := d.Finish(d.ZoneKey(zone), zone, factors, "", &low, &high)
			if result == nil {
				continue
			}
			dec := &strategyutil.TechniqueDecision{Technique: "confluence_zone", Direction: direction, Detector: d, Confirmation: conf, Result: result, ID: r.EpisodeID}
			if best == nil || dec.Result.Stars > best.Result.Stars {
				best, bestRegion = dec, r
			}
		}
		if best == nil {
			continue
		}
		cand, ok := strategyutil.TechniqueCandidate(ctx, best, frozenSpec(fingerprint))
		if !ok {
			continue
		}
		c := &Decision{
			Model: "C", Direction: directionName(direction), EvalTime: lastBarTime(src),
			TouchTime: best.Confirmation.TouchTime, ConfirmTime: best.Confirmation.ConfirmationTime,
			EntryLow: cand.Entry.Low, EntryHigh: cand.Entry.High, Stop: float64(cand.Invalidation.Price), Target: float64(cand.Targets[0].Price.Price),
			TargetKind: cand.Targets[0].Price.Label, Pattern: best.Confirmation.Type, Stars: best.Result.Stars, ATR: base.ATR,
			Independent: bestRegion.MinGroups,
		}
		c.Key = fmt.Sprintf("C|%s|%d|%s", c.Direction, c.ConfirmTime, bestRegion.EpisodeID)
		fillGeometry(c, pip)
		decisions = append(decisions, c)
	}
	return decisions, causalityRejected
}

// constantFactors are the v2 factors: two of them hard-coded true.
func constantFactors(d *strategyutil.LegacyDetector, touches int, score float64) (f confluence.Factors) {
	f.HTFAligned, f.Touches, f.WickRejection, f.StructuralAgreement = d.HTFAligned(), touches, true, true
	f.DisplacementGrade = score >= 8.0
	return f
}
