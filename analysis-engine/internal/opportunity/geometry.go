package opportunity

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// NormalizeGeometry aligns the technical prices carried by a Candidate to
// the instrument's configured broker tick. The direction of each rounding
// operation is deliberate:
//
//   - the entry band is widened (floor low, ceil high), never narrowed;
//   - a BUY invalidation is rounded down and a SELL invalidation up, keeping
//     the stop at least as protective as the unrounded technical value;
//   - BUY targets are rounded down and SELL targets up, avoiding a target
//     farther away than the strategy actually established.
//
// The function changes no identity, timestamps, evidence, quality, or policy
// fields. It is therefore safe at the Go technical-opportunity publication
// boundary and keeps execution policy responsible for any later executable
// quote, spread, ladder, and broker-volume decisions.
func NormalizeGeometry(candidate Candidate, geometry market.Geometry) (Candidate, error) {
	// Some isolated strategy tests construct Settings without applying an
	// instrument manifest. Production workers always receive Geometry through
	// ApplyInstrument; preserve those unit-test candidates until the manifest
	// is present rather than inventing a tick size.
	if geometry.Symbol == "" || geometry.PipSize <= 0 {
		return candidate, nil
	}
	if string(candidate.Symbol) != geometry.Symbol {
		return Candidate{}, fmt.Errorf("opportunity: geometry symbol %s does not match candidate symbol %s", geometry.Symbol, candidate.Symbol)
	}

	candidate.Entry.Low = geometry.FloorToTick(candidate.Entry.Low)
	candidate.Entry.High = geometry.CeilToTick(candidate.Entry.High)
	if candidate.Entry.Low >= candidate.Entry.High {
		// A valid raw band can be narrower than one tick. Preserve a usable
		// positive-width broker band instead of publishing a degenerate one.
		candidate.Entry.High = geometry.CeilToTick(candidate.Entry.Low + tickSize(geometry))
	}

	invalid := float64(candidate.Invalidation.Price)
	targets := append([]Target(nil), candidate.Targets...)
	switch candidate.Direction {
	case market.Buy:
		invalid = geometry.FloorToTick(invalid)
		if invalid >= candidate.Entry.Low {
			invalid = geometry.FloorToTick(candidate.Entry.Low - tickSize(geometry))
		}
		for i := range targets {
			price := geometry.FloorToTick(float64(targets[i].Price.Price))
			if price <= candidate.Entry.High {
				price = geometry.CeilToTick(candidate.Entry.High + tickSize(geometry))
			}
			targets[i].Price.Price = market.Price(price)
		}
	case market.Sell:
		invalid = geometry.CeilToTick(invalid)
		if invalid <= candidate.Entry.High {
			invalid = geometry.CeilToTick(candidate.Entry.High + tickSize(geometry))
		}
		for i := range targets {
			price := geometry.CeilToTick(float64(targets[i].Price.Price))
			if price >= candidate.Entry.Low {
				price = geometry.FloorToTick(candidate.Entry.Low - tickSize(geometry))
			}
			targets[i].Price.Price = market.Price(price)
		}
	default:
		return Candidate{}, fmt.Errorf("opportunity: cannot normalize invalid direction %q", candidate.Direction)
	}
	candidate.Invalidation.Price = market.Price(invalid)
	candidate.Targets = targets
	if err := candidate.Validate(); err != nil {
		return Candidate{}, fmt.Errorf("opportunity: normalized candidate is invalid: %w", err)
	}
	return candidate, nil
}

func tickSize(geometry market.Geometry) float64 {
	return 1 / geometryScale(geometry)
}

func geometryScale(geometry market.Geometry) float64 {
	scale := 1.0
	for i := 0; i < geometry.PriceDigits; i++ {
		scale *= 10
	}
	return scale
}
