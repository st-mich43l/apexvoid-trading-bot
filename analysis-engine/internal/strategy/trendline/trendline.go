// Package trendline publishes the frozen trendline_reaction (V2) decision: a
// causally anchored line, in good health, that price touched from the correct
// side and closed back off, and that shows a confirmed structural reaction within
// the shared confluence floor.
//
// Two confirmed anchors establish the line (v7); the line is knowable from the
// close that confirmed its second anchor, and the trade is the first reaction after
// that, never the anchor itself. A validation touch after the anchors is not a
// requirement, only a measure of how respected the line is. The anchors are
// immutable and the line's health (tentative, broken, degraded, exhausted, stale) is
// judged on the frame's bounded window. A broken line, a test without a reaction,
// or an entry outside the interaction band is never published.
package trendline

import (
	"fmt"
	"math"
	"sort"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	technical "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

const ID strategy.StrategyID = "trendline"
const Version = "v8"

// frame is what one setup timeframe owns: the parameters that are measured in bars or in hours of that timeframe. Everything else (the interaction band, the close
// violation, the approach, the invalidation buffer, the target distance) is in ATR of the frame and is shared.
type frame struct {
	timeframe                  market.Timeframe
	minutes                    int
	maximumBarsSinceLastTouch  int
	expiryHours                float64
	reactionLookbackBars       int
	structureEvidence, setupID string
}

type Strategy struct {
	strategyutil.RefusalCounter
	frames                                                        []frame
	minimumValidationTouches, chopMinimumValidationTouches        int
	invalidationATR, minimumTargetDistanceATR                     float64
	interactionBandATR, closeViolationATR, approachMinDistanceATR float64
	chopRequireHTFAligned, requireHTFAligned, rejectExhausted     bool
	legacy                                                        strategyutil.LegacyDetectorSettings
	stopBudget                                                    strategyutil.StopBudget
	barrier                                                       strategyutil.BarrierPolicy
	fingerprint                                                   string
}

// setupTimeframes are the timeframes Trendline can decide on. A line is found, reacted to and published on one timeframe's own closed candles; M5 and M15 are
// independent setups of one strategy, neither needing the other.
var setupTimeframes = map[market.Timeframe]bool{market.M5: true, market.M15: true}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("trendline: wrong strategy ID %q", cfg.ID)
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	var err error
	var defaultExpiryHours float64
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr": &s.invalidationATR, "expiry_hours": &defaultExpiryHours,
		"interaction_band_atr": &s.interactionBandATR, "close_violation_atr": &s.closeViolationATR,
		"approach_min_distance_atr": &s.approachMinDistanceATR, "minimum_target_distance_atr": &s.minimumTargetDistanceATR,
	} {
		if *dst, err = strategyutil.Float(cfg.Parameters, key); err != nil {
			return nil, err
		}
	}
	var defaultMaximumBars int
	for key, dst := range map[string]*int{
		"minimum_validation_touches": &s.minimumValidationTouches, "chop_minimum_validation_touches": &s.chopMinimumValidationTouches,
		"maximum_bars_since_last_touch": &defaultMaximumBars,
	} {
		if *dst, err = strategyutil.Int(cfg.Parameters, key); err != nil {
			return nil, err
		}
	}
	for key, dst := range map[string]*bool{
		"chop_require_htf_aligned": &s.chopRequireHTFAligned, "require_htf_aligned": &s.requireHTFAligned, "reject_exhausted": &s.rejectExhausted,
	} {
		value, ok := cfg.Parameters[key].(bool)
		if !ok {
			return nil, fmt.Errorf("trendline: parameter %q must be boolean", key)
		}
		*dst = value
	}
	if s.legacy, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.barrier, err = strategyutil.ParseBarrierPolicy(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.stopBudget, err = strategyutil.ParseStopBudget(cfg.Parameters); err != nil {
		return nil, err
	}
	s.stopBudget.Refusals = &s.RefusalCounter
	if s.minimumValidationTouches < 0 || s.invalidationATR <= 0 || defaultExpiryHours <= 0 || s.interactionBandATR <= 0 ||
		s.closeViolationATR <= 0 || s.approachMinDistanceATR < 0 || defaultMaximumBars < 1 || s.minimumTargetDistanceATR <= 0 {
		return nil, fmt.Errorf("trendline: invalid parameters")
	}
	if s.frames, err = parseFrames(cfg.Parameters, defaultMaximumBars, defaultExpiryHours, s.legacy.ReactionLookbackBars); err != nil {
		return nil, err
	}
	return s, nil
}

