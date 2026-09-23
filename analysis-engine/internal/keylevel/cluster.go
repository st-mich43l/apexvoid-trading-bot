package keylevel

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Update is this domain's engine-pipeline entrypoint: Cluster against the
// canonical current ATR (atrSeries' last value — see doc.go on why this
// package uses one scalar rather than porting atr_scalar/atr_at's two
// different ATR reads). Pure and causal, same contract as
// structure.Update/zone.Update/fib.Update.
func Update(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) State {
	return State{Levels: Cluster(candles, lastValue(atrSeries), swings, cfg)}
}

// Cluster ports key_levels(): greedy price-sorted clustering of swings
// into reaction levels, plus a round-number price scan, deduped by
// price proximity, then optionally wick-touch re-enriched when candles
// is non-empty (Python's own optional `bars` parameter).
func Cluster(candles []market.Candle, atr float64, swings []structure.Swing, cfg Config) []Level {
	clusterATR := cfg.ClusterATR
	if clusterATR < 0 {
		clusterATR = 0
	}
	tolerance := atr * clusterATR
	spanMultiple := cfg.MaximumClusterSpanMultiple
	if spanMultiple < 0 {
		spanMultiple = 0
	}
	maxSpan := tolerance * spanMultiple

	var levels []Level
	for _, cluster := range priceClusters(swings, tolerance, maxSpan) {
		if len(cluster) < cfg.MinimumTouches {
			continue
		}
		levels = append(levels, Level{
			Price:    averagePrice(cluster),
			Kind:     KindReaction,
			Touches:  len(cluster),
			Band:     tolerance,
			Strength: float64(len(cluster)),
		})
	}
	levels = append(levels, roundLevels(swings, atr, cfg.RoundStep, tolerance, cfg.MinimumTouches)...)

	deduped := dedupe(levels, tolerance)
	if len(candles) > 0 {
		for i := range deduped {
			deduped[i] = withWickTouches(deduped[i], candles, tolerance)
		}
	}
	return deduped
}

// priceClusters ports _price_clusters: swings sorted ascending by price,
// greedily appended to the last open cluster when _can_join_cluster
// allows it, else starting a new cluster.
func priceClusters(swings []structure.Swing, tolerance, maxSpan float64) [][]structure.Swing {
	sorted := append([]structure.Swing(nil), swings...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Price < sorted[j].Price })

	var clusters [][]structure.Swing
	for _, s := range sorted {
		if n := len(clusters); n > 0 && canJoinCluster(clusters[n-1], s, tolerance, maxSpan) {
			clusters[n-1] = append(clusters[n-1], s)
		} else {
			clusters = append(clusters, []structure.Swing{s})
		}
	}
	return clusters
}

// canJoinCluster ports _can_join_cluster: the cluster's total span
// (including the candidate) must stay within maxSpan, AND the candidate
// must be within tolerance of EVERY existing member (not just the
// nearest one) — the dual gate that keeps a cluster tight even along a
// long, slowly-drifting chain of swings.
func canJoinCluster(cluster []structure.Swing, s structure.Swing, tolerance, maxSpan float64) bool {
	minP, maxP := float64(s.Price), float64(s.Price)
	for _, item := range cluster {
		p := float64(item.Price)
		if p < minP {
			minP = p
		}
		if p > maxP {
			maxP = p
		}
	}
	if maxP-minP > maxSpan {
		return false
	}
	for _, item := range cluster {
		if absF(float64(item.Price)-float64(s.Price)) > tolerance {
			return false
		}
	}
	return true
}

func averagePrice(swings []structure.Swing) market.Price {
	var sum float64
	for _, s := range swings {
		sum += float64(s.Price)
	}
	return market.Price(sum / float64(len(swings)))
}

