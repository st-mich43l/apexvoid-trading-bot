package engine

import (
	"fmt"
	"sync"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
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
			instances = techniquezone.InstancesFromScoredZones(exec.TechniqueZones, bars, execATR, settings.TechniqueZones.Technique)
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
