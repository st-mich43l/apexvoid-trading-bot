// Package legacyread computes the detector-contract read: the bounded-window
// swing, structure, level, zone, liquidity, session, trendline, dealing-range,
// regime, momentum and higher-timeframe-bias facts that the frozen detectors
// gate their decisions on.
//
// It is derived only from the canonical closed candles the engine already
// owns, with the exact-parity ports of the frozen analysis steps, and is never
// a second source of bars. It exists so a strategy that reproduces a frozen
// detector decision (direction, premium/discount gate, chop gate, zone
// selection, sweep grade, confluence stars) reads the same facts the frozen
// detector read; the hierarchical canonical structure keeps owning every other
// consumer.
package legacyread

import (
	"fmt"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// Config carries every tunable of the read; all values come from
// config/analysis.yml (instrument-scale values are applied per instrument).
type Config struct {
	ATRLength               int
	FractalN                int
	ZigzagPct               float64
	ZigzagATRMult           float64
	WindowBars              map[market.Timeframe]int
	HTFOrder                []market.Timeframe
	MinPrimaryHTFWarmupBars int
	AllowCounterTrend       bool

	LevelClusterATR     float64
	LevelMinimumTouches int
	// FrameLevelMinimumTouches is the instrument's analysis.levels.minimum_key_touches
	// override, which the frozen analysis applied to every level it built (and so
	// to the zones scored against them). Zero means no override. The profitable-
	// week SwingLevels contract keeps LevelMinimumTouches.
	FrameLevelMinimumTouches   int
	MaximumClusterSpanMultiple float64
	DisplacementATRMult        float64
	MomentumBodyFraction       float64
	ZoneWidth                  string
	ZoneMergeOverlap           float64
	MaximumMergedZoneATR       float64
	FlipAcceptBars             int
	FlipMaximumBreakAgeBars    int
	FlipBandBodyFraction       float64
	EqualToleranceATR          float64
	InducementBandATR          float64
	SweepBodyFraction          float64
	SweepReactBars             int

	// Instrument scale, applied per instrument.
	PipSize   float64
	RoundStep float64

	Scalp     techniquezone.ScalpConfig
	Compat    techniquezone.CompatConfig
	Fib       fib.Config
	Regime    regime.Config
	Momentum  momentum.Config
	Session   session.Config
	Trendline trendline.Config
}

// stageOne is one timeframe's frame before the multi-timeframe zone score.
type stageOne struct {
	frame analysiscontext.LegacyFrame
	input techniquezone.ScoreInputs
	// unscored are the merged, mitigation-stamped zones; techniqueUnscored the
	// unmerged technique zones.
	unscored          []techniquezone.Zone
	techniqueUnscored []techniquezone.Zone
}

// Stage computes one timeframe's frame from its closed candles. weekly are the
// prior-week levels of the highest timeframe, appended to every timeframe's
// session levels exactly as the frozen analysis does.
func Stage(candles []market.Candle, tf market.Timeframe, weekly []session.Level, cfg Config) Staged {
	bars := candles
	if window := cfg.WindowBars[tf]; window > 0 && len(bars) > window {
		bars = bars[len(bars)-window:]
	}
	out := stageOne{frame: analysiscontext.LegacyFrame{Bars: bars, Structure: "range", Momentum: momentum.Neutral}}
	if len(bars) == 0 {
		return Staged{stage: out}
	}
	frame := &out.frame
	atr := techniquezone.ATRSeries(bars, cfg.ATRLength)
	swings := techniquezone.FindSwings(bars, cfg.FractalN, cfg.ZigzagPct, cfg.ZigzagATRMult, atr, -1)
	frame.ATR, frame.Swings = atr, swings
	frame.DetectorATR = techniquezone.DetectorATR(bars, cfg.ATRLength, 1)
	frame.Compat = cfg.Compat
	frame.Structure = techniquezone.MarketStructure(swings)
	frame.Breaks = techniquezone.StructureBreaks(swings, bars, false, cfg.FractalN)

	canonical := make([]structure.Swing, 0, len(swings))
	for i, swing := range swings {
		kind := structure.SwingHigh
		if swing.Kind == "low" {
			kind = structure.SwingLow
		}
		confirmed := swing.ConfirmedIndex
		if confirmed < 0 || confirmed >= len(bars) {
			confirmed = swing.Index
		}
		canonical = append(canonical, structure.Swing{
			ID: fmt.Sprintf("legacy:%d:%d", swing.Index, i), Kind: kind, Price: market.Price(swing.Price),
			Time: bars[swing.Index].Time, ConfirmedAt: bars[confirmed].Time,
		})
	}
	frame.Trendlines = trendline.Build(bars, atr, canonical, cfg.Trendline)
	frameTouches := cfg.LevelMinimumTouches
	if cfg.FrameLevelMinimumTouches > 0 {
		frameTouches = cfg.FrameLevelMinimumTouches
	}
	frame.Levels = techniquezone.KeyLevels(swings, atr, cfg.LevelClusterATR, cfg.RoundStep, frameTouches, cfg.MaximumClusterSpanMultiple, bars)
	frame.Legs = techniquezone.Displacement(bars, atr, cfg.DisplacementATRMult, cfg.MomentumBodyFraction)

	supplyDemand := techniquezone.BreakerBlocks(techniquezone.SupplyDemand(bars, frame.Legs), bars)
	orderBlocks := techniquezone.BreakerBlocks(techniquezone.OrderBlocks(bars, frame.Legs, frame.Breaks, cfg.ZoneWidth), bars)
	flips := techniquezone.FlipZones(frame.Levels, frame.Breaks, bars, cfg.FlipAcceptBars, cfg.FlipMaximumBreakAgeBars, cfg.FlipBandBodyFraction)
	gaps := techniquezone.FVG(bars)
	frame.Pools = techniquezone.LiquidityPools(swings, bars, cfg.EqualToleranceATR, atr, cfg.MaximumClusterSpanMultiple)

	sessions := session.Update(bars, cfg.Session).Levels
	for _, level := range sessions {
		if level.Name == "PWH" || level.Name == "PWL" {
			continue
		}
		frame.Sessions = append(frame.Sessions, techniquezone.SessionRef{Name: level.Name, Price: float64(level.Price), Swept: level.Swept})
	}
	for _, level := range weekly {
		frame.Sessions = append(frame.Sessions, techniquezone.SessionRef{Name: level.Name, Price: float64(level.Price), Swept: level.Swept})
	}

	price := bars[len(bars)-1].Close
	rangeHigh, rangeLow, hasRange := 0.0, 0.0, false
	hasEq, equilibrium := false, 0.0
	if dealing, ok := fib.Resolve(canonical, market.Price(price), cfg.Fib); ok {
		frame.Range = &dealing
		rangeHigh, rangeLow, hasRange = float64(dealing.High), float64(dealing.Low), true
		hasEq, equilibrium = true, float64(dealing.Equilibrium)
	}
	frame.FibLadder = fib.LadderForPrice(canonical, price)
	frame.Regime = regime.Classify(bars, atr, canonical, frame.Structure, rangeHigh, rangeLow, hasRange, cfg.Regime)
	mom := momentum.Classify(bars, atr, cfg.Momentum)
	frame.Momentum, frame.MomentumVelocity, frame.MomentumAcceleration = mom.State, mom.Velocity, mom.Acceleration

	all := make([]techniquezone.Zone, 0, len(supplyDemand)+len(orderBlocks)+len(flips)+len(gaps))
	all = append(all, supplyDemand...)
	all = append(all, orderBlocks...)
	all = append(all, flips...)
	all = append(all, gaps...)
	maxWidth := techniquezone.ATRScalar(atr, 1) * maxFloat(0, cfg.MaximumMergedZoneATR)
	merged := techniquezone.MergeZones(all, cfg.ZoneMergeOverlap, maxWidth)
	out.unscored = techniquezone.MarkMitigation(merged, bars, maxInt(0, len(bars)-1))
	frame.Grabs = techniquezone.LiquidityGrabs(bars, frame.Pools, frame.Legs, out.unscored, atr, cfg.SweepBodyFraction, cfg.SweepReactBars, cfg.InducementBandATR, cfg.PipSize)

	out.input = techniquezone.ScoreInputs{
		Levels: frame.Levels, Pools: frame.Pools, RoundStep: cfg.RoundStep, Sessions: frame.Sessions,
		HasEq: hasEq, Equilibrium: equilibrium, Grabs: frame.Grabs, HasBarIndex: true, PipSize: cfg.PipSize,
	}
	last := len(bars) - 1
	for _, line := range frame.Trendlines {
		if line.BrokenAt == nil {
			out.input.LineValues = append(out.input.LineValues, float64(trendline.ValueAt(line, last)))
		}
	}
	frame.Zones = techniquezone.ScoreZones(out.unscored, out.input)
	singles := make([]techniquezone.Zone, 0, len(supplyDemand)+len(orderBlocks)+len(gaps))
	singles = append(singles, supplyDemand...)
	singles = append(singles, orderBlocks...)
	singles = append(singles, gaps...)
	out.techniqueUnscored = techniquezone.MarkMitigation(techniquezone.AsSingleZones(singles), bars, maxInt(0, len(bars)-1))
	frame.TechniqueZones = techniquezone.ScoreZones(out.techniqueUnscored, out.input)

	// The profitable-week structure (see LegacyFrame.SwingLevels).
	frame.SwingLevels = techniquezone.KeyLevels(swings, atr, cfg.LevelClusterATR, cfg.RoundStep, cfg.LevelMinimumTouches, cfg.MaximumClusterSpanMultiple, nil)
	contractBlocks := techniquezone.BreakerBlocks(techniquezone.OrderBlocks(bars, frame.Legs, techniquezone.BOSBreaks(frame.Breaks), cfg.ZoneWidth), bars)
	contractFlips := techniquezone.FlipZones(frame.SwingLevels, frame.Breaks, bars, cfg.FlipAcceptBars, cfg.FlipMaximumBreakAgeBars, cfg.FlipBandBodyFraction)
	contract := make([]techniquezone.Zone, 0, len(supplyDemand)+len(contractBlocks)+len(contractFlips)+len(gaps))
	contract = append(contract, supplyDemand...)
	contract = append(contract, contractBlocks...)
	contract = append(contract, contractFlips...)
	contract = append(contract, gaps...)
	contractInput := out.input
	contractInput.Levels = frame.SwingLevels
	frame.ContractZones = techniquezone.ScoreZones(techniquezone.MarkMitigation(techniquezone.MergeZones(contract, cfg.ZoneMergeOverlap, maxWidth), bars, maxInt(0, len(bars)-1)), contractInput)
	scalp := cfg.Scalp
	scalp.PipSize, scalp.RoundStep = cfg.PipSize, cfg.RoundStep
	lines := make([]techniquezone.ScalpLine, 0, len(frame.Trendlines))
	for _, line := range frame.Trendlines {
		lines = append(lines, techniquezone.ScalpLine{Kind: line.Kind.String(), Broken: line.BrokenAt != nil, Touches: 2 + len(line.ValidationTouches), Value: float64(trendline.ValueAt(line, last))})
	}
	structureResult := techniquezone.BuildScalpStructure(bars, atr, frame.Sessions, lines, frame.Regime.RangeHigh, frame.Regime.RangeLow, true, scalp)
	frame.Barriers, frame.ScalpRange, frame.ScalpState = structureResult.Barriers, structureResult.Range, structureResult.State
	frame.OrderBlocks = orderBlockView(frame.Zones)
	return Staged{stage: out}
}

// Staged is one timeframe's stage-one frame, cached until its last bar changes.
type Staged struct{ stage stageOne }

// Bars exposes the analysis window of the staged frame.
func (s Staged) Bars() []market.Candle { return s.stage.frame.Bars }

// Complete scores every timeframe's zones against the zones of all higher
// timeframes (highest first), as the frozen multi-timeframe scoring does, and
// returns the finished frames. Inputs are not modified.
func Complete(staged map[market.Timeframe]Staged) map[market.Timeframe]*analysiscontext.LegacyFrame {
	order := make([]market.Timeframe, 0, len(staged))
	for tf := range staged {
		order = append(order, tf)
	}
	sort.Slice(order, func(i, j int) bool {
		mi, _ := order[i].Minutes()
		mj, _ := order[j].Minutes()
		if mi != mj {
			return mi > mj
		}
		return order[i] < order[j]
	})
	frames := make(map[market.Timeframe]*analysiscontext.LegacyFrame, len(staged))
	var higher []techniquezone.Zone
	for _, tf := range order {
		stage := staged[tf].stage
		frame := stage.frame
		if len(higher) > 0 && len(stage.unscored) > 0 {
			input := stage.input
			input.HTFZones = higher
			frame.Zones = techniquezone.ScoreZones(frame.Zones, input)
			frame.OrderBlocks = orderBlockView(frame.Zones)
		}
		if len(higher) > 0 && len(stage.techniqueUnscored) > 0 {
			input := stage.input
			input.HTFZones = higher
			frame.TechniqueZones = techniquezone.ScoreZones(frame.TechniqueZones, input)
		}
		higher = append(higher, frame.Zones...)
		copied := frame
		frames[tf] = &copied
	}
	return frames
}

func orderBlockView(zones []techniquezone.Zone) []techniquezone.Zone {
	var out []techniquezone.Zone
	for _, zone := range zones {
		if hasSource(zone, "order_block") {
			out = append(out, zone)
		}
	}
	return out
}

func hasSource(zone techniquezone.Zone, source string) bool {
	if zone.Source == source {
		return true
	}
	for _, candidate := range zone.Sources {
		if candidate == source {
			return true
		}
	}
	return false
}

// Weekly returns the prior-week levels of the highest timeframe's window.
func Weekly(candles []market.Candle, tf market.Timeframe, cfg Config) []session.Level {
	bars := candles
	if window := cfg.WindowBars[tf]; window > 0 && len(bars) > window {
		bars = bars[len(bars)-window:]
	}
	if len(bars) == 0 {
		return nil
	}
	var out []session.Level
	for _, level := range session.Update(bars, cfg.Session).Levels {
		if level.Name == "PWH" || level.Name == "PWL" {
			out = append(out, level)
		}
	}
	return out
}

// Read combines the per-timeframe frames into the symbol-level read.
func Read(frames map[market.Timeframe]*analysiscontext.LegacyFrame, primary market.Timeframe, cfg Config) analysiscontext.LegacyRead {
	read := analysiscontext.LegacyRead{LocalStructure: "range", AllowCounterTrend: cfg.AllowCounterTrend, HTFBias: htfBias(frames, cfg)}
	if frame := frames[primary]; frame != nil {
		read.LocalStructure = frame.Structure
	}
	return read
}

// htfBias ports _htf_bias: the first configured higher timeframe with a
// decided read wins, then any frame from the highest timeframe down. The
// primary higher timeframe must be warm, otherwise the bias fails closed.
func htfBias(frames map[market.Timeframe]*analysiscontext.LegacyFrame, cfg Config) string {
	if len(cfg.HTFOrder) > 0 {
		if frame := frames[cfg.HTFOrder[0]]; frame == nil || len(frame.Bars) < cfg.MinPrimaryHTFWarmupBars {
			return "unknown"
		}
	}
	for _, tf := range cfg.HTFOrder {
		if frame := frames[tf]; frame != nil {
			if bias := frameBias(frame); bias != "range" {
				return bias
			}
		}
	}
	ordered := make([]market.Timeframe, 0, len(frames))
	for tf, frame := range frames {
		if frame != nil {
			ordered = append(ordered, tf)
		}
	}
	sort.Slice(ordered, func(i, j int) bool {
		mi, _ := ordered[i].Minutes()
		mj, _ := ordered[j].Minutes()
		if mi != mj {
			return mi > mj
		}
		return ordered[i] < ordered[j]
	})
	for _, tf := range ordered {
		if bias := frameBias(frames[tf]); bias != "range" {
			return bias
		}
	}
	return "range"
}

// frameBias ports _bias_from_tf.
func frameBias(frame *analysiscontext.LegacyFrame) string {
	switch {
	case frame.Structure == "up" && frame.Momentum != momentum.Bear:
		return "up"
	case frame.Structure == "down" && frame.Momentum != momentum.Bull:
		return "down"
	case frame.Momentum == momentum.Bull:
		return "up"
	case frame.Momentum == momentum.Bear:
		return "down"
	default:
		return "range"
	}
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