// parseFrames reads setup_timeframes and the per-timeframe overrides (timeframes.<TF>.{maximum_bars_since_last_touch, expiry_hours, reaction_lookback_bars}) over the
// flat, M5-calibrated defaults. A timeframe without an override inherits the default; an unknown timeframe or key fails at startup.
func parseFrames(params map[string]any, defaultMaximumBars int, defaultExpiryHours float64, defaultLookback int) ([]frame, error) {
	raw, present := params["setup_timeframes"]
	if !present {
		return nil, fmt.Errorf("trendline: missing required parameter %q", "setup_timeframes")
	}
	var names []any
	switch list := raw.(type) {
	case []any:
		names = list
	case []string:
		for _, name := range list {
			names = append(names, name)
		}
	default:
		return nil, fmt.Errorf("trendline: parameter %q must be a list of timeframes", "setup_timeframes")
	}
	overrides := map[string]any{}
	if rawOverrides, ok := params["timeframes"]; ok {
		if overrides, ok = rawOverrides.(map[string]any); !ok {
			return nil, fmt.Errorf("trendline: parameter %q must be a map of timeframe overrides", "timeframes")
		}
	}
	seen := map[market.Timeframe]bool{}
	var out []frame
	for _, item := range names {
		name, isString := item.(string)
		tf := market.Timeframe(name)
		if !isString || !setupTimeframes[tf] || seen[tf] {
			return nil, fmt.Errorf("trendline: setup_timeframes must list distinct timeframes among M5 and M15, got %v", item)
		}
		seen[tf] = true
		minutes, _ := tf.Minutes()
		f := frame{timeframe: tf, minutes: minutes, maximumBarsSinceLastTouch: defaultMaximumBars, expiryHours: defaultExpiryHours, reactionLookbackBars: defaultLookback}
		if override, ok := overrides[string(tf)]; ok {
			values, isMap := override.(map[string]any)
			if !isMap {
				return nil, fmt.Errorf("trendline: timeframes.%s must be a map", tf)
			}
			for key, value := range values {
				switch key {
				case "maximum_bars_since_last_touch":
					v, err := strategyutil.Int(values, key)
					if err != nil || v < 1 {
						return nil, fmt.Errorf("trendline: timeframes.%s.%s must be a positive integer", tf, key)
					}
					f.maximumBarsSinceLastTouch = v
				case "expiry_hours":
					v, err := strategyutil.Float(values, key)
					if err != nil || v <= 0 {
						return nil, fmt.Errorf("trendline: timeframes.%s.%s must be positive", tf, key)
					}
					f.expiryHours = v
				case "reaction_lookback_bars":
					v, err := strategyutil.Int(values, key)
					if err != nil || v < 1 {
						return nil, fmt.Errorf("trendline: timeframes.%s.%s must be a positive integer", tf, key)
					}
					f.reactionLookbackBars = v
				default:
					return nil, fmt.Errorf("trendline: timeframes.%s has unknown key %q (%v)", tf, key, value)
				}
			}
		}
		prefix := strings.ToLower(string(tf)) + "_trendline_"
		f.structureEvidence, f.setupID = prefix+"causal_anchors", prefix+"reaction_confirmed"
		out = append(out, f)
	}
	// A timeframe taken out of setup_timeframes keeps its block (switching a frame off is one edit); only a timeframe Trendline cannot decide on is an error.
	for tf := range overrides {
		if !setupTimeframes[market.Timeframe(tf)] {
			return nil, fmt.Errorf("trendline: timeframes.%s is not a timeframe Trendline decides on (M5, M15)", tf)
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("trendline: setup_timeframes must not be empty")
	}
	return out, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes are the setup timeframes: a closed M5 or M15 bar triggers an evaluation of that frame.
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	out := make([]market.Timeframe, len(s.frames))
	for i, f := range s.frames {
		out[i] = f.timeframe
	}
	return out
}

// Evaluate is every setup timeframe at once, for a caller that has no closing event. The engine calls EvaluateClosed.
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	var out []opportunity.Candidate
	for _, f := range s.frames {
		out = append(out, s.evaluateFrame(ctx, f)...)
	}
	return out
}

