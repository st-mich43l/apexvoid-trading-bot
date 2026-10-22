// Package fadescalp implements the liquidity-sweep fade: price runs through a pool of
// equal highs or lows, the pool's stops are taken, the market closes back and shows a
// confirmed reaction, and the trade is the reversion toward the local mean.
//
// The thesis is measured, not named. For every candidate the strategy reads the
// extension (how far the sweep ran from the local mean, in ATR), the sweep candle's
// body and rejection wick, whether the move continued after the sweep, and the room to
// the nearest structural objective. A fade is published only if the sweep was reclaimed
// and has not been followed by continuation, the reaction confirmed, the invalidation
// sits beyond the sweep's own extreme, and the nearest structural objective offers at
// least the configured reward:risk. A strong displacement that is not reclaimed is never
// faded, and there is no invented target: with no structural objective there is no
// opportunity. The decision runs on the engine's detector-contract frame, so the
// direction, premium/discount gate, chop gate, sweep grade and confluence qualification
// are the frozen detector's. See docs/strategies/fade_scalp.md.
package fadescalp

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "fade_scalp"

// Version v3 replaces v2, which invented a 2R target when no structural objective
// existed, set the rejection factor on every decision and put the stop inside the
// sweep's wick.
const Version = "v3"

type Strategy struct {
	invalidationATR, minimumRewardRisk, expiryHours float64
	chopEdgeFraction                                float64
	strictPD                                        bool
	detector                                        strategyutil.LegacyDetectorSettings
	fingerprint                                     string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("fadescalp: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr": &s.invalidationATR, "minimum_reward_risk": &s.minimumRewardRisk,
		"expiry_hours": &s.expiryHours, "chop_edge_fraction": &s.chopEdgeFraction,
	} {
		v, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var err error
	var ok bool
	if s.strictPD, ok = cfg.Parameters["strict_premium_discount"].(bool); !ok {
		return nil, fmt.Errorf("fadescalp: strict_premium_discount must be boolean")
	}
	if s.detector, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.invalidationATR <= 0 || s.minimumRewardRisk <= 0 || s.expiryHours <= 0 || s.chopEdgeFraction < 0 || s.chopEdgeFraction > .5 {
		return nil, fmt.Errorf("fadescalp: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Objective is the structural price a fade aims at.
type Objective struct {
	Price float64
	Label string
}

// Reasons a confirmed sweep reaction is not published. A candidate that fails a mandatory fact is rejected whatever its stars; the
// confluence floor is the one quality gate and only applies to a setup that already satisfied every fact above it.
const (
	ReasonContinued       = "continued_past_the_sweep"
	ReasonConfluenceFloor = "confluence_below_floor"
	ReasonNoObjective     = "no_structural_objective"
	ReasonRewardRisk      = "reward_risk_below_minimum"
	ReasonSuperseded      = "superseded_by_better_pool"
)

// Consideration records a sweep reaction that was confirmed, whether it was published (Reason is empty) or refused (Reason names the
// gate), with the geometry it has or would have had where that is known, and the facts it was judged on. It is a diagnostic account
// (replay and calibration read it); it never changes a decision.
type Consideration struct {
	Reason                          string
	Direction                       market.Direction
	PoolLevel                       float64
	SweepTime, TouchTime, ConfirmAt int64
	EntryLow, EntryHigh             float64
	Invalidation                    float64 // 0 until the gate that needs it
	Objective                       float64 // 0 when none
	ObjectiveLabel                  string
	RewardRisk                      float64
	Stars                           int // 0 when the confluence was not scored
	Pattern                         string
	// Facts are the measurements known at the deciding candle: the sweep candle's body and wick ratios, the sweep's penetration, the
	// move that ran into it, the pool's touches, the higher-timeframe agreement. None of them gates; they are what a calibration reads.
	Facts map[string]float64
}

// Analysis is everything one evaluation decided: every confirmed sweep reaction, published or refused.
type Analysis struct {
	Considered []Consideration
}

// Refused are the considerations that were not published.
func (a Analysis) Refused() []Consideration {
	var out []Consideration
	for _, c := range a.Considered {
		if c.Reason != "" {
			out = append(out, c)
		}
	}
	return out
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// Analyze is Evaluate plus the account of every confirmed sweep reaction it refused and why.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	var analysis Analysis
	tf := ctx.Timeframes[market.M5]
	direction := strategyutil.StructuralDirection(ctx)
	if tf == nil || !direction.IsValid() || !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) {
		return analysis, nil
	}
	d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, direction, s.detector)
	if !ok {
		return analysis, nil
	}
	wantSide, wantKind := "sell", "equal_low"
	if direction == market.Sell {
		wantSide, wantKind = "buy", "equal_high"
	}

	var best *opportunity.Candidate
	var bestRejection Consideration
	var bestResult *strategyutil.LegacyResult
	bestDistance := math.Inf(1)
	for _, pool := range d.Frame.Pools {
		// Equal levels are the pools with at least two touches.
		if pool.Touches < 2 || pool.Side != wantSide {
			continue
		}
		grab := d.LevelGrab(pool.Level, pool.Band)
		if grab == nil || grab.Grade != "A" && grab.Grade != "B" || grab.Index < 0 || grab.Index >= len(d.Frame.Bars) {
			continue
		}
		zone := d.EntryZone(pool.Level)
		if strategyutil.InChop(tf) && (grab.Grade != "A" || !strategyutil.ChopEdgeAllows(tf, direction, zone.Low(), zone.High(), s.chopEdgeFraction)) {
			continue
		}
		confirmation := d.Reaction(zone.Low(), zone.High(), grab)
		if confirmation == nil {
			continue
		}
		sweep := d.Frame.Bars[grab.Index]
		rejected := Consideration{
			Direction: direction, PoolLevel: pool.Level, SweepTime: sweep.Time, TouchTime: confirmation.TouchTime, ConfirmAt: confirmation.ConfirmationTime,
			EntryLow: zone.Low(), EntryHigh: zone.High(),
		}
		// The thesis fails when the sweep's own extreme is exceeded, so the stop sits beyond it (and beyond the entry band,
		// whichever reaches further).
		stopFor := func(low, high float64) (invalid, reference float64) {
			if direction == market.Sell {
				return math.Max(high, sweep.High) + s.invalidationATR*d.ATR, low
			}
			return math.Min(low, sweep.Low) - s.invalidationATR*d.ATR, high
		}
		rejected.Invalidation, _ = stopFor(zone.Low(), zone.High())
		rejected.Pattern, rejected.Facts = confirmation.Type, sweepFacts(d, direction, grab, pool, sweep)
		// The sweep failed to continue: nothing after it has traded beyond its extreme. A
		// market that keeps running through the swept level is accepting it, not reverting.
		if continued(d, direction, grab.Index) {
			rejected.Reason = ReasonContinued
			analysis.Considered = append(analysis.Considered, rejected)
			continue
		}
		factors := strategyutil.FactorsForConfirmation(confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: pool.Touches,
			WickRejection:     reaction.WickRejection(sweep, string(direction)),
			DisplacementGrade: grab.ReactionDisplacement,
		}, confirmation.Type)
		structuralLow, structuralHigh := zone.Low(), zone.High()
		result := d.Finish(pool.Level, zone, factors, wantKind, &structuralLow, &structuralHigh)
		if result == nil {
			rejected.Reason = ReasonConfluenceFloor
			// The account also says whether the setup would have had the room its risk demands, so a replay can tell what the
			// floor alone refused from what it refused in company. It decides nothing.
			if invalid, reference := stopFor(zone.Low(), zone.High()); math.Abs(reference-invalid) > 0 {
				if objective, found := s.objective(d, tf, direction, reference); found {
					rejected.Objective, rejected.ObjectiveLabel = objective.Price, objective.Label
					rejected.RewardRisk = math.Abs(objective.Price-reference) / math.Abs(reference-invalid)
				}
			}
			analysis.Considered = append(analysis.Considered, rejected)
			continue
		}

		low, high := result.Zone.Low(), result.Zone.High()
		invalid, reference := stopFor(low, high)
		rejected.EntryLow, rejected.EntryHigh, rejected.Invalidation, rejected.Stars = low, high, invalid, result.Stars
		risk := math.Abs(reference - invalid)
		objective, found := s.objective(d, tf, direction, reference)
		if !found || risk <= 0 {
			rejected.Reason = ReasonNoObjective
			analysis.Considered = append(analysis.Considered, rejected)
			continue
		}
		reward := math.Abs(objective.Price - reference)
		rejected.Objective, rejected.ObjectiveLabel, rejected.RewardRisk = objective.Price, objective.Label, reward/risk
		if reward/risk < s.minimumRewardRisk {
			rejected.Reason = ReasonRewardRisk
			analysis.Considered = append(analysis.Considered, rejected)
			continue
		}
		mean, _, hasMean := strategyutil.MeanReference(tf)
		extension := 0.0
		if hasMean && d.ATR > 0 {
			extension = math.Max(0, math.Abs(mean-sweepExtreme(sweep, direction))/d.ATR)
		}
		evidence := []string{"equal_level_" + wantKind, "liquidity_grab_grade_" + grab.Grade, "legacy_pd_location", "structural_reaction_" + confirmation.Type, "sweep_not_continued"}
		if strategyutil.InChop(tf) {
			evidence = append(evidence, "chop_edge_grade_a")
		}
		sweepTime := sweep.Time
		formedAt := sweepTime
		if formedAt > confirmation.ConfirmationTime {
			formedAt = confirmation.TouchTime
		}
		model := scoreQuality(qualityInputs{
			bars: d.Frame.Bars, direction: direction, pool: pool, grab: grab, confirmation: confirmation, facts: rejected.Facts, atr: d.ATR,
			price: d.Price, reference: reference, invalidation: invalid, objective: objective.Price,
		})
		model.Measure("confluence_stars", float64(result.Stars))
		model.Measure("extension_atr", extension)
		model.Measure("target_room_atr", reward/math.Max(d.ATR, 1e-12))
		model.Measure("sweep_body_ratio", sweep.BodyRatio())
		model.Measure("sweep_upper_wick", sweep.UpperWickRatio())
		model.Measure("sweep_lower_wick", sweep.LowerWickRatio())
		if grab.ReactionDisplacement {
			model.Measure("reaction_displacement", 1)
		}
		setupKey := fmt.Sprintf("fade:%s:%.8f", wantKind, pool.Level)
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "sweep_extreme_exceeded",
			Target: objective.Price, TargetLabel: objective.Label, Evidence: evidence,
			Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
			FormedAt: formedAt, ConfirmedAt: confirmation.ConfirmationTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if err != nil {
			continue
		}
		candidate.Reaction = &opportunity.ReactionConfirmation{
			ZoneID: setupKey, TouchBarTime: confirmation.TouchTime, ConfirmationBarTime: confirmation.ConfirmationTime,
			ReactionType: "rejection", Pattern: confirmation.Type,
		}
		candidate.DetectorConfluence = result.ConfluenceContext()
		distance := d.ZoneDistance(result.Zone)
		if best == nil || result.Stars > bestResult.Stars || result.Stars == bestResult.Stars && distance < bestDistance {
			if best != nil {
				analysis.Considered = append(analysis.Considered, supersededBy(bestRejection))
			}
			copy := candidate
			best, bestResult, bestDistance, bestRejection = &copy, result, distance, rejected
		} else {
			rejected.Reason = ReasonSuperseded
			analysis.Considered = append(analysis.Considered, rejected)
		}
	}
	if best == nil {
		return analysis, nil
	}
	analysis.Considered = append(analysis.Considered, bestRejection) // published: no Reason
	return analysis, []opportunity.Candidate{*best}
}

func supersededBy(r Consideration) Consideration {
	r.Reason = ReasonSuperseded
	return r
}

// sweepFacts are the measurements of one sweep known at the deciding candle. The run into the sweep is the move over the ten candles
// before it, against the trade, in ATR: how hard the market was travelling when it took the liquidity.
func sweepFacts(d *strategyutil.LegacyDetector, direction market.Direction, grab *techniquezone.Grab, pool techniquezone.Pool, sweep market.Candle) map[string]float64 {
	flag := func(v bool) float64 {
		if v {
			return 1
		}
		return 0
	}
	facts := map[string]float64{
		"sweep_body_ratio": sweep.BodyRatio(), "sweep_upper_wick": sweep.UpperWickRatio(), "sweep_lower_wick": sweep.LowerWickRatio(),
		"penetration_atr": penetrationATR(sweep, pool.Level, direction, d.ATR), "pool_touches": float64(pool.Touches),
		"bars_since_sweep": float64(len(d.Frame.Bars) - 1 - grab.Index),
		"htf_aligned":      flag(d.HTFAligned()), "grade_a": flag(grab.Grade == "A"), "reaction_displacement": flag(grab.ReactionDisplacement),
	}
	bars := d.Frame.Bars
	if from := grab.Index - 10; from >= 0 && d.ATR > 0 {
		run := bars[grab.Index-1].Close - bars[from].Close
		if direction == market.Buy {
			run = -run
		}
		facts["run_into_sweep_atr"] = run / d.ATR
	}
	// How the market was travelling before the sweep: the average range of the five candles before it against the ATR (a spike of
	// volatility is what an exhaustion looks like), and how many closes in a row ran against the trade.
	if from := grab.Index - 5; from >= 0 && d.ATR > 0 {
		var total float64
		for _, bar := range bars[from:grab.Index] {
			total += bar.Range()
		}
		facts["range_expansion"] = total / 5 / d.ATR
	}
	run := 0
	for i := grab.Index - 1; i > 0; i-- {
		against := bars[i].Close < bars[i-1].Close
		if direction == market.Sell {
			against = bars[i].Close > bars[i-1].Close
		}
		if !against {
			break
		}
		run++
	}
	facts["closes_against_before_sweep"] = float64(run)
	return facts
}

// sweepExtreme is the price the sweep ran to: the low of a sell-side sweep (a BUY fade) or
// the high of a buy-side one.
func sweepExtreme(sweep market.Candle, direction market.Direction) float64 {
	if direction == market.Buy {
		return sweep.Low
	}
	return sweep.High
}

// penetrationATR is how far the sweep candle ran beyond the pool's level, in ATR.
func penetrationATR(sweep market.Candle, level float64, direction market.Direction, atr float64) float64 {
	depth := level - sweep.Low
	if direction == market.Sell {
		depth = sweep.High - level
	}
	if atr <= 0 || depth < 0 {
		return 0
	}
	return depth / atr
}

// continued reports whether any candle after the sweep traded beyond the sweep's extreme:
// the move was not exhausted, it carried on.
func continued(d *strategyutil.LegacyDetector, direction market.Direction, sweepIndex int) bool {
	extreme := sweepExtreme(d.Frame.Bars[sweepIndex], direction)
	for _, bar := range d.Frame.Bars[sweepIndex+1:] {
		if direction == market.Buy && bar.Low < extreme-d.Settings.PipSize || direction == market.Sell && bar.High > extreme+d.Settings.PipSize {
			return true
		}
	}
	return false
}

// objective is the NEAREST structural price beyond the entry in the trade direction: the
// local mean (the established range's equilibrium, else the dealing range's) or the nearest
// opposing liquidity pool, whichever is closer. The nearest decides: an objective that
// leaves too little room is not skipped in favour of a farther one the market would have to
// travel through it to reach.
func (s *Strategy) objective(d *strategyutil.LegacyDetector, tf *analysiscontext.TimeframeContext, direction market.Direction, reference float64) (Objective, bool) {
	ahead := func(price float64) float64 {
		if direction == market.Buy {
			return price - reference
		}
		return reference - price
	}
	var best Objective
	bestDistance := math.Inf(1)
	if mean, source, ok := strategyutil.MeanReference(tf); ok && ahead(mean) > 0 {
		best, bestDistance = Objective{Price: mean, Label: source}, ahead(mean)
	}
	want := "buy"
	if direction == market.Sell {
		want = "sell"
	}
	for _, pool := range d.Frame.Pools {
		if pool.Side != want {
			continue
		}
		if distance := ahead(pool.Level); distance > 0 && distance < bestDistance {
			best, bestDistance = Objective{Price: pool.Level, Label: "opposing_liquidity"}, distance
		}
	}
	return best, !math.IsInf(bestDistance, 1)
}
