package engine

import (
	"fmt"
	"sync"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// TechniqueZoneSettings configures the frozen-Python-parity technique
// geometry that feeds the one canonical production zone context. It is not an
// authority selector: this Go implementation is always active and Python is
// never called at runtime.
type TechniqueZoneSettings struct {
	WindowBars int
	Technique  techniquezone.TechniqueSettings
}

func productionTechniqueZoneSettings() TechniqueZoneSettings {
	return TechniqueZoneSettings{WindowBars: 400, Technique: techniquezone.ProductionTechniqueSettings()}
}

var techniqueChain = techniquezone.ProductionChainSettings()

// techniqueZoneState replaces the trade-qualification population for the
// technique families whose geometry has exact frozen-Python parity. The V2
// zone book still owns every other kind and the resulting ZoneState is the
// sole context consumed by strategies.
func techniqueZoneState(candles []market.Candle, original zone.ZoneState, s TechniqueZoneSettings) zone.ZoneState {
	if len(candles) < 10 || s.WindowBars < 50 {
		return original
	}
	bars := candles
	if len(bars) > s.WindowBars {
		bars = bars[len(bars)-s.WindowBars:]
	}
	instances := techniquezone.TechniqueInstances(bars, techniqueChain, s.Technique)
	out := zone.ZoneState{ATR: original.ATR}
	for _, z := range original.Zones {
		switch z.Kind {
		case zone.KindSupply, zone.KindDemand, zone.KindOrderBlock, zone.KindFVG, zone.KindIFVG:
			continue
		}
		out.Zones = append(out.Zones, z)
	}
	for _, in := range instances {
		out.Zones = append(out.Zones, techniqueInstanceZone(in, bars))
	}
	return out
}

func techniqueInstanceZone(in techniquezone.Instance, bars []market.Candle) zone.Zone {
	kind := zone.KindSupply
	switch in.Technique {
	case "supply_demand":
		if in.Side == "buy" {
			kind = zone.KindDemand
		}
	case "order_block":
		kind = zone.KindOrderBlock
	case "fvg":
		kind = zone.KindFVG
	case "ifvg":
		kind = zone.KindIFVG
	}
	side := zone.Supply
	if in.Side == "buy" {
		side = zone.Demand
	}
	low, high := in.Low, in.High
	if in.EntryClipped {
		low, high = in.StructuralLow, in.StructuralHigh
	}
	originTime := int64(0)
	if in.OriginIndex >= 0 && in.OriginIndex < len(bars) {
		originTime = bars[in.OriginIndex].Time
	}
	state := zone.StateFresh
	if in.Touches > 0 {
		state = zone.StateTouched
	}
	return zone.Zone{
		ID:   fmt.Sprintf("technique:%s:%s:%d:%.6f:%.6f", in.Technique, in.Side, originTime, low, high),
		Kind: kind, Side: side, Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureIntermediate, Timeframe: market.M5,
		OriginTime: originTime, CreatedAt: originTime, TouchCount: in.Touches,
		Strength: 1, LegacyScore: in.Score, State: state, Relevance: zone.Immediate,
	}
}

// techniqueInstanceSource returns the lazily computed technique instances the
// frozen technique detectors publish from (collect_technique_instances): the
// zone-chain techniques collected from the execution frame's scored unmerged
// zones, plus the CRT instances the H1 frame and the execution window produce.
// The result is memoised for the frame's one closed-bar evaluation.
func techniqueInstanceSource(exec *analysiscontext.LegacyFrame, h1 *analysiscontext.LegacyFrame, settings Settings) func() []techniquezone.Instance {
	var once sync.Once
	var instances []techniquezone.Instance
	return func() []techniquezone.Instance {
		once.Do(func() {
			bars := exec.Bars
			if len(bars) < 10 {
				return
			}
			execATR := techniquezone.ATRScalar(exec.ATR, 1)
			instances = techniquezone.InstancesFromScoredZones(exec.TechniqueZones, bars, techniquezone.ATRScalar(exec.ATR, 1), settings.TechniqueZones.Technique)
			if h1 == nil || len(h1.Bars) == 0 {
				return
			}
			crt := techniquezone.ProductionCRTSettings()
			crt.MinATR = crtMinimumH1RangeATR(settings, crt.MinATR)
			crt.EntryMaxWidthPrice = settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice
			h1ATR := techniquezone.ATRScalar(h1.ATR, 1)
			instances = append(instances, techniquezone.CollectCRT(h1.Bars, bars, h1ATR, execATR, crt, settings.TechniqueZones.Technique)...)
		})
		return instances
	}
}

// structuralInstanceSource yields the execution frame's zone-chain instances
// (without CRT) judged only on what they are as of a given candle, never on where
// price is: the facts Confluence Zone reads. It is the instance collection above
// with the retest-proximity test off and the judgement re-run on the candles before
// asOf; the frozen publishers never see it. Results are memoised per asOf for the
// frame's one closed-bar evaluation.
func structuralInstanceSource(exec *analysiscontext.LegacyFrame, settings Settings) func(int) []techniquezone.Instance {
	var mu sync.Mutex
	cache := map[int][]techniquezone.Instance{}
	return func(asOf int) []techniquezone.Instance {
		mu.Lock()
		defer mu.Unlock()
		if got, ok := cache[asOf]; ok {
			return got
		}
		technique := settings.TechniqueZones.Technique
		technique.IgnoreProximity = true
		got := techniquezone.InstancesFromScoredZonesAsOf(exec.TechniqueZones, exec.Bars, exec.ATR, asOf, technique)
		cache[asOf] = got
		return got
	}
}

// higherTimeframeTechniques are the techniques exported from a higher timeframe's own scored zones: the ones the zone strategies (supply, demand,
// order_block, fvg) read. Every other instance (CRT, iFVG, ...) stays on the execution frame.
var higherTimeframeTechniques = map[string]bool{"supply_demand": true, "order_block": true, "fvg": true}

// exportHigherTimeframe keeps the instances of the techniques the zone strategies read and stamps each with the timeframe it was found on.
func exportHigherTimeframe(all []techniquezone.Instance, tf market.Timeframe) []techniquezone.Instance {
	var out []techniquezone.Instance
	for _, in := range all {
		if !higherTimeframeTechniques[in.Technique] {
			continue
		}
		in.Timeframe = string(tf)
		out = append(out, in)
	}
	return out
}

// higherTimeframeInstanceSource lazily yields the supply/demand, order block and fair value gap instances of a higher timeframe's own scored zones
// (same collection the execution frame uses, on that frame's bars and ATR). OriginIndex is relative to that frame; the strategy that binds them to
// the execution frame maps it by bar time.
//
// The structure is judged as a fact (spent, invalidated, width, FVG fill and minimum, order block body and BOS), not by where the higher frame's
// own last close sits: IgnoreProximity is on. With it off, an H1 zone was offered only if the last CLOSED H1 bar closed in it or at its proximal
// edge, so an M5 wick into the zone in the middle of an hour (after an H1 bar that closed away from it) found no instance. Proximity is decided
// where the trade is executed, by the M5 EntryValid check in TechniqueSource.publish.
func higherTimeframeInstanceSource(frame *analysiscontext.LegacyFrame, tf market.Timeframe, settings Settings) func() []techniquezone.Instance {
	var once sync.Once
	var instances []techniquezone.Instance
	return func() []techniquezone.Instance {
		once.Do(func() {
			if len(frame.Bars) < 10 {
				return
			}
			technique := settings.TechniqueZones.Technique
			technique.IgnoreProximity = true
			instances = exportHigherTimeframe(techniquezone.InstancesFromScoredZones(frame.TechniqueZones, frame.Bars, techniquezone.ATRScalar(frame.ATR, 1), technique), tf)
		})
		return instances
	}
}

// crtMinimumH1RangeATR reads the CRT strategy's own impulse threshold.
func crtMinimumH1RangeATR(settings Settings, fallback float64) float64 {
	for _, cfg := range settings.Strategies {
		if cfg.ID != "crt" {
			continue
		}
		if value, ok := cfg.Parameters["minimum_h1_range_atr"].(float64); ok && value > 0 {
			return value
		}
	}
	return fallback
}

// orderBlockBreakBuffer is strategies.order_block.confirmed_break_buffer_atr, 0 when absent (the rule is off).
func orderBlockBreakBuffer(settings Settings) float64 {
	for _, cfg := range settings.Strategies {
		if cfg.ID == "order_block" {
			if v, ok := cfg.Parameters["confirmed_break_buffer_atr"].(float64); ok && v > 0 {
				return v
			}
		}
	}
	return 0
}

// structureBreakBuffer is the break buffer, in price units, of a live opportunity's structure on the timeframe whose bar just closed: the strategy's buffer
// in ATRs times that frame's ATR at the closed bar (the detector-contract frame's, the same series the instance definition judges with; the canonical ATR
// only where the frame has none). A strategy without the rule has no buffer, so none of its opportunities is retired this way.
func (w *SymbolWorker) structureBreakBuffer(tf market.Timeframe, canonicalATR []float64) func(opportunity.StrategyID) (float64, bool) {
	atrs := orderBlockBreakBuffer(w.settings)
	if atrs <= 0 {
		return nil
	}
	frameATR := 0.0
	if frame := w.state.Context.Timeframes[tf]; frame != nil && frame.Legacy != nil && len(frame.Legacy.ATR) > 0 {
		frameATR = frame.Legacy.ATR[len(frame.Legacy.ATR)-1]
	}
	if !(frameATR > 0) && len(canonicalATR) > 0 {
		frameATR = canonicalATR[len(canonicalATR)-1]
	}
	if !(frameATR > 0) {
		return nil
	}
	return func(id opportunity.StrategyID) (float64, bool) {
		if id != "order_block" {
			return 0, false
		}
		return atrs * frameATR, true
	}
}
