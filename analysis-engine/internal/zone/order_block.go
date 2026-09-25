package zone

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// buildOrderBlockZones ports zones.py::order_blocks(): a displacement
// run only produces an Order Block if a confirming structure break falls
// inside its span. Both BOS and CHoCH count (a real 2026-09-21 Python
// fix — a version that only accepted BOS silently dropped every
// reversal OB). Origin is the last candle in the OPPOSITE direction from
// the run before it starts (the classic "last down candle before an up
// impulse"); the zone band is the origin candle's own body
// ([min(open,close), max(open,close)]), not its full range.
func buildOrderBlockZones(candles []market.Candle, runs []displacementRun, breaks []structure.StructureBreak, tf market.Timeframe, atr float64, cfg Config) []Zone {
	var zones []Zone
	for _, run := range runs {
		if run.StartIndex <= 0 || run.EndIndex >= len(candles) {
			continue
		}
		confirming, ok := causingBreak(breaks, run, candles)
		if !ok {
			continue
		}
		confirmIndex := indexOfTimeFrom(candles, run.StartIndex, confirming.Time)
		if confirmIndex < 0 || !QualifyingBreakBody(candles[confirmIndex], cfg.OrderBlockBodyFraction) {
			continue
		}
		originIdx := lastOppositeCandle(candles, run.StartIndex, run.Direction)
		if originIdx < 0 {
			continue
		}
		origin := candles[originIdx]
		low, high := origin.Open, origin.Close
		if low > high {
			low, high = high, low
		}
		side := Demand
		if run.Direction == market.Sell {
			side = Supply
		}
		zones = append(zones, Zone{
			ID:              fmt.Sprintf("zone:%s:%s:%d", tf, KindOrderBlock, origin.Time),
			Kind:            KindOrderBlock,
			Side:            side,
			Low:             market.Price(low),
			High:            market.Price(high),
			Timeframe:       tf,
			OriginTime:      origin.Time,
			StructureRef:    confirming.BrokenSwingID,
			DisplacementRef: fmt.Sprintf("displacement:%s:%d:%d", tf, run.StartTime, run.EndTime),
			CreatedAt:       origin.Time,
			BreakIndex:      run.EndIndex,
			Strength:        run.Strength,
		})
	}
	return zones
}

// QualifyingBreakBody reports whether c's body/range ratio clears
// minimumFraction — technique_geometry.py::validate_technique_instance's
// OB-only momentum_body_frac gate (cfg.OrderBlockBodyFraction). Exported
// for test/zone's black-box coverage of this one small primitive check
// (see ADR-006 — all tests live under test/, never colocated inside this
// package).
func QualifyingBreakBody(c market.Candle, minimumFraction float64) bool {
	if minimumFraction <= 0 {
		return true
	}
	rangeSize := c.Range()
	return rangeSize > 0 && c.Body()/rangeSize >= minimumFraction
}

// causingBreak finds the first BOS or CHoCH break, matching run's
// direction, whose Time falls inside [run start, run end] — the exact
// [leg.start, leg.end] window zones.py::_causing_bos scans.
func causingBreak(breaks []structure.StructureBreak, run displacementRun, candles []market.Candle) (structure.StructureBreak, bool) {
	if run.StartIndex >= len(candles) || run.EndIndex >= len(candles) {
		return structure.StructureBreak{}, false
	}
	windowStart := candles[run.StartIndex].Time
	windowEnd := candles[run.EndIndex].Time
	for _, b := range breaks {
		if b.Event != structure.EventBOS && b.Event != structure.EventCHoCH {
			continue
		}
		if b.Direction != run.Direction {
			continue
		}
		if b.Time < windowStart || b.Time > windowEnd {
			continue
		}
		return b, true
	}
	return structure.StructureBreak{}, false
}

// lastOppositeCandle scans backward from before-index (exclusive) for
// the most recent candle whose direction is opposite dir, or -1 if none
// exists.
func lastOppositeCandle(candles []market.Candle, beforeIndex int, dir market.Direction) int {
	for i := beforeIndex - 1; i >= 0; i-- {
		c := candles[i]
		if dir == market.Buy && c.IsBearish() {
			return i
		}
		if dir == market.Sell && c.IsBullish() {
			return i
		}
	}
	return -1
}