// EvaluateClosed evaluates the setup timeframe whose bar just closed, against that timeframe's own candles, swings, structure, ATR and projected lines, and nothing
// else: an M5 setup never waits for or reads M15, an M15 setup never reads M5.
func (s *Strategy) EvaluateClosed(ctx *analysiscontext.MarketContext, closed market.Timeframe) []opportunity.Candidate {
	for _, f := range s.frames {
		if f.timeframe == closed {
			return s.evaluateFrame(ctx, f)
		}
	}
	return nil
}

// evaluateFrame mirrors _trendline_reaction_v2 on one timeframe: lines nearest to price first, each gated on structure, then on the live interaction, then on a
// confirmed reaction; the best-scored qualifying line (the first on a tie) is published.
func (s *Strategy) evaluateFrame(ctx *analysiscontext.MarketContext, f frame) []opportunity.Candidate {
	settings := s.legacy
	settings.ReactionLookbackBars = f.reactionLookbackBars
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, f.timeframe, settings)
	if !ok {
		return nil
	}
	if f.timeframe != market.M5 {
		// The higher-timeframe context of a frame is the frames above it: an M15 setup is aligned with the H1 bias, never with its own.
		base.Read.HTFBias = higherFrameBias(ctx, f.timeframe)
	}
	bars := base.Frame.Bars
	last := len(bars) - 1
	band := math.Max(1e-9, s.interactionBandATR*math.Max(0, base.ATR))
	lines := append([]technical.Trendline(nil), base.Frame.Trendlines...)
	sort.SliceStable(lines, func(i, j int) bool {
		return math.Abs(float64(technical.ValueAt(lines[i], last))-base.Price) < math.Abs(float64(technical.ValueAt(lines[j], last))-base.Price)
	})
	cfg := technical.Config{InteractionBandATR: s.interactionBandATR, CloseViolationATR: s.closeViolationATR, ApproachMinDistanceATR: s.approachMinDistanceATR}
	var best *strategyutil.TechniqueDecision
	for _, line := range lines {
		direction := market.Buy
		side := "demand"
		if line.Kind == technical.KindResistance {
			direction, side = market.Sell, "supply"
		} else if line.Kind != technical.KindSupport {
			continue
		}
		d := base.WithDirection(direction)
		if s.structurallyRejected(d, line, last, f) {
			continue
		}
		// The line now: its price and interaction band at the latest closed bar, which is where the entry rests.
		interaction := technical.EvaluateInteraction(bars, line, base.ATR, cfg)
		if !holdsAtLatestBar(interaction.State) {
			continue
		}
		// The line is knowable from the close that confirmed its second anchor, and not before. The reaction is a touch and, on or after it, a confirming candle,
		// both inside the last reaction_lookback_bars closed bars; they may be different candles. The touch may be the second anchor's own candle: B is a swing
		// low (high) only once its confirming candles have closed, and the reaction at B is the very reversal that confirmed it (wick, reclaim or engulfing candle
		// among the bars from B to that close), so a line is tradeable at the close that confirms B without a further visit. Nothing is backdated: the
		// candidate is created on the bar that closes now, the touch and confirmation are the bars that really formed the reaction, and a B older than the
		// lookback has no fresh reaction. Anything before B is the line's other anchor or earlier history, never a reaction to it.
		conf := d.ReactionFrom(float64(interaction.BandLow), float64(interaction.BandHigh), nil, line.AnchorBIndex)
		if conf == nil {
			continue
		}
		// The touch is judged where it happened: price reached the PROJECTED line at that bar, from the intended side, without closing through it.
		touch := technical.EvaluateInteraction(bars[:conf.TouchIndex+1], line, base.ATR, cfg)
		if touch.State != technical.InteractionReclaimedSupport && touch.State != technical.InteractionReclaimedResistance &&
			touch.State != technical.InteractionTestingSupport && touch.State != technical.InteractionTestingResistance {
			continue
		}
		linePrice := float64(interaction.LinePrice)
		zone := techniquezone.Zone{Bottom: linePrice - band, Top: linePrice + band, Side: side, Source: "trendline", BreakIndex: -1}
		if !d.EntryValid(zone) {
			continue
		}
		touches := 2 + len(line.ValidationTouches)
		factors := strategyutil.FactorsForConfirmation(strategyutil.ReactionFactors(conf.Type, d.HTFAligned(), touches, d.SessionSweepContext(conf)), conf.Type)
		low, high := float64(interaction.BandLow), float64(interaction.BandHigh)
		result := d.Finish(linePrice, zone, factors, line.Kind.String(), &low, &high)
		if result == nil {
			continue
		}
		if best == nil || result.Stars > best.Result.Stars {
			best = &strategyutil.TechniqueDecision{
				Technique: "trendline", Direction: direction, Detector: d, Confirmation: conf, Result: result,
				ID: lineID(f.timeframe, line, bars),
				Score: func(dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) opportunity.StrategyQuality {
					return scoreQuality(f.timeframe, line, touch, dec, g)
				},
			}
		}
	}
	candidate, ok := strategyutil.TechniqueCandidate(ctx, best, strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, AnchorToReaction: true, StopBudget: s.stopBudget, Barrier: s.barrier, ZoneEvidence: f.structureEvidence, ConfirmedEvidence: f.setupID,
		InvalidationLabel: "trendline_close_violation", InvalidationBufferATR: s.invalidationATR, ExpiryHours: f.expiryHours, Fingerprint: s.fingerprint,
		// The target is the nearest opposing liquidity at least this far from the entry; with none there is
		// no technical objective and nothing is published (v3 invented twice the distance to the band edge).
		MinimumTargetDistanceATR: s.minimumTargetDistanceATR, RequireStructuralTarget: true,
	})
	if !ok {
		return nil
	}
	// The line lives on this timeframe: it is the structure behind the setup, and the bar that closed is the one the setup is observed on.
	candidate.StructureTimeframe = f.timeframe
	return []opportunity.Candidate{candidate}
}

