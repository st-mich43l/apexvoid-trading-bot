// Package breakretest restores the frozen Python Break & Retest thesis as an
// independent strategy: after a trendline or a key level breaks, price retests
// it from the broken side and the current bar rejects it. The trendline path
// has priority, then the key-level path (nearest level first); the first valid
// result wins. The decision runs on the engine's detector-contract frame so
// direction, premium/discount, chop, retest and confluence qualification are
// the frozen detector's.
package breakretest

import (
	"fmt"
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	technicaltrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

const ID strategy.StrategyID = "break_retest"
const Version = "v2"

// trendlineRetestEpsilon is the frozen detector's own comparison slack.
const trendlineRetestEpsilon = 1e-9

type Strategy struct {
	breakoutAcceptBars                         int
	minimumBodyFraction, trendlineToleranceATR float64
	invalidationATR, targetR, expiryHours      float64
	strictPD                                   bool
	detector                                   strategyutil.LegacyDetectorSettings
	fingerprint                                string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("breakretest: wrong strategy ID %q", cfg.ID)
	}
	accept, err := strategyutil.Int(cfg.Parameters, "breakout_accept_bars")
	if err != nil {
		return nil, err
	}
	body, err := strategyutil.Float(cfg.Parameters, "momentum_body_fraction")
	if err != nil {
		return nil, err
	}
	tolerance, err := strategyutil.Float(cfg.Parameters, "trendline_tolerance_atr")
	if err != nil {
		return nil, err
	}
	invalid, err := strategyutil.Float(cfg.Parameters, "invalidation_buffer_atr")
	if err != nil {
		return nil, err
	}
	targetR, err := strategyutil.Float(cfg.Parameters, "target_r")
	if err != nil {
		return nil, err
	}
	expiry, err := strategyutil.Float(cfg.Parameters, "expiry_hours")
	if err != nil {
		return nil, err
	}
	strictPD, ok := cfg.Parameters["strict_premium_discount"].(bool)
	if !ok {
		return nil, fmt.Errorf("strict_premium_discount must be boolean")
	}
	detector, err := strategyutil.ParseLegacyDetectorSettings(cfg.Parameters)
	if err != nil {
		return nil, err
	}
	if accept < 1 || body <= 0 || body > 1 || tolerance <= 0 || invalid <= 0 || targetR <= 0 || expiry <= 0 {
		return nil, fmt.Errorf("breakretest: invalid parameters")
	}
	return &Strategy{
		breakoutAcceptBars: accept, minimumBodyFraction: body, trendlineToleranceATR: tolerance,
		invalidationATR: invalid, targetR: targetR, expiryHours: expiry, strictPD: strictPD, detector: detector,
		fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters),
	}, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	if tf == nil || strategyutil.InChop(tf) {
		return nil
	}
	direction := strategyutil.StructuralDirection(ctx)
	if !direction.IsValid() || !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) {
		return nil
	}
	d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, direction, s.detector)
	if !ok || !d.Rejection() {
		return nil
	}
	bars := d.Frame.Bars
	last := len(bars) - 1

	// Trendline path first: the line nearest to price, broken in the trade's
	// favour (a broken resistance for a buy, a broken support for a sell).
	lines := append([]technicaltrendline.Trendline(nil), d.Frame.Trendlines...)
	sort.SliceStable(lines, func(i, j int) bool {
		return math.Abs(float64(technicaltrendline.ValueAt(lines[i], last))-d.Price) < math.Abs(float64(technicaltrendline.ValueAt(lines[j], last))-d.Price)
	})
	for _, line := range lines {
		breakIndex, broken := brokenIndex(bars, line, direction)
		if !broken {
			continue
		}
		level := float64(technicaltrendline.ValueAt(line, last))
		zone, ok := s.trendlineRetestZone(d, level, breakIndex)
		if !ok {
			continue
		}
		factors := confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: 2 + len(line.ValidationTouches), WickRejection: true,
			DisplacementGrade: true, StructuralAgreement: true,
		}
		structuralLow, structuralHigh := zone.Low(), zone.High()
		result := d.Finish(level, zone, factors, line.Kind.String(), &structuralLow, &structuralHigh)
		if result == nil {
			continue
		}
		anchor := int64(0)
		if line.AnchorAIndex >= 0 && line.AnchorAIndex < len(bars) {
			anchor = bars[line.AnchorAIndex].Time
		}
		second := int64(0)
		if line.AnchorBIndex >= 0 && line.AnchorBIndex < len(bars) {
			second = bars[line.AnchorBIndex].Time
		}
		candidate, err := s.candidate(ctx, d, result, fmt.Sprintf("trendline:%s:%d:%d", line.Kind, anchor, second),
			[]string{"m5_trendline_break", "m5_trendline_retest", "m5_retest_holds", "m5_retest_rejection", "htf_aligned", "structural_agreement"}, anchor)
		if err == nil {
			return []opportunity.Candidate{candidate}
		}
	}

	for _, level := range d.LevelsByDistance() {
		if !d.LevelValid(level.Price) {
			continue
		}
		zone, ok := techniquezone.FindRetest(bars, level.Price, s.breakoutAcceptBars, d.Settings.PipSize)
		if !ok {
			continue
		}
		if direction == market.Buy && zone.Source != "retest_support" || direction == market.Sell && zone.Source != "retest_resistance" {
			continue
		}
		bodyBreak := d.StrongBodyBreak(s.minimumBodyFraction)
		factors := confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: level.Touches, WickRejection: true,
			DisplacementGrade: bodyBreak, StructuralAgreement: true,
		}
		structuralLow, structuralHigh := zone.Low(), zone.High()
		result := d.Finish(level.Price, zone, factors, level.Kind, &structuralLow, &structuralHigh)
		if result == nil {
			continue
		}
		evidence := []string{"m5_key_level_break", "m5_key_level_retest", "m5_retest_holds", "m5_retest_rejection", "htf_aligned", "structural_agreement"}
		if bodyBreak {
			evidence = append(evidence, "displacement_grade")
		}
		formedAt := bars[zone.OriginIndex].Time
		candidate, err := s.candidate(ctx, d, result, strategyutil.LevelID(level), evidence, formedAt)
		if err == nil {
			return []opportunity.Candidate{candidate}
		}
	}
	return nil
}

