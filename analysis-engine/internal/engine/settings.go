package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// Settings is every config-derived value the engine needs to run a
// symbol, assembled once from config.Document by LoadSettings — the
// single point where config (rank 1) becomes reachable by the domain
// packages engine wires together (see config.go's own doc comment).
type Settings struct {
	ATR              ATRSettings
	Structure        structure.Settings
	Liquidity        liquidity.Config
	Zone             zone.Config
	Trendline        trendline.Config
	KeyLevel         keylevel.Config
	Session          session.Config
	Fib              fib.Config
	Strategies       []strategy.Config
	HistoryDepths    map[market.Timeframe]int
	PrimaryTimeframe market.Timeframe

	// ConfigVersion/ConfigFingerprint are the resolved document's own
	// whole-document provenance (ConfigProvenanceFromConfig — the SAME
	// value already used for Kafka envelope provenance), computed once
	// here rather than per event. Phase S8 uses these to overwrite each
	// Candidate's own Provenance.ConfigVersion/ConfigFingerprint before it
	// reaches the OpportunityBook, replacing the narrower per-strategy-
	// parameters placeholder every Phase S7 strategy computes itself (a
	// strategy has no *config.Document access — see each strategy's own
	// configFingerprint doc comment for why that placeholder existed).
	ConfigVersion     int
	ConfigFingerprint string

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
	zoneConfig, err := ZoneConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	trendlineConfig, err := TrendlineConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	keyLevelConfig, err := KeyLevelConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	sessionConfig, err := SessionConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	fibConfig, err := FibConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	strategyConfigs, err := StrategyConfigsFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	depths, err := HistoryDepthsFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	configProvenance, err := ConfigProvenanceFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	return Settings{
		ATR: atr, Structure: structureSettings, Liquidity: liquidityConfig, Zone: zoneConfig,
		Trendline: trendlineConfig, KeyLevel: keyLevelConfig, Session: sessionConfig, Fib: fibConfig, Strategies: strategyConfigs,
		HistoryDepths: depths, PrimaryTimeframe: primary,
		AllowReplaceForming: allowReplaceForming,
		ConfigVersion:       configProvenance.Version,
		ConfigFingerprint:   configProvenance.Fingerprint,
	}, nil
}
