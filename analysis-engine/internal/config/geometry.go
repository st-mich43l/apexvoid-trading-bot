package config

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// GeometryFor builds a market.Geometry for symbol straight from the loaded
// manifest's `instrument_runtimes.<symbol>.units` block. This is the only
// place manifest field names should be translated into engine-internal
// geometry — every other package asks for a market.Geometry, never reads
// the manifest's `units` shape directly (§13: the manifest stays the one
// configuration authority; this is its single Go entry point).
func (m *Manifest) GeometryFor(symbol string) (market.Geometry, error) {
	rt, err := m.Instrument(symbol)
	if err != nil {
		return market.Geometry{}, err
	}
	return market.NewGeometry(
		rt.Identity.CanonicalSymbol,
		rt.Identity.BrokerSymbol,
		float64(rt.Units.PipSize),
		rt.Units.PriceDigits,
	)
}
