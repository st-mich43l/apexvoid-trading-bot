package strategyutil

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// TechniqueGeometry is the legacy Python technique validation
// (app/analysis/technique_geometry.py::validate_technique_instance and the
// entry clip) that decided which zones a detector was allowed to publish. The
// Go zone strategies emitted a confirmed reaction for any live zone; Python
// published only a zone price was AT, of a sane width, and clipped its entry to
// the proximal slice.
type TechniqueGeometry struct {
	// PipSize is the instrument's pip (override-only parameter set per symbol
	// by the engine; zero when absent, leaving only the ATR fraction).
	PipSize float64
	// EpsilonATRFraction scales the "price is at the zone edge" tolerance.
	EpsilonATRFraction float64
	// MaximumZoneATR caps a zone's width in ATR (FVG has its own, tighter cap).
	MaximumZoneATR float64
	// EntryMaxWidthPrice caps the tradeable entry width in price units
	// (instrument price_scale.fvg_entry_max_width_price); the full zone stays
	// the structural band.
	EntryMaxWidthPrice float64
	// MomentumBodyFraction is the legacy order-block rule: the origin bar's
	// body must be at least this fraction of its range. Zero disables it.
	MomentumBodyFraction float64
}

// ParseTechniqueGeometry reads the shared technique parameters; pip_size is
// optional (override-only).
func ParseTechniqueGeometry(params map[string]any) (TechniqueGeometry, error) {
	epsilon, err := Float(params, "epsilon_atr_fraction")
	if err != nil {
		return TechniqueGeometry{}, err
	}
	maxZone, err := Float(params, "maximum_zone_atr")
	if err != nil {
		return TechniqueGeometry{}, err
	}
	entryMax, err := Float(params, "entry_max_width_price")
	if err != nil {
		return TechniqueGeometry{}, err
	}
	if epsilon < 0 || maxZone <= 0 || entryMax <= 0 {
		return TechniqueGeometry{}, fmt.Errorf("technique geometry parameters must be positive")
	}
	g := TechniqueGeometry{EpsilonATRFraction: epsilon, MaximumZoneATR: maxZone, EntryMaxWidthPrice: entryMax}
	if raw, ok := params["momentum_body_fraction"]; ok {
		v, err := Float(map[string]any{"momentum_body_fraction": raw}, "momentum_body_fraction")
		if err != nil {
			return TechniqueGeometry{}, err
		}
		g.MomentumBodyFraction = v
	}
	if raw, ok := params["pip_size"]; ok {
		pip, err := Float(map[string]any{"pip_size": raw}, "pip_size")
		if err != nil {
			return TechniqueGeometry{}, err
		}
		g.PipSize = pip
	}
	return g, nil
}

func (g TechniqueGeometry) epsilon(atr float64) float64 {
	return math.Max(g.PipSize, g.EpsilonATRFraction*math.Max(0, atr))
}

// ProximalRetest mirrors proximal_retest: price is at the near edge (within
// epsilon) or inside the zone.
func (g TechniqueGeometry) ProximalRetest(direction market.Direction, low, high, price, atr float64) bool {
	e := g.epsilon(atr)
	proximal := low
	if direction == market.Buy {
		proximal = high
	}
	return math.Abs(price-proximal) <= e || (low-e <= price && price <= high+e)
}

// WidthWithinATR mirrors width_within_atr.
func WidthWithinATR(low, high, atr, maxATR float64) bool {
	if atr <= 0 {
		return true
	}
	return high-low <= math.Max(0, maxATR)*atr
}

// ClipEntry mirrors optimize_technique_entry_zone: a wide zone keeps only its
// proximal slice (supply keeps the lower edge, demand the upper edge).
func (g TechniqueGeometry) ClipEntry(direction market.Direction, low, high float64) (float64, float64, bool) {
	if !(high > low) || !(g.EntryMaxWidthPrice > 0) || high-low <= g.EntryMaxWidthPrice+1e-12 {
		return low, high, false
	}
	if direction == market.Sell {
		return low, low + g.EntryMaxWidthPrice, true
	}
	return high - g.EntryMaxWidthPrice, high, true
}

// FVGNotFullyFilled mirrors fvg_not_fully_filled: no bar after the gap's
// creation has traded through its far edge.
func FVGNotFullyFilled(direction market.Direction, low, high float64, candles []market.Candle, createdAt int64) bool {
	for _, c := range candles {
		if c.Time <= createdAt {
			continue
		}
		if direction == market.Buy && c.Low <= low {
			return false
		}
		if direction == market.Sell && c.High >= high {
			return false
		}
	}
	return true
}

// BodyFractionAt returns the body/range fraction of the bar at time t (0 when
// absent), as the legacy order-block momentum check measured it.
func BodyFractionAt(candles []market.Candle, t int64) float64 {
	for _, c := range candles {
		if c.Time == t {
			full := math.Max(c.High-c.Low, 1e-9)
			return c.Body() / full
		}
	}
	return 0
}

// LastClose is the latest closed bar's close, or 0 with no bars.
func LastClose(candles []market.Candle) float64 {
	if len(candles) == 0 {
		return 0
	}
	return candles[len(candles)-1].Close
}
