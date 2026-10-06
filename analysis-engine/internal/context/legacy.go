package context

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// LegacyFrame is one timeframe's detector-contract read: the bounded analysis
// window the frozen detectors were evaluated on and every fact derived from
// exactly that window (swings, structure, levels, zones, liquidity, sessions,
// trendlines, dealing range, regime, momentum). It is computed once per closed
// bar by the engine from the canonical candles; strategies that reproduce a
// frozen detector decision consume it so their direction, premium/discount,
// chop, zone and sweep gates are the ones the frozen detector contract
// defines. The hierarchical canonical structure remains the read for
// everything else.
type LegacyFrame struct {
	Bars []market.Candle
	ATR  []float64
	// DetectorATR is the detectors' own volatility (Wilder-smoothed true range),
	// distinct from the simple ATR series the analysis steps use.
	DetectorATR float64
	// Compat are the default arguments of the frozen compat helpers.
	Compat    techniquezone.CompatConfig
	Swings    []techniquezone.Swing
	Structure string // "up" | "down" | "range"
	Breaks    []techniquezone.Break
	Levels    []techniquezone.Level
	// SwingLevels are the levels as counted before 21 Sep 2026, from fractal
	// swings only, and ContractZones the merged, mitigation-stamped zones built
	// from them with order blocks caused by a BOS break only. Together they are
	// the structure the profitable-week (14–18 Sep) Key Level read; the two
	// later changes (wick-touch episodes in the touch count, CHoCH-caused order
	// blocks) each moved its decisions. Other strategies read Levels/Zones.
	SwingLevels   []techniquezone.Level
	ContractZones []techniquezone.Zone
	Legs          []techniquezone.Leg
	// Zones is the merged, mitigation-stamped and multi-timeframe-scored zone
	// population; OrderBlocks is its order-block view.
	Zones       []techniquezone.Zone
	OrderBlocks []techniquezone.Zone
	Pools       []techniquezone.Pool
	Grabs       []techniquezone.Grab
	Sessions    []techniquezone.SessionRef
	Trendlines  []trendline.Trendline
	// Barriers/ScalpRange are the scalp structure: micro barriers and the best
	// local range with its state.
	Barriers                               []techniquezone.ScalpBarrier
	ScalpRange                             *techniquezone.ScalpRange
	ScalpState                             string
	Range                                  *fib.DealingRange
	FibLadder                              []fib.Level
	Regime                                 regime.State
	Momentum                               momentum.State
	MomentumVelocity, MomentumAcceleration float64
}

// LegacyRead is the symbol-level part of the detector-contract read.
type LegacyRead struct {
	// LocalStructure is the primary timeframe's structure label.
	LocalStructure string
	// HTFBias is "up", "down", "range" or "unknown" (higher timeframe not warm).
	HTFBias           string
	AllowCounterTrend bool
}

// Direction is the frozen detector direction: the primary timeframe's own
// structure when counter-trend setups are allowed and it is decided, otherwise
// the higher-timeframe bias. An undecided read has no direction.
func (r LegacyRead) Direction() market.Direction {
	bias := r.HTFBias
	if r.AllowCounterTrend && (r.LocalStructure == "up" || r.LocalStructure == "down") {
		bias = r.LocalStructure
	}
	switch bias {
	case "up":
		return market.Buy
	case "down":
		return market.Sell
	default:
		return ""
	}
}

// AlignedWithHTF reports whether direction agrees with the higher-timeframe
// bias, the `htf_aligned` confluence factor.
func (r LegacyRead) AlignedWithHTF(direction market.Direction) bool {
	return direction == market.Buy && r.HTFBias == "up" || direction == market.Sell && r.HTFBias == "down"
}
