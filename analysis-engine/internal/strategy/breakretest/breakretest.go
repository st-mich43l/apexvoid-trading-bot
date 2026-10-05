// Package breakretest implements the M5 structural Break & Retest thesis.
// It is deliberately independent from box_breakout and the M1 scalp
// breakout strategy: its structural anchor is either a canonical M5
// trendline or a canonical M5 key level.
package breakretest

import (
	"fmt"
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	technicaltrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

const ID strategy.StrategyID = "break_retest"
const Version = "v2"

type Strategy struct {
	breakoutAcceptBars                         int
	minimumBodyFraction, trendlineToleranceATR float64
	invalidationATR, targetR, expiryHours      float64
	maximumEntryATR                            float64
	strictPD                                   bool
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
	maximumEntry, err := strategyutil.Float(cfg.Parameters, "maximum_entry_atr")
	if err != nil {
		return nil, err
	}
	strictPD, ok := cfg.Parameters["strict_premium_discount"].(bool)
	if !ok {
		return nil, fmt.Errorf("strict_premium_discount must be boolean")
	}
	if accept < 1 || body <= 0 || body > 1 || tolerance <= 0 || invalid <= 0 || targetR <= 0 || expiry <= 0 || maximumEntry <= 0 {
		return nil, fmt.Errorf("breakretest: invalid parameters")
	}
	return &Strategy{
		breakoutAcceptBars: accept, minimumBodyFraction: body,
		trendlineToleranceATR: tolerance, invalidationATR: invalid, targetR: targetR,
		expiryHours: expiry, maximumEntryATR: maximumEntry, strictPD: strictPD,
		fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters),
	}, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	if tf == nil || len(tf.Candles) < 5 || ctx.Volatility.ATR <= 0 || tf.Regime.Kind == "chop" {
		return nil
	}
	direction := strategyutil.StructuralDirection(ctx)
	if !direction.IsValid() || !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) || !rejection(tf.Candles[len(tf.Candles)-1], direction) {
		return nil
	}
	price := tf.Candles[len(tf.Candles)-1].Close
	// The frozen detector gives trendline Break & Retest priority over a
	// key-level setup.  Return immediately after the first valid candidate.
	lines := append([]technicaltrendline.Trendline(nil), tf.Trendline.Lines...)
	sort.SliceStable(lines, func(i, j int) bool {
		return math.Abs(float64(technicaltrendline.ValueAt(lines[i], len(tf.Candles)-1))-price) < math.Abs(float64(technicaltrendline.ValueAt(lines[j], len(tf.Candles)-1))-price)
	})
	for _, line := range lines {
		zone, level, ok := s.trendlineRetest(tf.Candles, line, direction, ctx.Volatility.ATR)
		if !ok {
			continue
		}
		candidate, err := s.candidate(ctx, direction, level, zone.low, zone.high,
			fmt.Sprintf("trendline:%s:%s", line.AnchorA, line.AnchorB),
			[]string{"m5_trendline_break", "m5_trendline_retest", "m5_retest_holds", "m5_retest_rejection", "htf_aligned", "structural_agreement"},
			anchorTime(line, tf.Candles))
		if err == nil {
			return []opportunity.Candidate{candidate}
		}
	}

	levels := append([]keylevel.Level(nil), tf.KeyLevel.Levels...)
	sort.SliceStable(levels, func(i, j int) bool {
		return math.Abs(float64(levels[i].Price)-price) < math.Abs(float64(levels[j].Price)-price)
	})
	for _, level := range levels {
		pipSize := tf.Geometry.PipSize
		if pipSize <= 0 {
			pipSize = .1
		}
		zone, breakIndex, ok := findLevelRetest(tf.Candles, float64(level.Price), pipSize, direction, s.breakoutAcceptBars)
		if !ok || !s.validSide(float64(level.Price), price, direction) {
			continue
		}
		bodyBreak := strongBodyBreak(tf.Candles[breakIndex], tf.Structure.Swings, direction, s.minimumBodyFraction)
		evidence := []string{"m5_key_level_break", "m5_key_level_retest", "m5_retest_holds", "m5_retest_rejection", "htf_aligned", "structural_agreement"}
		if bodyBreak {
			evidence = append(evidence, "displacement_grade")
		}
		candidate, err := s.candidate(ctx, direction, float64(level.Price), zone.low, zone.high,
			"key_level:"+level.ID, evidence, tf.Candles[breakIndex].Time)
		if err == nil {
			return []opportunity.Candidate{candidate}
		}
	}
	return nil
}

type retestZone struct{ low, high float64 }

func (s *Strategy) trendlineRetest(candles []market.Candle, line technicaltrendline.Trendline, direction market.Direction, atr float64) (retestZone, float64, bool) {
	if line.BrokenAt == nil || len(candles) == 0 || atr <= 0 {
		return retestZone{}, 0, false
	}
	breakIndex := -1
	for i, c := range candles {
		if c.Time == *line.BrokenAt {
			breakIndex = i
		}
	}
	current := len(candles) - 1
	if breakIndex < 0 || current <= breakIndex {
		return retestZone{}, 0, false
	}
	level := float64(technicaltrendline.ValueAt(line, current))
	tolerance := s.trendlineToleranceATR * atr
	row := candles[current]
	touched := row.Low <= level+tolerance && row.High >= level-tolerance
	held := direction == market.Buy && row.Close >= level || direction == market.Sell && row.Close <= level
	wantedKind := technicaltrendline.KindResistance
	if direction == market.Sell {
		wantedKind = technicaltrendline.KindSupport
	}
	if line.Kind != wantedKind || !touched || !held {
		return retestZone{}, 0, false
	}
	return retestZone{low: level - tolerance, high: level + tolerance}, level, true
}

