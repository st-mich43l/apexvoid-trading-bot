package structure

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// BreakType distinguishes HOW price crossed a level — source task §20:
// "do not automatically call every penetration a BOS." Whether a held
// Close/Displacement break is itself a BOS or a CHoCH is a separate
// question, answered by ClassifyEvent below using the layer's trend
// context at the time — BreakType alone never implies BOS/CHoCH.
type BreakType uint8

const (
	BreakWick BreakType = iota
	BreakClose
	BreakDisplacement
	BreakSweep
	BreakFailed
)

func (t BreakType) String() string {
	switch t {
	case BreakWick:
		return "wick"
	case BreakClose:
		return "close"
	case BreakDisplacement:
		return "displacement"
	case BreakSweep:
		return "sweep"
	case BreakFailed:
		return "failed"
	default:
		return "unknown"
	}
}

// StructureBreak is one level-crossing event — source task §19. Event is
// EventNone until the state-building pass (hierarchy.go) classifies it
// against the layer's trend/protected context at the time; DetectBreak
// itself never sets it (a pure per-level break scan has no trend context
// to classify against).
type StructureBreak struct {
	Time int64

	Direction market.Direction

	Layer         StructureLayer
	BrokenSwingID string

	Type BreakType

	PenetrationPrice float64
	PenetrationATR   float64

	CloseBeyond  bool
	Displacement bool

	Event EventKind

	ConfirmedAt int64
}

// BreakConfig mirrors config/analysis.yml's analysis.structure.break.*
// leaves.
type BreakConfig struct {
	MinimumPenetrationATR  float64
	SweepReclaimBars       int
	FailedBreakReclaimBars int
	DisplacementMaxBars    int
	Displacement           DisplacementConfig
}

// DetectBreak scans candles from fromIndex forward for the first time
// price crosses level by more than MinimumPenetrationATR, then resolves
// what kind of break it turned out to be — waiting out the configured
// reclaim windows before committing to a held classification (Close/
// Displacement), exactly the same sweep-and-reclaim-forgiveness rule this
// session's PR #574 proved live in the Python system, generalized here to
// the explicit BreakType taxonomy §20 asks for. Returns nil if no crossing
// exists anywhere in the given candles.
//
// ConfirmedAt is always <= the last candle DetectBreak actually looked at.
// When the given candles run out before a reclaim window can fully
// elapse, DetectBreak still returns its best classification given the
// data it has — this is legitimate "confirmed as of the data available,"
// not a causality violation: ConfirmedAt equals the last available
// candle's time in that case, correctly signaling "not yet fully waited
// out," and calling again later with more data may (correctly) revise the
// classification. test/structure/causality_test.go proves the invariant
// that actually matters: once a call's window has genuinely elapsed
// (enough data existed to complete it), re-running on a longer prefix
// never changes that result.
func DetectBreak(candles []market.Candle, fromIndex int, level Swing, atrSeries []float64, cfg BreakConfig) *StructureBreak {
	direction := directionFor(level)

	for i := fromIndex; i < len(candles); i++ {
		atr := atrAt(atrSeries, i)
		tol := cfg.MinimumPenetrationATR * atr
		beyond, penetration := wickBeyond(candles[i], level, tol)
		if !beyond {
			continue
		}

		reclaimWindow := cfg.SweepReclaimBars
		if cfg.FailedBreakReclaimBars > reclaimWindow {
			reclaimWindow = cfg.FailedBreakReclaimBars
		}
		scanEnd := i + reclaimWindow
		if scanEnd > len(candles)-1 {
			scanEnd = len(candles) - 1
		}
		closeBeyondAt := -1
		for k := i; k <= scanEnd; k++ {
			if closeBeyond(candles[k], level) {
				closeBeyondAt = k
				break
			}
		}
		if closeBeyondAt == -1 {
			return &StructureBreak{
				Time: candles[i].Time, Direction: direction, Layer: level.Layer,
				BrokenSwingID: level.ID, Type: BreakWick,
				PenetrationPrice: penetration, PenetrationATR: safeDiv(penetration, atr),
				CloseBeyond: false, ConfirmedAt: candles[scanEnd].Time,
			}
		}

		reclaimScanEnd := closeBeyondAt + cfg.SweepReclaimBars
		if reclaimScanEnd > len(candles)-1 {
			reclaimScanEnd = len(candles) - 1
		}
		reclaimAt := -1
		for j := closeBeyondAt + 1; j <= reclaimScanEnd; j++ {
			if reclaimed(candles[j], level) {
				reclaimAt = j
				break
			}
		}
		closeAtr := atrAt(atrSeries, closeBeyondAt)
		closePenetration := penetrationAt(candles[closeBeyondAt], level)
		if reclaimAt != -1 {
			breakType := BreakSweep
			if reclaimAt-closeBeyondAt <= cfg.FailedBreakReclaimBars {
				breakType = BreakFailed
			}
			return &StructureBreak{
				Time: candles[closeBeyondAt].Time, Direction: direction, Layer: level.Layer,
				BrokenSwingID: level.ID, Type: breakType,
				PenetrationPrice: closePenetration, PenetrationATR: safeDiv(closePenetration, closeAtr),
				CloseBeyond: true, ConfirmedAt: candles[reclaimAt].Time,
			}
		}

		disp, hasDisp := DetectDisplacement(candles, closeBeyondAt, cfg.DisplacementMaxBars, closeAtr, cfg.Displacement)
		breakType := BreakClose
		if hasDisp && disp.Direction == direction {
			breakType = BreakDisplacement
		}
		return &StructureBreak{
			Time: candles[closeBeyondAt].Time, Direction: direction, Layer: level.Layer,
			BrokenSwingID: level.ID, Type: breakType,
			PenetrationPrice: closePenetration, PenetrationATR: safeDiv(closePenetration, closeAtr),
			CloseBeyond: true, Displacement: hasDisp && disp.Direction == direction,
			ConfirmedAt: candles[reclaimScanEnd].Time,
		}
	}
	return nil
}

func directionFor(level Swing) market.Direction {
	if level.Kind == SwingHigh {
		return market.Buy // breaking UP through a high
	}
	return market.Sell // breaking DOWN through a low
}

func wickBeyond(c market.Candle, level Swing, tol float64) (bool, float64) {
	price := float64(level.Price)
	if level.Kind == SwingHigh {
		p := c.High - price
		return p > tol, p
	}
	p := price - c.Low
	return p > tol, p
}

func closeBeyond(c market.Candle, level Swing) bool {
	price := float64(level.Price)
	if level.Kind == SwingHigh {
		return c.Close > price
	}
	return c.Close < price
}

func reclaimed(c market.Candle, level Swing) bool {
	price := float64(level.Price)
	if level.Kind == SwingHigh {
		return c.Close <= price
	}
	return c.Close >= price
}

func penetrationAt(c market.Candle, level Swing) float64 {
	price := float64(level.Price)
	if level.Kind == SwingHigh {
		return c.Close - price
	}
	return price - c.Close
}

func safeDiv(a, b float64) float64 {
	if b <= 0 {
		return 0
	}
	return a / b
}