// higherFrameBias is the bias of the closest higher frame the context holds with a decided read ("range" when none decides, "unknown" when none exists).
func higherFrameBias(ctx *analysiscontext.MarketContext, tf market.Timeframe) string {
	minutes, _ := tf.Minutes()
	bias := "unknown"
	for _, candidate := range []market.Timeframe{market.H1} {
		if above, _ := candidate.Minutes(); above <= minutes {
			continue
		}
		if frame := ctx.Timeframes[candidate]; frame != nil && frame.Legacy != nil {
			return frame.Legacy.Bias()
		}
	}
	return bias
}

// lineID is the line's identity: its timeframe, kind and the open times of its two anchor candles. The swing ids the builder carries (legacy:<index>:<n>) are
// positions in the analysis window, which moves one bar every evaluation, so an id built from them named the same line differently on every bar and published
// the same reaction as a new opportunity each time. The timeframe is part of it: an M5 line and an M15 line through the same two instants are two theses.
func lineID(tf market.Timeframe, line technical.Trendline, bars []market.Candle) string {
	return fmt.Sprintf("trendline:%s:%s:%d:%d", tf, line.Kind, bars[line.AnchorAIndex].Time, bars[line.AnchorBIndex].Time)
}

// holdsAtLatestBar is whether the latest closed bar leaves the line standing: it is on the valid side of the line (reclaimed it, or has not come back to it).
// A close through the line, or a touch from the wrong side, is not a reaction to publish whatever happened on the bars before.
func holdsAtLatestBar(state technical.InteractionState) bool {
	switch state {
	case technical.InteractionReclaimedSupport, technical.InteractionReclaimedResistance,
		technical.InteractionAboveLine, technical.InteractionBelowLine,
		technical.InteractionApproachingSupport, technical.InteractionApproachingResistance:
		return true
	}
	return false
}

// structurallyRejected mirrors _trendline_v2_structural_rejection. A line with no validation is not rejected for that (v7): Tentative here means a line
// shorter than the minimum span, not one without a third touch. Staleness (the last validation too long ago) only exists for a line that has one.
func (s *Strategy) structurallyRejected(d *strategyutil.LegacyDetector, line technical.Trendline, last int, f frame) bool {
	validations := len(line.ValidationTouches)
	if line.State == technical.StateTentative || validations < s.minimumValidationTouches {
		return true
	}
	if line.State == technical.StateBroken || line.BrokenAt != nil || line.State == technical.StateDegraded {
		return true
	}
	if line.State == technical.StateExhausted || s.rejectExhausted && line.Exhausted {
		return true
	}
	if validations > 0 && last-line.ValidationTouches[validations-1].BarIndex > f.maximumBarsSinceLastTouch {
		return true
	}
	aligned := d.HTFAligned()
	if s.requireHTFAligned && !aligned {
		return true
	}
	if d.Frame.Regime.Kind == "chop" {
		if validations < s.chopMinimumValidationTouches || s.chopRequireHTFAligned && !aligned {
			return true
		}
	}
	return false
}
