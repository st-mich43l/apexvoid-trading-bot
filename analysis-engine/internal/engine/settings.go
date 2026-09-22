package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Settings is every config-derived value the engine needs to run a
// symbol, assembled once from config.Document by LoadSettings — the
// single point where config (rank 1) becomes reachable by the domain
// packages engine wires together (see config.go's own doc comment).
type Settings struct {
	ATR              ATRSettings
	Structure        structure.Settings
	Liquidity        liquidity.Config
	HistoryDepths    map[market.Timeframe]int
	PrimaryTimeframe market.Timeframe

	// AllowReplaceForming controls whether the SAME timestamp arriving
	// twice replaces the retained candle (a live-updating forming bar) or
	// is reported as a duplicate — see marketdata.NewTimeframeHistory's
	// own doc comment. False (closed-bar-only feeds, replay, and cmd/replay)
	// is the safe default; a live feed that streams the forming bar sets
	// this true at construction.
	AllowReplaceForming bool
}

// LoadSettings reads every Analysis Engine V2 config leaf this package
// needs from an already-resolved Configuration V3 document
// (internal/config.ResolveDocument's result) — never a second
// configuration authority (source task §68).
func LoadSettings(doc *config.Document, primary market.Timeframe, allowReplaceForming bool) (Settings, error) {
	atr, err := ATRSettingsFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	structureSettings, err := StructureSettingsFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	liquidityConfig, err := LiquidityConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	depths, err := HistoryDepthsFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	return Settings{
		ATR: atr, Structure: structureSettings, Liquidity: liquidityConfig,
		HistoryDepths: depths, PrimaryTimeframe: primary,
		AllowReplaceForming: allowReplaceForming,
	}, nil
}
