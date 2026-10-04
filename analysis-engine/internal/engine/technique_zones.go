package engine

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyzone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// TechniqueZoneSettings configures the frozen-Python-parity technique
// geometry that feeds the one canonical production zone context. It is not an
// authority selector: this Go implementation is always active and Python is
// never called at runtime.
type TechniqueZoneSettings struct {
	WindowBars int
	Technique  legacyzone.TechniqueSettings
}

func productionTechniqueZoneSettings() TechniqueZoneSettings {
	return TechniqueZoneSettings{WindowBars: 400, Technique: legacyzone.ProductionTechniqueSettings()}
}

var techniqueChain = legacyzone.ProductionChainSettings()

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
	instances := legacyzone.TechniqueInstances(bars, techniqueChain, s.Technique)
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

func techniqueInstanceZone(in legacyzone.Instance, bars []market.Candle) zone.Zone {
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
