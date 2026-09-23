package zone

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// buildFlipZones ports zones.py::flip_zones(): a broken structural level
// whose break is ACCEPTED (cfg.FlipAcceptBars consecutive closes all
// beyond the level, starting at the break's own index) is re-anchored as
// a zone on the new side. The "level" here is the swing
// structure.StructureBreak.BrokenSwingID references — this package has
// no separate key-level primitive yet (that is Phase S4's
// analysis.strategies.key_level scope), so a broken swing's own price is
// the level, not a second independently-tracked list. Band width is an
// ATR-tolerance floor widened by the break candle's own body (matching
// zones.py's band_body_fraction) — rejected outright if the anchor would
// cross the level (Python's own invalid_anchor guard), never silently
// clamped.
func buildFlipZones(candles []market.Candle, swings []structure.Swing, breaks []structure.StructureBreak, tf market.Timeframe, atr float64, cfg Config) []Zone {
	swingByID := make(map[string]structure.Swing, len(swings))
	for _, s := range swings {
		swingByID[s.ID] = s
	}

	var zones []Zone
	for _, b := range breaks {
		swing, ok := swingByID[b.BrokenSwingID]
		if !ok {
			continue
		}
		breakIdx := indexOfTimeFrom(candles, 0, b.Time)
		if breakIdx < 0 || breakIdx >= len(candles) {
			continue
		}
		level := float64(swing.Price)
		if !acceptedBreak(candles, breakIdx, level, b.Direction, cfg.FlipAcceptBars) {
			continue
		}
		side := Demand
		if b.Direction == market.Sell {
			side = Supply
		}
		band := cfg.FlipLevelBandATR * atr
		if bodyWidth := candles[breakIdx].Body() * cfg.FlipBandBodyFraction; bodyWidth > band {
			band = bodyWidth
		}
		var low, high float64
		if b.Direction == market.Buy {
			low, high = level, level+band
		} else {
			low, high = level-band, level
		}
		if !validFlipAnchor(low, high, level, side) {
			continue
		}
		origin := candles[breakIdx]
		zones = append(zones, Zone{
			ID:           fmt.Sprintf("zone:%s:%s:%d", tf, KindFlip, origin.Time),
			Kind:         KindFlip,
			Side:         side,
			Low:          market.Price(low),
			High:         market.Price(high),
			Timeframe:    tf,
			OriginTime:   origin.Time,
			StructureRef: swing.ID,
			CreatedAt:    origin.Time,
			BreakIndex:   breakIdx,
		})
	}
	return zones
}

// acceptedBreak requires the next acceptBars consecutive closes,
// starting AT from (the break's own index), to all sit beyond level —
// zones.py::_accepted.
func acceptedBreak(candles []market.Candle, from int, level float64, dir market.Direction, acceptBars int) bool {
	if acceptBars <= 0 || from+acceptBars > len(candles) {
		return false
	}
	for i := from; i < from+acceptBars; i++ {
		c := candles[i].Close
		if dir == market.Buy && c <= level {
			return false
		}
		if dir == market.Sell && c >= level {
			return false
		}
	}
	return true
}

// validFlipAnchor is zones.py's invalid_anchor guard, inverted to a
// positive predicate: the zone must stay anchored on the correct side of
// the level (never cross it) and have positive width.
func validFlipAnchor(low, high, level float64, side Side) bool {
	if math.IsNaN(low) || math.IsNaN(high) || math.IsInf(low, 0) || math.IsInf(high, 0) {
		return false
	}
	const eps = 1e-9
	if side == Demand && low < level-eps {
		return false
	}
	if side == Supply && high > level+eps {
		return false
	}
	return high > low
}