// brokenIndex mirrors _trendline_break_direction: the line must be broken
// (with a known break bar) and of the kind a break in the trade's favour
// leaves behind.
func brokenIndex(bars []market.Candle, line technicaltrendline.Trendline, direction market.Direction) (int, bool) {
	if line.BrokenAt == nil {
		return -1, false
	}
	if direction == market.Buy && line.Kind != technicaltrendline.KindResistance || direction == market.Sell && line.Kind != technicaltrendline.KindSupport {
		return -1, false
	}
	for i, bar := range bars {
		if bar.Time == *line.BrokenAt {
			return i, true
		}
	}
	return -1, false
}

// trendlineRetestZone mirrors _trendline_retest_zone: the latest bar touches
// the line within tolerance and closes on the trade's side of it.
func (s *Strategy) trendlineRetestZone(d *strategyutil.LegacyDetector, level float64, breakIndex int) (techniquezone.Zone, bool) {
	bars := d.Frame.Bars
	index := len(bars) - 1
	if index <= breakIndex {
		return techniquezone.Zone{}, false
	}
	tolerance := math.Max(trendlineRetestEpsilon, math.Max(0, s.trendlineToleranceATR)*d.ATR)
	row := bars[index]
	touched := row.Low <= level+tolerance && row.High >= level-tolerance
	held := row.Close <= level
	if d.Direction == market.Buy {
		held = row.Close >= level
	}
	if !touched || !held {
		return techniquezone.Zone{}, false
	}
	side := "supply"
	if d.Direction == market.Buy {
		side = "demand"
	}
	return techniquezone.Zone{Bottom: level - tolerance, Top: level + tolerance, Side: side, Source: "trendline", Sources: []string{"trendline"}, OriginIndex: -1, BreakIndex: -1}, true
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, d *strategyutil.LegacyDetector, result *strategyutil.LegacyResult, setup string, evidence []string, formedAt int64) (opportunity.Candidate, error) {
	low, high := result.Zone.Low(), result.Zone.High()
	bars := d.Frame.Bars
	last := bars[len(bars)-1]
	invalid := low - s.invalidationATR*d.ATR
	entry := high
	if d.Direction == market.Sell {
		invalid, entry = high+s.invalidationATR*d.ATR, low
	}
	risk := math.Abs(entry - invalid)
	target := entry + s.targetR*risk
	if d.Direction == market.Sell {
		target = entry - s.targetR*risk
	}
	if formedAt > last.Time {
		formedAt = last.Time
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setup, Symbol: ctx.Symbol, Direction: d.Direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "break_retest_failed",
		Target: target, TargetLabel: "break_retest_2r", Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: .75, Components: map[string]float64{"break": 1, "retest": 1, "rejection": 1}},
		FormedAt: formedAt, ConfirmedAt: last.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return candidate, err
	}
	candidate.DetectorConfluence = result.ConfluenceContext()
	return candidate, nil
}
