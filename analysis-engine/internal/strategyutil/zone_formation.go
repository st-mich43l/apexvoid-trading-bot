package strategyutil

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// ZoneModelSwing and ZoneModelLegacy are the two zone definitions of supply and demand. Swing anchors a zone on a classified major swing and its departure
// (structure.FormSwingZones); legacy is the earlier definition (the range of the three candles before a displacement leg), kept as the explicit rollback: set
// zone_model: legacy and the strategy finds zones as it did before SD-3 (the lifecycle fixes of SD-1 still apply).
const (
	ZoneModelSwing  = "swing"
	ZoneModelLegacy = "legacy"
)

// ZoneFormation is the parsed zone-definition configuration of a supply or demand strategy. The zero value is the legacy model.
type ZoneFormation struct {
	Model string
	// MinimumStrength is the weakest zone that may publish; a weaker one stays context.
	MinimumStrength structure.Strength
	// EntryCandidates selects the SD-5 entry: evaluate candidate prices inside the whole structural zone (entry_model: candidates). entry_model: proximal is the earlier
	// entry (the zone's proximal slice, cut to the stop cap by the stop budget) and is the rollback of the entry alone.
	EntryCandidates bool
	// EntryMaxDepthFraction is the deepest entry, as a share of the structure from its proximal edge (entry_max_depth_fraction, in (0, 1]).
	EntryMaxDepthFraction float64
	Zone                  structure.ZoneConfig
}

// Swing reports whether the swing-anchored model is in force.
func (f ZoneFormation) Swing() bool { return f.Model == ZoneModelSwing }

// ParseZoneFormation reads the zone-definition leaves of a supply or demand strategy. maximumZoneATR is the strategy's own maximum_zone_atr (the one width limit).
// Every key is required: a strategy never trades a guessed threshold.
func ParseZoneFormation(params map[string]any, maximumZoneATR float64) (ZoneFormation, error) {
	var f ZoneFormation
	model, ok := params["zone_model"].(string)
	if !ok || (model != ZoneModelSwing && model != ZoneModelLegacy) {
		return f, fmt.Errorf("parameter %q must be %q or %q", "zone_model", ZoneModelSwing, ZoneModelLegacy)
	}
	f.Model = model
	entryModel, ok := params["entry_model"].(string)
	if !ok || (entryModel != "candidates" && entryModel != "proximal") {
		return f, fmt.Errorf("parameter %q must be %q or %q", "entry_model", "candidates", "proximal")
	}
	f.EntryCandidates = entryModel == "candidates"
	strength, ok := params["minimum_zone_strength"].(string)
	if !ok {
		return f, fmt.Errorf("parameter %q must be weak, moderate or strong", "minimum_zone_strength")
	}
	switch strength {
	case "weak":
		f.MinimumStrength = structure.StrengthWeak
	case "moderate":
		f.MinimumStrength = structure.StrengthModerate
	case "strong":
		f.MinimumStrength = structure.StrengthStrong
	default:
		return f, fmt.Errorf("parameter %q must be weak, moderate or strong, got %q", "minimum_zone_strength", strength)
	}
	z := &f.Zone
	z.MaximumZoneATR = maximumZoneATR
	floats := map[string]*float64{
		"swing_major_prominence_atr": &z.Major.MajorProminenceATR, "swing_intermediate_prominence_atr": &z.Major.IntermediateProminenceATR,
		"swing_major_departure_atr": &z.Major.MajorDepartureATR, "swing_intermediate_departure_atr": &z.Major.IntermediateDepartureATR,
		"base_max_candle_atr": &z.BaseMaxCandleATR, "minimum_zone_atr": &z.MinimumZoneATR, "strong_departure_atr": &z.StrongDepartureATR,
	}
	ints := map[string]*int{
		"swing_pivot_left_bars": &z.Major.PivotLeftBars, "swing_pivot_right_bars": &z.Major.PivotRightBars, "swing_departure_bars": &z.Major.DepartureBars,
		"swing_external_bars": &z.Major.ExternalBars, "swing_relevance_bars": &z.Major.RelevanceBars,
		"base_departure_gap_bars": &z.DepartureGapBars, "base_max_candles": &z.BaseMaxCandles,
	}
	depth := 0.0
	floats["entry_max_depth_fraction"] = &depth
	if err := ParseNumbers("zone formation", params, floats, ints); err != nil {
		return f, err
	}
	if !(depth > 0) || depth > 1 {
		return f, fmt.Errorf("parameter %q must be in (0, 1]", "entry_max_depth_fraction")
	}
	f.EntryMaxDepthFraction = depth
	if err := z.Valid(); err != nil {
		return f, err
	}
	return f, nil
}