func findLevelRetest(candles []market.Candle, level, pipSize float64, direction market.Direction, required int) (retestZone, int, bool) {
	if len(candles) < 3 {
		return retestZone{}, -1, false
	}
	tolerance := pipSize
	spanLow, spanHigh := candles[0].Low, candles[0].High
	for _, candle := range candles[1:] {
		spanLow = math.Min(spanLow, candle.Low)
		spanHigh = math.Max(spanHigh, candle.High)
	}
	tolerance = math.Max(tolerance, (spanHigh-spanLow)*.003)
	upBreak, downBreak := -1, -1
	upRun, downRun := 0, 0
	for i, c := range candles {
		if c.Close > level {
			upRun++
			if upRun == required {
				upBreak = i - required + 1
			}
		} else {
			upRun = 0
		}
		if c.Close < level {
			downRun++
			if downRun == required {
				downBreak = i - required + 1
			}
		} else {
			downRun = 0
		}
	}
	breakIndex := upBreak
	breakDirection := market.Buy
	if downBreak > breakIndex {
		breakIndex, breakDirection = downBreak, market.Sell
	}
	if breakIndex < 0 || breakIndex >= len(candles)-1 || breakDirection != direction {
		return retestZone{}, -1, false
	}
	if tolerance <= 0 {
		tolerance = 0.0000001
	}
	for i := breakIndex + 1; i < len(candles); i++ {
		c := candles[i]
		if c.Low > level+tolerance || c.High < level-tolerance {
			continue
		}
		if direction == market.Buy && c.Close >= level || direction == market.Sell && c.Close <= level {
			return retestZone{low: level - tolerance, high: level + tolerance}, breakIndex, true
		}
	}
	return retestZone{}, -1, false
}

func (s *Strategy) validSide(level, price float64, direction market.Direction) bool {
	if direction == market.Buy {
		return level <= price
	}
	return level >= price
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, direction market.Direction, level, low, high float64, setup string, evidence []string, formedAt int64) (opportunity.Candidate, error) {
	atr := ctx.Volatility.ATR
	last := ctx.Timeframes[market.M5].Candles[len(ctx.Timeframes[market.M5].Candles)-1]
	if direction == market.Buy && last.Close < low-s.maximumEntryATR*atr || direction == market.Sell && last.Close > high+s.maximumEntryATR*atr {
		return opportunity.Candidate{}, fmt.Errorf("entry is outside maximum distance")
	}
	invalid := low - s.invalidationATR*atr
	entry := high
	if direction == market.Sell {
		invalid, entry = high+s.invalidationATR*atr, low
	}
	risk := math.Abs(entry - invalid)
	target := entry + s.targetR*risk
	if direction == market.Sell {
		target = entry - s.targetR*risk
	}
	return strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: setup, Symbol: ctx.Symbol, Direction: direction, EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "break_retest_failed", Target: target, TargetLabel: "break_retest_2r", Evidence: evidence, Quality: opportunity.StrategyQuality{Overall: .75, Components: map[string]float64{"break": 1, "retest": 1, "rejection": 1}}, FormedAt: formedAt, ConfirmedAt: last.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
}

func rejection(c market.Candle, direction market.Direction) bool {
	if c.Range() <= 0 {
		return false
	}
	body, upper, lower := c.Body(), c.High-math.Max(c.Open, c.Close), math.Min(c.Open, c.Close)-c.Low
	third := c.Range() / 3
	if direction == market.Sell {
		return upper >= body && c.IsBearish() && c.Close <= c.Low+third
	}
	return lower >= body && c.IsBullish() && c.Close >= c.High-third
}

func strongBodyBreak(c market.Candle, swings []structure.Swing, direction market.Direction, fraction float64) bool {
	if c.Range() <= 0 || c.Body() < fraction*c.Range() || direction == market.Buy && !c.IsBullish() || direction == market.Sell && !c.IsBearish() {
		return false
	}
	if direction == market.Buy {
		for i := len(swings) - 1; i >= 0; i-- {
			if swings[i].Kind == structure.SwingHigh {
				return c.Close > float64(swings[i].Price)
			}
		}
		return true
	}
	for i := len(swings) - 1; i >= 0; i-- {
		if swings[i].Kind == structure.SwingLow {
			return c.Close < float64(swings[i].Price)
		}
	}
	return true
}

// anchorTime keeps the formation timestamp causal without exposing
// trendline internals to the candidate model.
func anchorTime(line technicaltrendline.Trendline, candles []market.Candle) int64 {
	if line.AnchorAIndex >= 0 && line.AnchorAIndex < len(candles) {
		return candles[line.AnchorAIndex].Time
	}
	return candles[0].Time
}
