package engine

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyzone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// LegacyZonesSettings turns on the Python-parity zone population for the
// primary timeframe: supply/demand, order-block, FVG and iFVG zones are the
// legacy technique instances (internal/legacyzone) instead of every live zone
// the redesigned zone package tracks. Technique holds the per-symbol fields
// (pip size, entry-width cap) set by ApplyInstrument.
type LegacyZonesSettings struct {
	Enabled    bool
	WindowBars int
	Technique  legacyzone.TechniqueSettings
}

// LegacyZonesSettingsFromConfig reads analysis.legacy_zones.*.
func LegacyZonesSettingsFromConfig(doc *config.Document) (LegacyZonesSettings, error) {
	enabled, ok := doc.Get("analysis.legacy_zones.enabled")
	if !ok {
		return LegacyZonesSettings{}, fmt.Errorf("config: missing analysis.legacy_zones.enabled")
	}
	on, isBool := enabled.(bool)
	if !isBool {
		return LegacyZonesSettings{}, fmt.Errorf("config: analysis.legacy_zones.enabled must be a boolean")
	}
	window, err := getFloat(doc, "analysis.legacy_zones.window_bars")
	if err != nil {
		return LegacyZonesSettings{}, err
	}
	if window < 50 {
		return LegacyZonesSettings{}, fmt.Errorf("config: analysis.legacy_zones.window_bars must be >= 50")
	}
	return LegacyZonesSettings{Enabled: on, WindowBars: int(window), Technique: legacyzone.ProductionTechniqueSettings()}, nil
}

// legacyChain is the production Python detector settings the chain runs with
// (the golden parity test pins them to the Python values).
var legacyChain = legacyzone.ProductionChainSettings()

// legacyZoneState rebuilds the primary timeframe's tradeable zones from the
// legacy chain over the last WindowBars closed bars and keeps the redesigned
// package's other kinds (breaker, flip) untouched.
func legacyZoneState(candles []market.Candle, original zone.ZoneState, s LegacyZonesSettings) zone.ZoneState {
	if len(candles) < 10 {
		return original
	}
	bars := candles
	if len(bars) > s.WindowBars {
		bars = bars[len(bars)-s.WindowBars:]
	}
	instances := legacyzone.TechniqueInstances(bars, legacyChain, s.Technique)
	out := zone.ZoneState{ATR: original.ATR}
	for _, z := range original.Zones {
		switch z.Kind {
		case zone.KindSupply, zone.KindDemand, zone.KindOrderBlock, zone.KindFVG, zone.KindIFVG:
			continue
		}
		out.Zones = append(out.Zones, z)
	}
	for _, in := range instances {
		out.Zones = append(out.Zones, legacyInstanceZone(in, bars))
	}
	return out
}

func legacyInstanceZone(in legacyzone.Instance, bars []market.Candle) zone.Zone {
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
	// Strategies clip the entry themselves; the zone carries the full
	// structural band.
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
		// Identity by anchor time, not the window-relative index: the index
		// shifts as the window slides, the anchor bar does not.
		ID:   fmt.Sprintf("legacy:%s:%s:%d:%.6f:%.6f", in.Technique, in.Side, originTime, low, high),
		Kind: kind, Side: side,
		Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureIntermediate, Timeframe: market.M5,
		OriginTime: originTime, CreatedAt: originTime, TouchCount: in.Touches,
		Strength: 1, State: state, Relevance: zone.Immediate,
	}
}
