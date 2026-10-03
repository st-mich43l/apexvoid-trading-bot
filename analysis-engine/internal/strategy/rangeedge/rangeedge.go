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
	index int
	price float64
	wick  bool
}
type barrier struct {
	side                                  string
	level, low, high                      float64
	touches, wicks, accepted, first, last int
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
	lower, upper := bestRange(barriers, last.Close, atr, s)
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
		quality := strategyutil.Clamp01(.25 + .1*math.Min(float64(b.touches), 4) + .1*math.Min(float64(b.wicks), 3) + .15*boolValue(gradeA))
		grade := "none"
		if grab != nil {
			grade = grab.Grade
		}
		evidence := []string{"legacy_range_barrier", "barrier_touch_episode", "wick_rejection_history", "accepted_close_valid", "structural_reaction_" + rc.Pattern, "liquidity_grab_grade_" + grade}
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
		contacts["support"] = append(contacts["support"], contact{offset + i, c.Low, lower/c.Range() >= s.minimumWickFraction && c.Close > c.Low})
		contacts["resistance"] = append(contacts["resistance"], contact{offset + i, c.High, upper/c.Range() >= s.minimumWickFraction && c.Close < c.High})
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
				level, wicks := 0.0, 0
				for _, e := range episodes {
					level += e.price
					if e.wick {
						wicks++
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
				out = append(out, barrier{side, level, level - half, level + half, len(episodes), wicks, accepted, episodes[0].index, episodes[len(episodes)-1].index})
			}
			start = end
		}
	}
	return out
}

func bestRange(barriers []barrier, price, atr float64, s *Strategy) (*barrier, *barrier) {
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
			if width < s.minimumWidthATR*atr || width > s.maximumWidthATR*atr {
				continue
			}
			score := math.Abs(price - (barriers[i].level+barriers[j].level)/2)
			if score < bestScore {
				lowCopy, highCopy := barriers[i], barriers[j]
				bestLow, bestHigh, bestScore = &lowCopy, &highCopy, score
			}
		}
	}
	return bestLow, bestHigh
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
