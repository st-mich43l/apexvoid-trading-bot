package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/arbitration"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/candle"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyread"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/mad"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
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
	Candle     candle.Config
	Confluence confluence.Config
	ATR        ATRSettings
	Structure  structure.Settings
	Liquidity  liquidity.Config
	Zone       zone.Config
	Trendline  trendline.Config
	KeyLevel   keylevel.Config
	Session    session.Config
	Fib        fib.Config
	Momentum   momentum.Config
	LegacyRead legacyread.Config
	// LegacyDetector are the shared frozen-detector thresholds injected into the
	// detector-contract strategies' parameters.
	LegacyDetector   map[string]any
	Regime           regime.Config
	MAD              mad.Config
	Arbitration      arbitration.Config
	TechniqueZones   TechniqueZoneSettings
	StopEnvelope     StopEnvelopeConfig
	Strategies       []strategy.Config
	HistoryDepths    map[market.Timeframe]int
	PrimaryTimeframe market.Timeframe

	// Geometry is left zero-value by LoadSettings (LoadSettings is symbol-
	// agnostic) — callers that need per-candidate stop-envelope pip
	// conversion (SymbolWorker) set it once per symbol, right after
	// LoadSettings returns, via config.Document.GeometryFor(symbol). Tests
	// that never populate a StopEnvelope may safely leave it zero.
	Geometry market.Geometry

	// DefendedLevels/DefendedLevelBuffer are set per symbol by
	// ApplyInstrument, like Geometry; see BlockedByDefendedLevel.
	DefendedLevels      []float64
	DefendedLevelBuffer float64

	// InstrumentStopMinPips/InstrumentStopMaxPips are resolved from the
	// concrete instrument (including its pack) by ApplyInstrument.  They are
	// intentionally separate from the global execution-family defaults: the
	// same strategy family has different safe envelopes on EURUSD, GBPJPY and
	// XAU.  InstrumentStopEnvelopeConfigured is false only for unit tests or
	// callers that have not applied an instrument yet; production registration
	// always applies it and therefore fails closed if the config is missing.
	InstrumentStopMinPips            float64
	InstrumentStopMaxPips            float64
	InstrumentStopEnvelopeConfigured bool

	// ConfigVersion/ConfigFingerprint are the resolved document's own
	// whole-document provenance (ConfigProvenanceFromConfig — the SAME
	// value already used for Kafka envelope provenance), computed once
	// here rather than per event. The worker uses these to overwrite each
	// Candidate's own Provenance.ConfigVersion/ConfigFingerprint before it
	// reaches the OpportunityBook, replacing the narrower per-strategy-
	// parameters placeholder every strategy computes itself (a
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

	// ObserveOnly names the strategies this instrument analyses but never trades
	// (instruments.<SYMBOL>.overrides.execution.go_opportunity.
	// observe_only_strategies, the same leaf Algo Bot's adapter reads). Their
	// opportunities are still produced and published, but they take no part in
	// arbitration: a contained setup must not suppress or hold an executable one.
	ObserveOnly map[opportunity.StrategyID]bool

	// OnEvaluation, when set, receives every strategy candidate produced by
	// one closed bar after the engine has attached its technical/confluence
	// context and before the lifecycle de-duplicates it. It observes only; it
	// can neither change nor suppress a candidate. Production leaves it nil;
	// replay and detector-parity tooling use it to see per-bar detections.
	OnEvaluation func(Evaluation)
}

// Evaluation is the per-bar strategy output delivered to Settings.OnEvaluation.
type Evaluation struct {
	Symbol     market.Symbol
	Timeframe  market.Timeframe
	BarTime    int64
	Candidates []opportunity.Candidate
	// Context is the canonical context the strategies just read. Observers
	// must treat it as read-only.
	Context *analysiscontext.MarketContext
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
	confluenceConfig, err := ConfluenceConfigFromConfig(doc)
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
	momentumConfig, err := MomentumConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	legacyDetector, err := LegacyDetectorParametersFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	arbitrationConfig, err := ArbitrationConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	regimeConfig, err := RegimeConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	legacyReadConfig, err := LegacyReadConfigFromConfig(doc, atr, fibConfig, regimeConfig, momentumConfig, sessionConfig, trendlineConfig)
	if err != nil {
		return Settings{}, err
	}
	madConfig, err := MADConfigFromConfig(doc)
	if err != nil {
		return Settings{}, err
	}
	stopEnvelopeConfig, err := StopEnvelopeConfigFromConfig(doc)
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
	settings := Settings{
		Candle: candle.DefaultConfig(), Confluence: confluenceConfig, ATR: atr, Structure: structureSettings, Liquidity: liquidityConfig, Zone: zoneConfig,
		Trendline: trendlineConfig, KeyLevel: keyLevelConfig, Session: sessionConfig, Fib: fibConfig, Momentum: momentumConfig, LegacyRead: legacyReadConfig, LegacyDetector: legacyDetector,
		Arbitration: arbitrationConfig, Regime: regimeConfig, MAD: madConfig, TechniqueZones: productionTechniqueZoneSettings(), StopEnvelope: stopEnvelopeConfig, Strategies: strategyConfigs,
		HistoryDepths: depths, PrimaryTimeframe: primary,
		AllowReplaceForming: allowReplaceForming,
		ConfigVersion:       configProvenance.Version,
		ConfigFingerprint:   configProvenance.Fingerprint,
	}
	// Seed CRT with the canonical defaults so construction remains fail-closed
	// before a symbol is attached. ApplyInstrument replaces the two geometry
	// values with the concrete instrument values in production.
	applyParityCRT(&settings)
	settings.LegacyRead.PipSize = settings.TechniqueZones.Technique.PipSize
	settings.LegacyRead.RoundStep = settings.KeyLevel.RoundStep
	applyLegacyDetector(&settings)
	// Seed the Breakout Retest Scalp the same way so construction is fail-closed
	// before a symbol is attached; ApplyInstrument sets the instrument scale.
	if err := applyScalpBreakoutRetest(&settings, doc); err != nil {
		return Settings{}, err
	}
	return settings, nil
}
