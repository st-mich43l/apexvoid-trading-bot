// Package rangeedge implements M5 range-edge rejection. It requires canonical
// range structure and repeated wick rejection at an established range edge.
package rangeedge

import (
	"fmt"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

const ID strategy.StrategyID = "range_edge"
const Version = "v2"

type Strategy struct {
	lookback, minimumRejections           int
	edgeATR, invalidationATR, expiryHours float64
	fingerprint                           string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("rangeedge: wrong strategy ID %q", cfg.ID)
	}
	lookback, e := strategyutil.Int(cfg.Parameters, "lookback_bars")
	if e != nil {
		return nil, e
	}
	rejects, e := strategyutil.Int(cfg.Parameters, "minimum_rejections")
	if e != nil {
		return nil, e
	}
	edge, e := strategyutil.Float(cfg.Parameters, "edge_tolerance_atr")
	if e != nil {
		return nil, e
	}
	invalid, e := strategyutil.Float(cfg.Parameters, "invalidation_buffer_atr")
	if e != nil {
		return nil, e
	}
	expiry, e := strategyutil.Float(cfg.Parameters, "expiry_hours")
	if e != nil {
		return nil, e
	}
	if lookback < 5 || rejects < 2 || edge <= 0 || invalid <= 0 || expiry <= 0 {
		return nil, fmt.Errorf("rangeedge: invalid parameters")
	}
	return &Strategy{lookback, rejects, edge, invalid, expiry, strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}, nil
}
func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	atr := ctx.Volatility.ATR
	if tf == nil || atr <= 0 || len(tf.Candles) < s.lookback+1 || tf.Structure.Internal.Trend != structure.TrendRange {
		return nil
	}
	bars := tf.Candles[len(tf.Candles)-s.lookback-1:]
	formation, last := bars[:len(bars)-1], bars[len(bars)-1]
	low, high := formation[0].Low, formation[0].High
	lowAt, highAt := formation[0].Time, formation[0].Time
	for _, b := range formation[1:] {
		if b.Low < low {
			low = b.Low
			lowAt = b.Time
		}
		if b.High > high {
			high = b.High
			highAt = b.Time
		}
	}
	tol := s.edgeATR * atr
	direction := market.Direction("")
	edge := 0.0
	rejects := 0
	for _, b := range formation {
		if b.Low <= low+tol && b.Close > low+tol {
			rejects++
		}
	}
	if last.Low <= low+tol && last.Close > low+tol {
		direction, edge = market.Buy, low
	} else {
		rejects = 0
		for _, b := range formation {
			if b.High >= high-tol && b.Close < high-tol {
				rejects++
			}
		}
		if last.High >= high-tol && last.Close < high-tol {
			direction, edge = market.Sell, high
		}
	}
	if !direction.IsValid() || rejects < s.minimumRejections {
		return nil
	}
	invalid, target := edge-s.invalidationATR*atr, high
	if direction == market.Sell {
		invalid, target = edge+s.invalidationATR*atr, low
	}
	q := strategyutil.Clamp01(float64(rejects) / 5)
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("range:%d:%d", lowAt, highAt), Symbol: ctx.Symbol, Direction: direction, EntryLow: edge - tol, EntryHigh: edge + tol, Invalidation: invalid, InvalidationLabel: "range_edge_failed", Target: target, TargetLabel: "opposite_range_edge", Evidence: []string{"m5_canonical_range", "m5_repeated_edge_rejection"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"rejection_history": q, "range_location": 1}}, FormedAt: formation[0].Time, ConfirmedAt: last.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}