// roundLevels ports _round_levels: every round_step-aligned price across
// the full swing price range, kept when at least minTouches swings fall
// within max(tolerance, atr*0.25) of it — Python's own per-swing
// atr_at() lookup collapses to the same canonical atr this package uses
// throughout (see doc.go).
func roundLevels(swings []structure.Swing, atr, roundStep, tolerance float64, minTouches int) []Level {
	if len(swings) == 0 || roundStep <= 0 {
		return nil
	}
	low, high := float64(swings[0].Price), float64(swings[0].Price)
	for _, s := range swings {
		p := float64(s.Price)
		if p < low {
			low = p
		}
		if p > high {
			high = p
		}
	}
	lowStep := math.Floor(low/roundStep) * roundStep
	highStep := math.Ceil(high/roundStep) * roundStep
	steps := int((highStep-lowStep)/roundStep) + 1

	band := maxF(tolerance, atr*0.25)
	var levels []Level
	for step := 0; step < steps; step++ {
		price := lowStep + float64(step)*roundStep
		touches := 0
		for _, s := range swings {
			if absF(float64(s.Price)-price) <= band {
				touches++
			}
		}
		if touches >= minTouches {
			levels = append(levels, Level{
				Price: market.Price(price), Kind: KindRound, Touches: touches, Band: tolerance, Strength: float64(touches),
			})
		}
	}
	return levels
}

// dedupe ports key_levels()'s own inline merge pass: levels sorted by
// price, adjacent ones within max(prev.Band, tolerance) of each other
// merged into one (price averaged; touches/band/strength each take the
// max of the pair; kind keeps prev's UNLESS the merged-in level has
// strictly more touches — a tie keeps prev's kind).
func dedupe(levels []Level, tolerance float64) []Level {
	sorted := append([]Level(nil), levels...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Price < sorted[j].Price })

	var out []Level
	for _, lvl := range sorted {
		if n := len(out); n > 0 && absF(float64(out[n-1].Price)-float64(lvl.Price)) <= maxF(out[n-1].Band, tolerance) {
			prev := out[n-1]
			touches := prev.Touches
			if lvl.Touches > touches {
				touches = lvl.Touches
			}
			kind := prev.Kind
			if lvl.Touches > prev.Touches {
				kind = lvl.Kind
			}
			band := prev.Band
			if lvl.Band > band {
				band = lvl.Band
			}
			strength := prev.Strength
			if lvl.Strength > strength {
				strength = lvl.Strength
			}
			out[n-1] = Level{
				Price: market.Price((float64(prev.Price) + float64(lvl.Price)) / 2),
				Kind:  kind, Touches: touches, Band: band, Strength: strength,
			}
		} else {
			out = append(out, lvl)
		}
	}
	return out
}

// withWickTouches ports _with_wick_touches: re-counts a level's touches
// using real OHLC overlap (wickTouchEpisodes) instead of only the
// fractal-swing count it started with, keeping the richer number when it
// is strictly higher.
func withWickTouches(level Level, candles []market.Candle, tolerance float64) Level {
	band := maxF(level.Band, tolerance)
	episodes := wickTouchEpisodes(candles, float64(level.Price), band)
	if episodes <= level.Touches {
		return level
	}
	strength := level.Strength
	if float64(episodes) > strength {
		strength = float64(episodes)
	}
	return Level{Price: level.Price, Kind: level.Kind, Touches: episodes, Band: level.Band, Strength: strength}
}

// wickTouchEpisodes ports wick_touch_episodes: a bar counts as touching
// [price-band, price+band] when its range overlaps the band, UNLESS it
// opened on one side and closed decisively through to the far side (a
// break, not a touch). Consecutive touching bars are one episode.
func wickTouchEpisodes(candles []market.Candle, price, band float64) int {
	if band < 0 {
		return 0
	}
	lo, hi := price-band, price+band
	episodes := 0
	inEpisode := false
	for _, c := range candles {
		touched := c.Low <= hi && c.High >= lo
		if touched {
			if c.Open > hi && c.Close < lo {
				touched = false
			} else if c.Open < lo && c.Close > hi {
				touched = false
			}
		}
		if touched && !inEpisode {
			episodes++
		}
		inEpisode = touched
	}
	return episodes
}

func lastValue(series []float64) float64 {
	if len(series) == 0 {
		return 0
	}
	return series[len(series)-1]
}

func absF(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func maxF(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
