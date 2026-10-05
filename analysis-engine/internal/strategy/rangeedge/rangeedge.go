// Package rangeedge restores the Python local-barrier range rejection thesis.
package rangeedge

import (
	"fmt"
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	technicaltrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

const ID strategy.StrategyID = "range_edge"
const Version = "v2"

type Strategy struct {
	lookback, minimumTouches, minimumWicks, breakCloses            int
	minimumInsideCloses, fallbackMinimumConfirmations              int
	insideLookbackBars, recentBreakoutLookbackBars                 int
	clusterATR, entryATR, minimumWickFraction                      float64
	minimumWidthATR, maximumWidthATR, minimumRoomATR               float64
	invalidationATR, expiryHours                                   float64
	clusterMinAbs, clusterPipMult, maximumEdgeWidthATR             float64
	recentBreakoutBufferATR, recentBreakoutMinSpanATR              float64
	fallbackMinWidthATR, fallbackMaxWidthATR, fallbackWickFraction float64
	postImpulseMinDisplacementATR, postImpulseMaxContractionATR    float64
	postImpulseMinInsideCloses, postImpulseLookbackBars            int
	postImpulseRecentBars                                          int
	maximumEntryATR                                                float64
	fallbackEnabled, provisionalEnabled, postImpulseEnabled        bool
	reaction                                                       strategyutil.ReactionConfig
	fingerprint                                                    string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("rangeedge: wrong strategy ID")
	}
	// Keep older unit fixtures constructible while production remains fully
	// explicit in analysis.yml. These are the frozen oracle defaults for the
	// newly surfaced range-state leaves.
	defaults := map[string]any{
		"break_closes": 2.0, "inside_lookback_bars": 24.0, "recent_breakout_lookback_bars": 12.0,
		"post_impulse_lookback_bars": 36.0, "post_impulse_recent_bars": 6.0,
		"cluster_min_abs": 0.0, "cluster_pip_mult": 2.0, "maximum_edge_width_atr": .75,
		"recent_breakout_buffer_atr": .15, "recent_breakout_min_span_atr": .8,
		"maximum_entry_atr": 2.0,
	}
	if cfg.Parameters == nil {
		cfg.Parameters = map[string]any{}
	}
	for key, value := range defaults {
		if _, ok := cfg.Parameters[key]; !ok {
			cfg.Parameters[key] = value
		}
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*int{"lookback_bars": &s.lookback, "minimum_touches": &s.minimumTouches, "minimum_wick_rejections": &s.minimumWicks, "break_closes": &s.breakCloses, "minimum_inside_closes": &s.minimumInsideCloses, "fallback_minimum_confirmations": &s.fallbackMinimumConfirmations, "inside_lookback_bars": &s.insideLookbackBars, "recent_breakout_lookback_bars": &s.recentBreakoutLookbackBars, "post_impulse_min_inside_closes": &s.postImpulseMinInsideCloses, "post_impulse_lookback_bars": &s.postImpulseLookbackBars, "post_impulse_recent_bars": &s.postImpulseRecentBars} {
		v, err := strategyutil.Int(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	for key, dst := range map[string]*bool{"fallback_enabled": &s.fallbackEnabled, "provisional_enabled": &s.provisionalEnabled, "post_impulse_enabled": &s.postImpulseEnabled} {
		v, ok := cfg.Parameters[key].(bool)
		if !ok {
			return nil, fmt.Errorf("rangeedge: parameter %q must be boolean", key)
		}
		*dst = v
	}
	for key, dst := range map[string]*float64{"cluster_atr": &s.clusterATR, "cluster_min_abs": &s.clusterMinAbs, "cluster_pip_mult": &s.clusterPipMult, "entry_tolerance_atr": &s.entryATR, "maximum_edge_width_atr": &s.maximumEdgeWidthATR, "maximum_entry_atr": &s.maximumEntryATR, "minimum_wick_fraction": &s.minimumWickFraction, "minimum_width_atr": &s.minimumWidthATR, "maximum_width_atr": &s.maximumWidthATR, "minimum_room_atr": &s.minimumRoomATR, "invalidation_buffer_atr": &s.invalidationATR, "expiry_hours": &s.expiryHours, "recent_breakout_buffer_atr": &s.recentBreakoutBufferATR, "recent_breakout_min_span_atr": &s.recentBreakoutMinSpanATR, "fallback_min_width_atr": &s.fallbackMinWidthATR, "fallback_max_width_atr": &s.fallbackMaxWidthATR, "fallback_wick_fraction": &s.fallbackWickFraction, "post_impulse_min_displacement_atr": &s.postImpulseMinDisplacementATR, "post_impulse_max_contraction_atr": &s.postImpulseMaxContractionATR} {
		v, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var err error
	if s.reaction, err = strategyutil.ParseReactionConfig(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.lookback < 5 || s.minimumTouches < 2 || s.minimumWicks < 1 || s.breakCloses < 1 || s.minimumInsideCloses < 1 || s.fallbackMinimumConfirmations < 1 || s.insideLookbackBars < 1 || s.recentBreakoutLookbackBars < 1 || s.postImpulseMinInsideCloses < 1 || s.postImpulseLookbackBars < 1 || s.postImpulseRecentBars < 1 || s.clusterATR <= 0 || s.clusterMinAbs < 0 || s.clusterPipMult < 0 || s.entryATR <= 0 || s.maximumEdgeWidthATR <= 0 || s.maximumEntryATR <= 0 || s.minimumWickFraction < 0 || s.minimumWickFraction > 1 || s.minimumWidthATR <= 0 || s.maximumWidthATR < s.minimumWidthATR || s.minimumRoomATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 || s.recentBreakoutBufferATR < 0 || s.recentBreakoutMinSpanATR <= 0 || s.fallbackMinWidthATR <= 0 || s.fallbackMaxWidthATR < s.fallbackMinWidthATR || s.fallbackWickFraction < 0 || s.fallbackWickFraction > 1 || s.postImpulseMinDisplacementATR <= 0 || s.postImpulseMaxContractionATR <= 0 {
		return nil, fmt.Errorf("rangeedge: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

type contact struct {
	index        int
	price        float64
	wick         bool
	wickFraction float64
	bodyHold     bool
}
type barrier struct {
	side                                             string
	level, low, high                                 float64
	touches, wicks, bodyHolds, accepted, first, last int
	score                                            float64
	grade                                            string
	fallback, executable                             bool
	confirmations                                    int
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	atr := ctx.Volatility.ATR
	if tf == nil || atr <= 0 || len(tf.Candles) < s.lookback {
		return nil
	}
	windowStart := len(tf.Candles) - s.lookback
	if windowStart < 0 {
		windowStart = 0
	}
	barriers := buildBarriers(tf.Candles[windowStart:], windowStart, atr, tf.Geometry.PipSize, s, tf.Session.Levels, tf.Trendline.Lines, tf.Regime)
	if recentBreakoutDisplacement(tf.Candles, atr, s) {
		return nil
	}
	if s.fallbackEnabled {
		barriers = append(barriers, s.fallbackBarriers(tf.Candles[windowStart:], windowStart, tf.Candles, atr, tf.Session.Levels, barriers)...)
	}
	last := tf.Candles[len(tf.Candles)-1]
	lower, upper := bestRange(barriers, last.Close, atr, s, tf.Candles)
	if lower == nil || upper == nil {
		return nil
	}
	eq := (lower.level + upper.level) / 2
	candidates := []*barrier{lower, upper}
	sort.SliceStable(candidates, func(i, j int) bool {
		return math.Abs(candidates[i].level-last.Close) < math.Abs(candidates[j].level-last.Close)
	})
	for _, b := range candidates {
		direction := market.Buy
		opposite := upper.level
		if b.side == "resistance" {
			direction, opposite = market.Sell, lower.level
		}
		grab := strategyutil.GrabForBand(tf, direction, b.low, b.high, 0)
		gradeA := grab != nil && grab.Grade == "A"
		if b.accepted >= s.breakCloses || b.touches < s.minimumTouches && !(b.touches >= 2 && gradeA) || b.wicks < s.minimumWicks && !gradeA {
			continue
		}
		if math.Abs(b.level-eq)/atr < s.minimumRoomATR {
			continue
		}
		if !entryWithinDistance(last.Close, b.low, b.high, direction, atr, s.maximumEntryATR) {
			continue
		}
		recency := len(tf.Candles) - 1 - b.last
		touchLookback := maxInt(s.reaction.LookbackBars, recency+1)
		if touchLookback > s.lookback {
			touchLookback = s.lookback
		}
		// The barrier episode, not its rolling arithmetic mean, is the thesis
		// identity. Including level here produced a new opportunity ID whenever
		// another touch nudged the mean by a tick.
		zoneID := fmt.Sprintf("range:%s:%d", b.side, tf.Candles[b.first].Time)
		rc := strategyutil.ConfirmReactionWithLookbacks(tf, zoneID, direction, b.low, b.high, atr, tf.Candles[b.first].Time, touchLookback, s.reaction.LookbackBars, s.reaction.EngulfingMinimumRangeATR)
		if rc == nil {
			continue
		}
		invalid, reference := b.low-s.invalidationATR*atr, b.high
		if direction == market.Sell {
			invalid, reference = b.high+s.invalidationATR*atr, b.low
		}
		if direction == market.Buy && eq <= reference || direction == market.Sell && eq >= reference {
			continue
		}
		quality := strategyutil.Clamp01(.25 + .06*math.Min(b.score, 10) + .15*boolValue(gradeA))
		grade := "none"
		if grab != nil {
			grade = grab.Grade
		}
		rangeState := stateForRange(lower, upper, tf.Candles, atr, s)
		evidence := []string{"legacy_range_barrier", "barrier_touch_episode", "wick_rejection_history", "legacy_barrier_score", "range_state_" + rangeState, "accepted_close_valid", "structural_reaction_" + rc.Pattern, "liquidity_grab_grade_" + grade}
		cand, err := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: zoneID, Symbol: ctx.Symbol, Direction: direction, EntryLow: b.low, EntryHigh: b.high, Invalidation: invalid, InvalidationLabel: "range_edge_accepted_break", Target: eq, TargetLabel: "range_equilibrium", Evidence: evidence, Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{"touch_history": strategyutil.Clamp01(float64(b.touches) / 4), "wick_history": strategyutil.Clamp01(float64(b.wicks) / 3), "room": strategyutil.Clamp01(math.Abs(b.level-eq) / (s.minimumRoomATR * atr))}}, FormedAt: tf.Candles[b.first].Time, ConfirmedAt: rc.ConfirmationBarTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
		if err != nil {
			continue
		}
		cand.Reaction = rc
		cand.Targets = append(cand.Targets, opportunity.Target{Price: market.PriceLevel{Price: market.Price(opposite), Label: "opposite_range_edge"}})
		if cand.Validate() == nil {
			return []opportunity.Candidate{cand}
		}
	}
	return nil
}

func entryWithinDistance(price, low, high float64, direction market.Direction, atr, maximumATR float64) bool {
	if direction == market.Buy {
		if price < low {
			return false
		}
		if price <= high {
			return true
		}
		return price-high <= maximumATR*atr
	}
	if price > high {
		return false
	}
	if price >= low {
		return true
	}
	return low-price <= maximumATR*atr
}

func buildBarriers(candles []market.Candle, offset int, atr, pipSize float64, s *Strategy, sessionLevels []session.Level, trendlines []technicaltrendline.Trendline, regimeState regime.State) []barrier {
	contacts := map[string][]contact{"support": {}, "resistance": {}}
	for i, c := range candles {
		if c.Range() <= 0 {
			continue
		}
		lower := math.Min(c.Open, c.Close) - c.Low
		upper := c.High - math.Max(c.Open, c.Close)
		lowerWick := lower/c.Range() >= s.minimumWickFraction && c.Close > c.Low
		upperWick := upper/c.Range() >= s.minimumWickFraction && c.Close < c.High
		lowerBody := c.Close <= c.Low+c.Range()*.15 && c.Close >= c.Open
		upperBody := c.Close >= c.High-c.Range()*.15 && c.Close <= c.Open
		microLow := i > 0 && i+1 < len(candles) && c.Low < candles[i-1].Low && c.Low < candles[i+1].Low
		microHigh := i > 0 && i+1 < len(candles) && c.High > candles[i-1].High && c.High > candles[i+1].High
		if lowerWick || microLow {
			contacts["support"] = append(contacts["support"], contact{index: offset + i, price: c.Low, wick: lowerWick, wickFraction: lower / c.Range(), bodyHold: lowerBody})
		}
		if upperWick || microHigh {
			contacts["resistance"] = append(contacts["resistance"], contact{index: offset + i, price: c.High, wick: upperWick, wickFraction: upper / c.Range(), bodyHold: upperBody})
		}
	}
	tol := math.Max(s.clusterMinAbs, math.Max(s.clusterATR*atr, s.clusterPipMult*pipSize))
	half := s.entryATR * atr
	var out []barrier
	for _, side := range []string{"support", "resistance"} {
		items := contacts[side]
		sort.SliceStable(items, func(i, j int) bool { return items[i].price < items[j].price })
		for start := 0; start < len(items); {
			end := start + 1
			for end < len(items) {
				cluster := items[start:end]
				center := 0.0
				for _, item := range cluster {
					center += item.price
				}
				center /= float64(len(cluster))
				unionWidth := items[end].price - items[start].price
				if math.Abs(items[end].price-center) > tol || unionWidth > 2*tol {
					break
				}
				end++
			}
			cluster := append([]contact(nil), items[start:end]...)
			sort.SliceStable(cluster, func(i, j int) bool { return cluster[i].index < cluster[j].index })
			episodes := touchEpisodes(cluster)
			// The frozen range oracle requires two rejected wick episodes before
			// a primary edge exists.  minimum_wick_rejections remains the later
			// execution-quality gate; this earlier structural gate prevents a
			// single incidental wick from becoming a range boundary.
			if len(episodes) >= s.minimumTouches && wicksForEpisodes(episodes) >= 2 {
				level, wicks, bodyHolds := 0.0, 0, 0
				for _, e := range episodes {
					level += e.price
					if e.wick {
						wicks++
					}
					if e.bodyHold {
						bodyHolds++
					}
				}
				level /= float64(len(episodes))
				accepted, run := 0, 0
				for i := episodes[0].index; i < offset+len(candles); i++ {
					close := candles[i-offset].Close
					beyond := side == "support" && close < level-half || side == "resistance" && close > level+half
					if beyond {
						run++
						if run > accepted {
							accepted = run
						}
					} else {
						run = 0
					}
				}
				score := math.Min(5, float64(len(episodes)))*1.2 + math.Min(4, float64(wicks)) + math.Min(3, float64(bodyHolds))*.5
				confluences := sessionConfluenceCount(sessionLevels, level, half) + trendlineConfluenceCount(trendlines, side, level, offset+len(candles)-1, half)
				if side == "support" && math.Abs(regimeState.RangeLow-level) <= half {
					confluences++
				}
				if side == "resistance" && math.Abs(regimeState.RangeHigh-level) <= half {
					confluences++
				}
				score += .75 * math.Min(3, float64(confluences))
				if episodes[len(episodes)-1].index >= offset+len(candles)-3 {
					score += 1
				}
				score -= math.Max(0, float64(accepted)) * 2
				grade := "C"
				if score >= 8 && len(episodes) >= 3 && wicks >= 2 {
					grade = "A"
				} else if score >= 4 && len(episodes) >= 2 {
					grade = "B"
				}
				score = math.Max(0, math.Round(score*1000)/1000)
				out = append(out, barrier{side: side, level: level, low: level - half, high: level + half, touches: len(episodes), wicks: wicks, bodyHolds: bodyHolds, accepted: accepted, first: episodes[0].index, last: episodes[len(episodes)-1].index, score: score, grade: grade, executable: grade == "A" || grade == "B"})
			}
			start = end
		}
	}
	return out
}

// touchEpisodes is the same episode representative selection as the frozen
// range builder: a wick rejection wins, then a body hold, then the larger wick
// fraction, then the earlier contact for deterministic ties.
func touchEpisodes(cluster []contact) []contact {
	var episodes [][]contact
	for _, item := range cluster {
		if len(episodes) > 0 && item.index <= episodes[len(episodes)-1][len(episodes[len(episodes)-1])-1].index+1 {
			episodes[len(episodes)-1] = append(episodes[len(episodes)-1], item)
		} else {
			episodes = append(episodes, []contact{item})
		}
	}
	result := make([]contact, 0, len(episodes))
	for _, episode := range episodes {
		best := episode[0]
		for _, item := range episode[1:] {
			if item.wick != best.wick {
				if item.wick {
					best = item
				}
				continue
			}
			if item.bodyHold != best.bodyHold {
				if item.bodyHold {
					best = item
				}
				continue
			}
			if item.wickFraction != best.wickFraction {
				if item.wickFraction > best.wickFraction {
					best = item
				}
				continue
			}
			if item.index < best.index {
				best = item
			}
		}
		result = append(result, best)
	}
	return result
}

func wicksForEpisodes(episodes []contact) int {
	count := 0
	for _, episode := range episodes {
		if episode.wick {
			count++
		}
	}
	return count
}

// fallbackBarriers is the controlled one-sided range path from the frozen
// Python range builder. It starts from a strong known edge, then requires
// independently observable local-extreme, touch, rejection, session or
// inside-close evidence before making the missing edge executable.
func (s *Strategy) fallbackBarriers(frame []market.Candle, offset int, all []market.Candle, atr float64, sessions []session.Level, existing []barrier) []barrier {
	if len(frame) == 0 || atr <= 0 {
		return nil
	}
	var supports, resistances []barrier
	for _, b := range existing {
		if b.side == "support" {
			supports = append(supports, b)
		} else {
			resistances = append(resistances, b)
		}
	}
	if (len(supports) > 0) == (len(resistances) > 0) {
		return nil
	}
	opposite := supports
	missingSide := "resistance"
	if len(supports) == 0 {
		opposite, missingSide = resistances, "support"
	}
	if len(opposite) == 0 {
		return nil
	}
	sort.SliceStable(opposite, func(i, j int) bool { return opposite[i].score > opposite[j].score })
	strong := opposite[0]
	start := maxInt(0, strong.first-offset)
	if start >= len(frame) {
		start = 0
	}
	level, localIndex := 0.0, start
	if missingSide == "support" {
		level = frame[start].Low
		for i := start; i < len(frame); i++ {
			if frame[i].Low < level {
				level, localIndex = frame[i].Low, i
			}
		}
	} else {
		level = frame[start].High
		for i := start; i < len(frame); i++ {
			if frame[i].High > level {
				level, localIndex = frame[i].High, i
			}
		}
	}
	if (missingSide == "support" && level >= all[len(all)-1].Close) || (missingSide == "resistance" && level <= all[len(all)-1].Close) {
		// Match the oracle's fallback-to-the-opposite-side search when the
		// strongest local extreme is not on the required side of price.
		if missingSide == "support" {
			level, localIndex = fallbackExtreme(frame, all[len(all)-1].Close, true)
		} else {
			level, localIndex = fallbackExtreme(frame, all[len(all)-1].Close, false)
		}
		if localIndex < 0 {
			return nil
		}
	}
	width := math.Abs(strong.level - level)
	if width < s.fallbackMinWidthATR*atr || width > s.fallbackMaxWidthATR*atr {
		return nil
	}
	touches, wicks, bodyHolds := 0, 0, 0
	for _, c := range frame {
		span := c.Range()
		if span <= 0 {
			continue
		}
		touched := (missingSide == "support" && c.Low <= level+s.entryATR*atr && c.High >= level-s.entryATR*atr) || (missingSide == "resistance" && c.High >= level-s.entryATR*atr && c.Low <= level+s.entryATR*atr)
		if !touched {
			continue
		}
		touches++
		if missingSide == "support" && (math.Min(c.Open, c.Close)-c.Low)/span >= s.fallbackWickFraction && c.Close > level {
			wicks++
		}
		if missingSide == "resistance" && (c.High-math.Max(c.Open, c.Close))/span >= s.fallbackWickFraction && c.Close < level {
			wicks++
		}
		if missingSide == "support" && c.Close <= c.Low+.15*span && c.Close >= c.Open || missingSide == "resistance" && c.Close >= c.High-.15*span && c.Close <= c.Open {
			bodyHolds++
		}
	}
	inside := 0
	lo, hi := math.Min(level, strong.level), math.Max(level, strong.level)
	for _, c := range all {
		if c.Close >= lo && c.Close <= hi {
			inside++
		}
	}
	confirmations := 0
	if touches >= 2 {
		confirmations++
	}
	if wicks >= 1 {
		confirmations++
	}
	if hasSessionConfluence(sessions, level, s.entryATR*atr) {
		confirmations++
	}
	if inside >= s.minimumInsideCloses {
		confirmations++
	}
	if confirmations < s.fallbackMinimumConfirmations {
		return nil
	}
	// The frozen fallback score intentionally does not count body holds. They
	// are a confirmation input, not a fallback score bonus.
	score := math.Max(0, math.Round((float64(maxInt(1, touches))*1.2+float64(wicks))*0.65*1000)/1000)
	grade := "C"
	if confirmations >= 2 && score >= 3 {
		grade = "B"
	}
	halfWidth := math.Min(s.entryATR*atr, s.maximumEdgeWidthATR*atr/2)
	return []barrier{{side: missingSide, level: level, low: level - halfWidth, high: level + halfWidth, touches: maxInt(1, touches), wicks: wicks, bodyHolds: bodyHolds, first: offset + localIndex, last: offset + localIndex, score: score, grade: grade, fallback: true, executable: grade == "A" || grade == "B", confirmations: confirmations}}
}

func fallbackExtreme(frame []market.Candle, price float64, support bool) (float64, int) {
	level, index := 0.0, -1
	for i, candle := range frame {
		candidate := candle.High
		if support {
			candidate = candle.Low
			if candidate < price && (index < 0 || candidate < level) {
				level, index = candidate, i
			}
			continue
		}
		if candidate > price && (index < 0 || candidate > level) {
			level, index = candidate, i
		}
	}
	return level, index
}

func hasSessionConfluence(levels []session.Level, price, tolerance float64) bool {
	return sessionConfluenceCount(levels, price, tolerance) > 0
}

func sessionConfluenceCount(levels []session.Level, price, tolerance float64) int {
	count := 0
	for _, level := range levels {
		if !isSessionLevel(level.Name) {
			continue
		}
		if math.Abs(float64(level.Price)-price) <= tolerance {
			count++
		}
	}
	return count
}

func isSessionLevel(name string) bool {
	switch name {
	case "ASIA_H", "ASIA_L", "LONDON_H", "LONDON_L", "NY_H", "NY_L", "PDH", "PDL", "PWH", "PWL":
		return true
	default:
		return false
	}
}

func hasTrendlineConfluence(lines []technicaltrendline.Trendline, side string, price float64, index int, tolerance float64) bool {
	return trendlineConfluenceCount(lines, side, price, index, tolerance) > 0
}

func trendlineConfluenceCount(lines []technicaltrendline.Trendline, side string, price float64, index int, tolerance float64) int {
	count := 0
	for _, line := range lines {
		if line.BrokenAt != nil || line.Kind.String() != side {
			continue
		}
		if math.Abs(float64(technicaltrendline.ValueAt(line, index))-price) <= tolerance {
			count++
		}
	}
	return count
}

// recentBreakoutDisplacement is the frozen range-state broken gate. It runs
// before fallback construction, so a decisive accepted displacement cannot be
// relabelled as a fresh two-sided range.
func recentBreakoutDisplacement(candles []market.Candle, atr float64, s *Strategy) bool {
	if atr <= 0 || len(candles) < s.breakCloses+6 {
		return false
	}
	recentStart := len(candles) - s.breakCloses
	priorStart := recentStart - s.recentBreakoutLookbackBars
	if priorStart < 0 {
		priorStart = 0
	}
	if priorStart >= recentStart {
		return false
	}
	prior := candles[priorStart:recentStart]
	recent := candles[recentStart:]
	priorHigh, priorLow := prior[0].High, prior[0].Low
	for _, candle := range prior[1:] {
		priorHigh = math.Max(priorHigh, candle.High)
		priorLow = math.Min(priorLow, candle.Low)
	}
	up, down := true, true
	for _, candle := range recent {
		up = up && candle.Close > priorHigh+s.recentBreakoutBufferATR*atr
		down = down && candle.Close < priorLow-s.recentBreakoutBufferATR*atr
	}
	if !up && !down {
		return false
	}
	recentHigh, recentLow := recent[0].High, recent[0].Low
	for _, candle := range recent[1:] {
		recentHigh = math.Max(recentHigh, candle.High)
		recentLow = math.Min(recentLow, candle.Low)
	}
	return (recentHigh-recentLow)/atr >= s.recentBreakoutMinSpanATR
}

func bestRange(barriers []barrier, price, atr float64, s *Strategy, candles []market.Candle) (*barrier, *barrier) {
	var bestLow, bestHigh *barrier
	best := rangeRank{state: 99, quality: math.Inf(-1), distance: math.Inf(1), width: math.Inf(1)}
	for i := range barriers {
		if barriers[i].side != "support" || barriers[i].level > price {
			continue
		}
		for j := range barriers {
			if barriers[j].side != "resistance" || barriers[j].level < price {
				continue
			}
			width := barriers[j].level - barriers[i].level
			minimumWidth := math.Max(s.minimumWidthATR, 2*s.minimumRoomATR) * atr
			if width < minimumWidth || width > s.maximumWidthATR*atr {
				continue
			}
			if barriers[i].accepted >= s.breakCloses || barriers[j].accepted >= s.breakCloses || !barriers[i].executable || !barriers[j].executable {
				continue
			}
			eq := (barriers[i].level + barriers[j].level) / 2
			room := math.Min(eq-barriers[i].level, barriers[j].level-eq) / atr
			if room < s.minimumRoomATR {
				continue
			}
			state := stateForRange(&barriers[i], &barriers[j], candles, atr, s)
			if state == "no_range" || state == "broken_range" || state == "provisional_range" && !s.provisionalEnabled {
				continue
			}
			quality := barriers[i].score + barriers[j].score
			if state == "provisional_range" {
				quality *= .75
			}
			if state == "post_impulse_range" {
				quality *= .85
			}
			rank := rangeRank{state: stateRank(state), quality: quality, distance: math.Abs(price - eq), width: width, low: barriers[i].level, high: barriers[j].level}
			if rank.less(best) {
				lowCopy, highCopy := barriers[i], barriers[j]
				bestLow, bestHigh, best = &lowCopy, &highCopy, rank
			}
		}
	}
	return bestLow, bestHigh
}

type rangeRank struct {
	state           int
	quality         float64
	distance, width float64
	low, high       float64
}

func stateRank(state string) int {
	switch state {
	case "confirmed_range":
		return 0
	case "post_impulse_range":
		return 1
	case "provisional_range":
		return 2
	default:
		return 99
	}
}

func (r rangeRank) less(other rangeRank) bool {
	if r.state != other.state {
		return r.state < other.state
	}
	if r.quality != other.quality {
		return r.quality > other.quality
	}
	if r.distance != other.distance {
		return r.distance < other.distance
	}
	if r.width != other.width {
		return r.width < other.width
	}
	if r.low != other.low {
		return r.low < other.low
	}
	return r.high < other.high
}

// stateForRange mirrors the market-state portion of scalp_ranges.py. The
// strategy remains non-executing until reaction confirmation, but its
// candidate now records whether the selected geometry is confirmed,
// post-impulse, or merely provisional.
func stateForRange(lower, upper *barrier, candles []market.Candle, atr float64, s *Strategy) string {
	if lower == nil || upper == nil || atr <= 0 {
		return "no_range"
	}
	if lower.accepted >= s.breakCloses || upper.accepted >= s.breakCloses {
		return "broken_range"
	}
	inside := 0
	start := len(candles) - s.insideLookbackBars
	if start < 0 {
		start = 0
	}
	for _, c := range candles[start:] {
		if c.Close >= lower.level && c.Close <= upper.level {
			inside++
		}
	}
	if inside < s.minimumInsideCloses {
		return "no_range"
	}
	if lower.wicks == 0 && upper.wicks == 0 && lower.bodyHolds == 0 && upper.bodyHolds == 0 {
		return "no_range"
	}
	if (lower.fallback || upper.fallback) && !(lower.fallback && upper.fallback) {
		if s.provisionalEnabled {
			return "provisional_range"
		}
		return "no_range"
	}
	if s.postImpulseEnabled && recentPostImpulse(candles, atr, lower.level, upper.level, s) {
		return "post_impulse_range"
	}
	return "confirmed_range"
}

func recentPostImpulse(candles []market.Candle, atr, lower, upper float64, s *Strategy) bool {
	if len(candles) < s.postImpulseLookbackBars || atr <= 0 {
		return false
	}
	start := len(candles) - s.postImpulseLookbackBars
	if start < 0 {
		start = 0
	}
	minClose, maxClose := candles[start].Close, candles[start].Close
	for _, c := range candles[start:] {
		minClose = math.Min(minClose, c.Close)
		maxClose = math.Max(maxClose, c.Close)
	}
	if (maxClose-minClose)/atr < s.postImpulseMinDisplacementATR {
		return false
	}
	recentStart := len(candles) - s.postImpulseRecentBars
	if recentStart < 0 {
		recentStart = 0
	}
	minPrice, maxPrice := candles[recentStart].Low, candles[recentStart].High
	inside := 0
	for _, c := range candles[recentStart:] {
		minPrice = math.Min(minPrice, c.Low)
		maxPrice = math.Max(maxPrice, c.High)
		if c.Close >= lower && c.Close <= upper {
			inside++
		}
	}
	return (maxPrice-minPrice)/atr <= s.postImpulseMaxContractionATR && inside >= s.postImpulseMinInsideCloses
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
func boolValue(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
