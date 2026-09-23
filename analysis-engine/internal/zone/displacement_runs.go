package zone

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// displacementRun pairs a structure.Displacement with the candle indices
// it spans. DetectDisplacement itself only returns timestamps; every
// zone kind builder that consumes a run needs indices (to find base
// candles, confirming breaks, and where mitigation scanning starts)
// without repeatedly re-deriving them from timestamps.
type displacementRun struct {
	structure.Displacement
	StartIndex int
	EndIndex   int
}

// detectDisplacementRuns scans candles[0:] for every non-overlapping
// qualifying displacement run — zones.py::displacement()'s own
// whole-dataframe walk, ported onto structure.DetectDisplacement's
// shortest-qualifying-run search instead of Python's fixed
// same-direction-candle grouping (source task §26: "do not reduce
// displacement to one large candle... prefer the shortest qualifying
// run"). Runs are found in Time order and never overlap: once a run is
// found starting at i, scanning resumes at its own EndIndex+1, mirroring
// how Python's leg walker never revisits candles already claimed by the
// previous leg.
func detectDisplacementRuns(candles []market.Candle, atrSeries []float64, maxBars int, cfg structure.DisplacementConfig) []displacementRun {
	var runs []displacementRun
	i := 0
	for i < len(candles) {
		if i >= len(atrSeries) {
			break
		}
		d, ok := structure.DetectDisplacement(candles, i, maxBars, atrSeries[i], cfg)
		if !ok {
			i++
			continue
		}
		endIndex := indexOfTimeFrom(candles, i, d.EndTime)
		if endIndex < i {
			i++
			continue
		}
		runs = append(runs, displacementRun{Displacement: d, StartIndex: i, EndIndex: endIndex})
		i = endIndex + 1
	}
	return runs
}

// indexOfTimeFrom linearly scans forward from `from` (displacement runs
// are short, bounded by maxBars, so this is cheap and avoids a second
// binary search per run) for the candle at exactly time t.
func indexOfTimeFrom(candles []market.Candle, from int, t int64) int {
	for i := from; i < len(candles); i++ {
		if candles[i].Time == t {
			return i
		}
	}
	return -1
}
