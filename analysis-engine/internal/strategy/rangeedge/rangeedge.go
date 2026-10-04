// Package rangeedge restores the Python local-barrier range rejection thesis.
package rangeedge

import (
	"fmt"
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "range_edge"
const Version = "v2"

type Strategy struct {
	lookback, minimumTouches, minimumWicks, breakCloses int
	clusterATR, entryATR, minimumWickFraction           float64
	minimumWidthATR, maximumWidthATR, minimumRoomATR    float64
	invalidationATR, expiryHours                        float64
	reaction                                            strategyutil.ReactionConfig
	fingerprint                                         string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("rangeedge: wrong strategy ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*int{"lookback_bars": &s.lookback, "minimum_touches": &s.minimumTouches, "minimum_wick_rejections": &s.minimumWicks, "break_closes": &s.breakCloses} {
		v, err := strategyutil.Int(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	for key, dst := range map[string]*float64{"cluster_atr": &s.clusterATR, "entry_tolerance_atr": &s.entryATR, "minimum_wick_fraction": &s.minimumWickFraction, "minimum_width_atr": &s.minimumWidthATR, "maximum_width_atr": &s.maximumWidthATR, "minimum_room_atr": &s.minimumRoomATR, "invalidation_buffer_atr": &s.invalidationATR, "expiry_hours": &s.expiryHours} {
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
	if s.lookback < 5 || s.minimumTouches < 2 || s.minimumWicks < 1 || s.breakCloses < 1 || s.clusterATR <= 0 || s.entryATR <= 0 || s.minimumWickFraction < 0 || s.minimumWickFraction > 1 || s.minimumWidthATR <= 0 || s.maximumWidthATR < s.minimumWidthATR || s.minimumRoomATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("rangeedge: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

type contact struct {
	index    int
	price    float64
	wick     bool
	bodyHold bool
}
type barrier struct {
	side                                             string
	level, low, high                                 float64
	touches, wicks, bodyHolds, accepted, first, last int
	score                                            float64
	grade                                            string
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
	barriers := buildBarriers(tf.Candles[windowStart:], windowStart, atr, s)
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
		minimumWicks := s.minimumWicks
		if minimumWicks < 2 {
			minimumWicks = 2 // legacy primary-barrier construction rule
		}
		if b.accepted >= s.breakCloses || b.touches < s.minimumTouches && !(b.touches >= 2 && gradeA) || b.wicks < minimumWicks && !gradeA {
			continue
		}
		if math.Abs(b.level-eq)/atr < s.minimumRoomATR {
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

func buildBarriers(candles []market.Candle, offset int, atr float64, s *Strategy) []barrier {
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
			contacts["support"] = append(contacts["support"], contact{offset + i, c.Low, lowerWick, lowerBody})
		}
		if upperWick || microHigh {
			contacts["resistance"] = append(contacts["resistance"], contact{offset + i, c.High, upperWick, upperBody})
		}
	}
	tol, half := s.clusterATR*atr, s.entryATR*atr
	var out []barrier
	for _, side := range []string{"support", "resistance"} {
		items := contacts[side]
		sort.SliceStable(items, func(i, j int) bool { return items[i].price < items[j].price })
		for start := 0; start < len(items); {
			end := start + 1
			for end < len(items) && items[end].price-items[end-1].price <= tol && items[end].price-items[start].price <= 2*tol {
				end++
			}
			cluster := append([]contact(nil), items[start:end]...)
			sort.SliceStable(cluster, func(i, j int) bool { return cluster[i].index < cluster[j].index })
			episodes := cluster[:0]
			for _, c := range cluster {
				if len(episodes) == 0 || c.index > episodes[len(episodes)-1].index+1 {
					episodes = append(episodes, c)
				} else if c.wick && !episodes[len(episodes)-1].wick {
					episodes[len(episodes)-1] = c
				}
			}
			if len(episodes) >= 2 {
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
				out = append(out, barrier{side, level, level - half, level + half, len(episodes), wicks, bodyHolds, accepted, episodes[0].index, episodes[len(episodes)-1].index, math.Max(0, math.Round(score*1000)/1000), grade})
			}
			start = end
		}
	}
	return out
}

func bestRange(barriers []barrier, price, atr float64, s *Strategy, candles []market.Candle) (*barrier, *barrier) {
	var bestLow, bestHigh *barrier
	bestScore := math.Inf(1)
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
			if barriers[i].accepted >= s.breakCloses || barriers[j].accepted >= s.breakCloses || barriers[i].grade == "C" || barriers[j].grade == "C" {
				continue
			}
			eq := (barriers[i].level + barriers[j].level) / 2
			room := math.Min(eq-barriers[i].level, barriers[j].level-eq) / atr
			if room < s.minimumRoomATR {
				continue
			}
			stateRank := 2.0
			switch stateForRange(&barriers[i], &barriers[j], candles, atr, s) {
			case "confirmed_range":
				stateRank = 0
			case "post_impulse_range":
				stateRank = 1
			}
			score := stateRank*1000 + math.Abs(price-eq) - .01*(barriers[i].score+barriers[j].score)
			if score < bestScore {
				lowCopy, highCopy := barriers[i], barriers[j]
				bestLow, bestHigh, bestScore = &lowCopy, &highCopy, score
			}
		}
	}
	return bestLow, bestHigh
}

// stateForRange mirrors the market-state portion of scalp_ranges.py. The
// strategy remains non-executing until reaction confirmation, but its
// candidate now records whether the selected geometry is confirmed,
// post-impulse, or merely provisional.
func stateForRange(lower, upper *barrier, candles []market.Candle, atr float64, s *Strategy) string {
	if lower == nil || upper == nil || atr <= 0 {
		return "no_range"
	}
	inside := 0
	start := len(candles) - 24
	if start < 0 {
		start = 0
	}
	for _, c := range candles[start:] {
		if c.Close >= lower.level && c.Close <= upper.level {
			inside++
		}
	}
	if inside < 3 {
		return "provisional_range"
	}
	if recentPostImpulse(candles, atr, lower.level, upper.level) {
		return "post_impulse_range"
	}
	return "confirmed_range"
}

func recentPostImpulse(candles []market.Candle, atr, lower, upper float64) bool {
	if len(candles) < 8 || atr <= 0 {
		return false
	}
	start := len(candles) - 36
	if start < 0 {
		start = 0
	}
	minClose, maxClose := candles[start].Close, candles[start].Close
	for _, c := range candles[start:] {
		minClose = math.Min(minClose, c.Close)
		maxClose = math.Max(maxClose, c.Close)
	}
	if (maxClose-minClose)/atr < 3 {
		return false
	}
	recentStart := len(candles) - 6
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
	return (maxPrice-minPrice)/atr <= 2.2 && inside >= 4
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
